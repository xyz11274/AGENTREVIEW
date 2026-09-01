#!/usr/bin/env python3
"""Aggregate reduced original-seed versus evolved-winner D/CoCo results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.tasks import SPECIALIST_TASKS


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evolved-run-root", required=True)
    parser.add_argument("--baseline-suite-root", required=True)
    parser.add_argument("--baseline-report", required=True)
    parser.add_argument("--evolved-report", required=True)
    parser.add_argument("--selection-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evolved_root = Path(args.evolved_run_root).expanduser().resolve()
    baseline_root = Path(args.baseline_suite_root).expanduser().resolve()
    baseline_path = Path(args.baseline_report).expanduser().resolve()
    evolved_path = Path(args.evolved_report).expanduser().resolve()
    selection_path = Path(args.selection_manifest).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite reduced summary: {output}")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    evolved = json.loads(evolved_path.read_text(encoding="utf-8"))
    suite = json.loads((evolved_root / "suite_manifest.json").read_text(encoding="utf-8"))
    baseline_suite = json.loads((baseline_root / "suite_manifest.json").read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))

    per_role = {}
    baseline_scores = []
    evolved_scores = []
    baseline_items = 0
    evolved_items = 0
    for task_id in SPECIALIST_TASKS:
        baseline_eval = baseline["specialists"][task_id]["evaluation"]
        evolved_eval = evolved["specialists"][task_id]["evaluation"]
        baseline_score = float(baseline_eval["mean_score"])
        evolved_score = float(evolved_eval["mean_score"])
        baseline_scores.append(baseline_score)
        evolved_scores.append(evolved_score)
        baseline_items += int(baseline_eval["runtime_stats"]["eval_item_count"])
        evolved_items += int(evolved_eval["runtime_stats"]["eval_item_count"])
        evolved_result = suite["results"][task_id][0]
        per_role[task_id] = {
            "baseline_seed": baseline_suite["baseline_seed_selection"][task_id],
            "evolution_winner": {
                "program_id": evolved_result["best_program_id"],
                "program_sha256": suite["promoted"][task_id]["program_sha256"],
                "static_score": float(evolved_result["best_score"]),
                "is_evolved_child": "_child" in evolved_result["best_program_id"],
            },
            "baseline_score": baseline_score,
            "evolved_score": evolved_score,
            "delta": evolved_score - baseline_score,
            "d_item_count": int(evolved_eval["runtime_stats"]["eval_item_count"]),
        }
    baseline_macro = _mean(baseline_scores)
    evolved_macro = _mean(evolved_scores)
    result = {
        "experiment": "mstar-reduced-fullflow-v1",
        "non_reportable": True,
        "baseline_score": baseline_macro,
        "evolved_score": evolved_macro,
        "delta": evolved_macro - baseline_macro,
        "score_aggregation": "equal-weight macro mean across five specialist CoCo means",
        "per_role_result": per_role,
        "coco_result": {
            "adapter": "CoCoMetricBenchmarkAdapter",
            "split": True,
            "classify": True,
            "judge": True,
            "baseline_report": str(baseline_path),
            "baseline_report_sha256": _sha256(baseline_path),
            "evolved_report": str(evolved_path),
            "evolved_report_sha256": _sha256(evolved_path),
            "baseline_item_count": baseline_items,
            "evolved_item_count": evolved_items,
        },
        "runtime": {
            "baseline_role_seconds_sum": sum(
                float(baseline["specialists"][task]["evaluation"]["runtime_stats"]["total_seconds"])
                for task in SPECIALIST_TASKS
            ),
            "evolved_role_seconds_sum": sum(
                float(evolved["specialists"][task]["evaluation"]["runtime_stats"]["total_seconds"])
                for task in SPECIALIST_TASKS
            ),
            "per_role": {
                task: {
                    "baseline": baseline["specialists"][task]["evaluation"]["runtime_stats"],
                    "evolved": evolved["specialists"][task]["evaluation"]["runtime_stats"],
                }
                for task in SPECIALIST_TASKS
            },
        },
        "config": {
            "selection_seed_label": selection["seed_label"],
            "selection_manifest": str(selection_path),
            "selection_manifest_sha256": _sha256(selection_path),
            "data_counts": selection["counts"],
            "static": 16,
            "rotating": 4,
            "episodes": 32,
            "mutation_attempts": 8,
            "specialists": list(SPECIALIST_TASKS),
            "meta": False,
            "model": "Qwen3.8-27B",
            "context_length": 65536,
            "tensor_parallel": 4,
            "max_num_seqs": 4,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
    print(json.dumps({"output": str(output), "baseline_score": baseline_macro, "evolved_score": evolved_macro, "delta": evolved_macro - baseline_macro}, indent=2))


if __name__ == "__main__":
    main()
