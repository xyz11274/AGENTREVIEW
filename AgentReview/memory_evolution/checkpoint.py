"""Atomic, identity-bound checkpoints for chronological historical replay."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


CHECKPOINT_VERSION = "historical-replay-checkpoint-v1"
REQUIRED_IDENTITY_FIELDS = (
    "candidate_id",
    "lineage",
    "replicate_id",
    "program_sha256",
    "historical_manifest_sha256",
    "experiment_config_sha256",
    "s2_asset_sha256",
    "s2_asset_version",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_run_identity(identity: Dict[str, Any]) -> None:
    missing = [name for name in REQUIRED_IDENTITY_FIELDS if not identity.get(name)]
    if missing:
        raise ValueError(f"Checkpoint run identity is incomplete: {missing}")
    if "generation_config" not in identity:
        raise ValueError("Checkpoint identity requires generation_config")
    if "seed_control_available" not in identity:
        raise ValueError("Checkpoint identity requires seed_control_available")


def validate_completed_prefix(completed_ids: Sequence[str], frozen_ids: Sequence[str]) -> None:
    completed = [str(value) for value in completed_ids]
    frozen = [str(value) for value in frozen_ids]
    if completed != frozen[: len(completed)]:
        raise ValueError(
            "Checkpoint completed paper IDs are not a strict prefix of the "
            "frozen Historical order"
        )


def _records_dir(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".records")


def _record_name(index: int, paper_id: str) -> str:
    safe = hashlib.sha256(str(paper_id).encode("utf-8")).hexdigest()[:16]
    return f"{index:06d}_{safe}.json"


def audit_history_records(
    checkpoint_path: Path,
    completed_ids: Sequence[str],
    frozen_ids: Sequence[str],
    *,
    allow_adjacent_uncommitted: bool = False,
    clean_adjacent_uncommitted: bool = False,
) -> List[str]:
    """Audit committed sidecars and optionally discard one crash-window record.

    A sidecar becomes committed only when the identity-bound checkpoint advances.
    Therefore a checkpoint may safely discard exactly the sidecar for its own
    ``next_paper_index``.  No checkpoint means there is no identity authority and
    even the first sidecar is treated as stale state.
    """
    validate_completed_prefix(completed_ids, frozen_ids)
    directory = _records_dir(checkpoint_path)
    if not directory.exists():
        return []
    allowed = {
        _record_name(index, paper_id)
        for index, paper_id in enumerate(completed_ids)
    }
    actual = {path.name for path in directory.glob("*.json")}
    unexpected = actual - allowed
    cleaned = []
    next_index = len(completed_ids)
    if allow_adjacent_uncommitted and next_index < len(frozen_ids):
        adjacent_name = _record_name(next_index, frozen_ids[next_index])
        if adjacent_name in unexpected:
            adjacent_path = directory / adjacent_name
            try:
                payload = json.loads(adjacent_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Adjacent uncommitted History record is unreadable"
                ) from exc
            if (
                str(payload.get("paper_id") or "") != str(frozen_ids[next_index])
                or int(payload.get("paper_index", -1)) != next_index
                or payload.get("completed") is not True
            ):
                raise ValueError(
                    "Adjacent uncommitted History record has invalid identity"
                )
            if clean_adjacent_uncommitted:
                adjacent_path.unlink()
                cleaned.append(adjacent_name)
            unexpected.remove(adjacent_name)
    unexpected = sorted(unexpected)
    if unexpected:
        raise ValueError(
            "History directory contains records beyond the legal completed "
            f"prefix: {unexpected}"
        )
    return cleaned


def _audit_no_checkpoint_residue(path: Path) -> None:
    """Fail closed when a purported fresh run already has persistent Memory."""
    residue = []
    for name in ("memory_library.jsonl", "history_records.jsonl", "memory_library"):
        candidate = path.parent / name
        if candidate.is_file() and candidate.stat().st_size:
            residue.append(str(candidate))
        elif candidate.is_dir() and any(candidate.iterdir()):
            residue.append(str(candidate))
    if residue:
        raise ValueError(
            "Formal fresh run has residual Memory but no identity-bound checkpoint: "
            + ", ".join(residue)
        )


def load_checkpoint(
    checkpoint_path: str,
    *,
    expected_identity: Dict[str, Any],
    frozen_ids: Sequence[str],
) -> Optional[Dict[str, Any]]:
    path = Path(checkpoint_path).expanduser().resolve()
    validate_run_identity(expected_identity)
    if not path.is_file():
        audit_history_records(path, [], frozen_ids)
        _audit_no_checkpoint_residue(path)
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError("Legacy or unsupported Historical checkpoint protocol")
    actual_identity = dict(payload.get("run_identity") or {})
    if actual_identity != expected_identity:
        raise ValueError("Checkpoint identity does not match this Candidate run")
    completed = list(payload.get("completed_paper_ids") or [])
    validate_completed_prefix(completed, frozen_ids)
    if int(payload.get("next_paper_index", -1)) != len(completed):
        raise ValueError("Checkpoint next_paper_index does not match completed prefix")
    snapshot = payload.get("memory_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Checkpoint is missing the Memory snapshot")
    if canonical_sha256(snapshot) != payload.get("memory_state_identity"):
        raise ValueError("Checkpoint Memory state identity mismatch")
    cleaned = audit_history_records(
        path,
        completed,
        frozen_ids,
        allow_adjacent_uncommitted=True,
        clean_adjacent_uncommitted=True,
    )
    payload["discarded_uncommitted_records"] = cleaned
    return payload


def save_checkpoint(
    checkpoint_path: str,
    *,
    run_identity: Dict[str, Any],
    frozen_ids: Sequence[str],
    completed_ids: Sequence[str],
    memory_snapshot: Dict[str, Any],
    paper_record: Dict[str, Any],
) -> Dict[str, Any]:
    validate_run_identity(run_identity)
    validate_completed_prefix(completed_ids, frozen_ids)
    path = Path(checkpoint_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _records_dir(path)
    records.mkdir(parents=True, exist_ok=True)
    index = len(completed_ids) - 1
    if index >= 0:
        record_path = records / _record_name(index, completed_ids[-1])
        temporary_record = record_path.with_suffix(".tmp")
        temporary_record.write_text(
            json.dumps(paper_record, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_record, record_path)
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "run_identity": run_identity,
        "completed_paper_ids": list(completed_ids),
        "next_paper_index": len(completed_ids),
        "memory_snapshot": memory_snapshot,
        "memory_state_identity": canonical_sha256(memory_snapshot),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    audit_history_records(path, completed_ids, frozen_ids)
    return payload
