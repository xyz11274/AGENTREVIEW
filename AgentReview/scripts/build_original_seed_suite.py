#!/usr/bin/env python3
"""Promote the best initial seed per role for an isolated D baseline suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.tasks import SPECIALIST_TASKS


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evolved-run-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    evolved_root = Path(args.evolved_run_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite baseline suite: {output}")
    temporary_output = output.with_name(output.name + ".tmp")
    if temporary_output.exists():
        raise FileExistsError(
            f"Refusing to overwrite partial baseline suite: {temporary_output}"
        )
    evolved_manifest_path = evolved_root / "suite_manifest.json"
    evolved_manifest = json.loads(evolved_manifest_path.read_text(encoding="utf-8"))
    promoted = {}
    selections = {}
    for task_id in SPECIALIST_TASKS:
        source_run = evolved_root / task_id / f"seed_{args.seed}"
        rows = [
            json.loads(line)
            for line in (source_run / "scores.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        eligible = [
            (index, row)
            for index, row in enumerate(rows)
            if int(row.get("iteration", -1)) == 0
            and row.get("quality_passed") is True
            and isinstance(row.get("static_score"), (int, float))
        ]
        if not eligible:
            raise RuntimeError(f"No completed original seed for {task_id}")
        index, winner = max(
            eligible,
            key=lambda value: (float(value[1]["static_score"]), -value[0]),
        )
        source_program = source_run / "pool" / f"{winner['program_id']}.py"
        destination = temporary_output / task_id / "best_program.py"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_program, destination)
        promoted[task_id] = {
            "program": f"{task_id}/best_program.py",
            "program_sha256": _sha256(destination),
            "source_run": str(source_run),
            "selection_mean": float(winner["static_score"]),
            "primary_seed": args.seed,
            "lineage": str(winner.get("lineage") or "original-seed"),
            "program_id": str(winner["program_id"]),
        }
        selections[task_id] = {
            "program_id": str(winner["program_id"]),
            "program_sha256": _sha256(destination),
            "static_score": float(winner["static_score"]),
            "tie_break": "earliest scores.jsonl row",
        }
    result = {
        "provenance": evolved_manifest.get("provenance"),
        "experiment_seeds": [args.seed],
        "primary_seed": args.seed,
        "program_selection": "best initial structural seed per role on frozen B static subset",
        "baseline_kind": "original-seed-static-winner",
        "source_evolved_suite": str(evolved_manifest_path),
        "source_evolved_suite_sha256": _sha256(evolved_manifest_path),
        "promoted": promoted,
        "baseline_seed_selection": selections,
        "meta_status": "disabled",
    }
    temporary_output.mkdir(parents=True, exist_ok=True)
    (temporary_output / "suite_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_output, output)
    print(json.dumps(selections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
