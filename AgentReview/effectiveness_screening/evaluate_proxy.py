"""Evaluate frozen outputs with CoCo's official prompt and proxy references."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping

from agentreview.backends.openai import OpenAIChat
from agentreview.single_paper_review_roles import (
    META_REVIEWER_NAME,
    REVIEWER_SPECS,
)
from memory_evolution.coco_adapter import FrozenCoCoAdapter
from memory_evolution.toolkit import MemoryToolkit

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


def _first_json_object(text: str) -> Dict[str, Any]:
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


def _load_reviews(output_dir: Path, paper_id: str) -> Dict[str, str]:
    path = output_dir / "single_paper_review" / paper_id / "reviews.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(text) for key, text in value.items()}


def _build_candidate_categories(
    adapter: FrozenCoCoAdapter,
    reviews: Mapping[str, str],
) -> Dict[str, list[str]]:
    by_category: Dict[str, list[str]] = defaultdict(list)
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


def classify_outputs(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    metrics = config["metrics"]
    adapter = FrozenCoCoAdapter(
        Path(config["external_roots"]["cocoreviewbench"]),
        str(metrics["review_split_revision"]),
        str(metrics["review_classify_revision"]),
    )
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
            / f"{cell['arm']}.jsonl"
        )
        existing = {
            str(value["paper_id"]): value
            for value in iter_jsonl(destination)
        } if destination.is_file() else {}
        for paper_id in papers:
            reviews = _load_reviews(run_output, paper_id)
            package_sha = hashlib.sha256(
                json.dumps(
                    reviews, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()
            if (
                paper_id in existing
                and existing[paper_id].get("review_package_sha256") == package_sha
            ):
                completed += 1
                continue
            existing[paper_id] = {
                "design_version": DESIGN_VERSION,
                "paper_id": paper_id,
                "arm": cell["arm"],
                "replicate": cell["replicate"],
                "review_package_sha256": package_sha,
                "candidate_category_texts": _build_candidate_categories(
                    adapter, reviews
                ),
            }
            write_jsonl(destination, [existing[key] for key in sorted(existing)])
            completed += 1
            print(
                f"[classify {completed}] replicate={cell['replicate']} "
                f"arm={cell['arm']} paper={paper_id}",
                flush=True,
            )
    return {"classified_cells": completed}


def _judge_backend(config: Mapping[str, Any]) -> MemoryToolkit:
    backend = OpenAIChat(
        model=str(config["metrics"]["judge_model"]),
        temperature=0.0,
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


def judge_outputs(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    references = {
        str(value["paper_id"]): value
        for value in iter_jsonl(
            output_root / "evaluation_references_proxy.jsonl"
        )
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
            / f"{cell['arm']}.jsonl"
        )
        classified = list(iter_jsonl(classified_path))
        destination = (
            output_root
            / "evaluation"
            / "judged"
            / f"replicate_{cell['replicate']}"
            / f"{cell['arm']}.jsonl"
        )
        existing = {
            str(value["paper_id"]): value
            for value in iter_jsonl(destination)
        } if destination.is_file() else {}
        for candidate in classified:
            paper_id = str(candidate["paper_id"])
            if paper_id in existing:
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
                    f"CANDIDATE:\n"
                    + "\n\n".join(candidate_by_tag[category])
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
            metric_values: Dict[str, list[float]] = {
                name: [] for name in OFFICIAL_METRIC_MAP
            }
            for category_value in category_scores.values():
                scores = category_value["scores"]
                for output_name, judge_name in OFFICIAL_METRIC_MAP.items():
                    score = scores[judge_name]
                    if isinstance(score, (int, float)) and not isinstance(
                        score, bool
                    ):
                        metric_values[output_name].append(float(score))
            paper_metrics = {
                name: (sum(values) / len(values) if values else None)
                for name, values in metric_values.items()
            }
            available = [
                value for value in paper_metrics.values() if value is not None
            ]
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
                "arm": cell["arm"],
                "replicate": cell["replicate"],
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
                f"arm={cell['arm']} paper={paper_id}",
                flush=True,
            )
    return {
        "judged_cells": completed,
        "metric_status": "CoCo-style proxy screening",
        "official_judge_prompt_sha256": prompt_sha,
    }


def build_blind_pairs(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    papers = [
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "effectiveness_validation.jsonl")
    ]
    pairs = []
    for replicate in config["experiment"]["replicates"]:
        run_root = output_root / "runs" / f"replicate_{replicate}"
        for paper_id in papers:
            seed_reviews = _load_reviews(run_root / "seed_experience", paper_id)
            evolved_reviews = _load_reviews(run_root / "evolved", paper_id)
            pair_key = f"{paper_id}|{replicate}|20260730"
            orientation = int(
                hashlib.sha256(pair_key.encode("utf-8")).hexdigest(), 16
            ) % 2
            if orientation == 0:
                a, b, key = evolved_reviews, seed_reviews, "A"
            else:
                a, b, key = seed_reviews, evolved_reviews, "B"
            pairs.append(
                {
                    "pair_id": hashlib.sha256(
                        pair_key.encode("utf-8")
                    ).hexdigest()[:20],
                    "paper_id": paper_id,
                    "replicate": int(replicate),
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
                "paper_id": pair["paper_id"],
                "replicate": pair["replicate"],
                "evolved_position": pair["_sealed_evolved_position"],
            }
            for pair in pairs
        ],
    )
    return {
        "pair_count": len(pairs),
        "public_pairs": str(public_path),
        "public_pairs_sha256": sha256_file(public_path),
        "sealed_key": str(key_path),
        "sealed_key_sha256": sha256_file(key_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("classify", "judge", "build-blind-pairs")
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    if args.command == "classify":
        result = classify_outputs(config)
    elif args.command == "judge":
        result = judge_outputs(config)
    else:
        result = build_blind_pairs(config)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
