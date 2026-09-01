"""Freeze the minimal formal Review runtime without a readiness graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import (
    load_meta_synthesizer,
    load_reviewer_suite,
)

from .constants import ROLES
from .io import atomic_write_json, file_sha256, read_json, value_sha256


def freeze_system(
    *,
    output_path: str | Path,
    programs: dict[str, str],
    memory_snapshots: dict[str, str],
    model_identity: dict[str, Any],
    embedding_identity: dict[str, Any],
    frozen_s2_pools: dict[str, str],
    primary_meta_enabled: bool,
    code_commit: str,
    development_manifest_path: str | Path,
    coco_manifest_path: str | Path,
    evolution_run_ids: dict[str, str],
    evaluator_config: dict[str, Any],
    reflector_config: dict[str, Any],
    summarizer_config: dict[str, Any],
) -> dict[str, Any]:
    if set(programs) != set(ROLES) or set(memory_snapshots) != set(ROLES):
        raise ValueError("freeze-system requires five role-local Programs and snapshots")
    runtime_programs = {}
    runtime_memories = {}
    for role in ROLES:
        program = Path(programs[role]).expanduser().resolve()
        snapshot_path = Path(memory_snapshots[role]).expanduser().resolve()
        snapshot = read_json(snapshot_path)
        if snapshot.get("role") != role:
            raise ValueError(f"Memory snapshot role mismatch: {role}")
        if file_sha256(program) != snapshot.get("program_sha256"):
            raise ValueError(f"Program differs from Memory snapshot: {role}")
        runtime_programs[role] = {
            "path": str(program),
            "sha256": file_sha256(program),
            "version": snapshot.get("program_version"),
        }
        runtime_memories[role] = {
            "path": str(snapshot_path),
            "sha256": file_sha256(snapshot_path),
            "snapshot_id": snapshot.get("snapshot_id"),
        }
    if set(frozen_s2_pools) != {"development", "coco_1300"}:
        raise ValueError("freeze-system requires isolated development and coco_1300 S2 pools")
    s2_pools = {
        name: Path(path).expanduser().resolve()
        for name, path in frozen_s2_pools.items()
    }
    development = Path(development_manifest_path).expanduser().resolve()
    coco_manifest_source = Path(coco_manifest_path).expanduser().resolve()
    coco_manifest = read_json(coco_manifest_source)
    if (
        coco_manifest.get("protocol") != "coco_1300_protocol_compatible_resample_v1"
        or int(coco_manifest.get("count") or 0) != 1300
    ):
        raise ValueError("freeze-system requires the frozen protocol-compatible CoCo-1300 manifest")
    payload = {
        "protocol": "research-v2-frozen-system-v1",
        "review_runtime": {
            "programs": runtime_programs,
            "memory_snapshots": runtime_memories,
            "specialist_identity": load_reviewer_suite().manifest_payload(),
            "model_identity": dict(model_identity),
            "embedding_identity": dict(embedding_identity),
            "frozen_s2_pools": {
                name: {"path": str(path), "sha256": file_sha256(path)}
                for name, path in sorted(s2_pools.items())
            },
            "primary_meta": {
                "enabled": bool(primary_meta_enabled),
                "identity": load_meta_synthesizer().identity()
                if primary_meta_enabled
                else None,
            },
            "concat_ablation": {"enabled": True, "role_order": list(ROLES)},
        },
        "reproducibility": {
            "code_commit": code_commit,
            "development_manifest": {
                "path": str(development),
                "sha256": file_sha256(development),
            },
            "evolution_run_ids": dict(evolution_run_ids),
            "evaluator_config": dict(evaluator_config),
            "reflector_config": dict(reflector_config),
            "summarizer_config": dict(summarizer_config),
        },
        "external_evaluation": {
            "coco_1300_manifest": {
                "path": str(coco_manifest_source),
                "sha256": file_sha256(coco_manifest_source),
                "protocol": coco_manifest["protocol"],
                "count": coco_manifest["count"],
                "manifest_sha256": coco_manifest["manifest_sha256"],
            }
        },
    }
    payload["system_id"] = value_sha256(payload)
    atomic_write_json(output_path, payload)
    return payload
