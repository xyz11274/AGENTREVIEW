"""Summarize paired proxy metrics and apply the preregistered screening gate."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

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
    scores: Dict[tuple[str, int, str], Dict[str, float]] = {}
    for cell in matrix_commands(config):
        path = (
            output_root
            / "evaluation"
            / "judged"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm']}.jsonl"
        )
        for value in iter_jsonl(path):
            if value.get("metric_status") != (
                "CoCo-style proxy screening; not official benchmark"
            ):
                raise RuntimeError(f"Unmarked proxy result in {path}")
            key = (
                str(value["paper_id"]),
                int(value["replicate"]),
                str(value["arm"]),
            )
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
    for paper_id in sorted(papers):
        for arm in config["experiment"]["arms"]:
            for metric in PRIMARY_METRICS:
                output[(paper_id, arm, metric)] = mean(
                    scores[(paper_id, int(replicate), arm)].get(metric)
                    for replicate in config["experiment"]["replicates"]
                )
    return papers, output


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
    upper_index = min(
        len(samples) - 1, int((1.0 - alpha / 2.0) * len(samples)) - 1
    )
    return [samples[lower_index], samples[upper_index]]


def _blind_summary(output_root: Path):
    results_path = output_root / "evaluation" / "blind_judge_results.jsonl"
    key_path = output_root / "evaluation" / "blind_pairs_key.jsonl"
    if not results_path.is_file():
        return {
            "available": False,
            "reason": "blind_judge_results.jsonl is missing",
        }
    keys = {value["pair_id"]: value for value in iter_jsonl(key_path)}
    counts = {"evolved": 0, "seed": 0, "tie": 0}
    by_replicate: Dict[int, Dict[str, int]] = {
        int(value["replicate"]): {"evolved": 0, "seed": 0, "tie": 0}
        for value in keys.values()
    }
    results = list(iter_jsonl(results_path))
    result_ids = [str(value["pair_id"]) for value in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("Blind judge results contain duplicate pair IDs")
    unknown = sorted(set(result_ids) - set(keys))
    if unknown:
        raise ValueError(f"Blind judge results contain unknown pairs: {unknown}")
    for result in results:
        pair_id = result["pair_id"]
        key = keys[pair_id]
        replicate = int(key["replicate"])
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
    decisive = counts["evolved"] + counts["seed"]
    return {
        "available": True,
        "complete": set(result_ids) == set(keys),
        "expected_pairs": len(keys),
        "result_pairs": len(result_ids),
        "counts": counts,
        "decisive_pairs": decisive,
        "evolved_win_rate": counts["evolved"] / decisive if decisive else None,
        "by_replicate": by_replicate,
    }


def summarize(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    scores = _load_scores(config)
    papers, averaged = _paper_arm_means(config, scores)
    arm_summary = {
        arm: {
            metric: mean(averaged[(paper_id, arm, metric)] for paper_id in papers)
            for metric in PRIMARY_METRICS
        }
        for arm in config["experiment"]["arms"]
    }
    paired_deltas = {}
    for left, right, label in (
        ("evolved", "seed_experience", "evolved_minus_seed"),
        ("seed_experience", "no_memory", "seed_minus_no_memory"),
        ("evolved", "no_memory", "evolved_minus_no_memory"),
    ):
        paired_deltas[label] = {}
        for metric in PRIMARY_METRICS:
            values = [
                averaged[(paper_id, left, metric)]
                - averaged[(paper_id, right, metric)]
                for paper_id in sorted(papers)
                if averaged[(paper_id, left, metric)] is not None
                and averaged[(paper_id, right, metric)] is not None
            ]
            paired_deltas[label][metric] = {
                "mean_delta": mean(values),
                "positive_papers": sum(value > 0 for value in values),
                "zero_papers": sum(value == 0 for value in values),
                "negative_papers": sum(value < 0 for value in values),
                "paper_count": len(values),
            }
    coco_paper_deltas = [
        averaged[(paper_id, "evolved", "CoCo_Average_proxy")]
        - averaged[(paper_id, "seed_experience", "CoCo_Average_proxy")]
        for paper_id in sorted(papers)
        if averaged[(paper_id, "evolved", "CoCo_Average_proxy")] is not None
        and averaged[(paper_id, "seed_experience", "CoCo_Average_proxy")]
        is not None
    ]
    confidence = float(config["screening_gate"]["paired_bootstrap_confidence"])
    bootstrap_ci = _bootstrap_ci(coco_paper_deltas, confidence)
    replicate_deltas = {}
    for replicate in config["experiment"]["replicates"]:
        values = []
        for paper_id in papers:
            evolved_value = scores[
                (paper_id, int(replicate), "evolved")
            ]["CoCo_Average_proxy"]
            seed_value = scores[
                (paper_id, int(replicate), "seed_experience")
            ]["CoCo_Average_proxy"]
            if evolved_value is not None and seed_value is not None:
                values.append(evolved_value - seed_value)
        replicate_deltas[str(replicate)] = mean(values)
    blind = _blind_summary(output_root)
    threshold = config["screening_gate"]
    evolved_seed = paired_deltas["evolved_minus_seed"]

    def at_least(value, minimum):
        return value is not None and value >= float(minimum)

    gate_checks = {
        "all_20_papers_scored": evolved_seed["CoCo_Average_proxy"][
            "paper_count"
        ]
        == 20,
        "delta_CoCo_Average_proxy": at_least(
            evolved_seed["CoCo_Average_proxy"]["mean_delta"],
            threshold["delta_CoCo_Average_proxy_min"],
        ),
        "positive_papers": evolved_seed["CoCo_Average_proxy"][
            "positive_papers"
        ]
        >= int(threshold["positive_papers_min"]),
        "both_replicates_positive": all(
            value is not None and value > 0 for value in replicate_deltas.values()
        ),
        "bootstrap_lower_positive": bootstrap_ci[0] is not None
        and bootstrap_ci[0]
        > float(threshold["paired_bootstrap_lower_bound_min"]),
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
        "blind_judge_available": bool(blind.get("available"))
        and bool(blind.get("complete")),
        "blind_judge_win_rate": bool(blind.get("available"))
        and blind.get("evolved_win_rate") is not None
        and blind["evolved_win_rate"]
        >= float(threshold["blind_judge_evolved_win_rate_min"]),
        "blind_judge_decisive_pairs": bool(blind.get("available"))
        and blind["decisive_pairs"]
        >= int(threshold["blind_judge_decisive_pairs_min"]),
        "blind_judge_each_replicate": bool(blind.get("available"))
        and all(
            value["evolved"] > value["seed"]
            for value in blind["by_replicate"].values()
        ),
    }
    report = {
        "design_version": DESIGN_VERSION,
        "metric_status": "CoCo-style proxy screening; not official benchmark",
        "paper_count": len(papers),
        "arm_summary": arm_summary,
        "paired_deltas": paired_deltas,
        "replicate_evolved_minus_seed_CoCo_Average_proxy": replicate_deltas,
        "paper_level_paired_bootstrap": {
            "confidence": confidence,
            "interval": bootstrap_ci,
            "resamples": 20000,
            "seed": 20260730,
        },
        "blind_judge": blind,
        "screening_gate": {
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
            "interpretation": (
                "Gate for considering a frozen formal CoCoReviewBench test; "
                "failure requires component diagnosis and is not a verdict that "
                "MemoryProgram evolution is ineffective."
            ),
        },
        "POL_note": (
            "POL evolved program is byte-identical to seed and is not interpreted "
            "as an evolution gain."
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
    print(json.dumps(summarize(config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
