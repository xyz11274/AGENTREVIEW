"""Evaluate frozen outputs with CoCo's official prompt and proxy references."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentreview.backends.openai import OpenAIChat
from agentreview.reviewer_identity import (
    META_REVIEWER_NAME,
    REVIEWER_SPECS,
)
from memory_evolution.coco_adapter import FrozenCoCoAdapter
from memory_evolution.toolkit import MemoryToolkit

from .activation import ActivationPending, require_activation
from .arms import lineage_arm_pairs
from .prepare import DESIGN_VERSION, iter_jsonl, load_config, sha256_file, write_jsonl
from .run_matrix import matrix_commands

OFFICIAL_METRIC_MAP = {
    "Correctness_proxy": "alignment",
    "Thoroughness": "completeness",
    "Grounding": "grounding_specificity",
    "Verifiability": "verifiability",
    "Clarity": "clarity",
}
META_METRICS = (
    "adjudication_clarity",
    "groundedness",
    "verifiability",
    "conflict_coverage",
)


def _evaluation_assets(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    return {
        "effectiveness_selection": output_root / "effectiveness_validation.jsonl",
        "run_audit": output_root / "run_audit.json",
    }


def _arm_record(cell: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "arm_schema_version": cell["arm_schema_version"],
        "arm_id": cell["arm_id"],
        "arm_kind": cell["arm_kind"],
        "lineage": cell["lineage"],
    }


def _validate_arm_record(
    value: Mapping[str, Any], cell: Mapping[str, Any], path: Path
) -> None:
    expected = _arm_record(cell)
    actual = {name: value.get(name) for name in expected}
    if actual == expected:
        return
    raise RuntimeError(
        f"Arm identity mismatch in {path}: expected={expected}, actual={actual}"
    )


def _existing_by_paper(
    path: Path, cell: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    if path.is_file():
        records = list(iter_jsonl(path))
    else:
        records = []
    paper_ids = [str(value["paper_id"]) for value in records]
    duplicates = sorted(
        {paper_id for paper_id in paper_ids if paper_ids.count(paper_id) > 1}
    )
    if duplicates:
        raise RuntimeError(f"Duplicate paper records in {path}: {duplicates}")
    for value in records:
        _validate_arm_record(value, cell, path)
    return {str(value["paper_id"]): value for value in records}


def _first_json_object(text: str) -> dict[str, Any]:
    start = str(text).find("{")
    if start < 0:
        raise ValueError("Judge response contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(text[start : index + 1])
                if not isinstance(value, dict):
                    raise ValueError("Judge JSON is not an object")
                return value
    raise ValueError("Judge response contains incomplete JSON")


def _reviewer_by_task():
    return {spec["dimension"]: spec["name"] for spec in REVIEWER_SPECS}


def _load_reviews(output_dir: Path, paper_id: str) -> dict[str, str]:
    path = output_dir / "single_paper_review" / paper_id / "reviews.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(text) for key, text in value.items()}


def _build_candidate_categories(
    adapter: FrozenCoCoAdapter,
    reviews: Mapping[str, str],
) -> dict[str, list[str]]:
    by_category: dict[str, list[str]] = defaultdict(list)
    for task_id, reviewer_name in _reviewer_by_task().items():
        opinions = adapter.split_review(reviews[reviewer_name])
        labels = adapter.classify_opinions(opinions)
        if len(labels) != len(opinions):
            raise RuntimeError(f"Classifier length mismatch for {task_id}")
        for opinion, opinion_labels in zip(opinions, labels):
            for label in opinion_labels:
                if str(label).startswith(f"{task_id}-"):
                    by_category[str(label)].append(str(opinion))
    return dict(sorted(by_category.items()))


def classify_outputs(config: Mapping[str, Any]) -> dict[str, Any]:
    require_activation(config, "evaluation", _evaluation_assets(config))
    output_root = Path(config["output_root"]).expanduser().resolve()
    metrics = config["metrics"]
    adapter = FrozenCoCoAdapter(
        Path(config["external_roots"]["cocoreviewbench"]),
        str(metrics["review_split_revision"]),
        str(metrics["review_classify_revision"]),
    )
    classifier_provenance = {
        "review_split_revision": str(metrics["review_split_revision"]),
        "review_classify_revision": str(metrics["review_classify_revision"]),
    }
    papers = [
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "effectiveness_validation.jsonl")
    ]
    completed = 0
    for cell in matrix_commands(config):
        run_output = Path(cell["output_dir"])
        destination = (
            output_root
            / "evaluation"
            / "classified"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm_id']}.jsonl"
        )
        existing = _existing_by_paper(destination, cell)
        for paper_id in papers:
            reviews = _load_reviews(run_output, paper_id)
            package_sha = hashlib.sha256(
                json.dumps(reviews, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if (
                paper_id in existing
                and existing[paper_id].get("review_package_sha256") == package_sha
                and existing[paper_id].get("classifier_provenance")
                == classifier_provenance
            ):
                completed += 1
                continue
            existing[paper_id] = {
                "design_version": DESIGN_VERSION,
                "paper_id": paper_id,
                **_arm_record(cell),
                "replicate": cell["replicate"],
                "review_package_sha256": package_sha,
                "classifier_provenance": classifier_provenance,
                "candidate_category_texts": _build_candidate_categories(
                    adapter, reviews
                ),
            }
            write_jsonl(destination, [existing[key] for key in sorted(existing)])
            completed += 1
            print(
                f"[classify {completed}] replicate={cell['replicate']} "
                f"arm_id={cell['arm_id']} paper={paper_id}",
                flush=True,
            )
    return {"classified_cells": completed}


def _judge_backend(config: Mapping[str, Any]) -> MemoryToolkit:
    backend = OpenAIChat(
        model=str(config["metrics"]["judge_model"]),
        temperature=float(config["metrics"].get("judge_temperature", 0)),
        max_tokens=4096,
        retry_max_tokens=8192,
        reasoning_effort="medium",
        request_timeout_seconds=600,
        connect_timeout_seconds=30,
    )
    return MemoryToolkit(
        llm_client=backend, llm_model=str(config["metrics"]["judge_model"])
    )


def _official_judge_prompt(config: Mapping[str, Any]) -> tuple[str, str]:
    path = (
        Path(config["external_roots"]["cocoreviewbench"])
        / "src"
        / "evaluation"
        / "prompt"
        / "judge.txt"
    )
    return path.read_text(encoding="utf-8").strip(), sha256_file(path)


def _validate_official_scores(parsed: Mapping[str, Any]) -> None:
    required = {
        "claim_extraction",
        "actionability",
        "grounding_specificity",
        "verifiability",
        "alignment",
        "completeness",
        "clarity",
    }
    if set(parsed) != required:
        raise ValueError(
            f"Official judge fields mismatch: {sorted(set(parsed) ^ required)}"
        )
    for name in required - {"claim_extraction", "actionability", "verifiability"}:
        value = parsed[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Non-numeric {name}: {value!r}")
        if not 1 <= float(value) <= 5:
            raise ValueError(f"Out-of-range {name}: {value!r}")
    for name in ("actionability", "verifiability"):
        value = parsed[name]
        if value != "X" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 1 <= float(value) <= 5
        ):
            raise ValueError(f"Invalid {name}: {value!r}")


def _meta_prompt(
    human_meta: str, candidate_meta: str, specialist_reviews: Mapping[str, str]
) -> str:
    specialist_text = "\n\n".join(
        f"=== {task_id} ===\n{specialist_reviews[reviewer_name]}"
        for task_id, reviewer_name in _reviewer_by_task().items()
    )
    return (
        "Evaluate the candidate final meta-review using four dimensions from 1 to "
        "5: adjudication_clarity, groundedness, verifiability, and "
        "conflict_coverage. Compare it with the human meta-review and the five "
        "specialist inputs. Return exactly one JSON object with those four keys "
        "and numeric values only.\n\n"
        f"HUMAN META-REVIEW:\n{human_meta}\n\n"
        f"SPECIALIST INPUTS:\n{specialist_text}\n\n"
        f"CANDIDATE META-REVIEW:\n{candidate_meta}"
    )


def judge_outputs(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    require_activation(
        config,
        "evaluation",
        {
            **_evaluation_assets(config),
            "classified_outputs": {
                "path": output_root / "evaluation" / "classified",
                "type": "directory",
            },
            "proxy_references": output_root / "evaluation_references_proxy.jsonl",
        },
    )
    references = {
        str(value["paper_id"]): value
        for value in iter_jsonl(output_root / "evaluation_references_proxy.jsonl")
    }
    judge = _judge_backend(config)
    official_prompt, prompt_sha = _official_judge_prompt(config)
    completed = 0
    for cell in matrix_commands(config):
        classified_path = (
            output_root
            / "evaluation"
            / "classified"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm_id']}.jsonl"
        )
        classified = list(iter_jsonl(classified_path))
        for candidate in classified:
            _validate_arm_record(candidate, cell, classified_path)
        destination = (
            output_root
            / "evaluation"
            / "judged"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm_id']}.jsonl"
        )
        existing = _existing_by_paper(destination, cell)
        for candidate in classified:
            paper_id = str(candidate["paper_id"])
            if (
                paper_id in existing
                and existing[paper_id].get("evaluated_review_package_sha256")
                == candidate.get("review_package_sha256")
                and existing[paper_id].get("official_judge_prompt_sha256") == prompt_sha
                and existing[paper_id].get("judge_model")
                == config["metrics"]["judge_model"]
            ):
                completed += 1
                continue
            reference = references[paper_id]
            if not str(reference.get("metric_status", "")).startswith(
                "CoCo-style proxy"
            ):
                raise RuntimeError("Reference is not explicitly marked proxy")
            candidate_by_tag = candidate["candidate_category_texts"]
            reference_by_tag = reference["category_reference_by_tag"]
            category_scores = {}
            for category in sorted(set(candidate_by_tag) & set(reference_by_tag)):
                user_prompt = (
                    f"REFERENCE:\n{reference_by_tag[category]}\n\n"
                    f"CANDIDATE:\n" + "\n\n".join(candidate_by_tag[category])
                )
                response = judge.llm_completion(
                    [
                        {"role": "system", "content": official_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    agent_name="CoCo Proxy Judge",
                )
                parsed = _first_json_object(response)
                _validate_official_scores(parsed)
                category_scores[category] = {
                    "scores": parsed,
                    "raw_response": response,
                }
            human_categories = set(reference_by_tag)
            ai_categories = set(candidate_by_tag)
            reviews = _load_reviews(Path(cell["output_dir"]), paper_id)
            meta_response = judge.llm_completion(
                [
                    {
                        "role": "user",
                        "content": _meta_prompt(
                            str(reference["meta_reference"]),
                            reviews[META_REVIEWER_NAME],
                            reviews,
                        ),
                    }
                ],
                agent_name="META Proxy Judge",
            )
            meta_scores = _first_json_object(meta_response)
            if set(meta_scores) != set(META_METRICS):
                raise ValueError("META judge fields mismatch")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 1 <= float(value) <= 5
                for value in meta_scores.values()
            ):
                raise ValueError("Invalid META judge scores")
            metric_values: dict[str, list[float]] = {
                name: [] for name in OFFICIAL_METRIC_MAP
            }
            for category_value in category_scores.values():
                scores = category_value["scores"]
                for output_name, judge_name in OFFICIAL_METRIC_MAP.items():
                    score = scores[judge_name]
                    if isinstance(score, (int, float)) and not isinstance(score, bool):
                        metric_values[output_name].append(float(score))
            paper_metrics = {
                name: (sum(values) / len(values) if values else None)
                for name, values in metric_values.items()
            }
            available = [value for value in paper_metrics.values() if value is not None]
            paper_metrics["CoCo_Average_proxy"] = (
                sum(available) / len(available) if available else None
            )
            paper_metrics.update(
                {
                    "Complete_official": (
                        len(ai_categories) / len(human_categories) * 100.0
                    ),
                    "Reference_category_recall": (
                        len(ai_categories & human_categories)
                        / len(human_categories)
                        * 100.0
                    ),
                    "AI_category_count": len(ai_categories),
                    "extra_category_count": len(ai_categories - human_categories),
                    "META_Average": sum(map(float, meta_scores.values()))
                    / len(meta_scores),
                }
            )
            existing[paper_id] = {
                "design_version": DESIGN_VERSION,
                "metric_status": "CoCo-style proxy screening; not official benchmark",
                "paper_id": paper_id,
                **_arm_record(cell),
                "replicate": cell["replicate"],
                "evaluated_review_package_sha256": candidate["review_package_sha256"],
                "official_judge_prompt_sha256": prompt_sha,
                "judge_model": config["metrics"]["judge_model"],
                "judge_temperature": 0,
                "category_scores": category_scores,
                "candidate_categories": sorted(ai_categories),
                "reference_categories": sorted(human_categories),
                "paper_metrics": paper_metrics,
                "meta_scores": meta_scores,
                "meta_raw_response": meta_response,
            }
            write_jsonl(destination, [existing[key] for key in sorted(existing)])
            completed += 1
            print(
                f"[judge {completed}] replicate={cell['replicate']} "
                f"arm_id={cell['arm_id']} paper={paper_id}",
                flush=True,
            )
    return {
        "judged_cells": completed,
        "metric_status": "CoCo-style proxy screening",
        "official_judge_prompt_sha256": prompt_sha,
    }


def build_blind_pairs(config: Mapping[str, Any]) -> dict[str, Any]:
    require_activation(config, "evaluation", _evaluation_assets(config))
    output_root = Path(config["output_root"]).expanduser().resolve()
    papers = [
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "effectiveness_validation.jsonl")
    ]
    pairs = []
    for replicate in config["experiment"]["replicates"]:
        run_root = output_root / "runs" / f"replicate_{replicate}"
        for lineage, seed, evolved in lineage_arm_pairs(config):
            comparison_id = f"{evolved.arm_id}_minus_{seed.arm_id}"
            for paper_id in papers:
                seed_reviews = _load_reviews(
                    run_root / seed.arm_id / "holdout", paper_id
                )
                evolved_reviews = _load_reviews(
                    run_root / evolved.arm_id / "holdout", paper_id
                )
                pair_key = f"{DESIGN_VERSION}|{paper_id}|{replicate}|{comparison_id}"
                orientation = (
                    int(hashlib.sha256(pair_key.encode("utf-8")).hexdigest(), 16) % 2
                )
                if orientation == 0:
                    a, b, key = evolved_reviews, seed_reviews, "A"
                else:
                    a, b, key = seed_reviews, evolved_reviews, "B"
                pairs.append(
                    {
                        "arm_schema_version": seed.to_record()["arm_schema_version"],
                        "pair_id": hashlib.sha256(pair_key.encode("utf-8")).hexdigest()[
                            :20
                        ],
                        "comparison_id": comparison_id,
                        "paper_id": paper_id,
                        "replicate": int(replicate),
                        "lineage": lineage,
                        "seed_arm_id": seed.arm_id,
                        "evolved_arm_id": evolved.arm_id,
                        "review_package_A": a,
                        "review_package_B": b,
                        "judge_instruction": (
                            "Blindly compare A and B for review correctness, coverage, "
                            "grounding, verifiability, clarity, and meta-review quality. "
                            "Return A, B, or tie with a concise justification."
                        ),
                        "_sealed_evolved_position": key,
                    }
                )
    public_path = output_root / "evaluation" / "blind_pairs.jsonl"
    key_path = output_root / "evaluation" / "blind_pairs_key.jsonl"
    write_jsonl(
        public_path,
        [
            {key: value for key, value in pair.items() if not key.startswith("_")}
            for pair in pairs
        ],
    )
    write_jsonl(
        key_path,
        [
            {
                "pair_id": pair["pair_id"],
                "arm_schema_version": pair["arm_schema_version"],
                "comparison_id": pair["comparison_id"],
                "paper_id": pair["paper_id"],
                "replicate": pair["replicate"],
                "lineage": pair["lineage"],
                "seed_arm_id": pair["seed_arm_id"],
                "evolved_arm_id": pair["evolved_arm_id"],
                "evolved_position": pair["_sealed_evolved_position"],
            }
            for pair in pairs
        ],
    )
    return {
        "pair_count": len(pairs),
        "lineage_pair_counts": {
            lineage: sum(pair["lineage"] == lineage for pair in pairs)
            for lineage, _, _ in lineage_arm_pairs(config)
        },
        "public_pairs": str(public_path),
        "public_pairs_sha256": sha256_file(public_path),
        "sealed_key": str(key_path),
        "sealed_key_sha256": sha256_file(key_path),
    }


def _blind_input_sha256(pair: Mapping[str, Any]) -> str:
    payload = json.dumps(
        pair, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def blind_judge_outputs(config: Mapping[str, Any]) -> dict[str, Any]:
    """Execute the configured blind judge and write strict traceable records."""
    output_root = Path(config["output_root"]).expanduser().resolve()
    require_activation(
        config,
        "evaluation",
        {
            **_evaluation_assets(config),
            "blind_pairs": output_root / "evaluation" / "blind_pairs.jsonl",
        },
    )
    pairs_path = output_root / "evaluation" / "blind_pairs.jsonl"
    pairs = list(iter_jsonl(pairs_path))
    pair_ids = [str(pair.get("pair_id") or "") for pair in pairs]
    if pair_ids and all(pair_ids) and len(set(pair_ids)) == len(pair_ids):
        pass
    else:
        raise ValueError("Blind pairs require unique non-empty pair_id values")
    required_fields = {
        "arm_schema_version",
        "pair_id",
        "comparison_id",
        "paper_id",
        "replicate",
        "lineage",
        "seed_arm_id",
        "evolved_arm_id",
        "review_package_A",
        "review_package_B",
        "judge_instruction",
    }
    for pair in pairs:
        if required_fields <= set(pair):
            continue
        raise ValueError(
            f"Blind pair fields are incomplete for {pair.get('pair_id')}: "
            f"{sorted(required_fields - set(pair))}"
        )

    destination = output_root / "evaluation" / "blind_judge_results.jsonl"
    existing_records = list(iter_jsonl(destination)) if destination.is_file() else []
    result_fields = {
        "design_version",
        "arm_schema_version",
        "pair_id",
        "comparison_id",
        "paper_id",
        "replicate",
        "lineage",
        "blind_input_sha256",
        "judge_model",
        "judge_temperature",
        "winner",
        "justification",
    }
    for value in existing_records:
        if set(value) == result_fields:
            continue
        raise RuntimeError(
            f"Blind judge result schema mismatch for {value.get('pair_id')}: "
            f"{sorted(set(value) ^ result_fields)}"
        )
    existing_ids = [str(value.get("pair_id") or "") for value in existing_records]
    if len(set(existing_ids)) == len(existing_ids):
        existing = {str(value["pair_id"]): value for value in existing_records}
    else:
        raise RuntimeError(
            f"Blind judge results contain duplicate pair IDs: {destination}"
        )
    unknown_existing = sorted(set(existing) - set(pair_ids))
    if unknown_existing:
        raise RuntimeError(
            f"Blind judge results contain unknown pair IDs: {unknown_existing}"
        )
    judge_model = str(config["metrics"]["judge_model"])
    judge_temperature = float(config["metrics"].get("judge_temperature", 0))
    judge = _judge_backend(config)
    completed = 0
    for pair in pairs:
        pair_id = str(pair["pair_id"])
        input_sha = _blind_input_sha256(pair)
        current = existing.get(pair_id) or {}
        current_identity = {
            "design_version": current.get("design_version"),
            "arm_schema_version": current.get("arm_schema_version"),
            "comparison_id": current.get("comparison_id"),
            "paper_id": current.get("paper_id"),
            "replicate": current.get("replicate"),
            "lineage": current.get("lineage"),
        }
        expected_identity = {
            "design_version": DESIGN_VERSION,
            "arm_schema_version": pair["arm_schema_version"],
            "comparison_id": pair["comparison_id"],
            "paper_id": pair["paper_id"],
            "replicate": int(pair["replicate"]),
            "lineage": pair["lineage"],
        }
        current_result_valid = (
            current.get("winner") in {"A", "B", "TIE"}
            and isinstance(current.get("justification"), str)
            and bool(str(current.get("justification") or "").strip())
        )
        resume_ready = (
            current_identity == expected_identity
            and current.get("blind_input_sha256") == input_sha
            and current.get("judge_model") == judge_model
            and current.get("judge_temperature") == judge_temperature
            and current_result_valid
        )
        if resume_ready:
            completed += 1
            continue
        user_payload = {
            "instruction": pair["judge_instruction"],
            "review_package_A": pair["review_package_A"],
            "review_package_B": pair["review_package_B"],
        }
        response = judge.llm_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "Act as an impartial blind review judge. Return exactly one "
                        "JSON object with keys winner and justification. winner uses "
                        "A, B, or TIE. justification is a concise non-empty string."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload, ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            agent_name="Effectiveness Blind Judge",
        )
        parsed = _first_json_object(response)
        if set(parsed) != {"winner", "justification"}:
            raise ValueError(
                f"Blind judge fields mismatch for {pair_id}: {sorted(parsed)}"
            )
        winner_value = parsed["winner"]
        justification_value = parsed["justification"]
        strict_types = isinstance(winner_value, str) and isinstance(
            justification_value, str
        )
        winner = winner_value.strip().upper() if strict_types else ""
        justification = justification_value.strip() if strict_types else ""
        if strict_types and winner in {"A", "B", "TIE"} and justification:
            pass
        else:
            raise ValueError(
                f"Blind judge winner/justification is invalid for {pair_id}"
            )
        existing[pair_id] = {
            "design_version": DESIGN_VERSION,
            "arm_schema_version": pair["arm_schema_version"],
            "pair_id": pair_id,
            "comparison_id": pair["comparison_id"],
            "paper_id": pair["paper_id"],
            "replicate": int(pair["replicate"]),
            "lineage": pair["lineage"],
            "blind_input_sha256": input_sha,
            "judge_model": judge_model,
            "judge_temperature": judge_temperature,
            "winner": winner,
            "justification": justification,
        }
        write_jsonl(destination, [existing[key] for key in sorted(existing)])
        completed += 1
    return {
        "judged_pairs": completed,
        "result_path": str(destination),
        "result_sha256": sha256_file(destination),
        "judge_model": judge_model,
        "judge_temperature": judge_temperature,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("classify", "judge", "build-blind-pairs", "blind-judge"),
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    try:
        if args.command == "classify":
            result = classify_outputs(config)
        elif args.command == "judge":
            result = judge_outputs(config)
        elif args.command == "build-blind-pairs":
            result = build_blind_pairs(config)
        else:
            result = blind_judge_outputs(config)
    except ActivationPending as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
