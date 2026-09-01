#!/usr/bin/env python3
"""Build deterministic A/B selections and a shared D selection manifest.

The source prepared artifacts are read-only. This script writes only derived
candidate/evaluator JSONL files and an auditable selection manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.tasks import SPECIALIST_TASKS


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _paper_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("paper_id") or row.get("id") or "").split("::", 1)[0]


def _stable_sample(values: set[str], count: int, seed_label: str, zone: str) -> list[str]:
    if len(values) < count:
        raise ValueError(
            f"{zone} requires {count} common eligible papers, found {len(values)}"
        )
    return sorted(
        values,
        key=lambda value: (
            hashlib.sha256(
                f"{seed_label}:{zone}:{value}".encode("utf-8")
            ).hexdigest(),
            value,
        ),
    )[:count]


def _common_ids(root: Path, filename: str) -> set[str]:
    per_task = []
    for task_id in SPECIALIST_TASKS:
        rows = _read_jsonl(root / task_id / filename)
        per_task.append({_paper_id(row) for row in rows})
    return set.intersection(*per_task)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-data-root", required=True)
    parser.add_argument("--source-test-root", required=True)
    parser.add_argument("--output-data-root", required=True)
    parser.add_argument("--selection-manifest", required=True)
    parser.add_argument("--seed-label", required=True)
    parser.add_argument("--a-count", type=int, default=300)
    parser.add_argument("--b-count", type=int, default=50)
    parser.add_argument("--d-count", type=int, default=100)
    args = parser.parse_args()

    source_data = Path(args.source_data_root).expanduser().resolve()
    source_test = Path(args.source_test_root).expanduser().resolve()
    output_data = Path(args.output_data_root).expanduser().resolve()
    selection_path = Path(args.selection_manifest).expanduser().resolve()
    if output_data.exists():
        raise FileExistsError(f"Refusing to overwrite reduced data: {output_data}")
    if selection_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite reduced selection manifest: {selection_path}"
        )

    source_manifest_path = source_data / "manifest.json"
    source_test_manifest_path = source_test / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))

    a_eligible = _common_ids(source_data, "episodes.jsonl")
    b_eligible_by_task = {
        task_id: {
            _paper_id(row)
            for row in _read_jsonl(source_data / task_id / "validation.jsonl")
        }
        for task_id in SPECIALIST_TASKS
    }
    d_eligible = _common_ids(source_test, "test.jsonl")
    selected_a = _stable_sample(a_eligible, args.a_count, args.seed_label, "A")
    selected_b_by_task = {
        task_id: _stable_sample(
            eligible,
            args.b_count,
            args.seed_label,
            f"B:{task_id}",
        )
        for task_id, eligible in b_eligible_by_task.items()
    }
    selected_d = _stable_sample(d_eligible, args.d_count, args.seed_label, "D")
    selected_a_set = set(selected_a)

    task_manifest: dict[str, dict[str, Any]] = {}
    for task_id in SPECIALIST_TASKS:
        source_task = source_data / task_id
        selected_b_set = set(selected_b_by_task[task_id])
        episodes = [
            row
            for row in _read_jsonl(source_task / "episodes.jsonl")
            if _paper_id(row) in selected_a_set
        ]
        validation = [
            row
            for row in _read_jsonl(source_task / "validation.jsonl")
            if _paper_id(row) in selected_b_set
        ]
        queries = [
            row
            for row in _read_jsonl(source_task / "queries.jsonl")
            if _paper_id(row) in selected_a_set | selected_b_set
        ]
        references = [
            row
            for row in _read_jsonl(
                source_data
                / "evaluator_only"
                / task_id
                / "validation_references.jsonl"
            )
            if _paper_id(row) in selected_b_set
        ]
        if not (
            len(episodes) == args.a_count
            and len(validation) == args.b_count
            and len(references) == args.b_count
            and {str(row["id"]) for row in validation}
            == {str(row["id"]) for row in references}
        ):
            raise RuntimeError(f"Reduced {task_id} A/B coverage mismatch")
        task_root = output_data / task_id
        reference_path = (
            output_data / "evaluator_only" / task_id / "validation_references.jsonl"
        )
        _write_jsonl(task_root / "episodes.jsonl", episodes)
        _write_jsonl(task_root / "validation.jsonl", validation)
        _write_jsonl(task_root / "queries.jsonl", queries)
        _write_jsonl(reference_path, references)
        task_manifest[task_id] = {
            "episodes": str(task_root / "episodes.jsonl"),
            "validation": str(task_root / "validation.jsonl"),
            "queries": str(task_root / "queries.jsonl"),
            "evaluator_references": str(reference_path),
            "evaluator_reference_access": {
                "artifact_class": "evolution_fitness_reference",
                "evaluator_only": True,
                "candidate_forbidden": True,
            },
            "episode_count": len(episodes),
            "validation_count": len(validation),
            "evaluator_reference_count": len(references),
            "evaluator_reference_nonempty_count": sum(
                bool(str(row.get("answer") or "").strip()) for row in references
            ),
        }

    manifest = dict(source_manifest)
    manifest.update(
        {
            "source": str(source_manifest_path),
            "source_manifest_sha256": _sha256(source_manifest_path),
            "selection_seed_label": args.seed_label,
            "selection_protocol": "sha256-rank-common-specialist-coverage-v1",
            "train_family_count": args.a_count,
            "validation_family_count": args.b_count,
            "meta_validation_family_count": 0,
            "semantic_scholar_required_count": args.a_count + args.b_count,
            "semantic_scholar_cached_count": args.a_count + args.b_count,
            "semantic_scholar_missing_ids": [],
            "tasks": task_manifest,
            "meta_status": "disabled for reduced full-flow",
            "derived_subset_audit": {
                "passed": True,
                "reason": "subset of source artifacts that passed the Human Reference boundary",
                "source_manifest_sha256": _sha256(source_manifest_path),
            },
        }
    )
    _write_json(output_data / "manifest.json", manifest)

    selection = {
        "protocol": "reduced-fullflow-fixed-selection-v1",
        "seed_label": args.seed_label,
        "seed_sha256": hashlib.sha256(args.seed_label.encode("utf-8")).hexdigest(),
        "algorithm": "ascending SHA256(seed_label:zone:paper_id), then paper_id",
        "eligibility": (
            "A and D use the intersection across all five specialists; "
            "B is sampled independently from each role's META-OFF specialist pool"
        ),
        "source_data_manifest": str(source_manifest_path),
        "source_data_manifest_sha256": _sha256(source_manifest_path),
        "source_test_manifest": str(source_test_manifest_path),
        "source_test_manifest_sha256": _sha256(source_test_manifest_path),
        "counts": {"A": args.a_count, "B": args.b_count, "C": 0, "D": args.d_count},
        "eligible_counts": {
            "A": len(a_eligible),
            "B": {
                task_id: len(values)
                for task_id, values in b_eligible_by_task.items()
            },
            "D": len(d_eligible),
        },
        "paper_ids": {
            "A": selected_a,
            "B": selected_b_by_task,
            "D": selected_d,
        },
        "tasks": {
            task_id: [f"{paper_id}::{task_id}" for paper_id in selected_d]
            for task_id in SPECIALIST_TASKS
        },
    }
    _write_json(selection_path, selection)
    print(json.dumps({"data_manifest": str(output_data / "manifest.json"), "selection_manifest": str(selection_path), "counts": selection["counts"], "eligible_counts": selection["eligible_counts"]}, indent=2))


if __name__ == "__main__":
    main()
