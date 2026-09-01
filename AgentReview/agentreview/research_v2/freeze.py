"""Freeze the minimal formal Review runtime without a readiness graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import (
    load_meta_synthesizer,
    load_reviewer_suite,
)

from .constants import ROLES
from .evolution import EvolutionPool
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
    static_check,
)

RETRIEVAL_PROVENANCE_PROTOCOL = "retrieval-provenance-v2"


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
    evolution_pools: dict[str, str],
    evaluator_config: dict[str, Any],
    reflector_config: dict[str, Any],
    summarizer_config: dict[str, Any],
) -> dict[str, Any]:
    if set(programs) != set(ROLES) or set(memory_snapshots) != set(ROLES):
        raise ValueError("freeze-system requires five role-local Programs and snapshots")
    if set(evolution_run_ids) != set(ROLES):
        raise ValueError("freeze-system requires five role-local Evolution run IDs")
    if set(evolution_pools) != set(ROLES):
        raise ValueError("freeze-system requires five role-local Evolution pools")
    verified_winners = {}
    for role in ROLES:
        pool_path = Path(evolution_pools[role]).expanduser().resolve()
        pool = EvolutionPool.from_dict(read_json(pool_path))
        if pool.role != role:
            raise ValueError(f"Evolution pool role mismatch: {role}")
        winner = pool.best(require_snapshot=True)
        requested_program = Path(programs[role]).expanduser().resolve()
        requested_snapshot = Path(memory_snapshots[role]).expanduser().resolve()
        if requested_program != Path(winner.program_path).expanduser().resolve():
            raise ValueError(f"Frozen Program is not the Evolution pool winner: {role}")
        if requested_snapshot != Path(winner.memory_snapshot_path).expanduser().resolve():
            raise ValueError(
                f"Frozen Memory snapshot is not the Evolution pool winner snapshot: {role}"
            )
        if (
            file_sha256(requested_program) != winner.program_sha256
            or file_sha256(requested_snapshot) != winner.memory_snapshot_sha256
        ):
            raise ValueError(f"Evolution winner artifact hash mismatch: {role}")
        snapshot_value = read_json(requested_snapshot)
        if snapshot_value.get("snapshot_id") != winner.memory_snapshot_id:
            raise ValueError(f"Evolution winner snapshot ID mismatch: {role}")
        verified_winners[role] = {
            "pool_path": str(pool_path),
            "pool_sha256": file_sha256(pool_path),
            "candidate_id": winner.candidate_id,
            "static_score": winner.static_score,
            "program_sha256": winner.program_sha256,
            "memory_snapshot_sha256": winner.memory_snapshot_sha256,
            "memory_snapshot_id": winner.memory_snapshot_id,
        }
    runtime_programs = {}
    runtime_memories = {}
    for role in ROLES:
        program = Path(programs[role]).expanduser().resolve()
        snapshot_path = Path(memory_snapshots[role]).expanduser().resolve()
        snapshot = read_json(snapshot_path)
        program_errors = static_check(program.read_text(encoding="utf-8"))
        if program_errors:
            raise ValueError(f"Invalid frozen Program for {role}: {program_errors}")
        if snapshot.get("role") != role:
            raise ValueError(f"Memory snapshot role mismatch: {role}")
        if file_sha256(program) != snapshot.get("program_sha256"):
            raise ValueError(f"Program differs from Memory snapshot: {role}")
        capability_manifest = dict(program_capability_manifest(path=program))
        required_capability_fields = {
            "interface_version",
            "program_sha256",
            "policy_ast_sha256",
            "memory_ast_sha256",
        }
        if set(capability_manifest) != required_capability_fields:
            raise ValueError(f"Incomplete Program capability manifest: {role}")
        if capability_manifest.get("interface_version") != REVIEW_PLAN_PROTOCOL:
            raise ValueError(f"Program has an incompatible Review Plan interface: {role}")
        if capability_manifest.get("program_sha256") != file_sha256(program):
            raise ValueError(f"Program capability manifest source hash mismatch: {role}")
        runtime_programs[role] = {
            "path": str(program),
            "sha256": file_sha256(program),
            "version": snapshot.get("program_version"),
            "capability_manifest": capability_manifest,
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
        "protocol": "research-v2-frozen-system-v2",
        "review_runtime": {
            "programs": runtime_programs,
            "memory_snapshots": runtime_memories,
            "review_plan": {
                "protocol": REVIEW_PLAN_PROTOCOL,
                "decision_authority": "role-local-evolved-program",
                "s2_capability": {
                    "name": "retrieve_current_s2",
                    "scope": "current-paper-frozen-pool",
                    "network_access": False,
                    "llm_calls": 0,
                },
                "primary_intervention": None,
            },
            "retrieval_provenance": {
                "protocol": RETRIEVAL_PROVENANCE_PROTOCOL,
                "records_program_decision": True,
                "records_memory_trace": True,
                "records_s2_trace": True,
            },
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
            "evolution_winners": verified_winners,
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
