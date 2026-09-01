"""External CoCo-only process boundary consuming saved Final Reviews."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .io import atomic_write_json, file_sha256, read_json, write_derived_jsonl


def prepare_coco_input(
    final_review_paths: list[str | Path],
    output_path: str | Path,
) -> Path:
    values = []
    seen: set[str] = set()
    for path in final_review_paths:
        review = read_json(path)
        paper_id = str(review.get("paper_id") or "")
        text = str(review.get("review_text") or "")
        if not paper_id or not text:
            raise ValueError("CoCo input requires saved Final Review id/text")
        if paper_id in seen:
            raise ValueError(f"Duplicate Final Review paper_id: {paper_id}")
        seen.add(paper_id)
        values.append({"id": paper_id, "text": text})
    return write_derived_jsonl(output_path, values)


def run_coco_evaluation(
    *,
    final_review_paths: list[str | Path],
    frozen_system_path: str | Path,
    coco_root: str | Path,
    output_root: str | Path,
    command: list[str],
) -> dict[str, Any]:
    """Launch an explicit external command; Review/Evolution modules never import CoCo."""
    system = read_json(frozen_system_path)
    if not system.get("system_id"):
        raise ValueError("CoCo Evaluation requires a Frozen System Bundle")
    root = Path(coco_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    manifest_binding = (
        (system.get("external_evaluation") or {}).get("coco_1300_manifest") or {}
    )
    manifest_path = str(manifest_binding.get("path") or "")
    if not manifest_path or file_sha256(manifest_path) != manifest_binding.get("sha256"):
        raise ValueError("Frozen System has no valid CoCo-1300 manifest binding")
    manifest = read_json(manifest_path)
    manifest_records = list(manifest.get("records") or [])
    expected_ids = {str(value.get("paper_id") or "") for value in manifest_records}
    if "" in expected_ids or len(expected_ids) != len(manifest_records):
        raise ValueError("Frozen CoCo-1300 manifest contains missing or duplicate paper IDs")
    supplied_ids = {
        str(read_json(path).get("paper_id") or "") for path in final_review_paths
    }
    if supplied_ids != expected_ids or len(final_review_paths) != len(expected_ids):
        missing = sorted(expected_ids - supplied_ids)
        extra = sorted(supplied_ids - expected_ids)
        raise ValueError(
            "CoCo Evaluation requires the exact frozen paper set: "
            f"expected={len(expected_ids)}, supplied={len(final_review_paths)}, "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    input_path = prepare_coco_input(
        final_review_paths,
        output / "candidate_official_input.jsonl",
    )
    if not command:
        raise ValueError("CoCo Evaluation requires an explicit external command")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "0",
        "COCO_CANDIDATE_INPUT": str(input_path),
        "COCO_OUTPUT_ROOT": str(output),
    }
    completed = subprocess.run(
        command,
        cwd=str(root),
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    report = {
        "protocol": "research-v2-external-coco-v1",
        "frozen_system_id": system["system_id"],
        "frozen_system_sha256": file_sha256(frozen_system_path),
        "coco_1300_manifest_sha256": manifest_binding["sha256"],
        "candidate_input": str(input_path),
        "candidate_input_sha256": file_sha256(input_path),
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": "complete" if completed.returncode == 0 else "failed",
    }
    atomic_write_json(output / "run.json", report)
    if completed.returncode != 0:
        raise RuntimeError(f"External CoCo process failed: {completed.returncode}")
    return report
