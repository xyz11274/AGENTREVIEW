"""Summarize paired proxy metrics and apply the preregistered screening gate."""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .activation import ActivationPending, require_activation
from .arms import ARM_SCHEMA_VERSION, arm_identities, lineage_arm_pairs
from .prepare import DESIGN_VERSION, iter_jsonl, load_config
from .run_matrix import matrix_commands

PRIMARY_METRICS = (
    "Correctness_proxy",
    "Thoroughness",
    "Grounding",
    "Verifiability",
    "Clarity",
    "CoCo_Average_proxy",
    "Complete_official",
    "Reference_category_recall",
    "AI_category_count",
    "extra_category_count",
    "META_Average",
)


def mean(values: Iterable[float]) -> float | None:
    items = [float(value) for value in values if value is not None]
    return sum(items) / len(items) if items else None


def _load_scores(config: Mapping[str, Any]):
    output_root = Path(config["output_root"]).expanduser().resolve()
    scores: dict[tuple[str, int, str], dict[str, float]] = {}
    for cell in matrix_commands(config):
        path = (
            output_root
            / "evaluation"
            / "judged"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm_id']}.jsonl"
        )
        for value in iter_jsonl(path):
            if value.get("metric_status") != (
                "CoCo-style proxy screening; not official benchmark"
            ):
                raise RuntimeError(f"Unmarked proxy result in {path}")
            expected_identity = {
                "arm_schema_version": cell["arm_schema_version"],
                "arm_id": cell["arm_id"],
                "arm_kind": cell["arm_kind"],
                "lineage": cell["lineage"],
            }
            actual_identity = {name: value.get(name) for name in expected_identity}
            if actual_identity != expected_identity:
                raise RuntimeError(
                    f"Arm identity mismatch in {path}: "
                    f"expected={expected_identity}, actual={actual_identity}"
                )
            if int(value["replicate"]) != int(cell["replicate"]):
                raise RuntimeError(f"Replicate identity mismatch in {path}")
            key = (
                str(value["paper_id"]),
                int(value["replicate"]),
                str(value["arm_id"]),
            )
            if key in scores:
                raise RuntimeError(f"Duplicate judged score identity: {key}")
            scores[key] = dict(value["paper_metrics"])
    return scores


def _paper_arm_means(
    config: Mapping[str, Any],
    scores: Mapping[tuple[str, int, str], Mapping[str, float]],
):
    papers = {
        str(value["paper_id"])
        for value in iter_jsonl(
            Path(config["output_root"]) / "effectiveness_validation.jsonl"
        )
    }
    output = {}
    configured_arm_ids = [identity.arm_id for identity in arm_identities(config)]
    for paper_id in sorted(papers):
        for arm_id in configured_arm_ids:
            for metric in PRIMARY_METRICS:
                output[(paper_id, arm_id, metric)] = mean(
                    scores[(paper_id, int(replicate), arm_id)].get(metric)
                    for replicate in config["experiment"]["replicates"]
                )
    return papers, output


def _paired_metric_deltas(
    papers: Iterable[str],
    averaged: Mapping[tuple[str, str, str], float | None],
    left_arm_id: str,
    right_arm_id: str,
) -> dict[str, dict[str, Any]]:
    output = {}
    for metric in PRIMARY_METRICS:
        values = [
            averaged[(paper_id, left_arm_id, metric)]
            - averaged[(paper_id, right_arm_id, metric)]
            for paper_id in sorted(papers)
            if averaged[(paper_id, left_arm_id, metric)] is not None
            and averaged[(paper_id, right_arm_id, metric)] is not None
        ]
        output[metric] = {
            "mean_delta": mean(values),
            "positive_papers": sum(value > 0 for value in values),
            "zero_papers": sum(value == 0 for value in values),
            "negative_papers": sum(value < 0 for value in values),
            "paper_count": len(values),
        }
    return output


def _bootstrap_ci(values: list[float], confidence: float, seed: int = 20260730):
    if not values:
        return [None, None]
    rng = random.Random(seed)
    samples = []
    for _ in range(20000):
        resample = [values[rng.randrange(len(values))] for _ in values]
        samples.append(sum(resample) / len(resample))
    samples.sort()
    alpha = 1.0 - float(confidence)
    lower_index = max(0, int((alpha / 2.0) * len(samples)))
    upper_index = min(len(samples) - 1, int((1.0 - alpha / 2.0) * len(samples)) - 1)
    return [samples[lower_index], samples[upper_index]]


def _blind_summary(config: Mapping[str, Any]):
    output_root = Path(config["output_root"]).expanduser().resolve()
    results_path = output_root / "evaluation" / "blind_judge_results.jsonl"
    key_path = output_root / "evaluation" / "blind_pairs_key.jsonl"
    if results_path.is_file():
        results = list(iter_jsonl(results_path))
    else:
        return {
            "available": False,
            "reason": "blind_judge_results.jsonl is pending",
            "by_lineage": {
                lineage: {
                    "available": False,
                    "reason": "blind judge results are pending",
                }
                for lineage, _, _ in lineage_arm_pairs(config)
            },
        }
    key_values = list(iter_jsonl(key_path))
    key_ids = [str(value["pair_id"]) for value in key_values]
    if len(key_ids) != len(set(key_ids)):
        raise ValueError("Blind pair keys contain duplicate pair IDs")
    keys = {str(value["pair_id"]): value for value in key_values}

    configured_pairs = {
        lineage: (seed, evolved) for lineage, seed, evolved in lineage_arm_pairs(config)
    }
    configured_replicates = {int(value) for value in config["experiment"]["replicates"]}
    configured_papers = {
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "effectiveness_validation.jsonl")
    }
    for pair_id, key in keys.items():
        lineage = str(key.get("lineage") or "")
        if lineage not in configured_pairs:
            raise ValueError(
                f"Blind pair key has unknown lineage {lineage!r}: {pair_id}"
            )
        seed, evolved = configured_pairs[lineage]
        expected = {
            "arm_schema_version": ARM_SCHEMA_VERSION,
            "comparison_id": f"{evolved.arm_id}_minus_{seed.arm_id}",
            "seed_arm_id": seed.arm_id,
            "evolved_arm_id": evolved.arm_id,
        }
        actual = {name: key.get(name) for name in expected}
        if actual != expected:
            raise ValueError(
                f"Blind pair arm identity mismatch for {pair_id}: "
                f"expected={expected}, actual={actual}"
            )
        if int(key["replicate"]) not in configured_replicates:
            raise ValueError(f"Blind pair key has unknown replicate for {pair_id}")
        if str(key.get("evolved_position") or "").upper() not in {"A", "B"}:
            raise ValueError(f"Blind pair key has invalid orientation for {pair_id}")

    expected_pair_cells = {
        (paper_id, replicate, lineage)
        for paper_id in configured_papers
        for replicate in configured_replicates
        for lineage in configured_pairs
    }
    actual_pair_cells = [
        (
            str(value["paper_id"]),
            int(value["replicate"]),
            str(value["lineage"]),
        )
        for value in key_values
    ]
    if len(actual_pair_cells) != len(set(actual_pair_cells)):
        raise ValueError("Blind pair keys contain duplicate experiment cells")
    if set(actual_pair_cells) != expected_pair_cells:
        raise ValueError(
            "Blind pair keys differ from the configured paper, replicate, and "
            "lineage cross-product"
        )

    counts = {"evolved": 0, "seed": 0, "tie": 0}
    by_replicate: dict[int, dict[str, int]] = {
        replicate: {"evolved": 0, "seed": 0, "tie": 0}
        for replicate in sorted(configured_replicates)
    }
    lineage_counts = {
        lineage: {"evolved": 0, "seed": 0, "tie": 0} for lineage in configured_pairs
    }
    lineage_replicates = {
        lineage: {
            replicate: {"evolved": 0, "seed": 0, "tie": 0}
            for replicate in sorted(configured_replicates)
        }
        for lineage in configured_pairs
    }
    result_ids = [str(value["pair_id"]) for value in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("Blind judge results contain duplicate pair IDs")
    unknown = sorted(set(result_ids) - set(keys))
    if unknown:
        raise ValueError(f"Blind judge results contain unknown pairs: {unknown}")
    for result in results:
        pair_id = str(result["pair_id"])
        key = keys[pair_id]
        replicate = int(key["replicate"])
        lineage = str(key["lineage"])
        winner = str(result.get("winner") or "").strip().upper()
        if winner == "TIE":
            label = "tie"
        elif winner == str(key["evolved_position"]).upper():
            label = "evolved"
        elif winner in {"A", "B"}:
            label = "seed"
        else:
            raise ValueError(f"Invalid blind winner for {pair_id}: {winner!r}")
        counts[label] += 1
        by_replicate[replicate][label] += 1
        lineage_counts[lineage][label] += 1
        lineage_replicates[lineage][replicate][label] += 1
    decisive = counts["evolved"] + counts["seed"]
    result_id_set = set(result_ids)
    by_lineage = {}
    for lineage in configured_pairs:
        expected_ids = {
            pair_id for pair_id, value in keys.items() if value["lineage"] == lineage
        }
        lineage_result_ids = expected_ids & result_id_set
        lineage_decisive = (
            lineage_counts[lineage]["evolved"] + lineage_counts[lineage]["seed"]
        )
        by_lineage[lineage] = {
            "available": True,
            "complete": lineage_result_ids == expected_ids,
            "expected_pairs": len(expected_ids),
            "result_pairs": len(lineage_result_ids),
            "counts": lineage_counts[lineage],
            "decisive_pairs": lineage_decisive,
            "evolved_win_rate": (
                lineage_counts[lineage]["evolved"] / lineage_decisive
                if lineage_decisive
                else None
            ),
            "by_replicate": lineage_replicates[lineage],
        }
    return {
        "available": True,
        "complete": result_id_set == set(keys),
        "expected_pairs": len(keys),
        "result_pairs": len(result_ids),
        "counts": counts,
        "decisive_pairs": decisive,
        "evolved_win_rate": counts["evolved"] / decisive if decisive else None,
        "by_replicate": by_replicate,
        "by_lineage": by_lineage,
    }


def summarize(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    require_activation(
        config,
        "summary",
        {
            "effectiveness_selection": output_root / "effectiveness_validation.jsonl",
            "judged_outputs": {
                "path": output_root / "evaluation" / "judged",
                "type": "directory",
            },
            "blind_pair_key": output_root / "evaluation" / "blind_pairs_key.jsonl",
            "blind_judge_results": output_root
            / "evaluation"
            / "blind_judge_results.jsonl",
        },
    )
    scores = _load_scores(config)
    papers, averaged = _paper_arm_means(config, scores)
    identities = arm_identities(config)
    baseline_ids = [
        identity.arm_id for identity in identities if identity.arm_kind == "no_memory"
    ]
    if len(baseline_ids) != 1:
        raise ValueError(
            "Effectiveness summary requires exactly one no_memory baseline"
        )
    baseline_arm_id = baseline_ids[0]
    arm_summary = {
        identity.arm_id: {
            metric: mean(
                averaged[(paper_id, identity.arm_id, metric)] for paper_id in papers
            )
            for metric in PRIMARY_METRICS
        }
        for identity in identities
    }
    confidence = float(config["screening_gate"]["paired_bootstrap_confidence"])
    blind = _blind_summary(config)
    threshold = config["screening_gate"]
    paired_deltas = {}
    lineage_reports = {}

    def at_least(value, minimum):
        return value is not None and value >= float(minimum)

    for lineage, seed, evolved in lineage_arm_pairs(config):
        comparison_specs = (
            ("evolved_minus_seed", evolved.arm_id, seed.arm_id),
            ("seed_minus_no_memory", seed.arm_id, baseline_arm_id),
            ("evolved_minus_no_memory", evolved.arm_id, baseline_arm_id),
        )
        lineage_deltas = {}
        comparison_ids = {}
        for label, left_arm_id, right_arm_id in comparison_specs:
            comparison_id = f"{left_arm_id}_minus_{right_arm_id}"
            comparison_ids[label] = comparison_id
            metrics = _paired_metric_deltas(papers, averaged, left_arm_id, right_arm_id)
            lineage_deltas[label] = metrics
            paired_deltas[comparison_id] = metrics

        evolved_seed = lineage_deltas["evolved_minus_seed"]
        coco_paper_deltas = [
            averaged[(paper_id, evolved.arm_id, "CoCo_Average_proxy")]
            - averaged[(paper_id, seed.arm_id, "CoCo_Average_proxy")]
            for paper_id in sorted(papers)
            if averaged[(paper_id, evolved.arm_id, "CoCo_Average_proxy")] is not None
            and averaged[(paper_id, seed.arm_id, "CoCo_Average_proxy")] is not None
        ]
        bootstrap_ci = _bootstrap_ci(coco_paper_deltas, confidence)
        replicate_deltas = {}
        for replicate in config["experiment"]["replicates"]:
            values = []
            for paper_id in papers:
                evolved_value = scores[(paper_id, int(replicate), evolved.arm_id)][
                    "CoCo_Average_proxy"
                ]
                seed_value = scores[(paper_id, int(replicate), seed.arm_id)][
                    "CoCo_Average_proxy"
                ]
                if evolved_value is not None and seed_value is not None:
                    values.append(evolved_value - seed_value)
            replicate_deltas[str(replicate)] = mean(values)

        lineage_blind = (blind.get("by_lineage") or {}).get(
            lineage,
            {
                "available": False,
                "reason": "blind judge results are pending",
            },
        )
        blind_replicates = lineage_blind.get("by_replicate") or {}
        gate_checks = {
            "all_selected_papers_scored": evolved_seed["CoCo_Average_proxy"][
                "paper_count"
            ]
            == len(papers),
            "delta_CoCo_Average_proxy": at_least(
                evolved_seed["CoCo_Average_proxy"]["mean_delta"],
                threshold["delta_CoCo_Average_proxy_min"],
            ),
            "positive_papers": evolved_seed["CoCo_Average_proxy"]["positive_papers"]
            >= int(threshold["positive_papers_min"]),
            "both_replicates_positive": all(
                value is not None and value > 0 for value in replicate_deltas.values()
            ),
            "bootstrap_lower_positive": bootstrap_ci[0] is not None
            and bootstrap_ci[0] > float(threshold["paired_bootstrap_lower_bound_min"]),
            "delta_Correctness_proxy": at_least(
                evolved_seed["Correctness_proxy"]["mean_delta"],
                threshold["delta_Correctness_proxy_min"],
            ),
            "delta_Reference_category_recall": at_least(
                evolved_seed["Reference_category_recall"]["mean_delta"],
                threshold["delta_Reference_category_recall_pp_min"],
            ),
            "delta_Complete_official": at_least(
                evolved_seed["Complete_official"]["mean_delta"],
                threshold["delta_Complete_official_pp_min"],
            ),
            "delta_META_Average": at_least(
                evolved_seed["META_Average"]["mean_delta"],
                threshold["delta_META_Average_min"],
            ),
            "blind_judge_available": bool(lineage_blind.get("available"))
            and bool(lineage_blind.get("complete")),
            "blind_judge_win_rate": bool(lineage_blind.get("available"))
            and lineage_blind.get("evolved_win_rate") is not None
            and lineage_blind["evolved_win_rate"]
            >= float(threshold["blind_judge_evolved_win_rate_min"]),
            "blind_judge_decisive_pairs": bool(lineage_blind.get("available"))
            and int(lineage_blind.get("decisive_pairs") or 0)
            >= int(threshold["blind_judge_decisive_pairs_min"]),
            "blind_judge_each_replicate": bool(lineage_blind.get("available"))
            and len(blind_replicates) == len(config["experiment"]["replicates"])
            and all(
                value["evolved"] > value["seed"] for value in blind_replicates.values()
            ),
        }
        lineage_reports[lineage] = {
            "seed_arm_id": seed.arm_id,
            "evolved_arm_id": evolved.arm_id,
            "comparison_ids": comparison_ids,
            "paired_deltas": lineage_deltas,
            "replicate_evolved_minus_seed_CoCo_Average_proxy": (replicate_deltas),
            "paper_level_paired_bootstrap": {
                "confidence": confidence,
                "interval": bootstrap_ci,
                "resamples": 20000,
                "seed": 20260730,
            },
            "blind_judge": lineage_blind,
            "screening_gate": {
                "checks": gate_checks,
                "passed": all(gate_checks.values()),
            },
        }

    lineage_gate_results = {
        lineage: value["screening_gate"]["passed"]
        for lineage, value in lineage_reports.items()
    }
    report = {
        "design_version": DESIGN_VERSION,
        "arm_schema_version": ARM_SCHEMA_VERSION,
        "metric_status": "CoCo-style proxy screening; not official benchmark",
        "paper_count": len(papers),
        "arm_summary": arm_summary,
        "paired_deltas": paired_deltas,
        "lineages": lineage_reports,
        "blind_judge": blind,
        "screening_gate": {
            "policy": "all_lineages_meet_preregistered_thresholds",
            "lineage_results": lineage_gate_results,
            "passed": bool(lineage_gate_results) and all(lineage_gate_results.values()),
            "interpretation": (
                "Each lineage advances to a frozen formal CoCoReviewBench test "
                "when its complete preregistered checks pass."
            ),
        },
        "POL_note": (
            "POL byte identity records an unchanged contribution for the "
            "corresponding lineage comparison."
        ),
    }
    path = output_root / "effectiveness_screening_report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    try:
        result = summarize(config)
    except ActivationPending as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
