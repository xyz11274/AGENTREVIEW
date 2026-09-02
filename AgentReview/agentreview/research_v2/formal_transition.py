"""Deterministic transition from completed Formal Evolution to one frozen system."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coco_protocol import PROTOCOL as COCO_1300_PROTOCOL
from .coco_scoring_spec import validate_coco_scoring_spec
from .config import ResearchConfig
from .constants import (
    EVALUATOR_MAX_TOKENS,
    REFLECTOR_MAX_TOKENS,
    ROLES,
    SPLIT_SIZES,
    SUMMARIZER_MAX_TOKENS,
)
from .evaluation import (
    INTERNAL_EVALUATOR_PROMPT_VERSION,
    INTERNAL_EVALUATOR_SYSTEM_PROMPT,
)
from .evolution import (
    MAX_MUTATION_ATTEMPTS,
    REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
    REFLECTOR_CONTEXT_HEADROOM_TOKENS,
    REFLECTOR_FEEDBACK_VIEW_PROTOCOL,
    REFLECTOR_PROMPT_PROTOCOL,
    EvolutionPool,
    _reflector_prompt,
    _validate_recorded_context_budget,
    candidate_static_mean,
    feedback_packet,
    snapshot_binding,
    validate_pool_entry_snapshot,
)
from .formal_evolution import (
    FORMAL_ATTEMPT_WORK_PROTOCOL,
    FORMAL_ALL_ROLES_PROTOCOL,
    FORMAL_AUTONOMOUS_S2_INTERVENTION,
    FORMAL_CANDIDATE_FAILURE_PROTOCOL,
    FORMAL_ROLE_EVOLUTION_PROTOCOL,
    FORMAL_ROTATING_BATCH_SIZE,
    FORMAL_RUN_SPEC_PROTOCOL,
    FORMAL_STATIC_SIZE,
    _validate_feedback_artifacts,
    validate_canonical_evaluation_summary,
)
from .freeze import (
    FORMAL_COCO_WINNER_RUN_ID,
    _cross_check_external_coco_scoring_identity,
    _validate_completed_evolution,
    freeze_system,
)
from .io import (
    atomic_write_json,
    file_sha256,
    read_json,
    read_jsonl,
    value_sha256,
)
from .memory import SEED_NAMES
from .program_checks import program_capability_manifest
from .s2 import S2_RECORD_BUILDER_SHA256, validate_frozen_s2_pool

FORMAL_FREEZE_SPEC_PROTOCOL = "research-v2-formal-freeze-spec-v1"
FORMAL_SOURCE_BINDINGS_PROTOCOL = "research-v2-formal-source-bindings-v1"
FORMAL_EVOLUTION_CODE_PROVENANCE_PROTOCOL = (
    "research-v2-evolution-code-provenance-v1"
)
FORMAL_RESUME_AUDIT_PROTOCOL = "research-v2-formal-resume-audit-v1"
FORMAL_RESUME_AUDIT_MODE = "audited_multi_event_resume_chain"
FORMAL_RESUME_AUDIT_SCHEMAS = {
    "original_process_context_fix_probe": {
        "checkpoints": (
            ("original_process_commit", "agentreview"),
            ("pre_fix_checkpoint", "agentreview"),
            ("context_fix_commit", "agentreview"),
            ("probe_commit", "agentreview"),
        ),
        "resume_checkpoint": "probe_commit",
    },
    "structured_output_external_coco": {
        "checkpoints": (
            ("pre_fix_checkpoint", "agentreview"),
            ("structured_output_fix_commit", "agentreview"),
            ("external_coco_checkpoint", "external_coco"),
        ),
        "resume_checkpoint": "structured_output_fix_commit",
    },
    "legacy_identity_compatibility": {
        "checkpoints": (
            ("structured_output_fix_commit", "agentreview"),
            ("legacy_identity_compatibility_commit", "agentreview"),
        ),
        "resume_checkpoint": "legacy_identity_compatibility_commit",
    },
}
FORMAL_RESUME_AUDIT_SCHEMA_ORDER = tuple(FORMAL_RESUME_AUDIT_SCHEMAS)
FORMAL_RESUME_AUDIT_FILENAME = re.compile(
    r"resume_(?P<timestamp>\d{8}T\d{6}[+-]\d{4})\.json"
)
DEVELOPMENT_MANIFEST_PROTOCOL = "research-v2-development-split-v1"
DEVELOPMENT_S2_VERSION = "frozen_s2_external_development_v1"
COCO_S2_VERSION = "frozen_s2_coco1300_v1"
COCO_1300_COUNT = 1300
EXTERNAL_COCO_IDENTITY_PROTOCOL = "research-v2-external-coco-identity-v1"
EXTERNAL_COCO_RUNNER_RELATIVE_PATH = Path("scripts/run_official_candidate.py")
FREEZE_ARGUMENT_FIELDS = {
    "programs",
    "memory_snapshots",
    "model_identity",
    "embedding_identity",
    "frozen_s2_pools",
    "primary_meta_enabled",
    "code_commit",
    "development_manifest_path",
    "coco_manifest_path",
    "evolution_run_ids",
    "evolution_pools",
    "evaluator_config",
    "reflector_config",
    "summarizer_config",
    "require_bubblewrap",
    "external_coco_identity",
    "coco_scoring_spec",
}


def clean_git_head(repository_root: str | Path) -> str:
    """Return the exact clean repository HEAD used for the frozen runtime."""
    root = Path(repository_root).expanduser().resolve()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commit = head.stdout.strip()
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Formal freeze requires a readable Git HEAD")
    if status.returncode != 0:
        raise RuntimeError("Formal freeze requires a readable Git worktree status")
    if status.stdout.strip():
        raise RuntimeError("Formal freeze requires a clean Git worktree")
    return commit


def _external_coco_identity(config: ResearchConfig) -> dict[str, Any]:
    root = Path(config.coco_root).expanduser().resolve()
    runner = _required_file(
        root / EXTERNAL_COCO_RUNNER_RELATIVE_PATH,
        "the official external CoCo runner",
    )
    return {
        "protocol": EXTERNAL_COCO_IDENTITY_PROTOCOL,
        "repository_root": str(root),
        "git_commit": clean_git_head(root),
        "runner": {
            "path": str(runner),
            "sha256": file_sha256(runner),
        },
    }


def _required_file(path: str | Path, label: str) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Formal freeze requires {label}: {source}")
    return source


def _self_hashed_object(path: Path, *, label: str) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object: {path}")
    body = {name: item for name, item in value.items() if name != "manifest_sha256"}
    if value.get("manifest_sha256") != value_sha256(body):
        raise ValueError(f"{label} identity mismatch: {path}")
    return value


def _validate_development_manifest(
    path: Path,
    *,
    query_path: Path,
) -> list[dict[str, Any]]:
    manifest = _self_hashed_object(path, label="Development manifest")
    if manifest.get("protocol") != DEVELOPMENT_MANIFEST_PROTOCOL:
        raise ValueError("Formal freeze requires the development split protocol")
    splits = manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != set(SPLIT_SIZES):
        raise ValueError("Development manifest requires the exact three formal splits")
    manifest_ids: list[str] = []
    for name, expected_count in SPLIT_SIZES.items():
        split = splits.get(name)
        if not isinstance(split, dict):
            raise TypeError(f"Development split {name} must be an object")
        paper_ids = split.get("paper_ids")
        if (
            split.get("count") != expected_count
            or not isinstance(paper_ids, list)
            or len(paper_ids) != expected_count
            or any(not isinstance(value, str) or not value for value in paper_ids)
            or len(set(paper_ids)) != expected_count
        ):
            raise ValueError(f"Development split {name} identity/count mismatch")
        manifest_ids.extend(paper_ids)
    if len(set(manifest_ids)) != sum(SPLIT_SIZES.values()):
        raise ValueError("Development manifest splits overlap")
    targets = read_jsonl(query_path)
    target_ids = [str(value.get("paper_id") or "") for value in targets]
    if (
        len(target_ids) != sum(SPLIT_SIZES.values())
        or "" in target_ids
        or len(set(target_ids)) != len(target_ids)
        or set(target_ids) != set(manifest_ids)
    ):
        raise ValueError("Development S2 targets differ from the formal split manifest")
    return targets


def _validate_coco_manifest(
    path: Path,
    *,
    query_path: Path,
) -> list[dict[str, Any]]:
    manifest = _self_hashed_object(path, label="CoCo-1300 manifest")
    records = manifest.get("records")
    if (
        manifest.get("protocol") != COCO_1300_PROTOCOL
        or manifest.get("count") != COCO_1300_COUNT
        or not isinstance(records, list)
        or len(records) != COCO_1300_COUNT
    ):
        raise ValueError("Formal freeze requires the exact CoCo-1300 manifest")
    manifest_ids = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"CoCo manifest record {index} must be an object")
        paper_id = str(record.get("paper_id") or "")
        if not paper_id:
            raise ValueError(f"CoCo manifest record {index} lacks paper_id")
        manifest_ids.append(paper_id)
    if len(set(manifest_ids)) != COCO_1300_COUNT:
        raise ValueError("CoCo manifest requires 1,300 unique paper IDs")
    targets = read_jsonl(query_path)
    target_ids = [str(value.get("paper_id") or "") for value in targets]
    if (
        len(target_ids) != COCO_1300_COUNT
        or "" in target_ids
        or len(set(target_ids)) != COCO_1300_COUNT
        or set(target_ids) != set(manifest_ids)
    ):
        raise ValueError("CoCo S2 targets differ from the frozen CoCo manifest")
    return targets


def _evidence_run_paths(
    *,
    artifact_root: Path,
    category: str,
    run_id: str,
    paper_ids: list[str],
    role: str,
    label: str,
    require_complete: bool,
) -> tuple[dict[str, Path], list[Path]]:
    """Bind one exact Review/Evaluation run and select its primary artifacts."""
    run_root = artifact_root / category / run_id
    expected_ids = set(paper_ids)
    values: dict[str, Path] = {}
    primary_by_id: dict[str, Path] = {}
    if run_root.exists() and (not run_root.is_dir() or run_root.is_symlink()):
        raise ValueError(f"Formal evidence run root is invalid: {run_root}")
    if run_root.is_dir():
        resolved_root = run_root.resolve()
        for path in sorted(run_root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Formal evidence must not contain symlinks: {path}")
            if not path.is_file():
                continue
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError(
                    f"Formal evidence path escaped its run root: {path}"
                ) from exc
            if len(relative.parts) < 2 or relative.parts[0] not in expected_ids:
                raise ValueError(
                    f"Formal evidence contains an undeclared paper path: {path}"
                )
            values[f"{label}.{relative.as_posix()}"] = resolved
    for paper_id in paper_ids:
        paper_root = run_root / paper_id
        candidates = [
            paper_root / f"{role}.json",
            paper_root / f"{role}.paper_failure.json",
        ]
        present = [path.resolve() for path in candidates if path.is_file()]
        if len(present) > 1 or (require_complete and len(present) != 1):
            raise ValueError(
                f"Formal {label} primary evidence count differs for {paper_id}"
            )
        if present:
            value = read_json(present[0])
            if not isinstance(value, dict) or {
                "run_id": value.get("run_id"),
                "paper_id": value.get("paper_id"),
                "role": value.get("role"),
            } != {"run_id": run_id, "paper_id": paper_id, "role": role}:
                raise ValueError(
                    f"Formal {label} primary evidence identity differs for {paper_id}"
                )
            primary_by_id[paper_id] = present[0]
    return values, [primary_by_id[paper_id] for paper_id in paper_ids if paper_id in primary_by_id]


def _closed_tree_paths(*, root: Path, label: str) -> dict[str, Path]:
    """Bind every ordinary file below one deterministic artifact tree."""
    values: dict[str, Path] = {}
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise ValueError(f"Formal evidence tree root is invalid: {root}")
    if not root.is_dir():
        return values
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Formal evidence must not contain symlinks: {path}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(
                f"Formal evidence path escaped its tree root: {path}"
            ) from exc
        values[f"{label}.{relative.as_posix()}"] = resolved
    return values


def _validate_reflector_artifacts(
    *,
    role_root: Path,
    key: str,
    parent_program_path: str,
    feedback: dict[str, Any],
    plan_sha256: str,
    completion_identity: dict[str, Any],
    outcome: dict[str, Any],
) -> None:
    """Bind one outcome to its committed Reflector response sequence."""

    def validate_response(
        path: Path,
        *,
        component: str,
        expected_request_sha256: str | None,
    ) -> str:
        value = read_json(_required_file(path, f"the {component} response"))
        expected = {
            "plan_sha256": plan_sha256,
            "component": component,
            "completion_identity": completion_identity,
            "prompt_protocol": REFLECTOR_PROMPT_PROTOCOL,
        }
        request_sha256 = value.get("request_sha256") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or {name: value.get(name) for name in expected} != expected
            or not isinstance(request_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", request_sha256)
            or (
                expected_request_sha256 is not None
                and request_sha256 != expected_request_sha256
            )
        ):
            raise ValueError(f"Formal {component} response identity differs")
        _validate_recorded_context_budget(
            value.get("context_budget"),
            completion_identity=completion_identity,
        )
        output = value.get("raw_model_output")
        if not isinstance(output, str) or not output.strip():
            raise ValueError(f"Formal {component} response is empty")
        return output

    parent_code = Path(parent_program_path).read_text(encoding="utf-8")
    reflector_request = value_sha256(
        [
            {
                "role": "user",
                "content": _reflector_prompt(parent_code, feedback),
            }
        ]
    )
    primary_output = validate_response(
        role_root / f"attempt-{key}.reflector.json",
        component="reflector",
        expected_request_sha256=reflector_request,
    )
    repair_used = outcome.get("repair_used")
    repair_path = role_root / f"attempt-{key}.repair.json"
    if not isinstance(repair_used, bool):
        raise TypeError("Formal Evolution repair_used must be a boolean")
    if not repair_used:
        if repair_path.exists() or outcome.get("raw_reflector_output") != primary_output:
            raise ValueError("Formal Reflector outcome sequence differs")
        return
    repair_output = validate_response(
        repair_path,
        component="reflector_repair",
        # The first-pass compiler/protocol errors were not separately persisted;
        # the immutable request hash is still closed by the response file binding.
        expected_request_sha256=None,
    )
    combined = primary_output + "\n\n--- EXPLICIT REPAIR ---\n\n" + repair_output
    allowed_outputs = {combined}
    # A repair response that cannot be parsed never reaches the concatenation
    # assignment in propose_candidate; the immutable repair artifact remains bound.
    if outcome.get("status") == "failed":
        allowed_outputs.add(primary_output)
    if outcome.get("raw_reflector_output") not in allowed_outputs:
        raise ValueError("Formal Reflector repair sequence differs")


def _validate_failed_candidate_evidence(
    *,
    artifact_root: Path,
    role: str,
    formal_run_id: str,
    candidate_id: str,
    parent_seed_kind: str,
    program_path: Path,
    phase: Any,
    static_review_bindings: dict[str, Path],
    static_reviews: list[Path],
    static_evaluation_bindings: dict[str, Path],
    static_evaluations: list[Path],
) -> dict[str, Path]:
    """Validate and bind the recoverable prefix of one failed candidate."""
    allowed_phases = {
        "candidate_memory_build",
        "candidate_static_review",
        "candidate_static_evaluation",
    }
    if phase not in allowed_phases:
        raise ValueError(f"Unknown formal candidate-failure phase: {phase}")
    program_version = f"{formal_run_id}-{role.lower()}-{candidate_id}"
    memory_root = artifact_root / "memories" / role / program_version
    memory_paths = _closed_tree_paths(
        root=memory_root,
        label=f"{role}.evidence.failed_candidate.{candidate_id}.memory",
    )
    snapshot_path = memory_root / "snapshot.json"
    if phase == "candidate_memory_build":
        if (
            snapshot_path.is_file()
            or static_review_bindings
            or static_evaluation_bindings
        ):
            raise ValueError(
                f"Formal failed Memory-build evidence differs for {role}/{candidate_id}"
            )
        return memory_paths
    _required_file(snapshot_path, f"the {role} failed-candidate Memory snapshot")
    snapshot_binding(
        snapshot_path=snapshot_path,
        role=role,
        program_path=program_path,
        seed_kind=parent_seed_kind,
    )
    snapshot = read_json(snapshot_path)
    if snapshot.get("program_version") != program_version:
        raise ValueError(
            f"Formal failed-candidate Memory version differs for {role}/{candidate_id}"
        )
    snapshot_id = snapshot["snapshot_id"]
    review_by_id: dict[str, dict[str, Any]] = {}
    for path in static_reviews:
        review = read_json(path)
        review_id = str(review.get("review_id") or "")
        if (
            not review_id
            or review_id in review_by_id
            or review.get("program_version") != program_version
            or review.get("memory_snapshot_id") != snapshot_id
        ):
            raise ValueError(
                f"Formal partial candidate Review differs for {role}/{candidate_id}"
            )
        review_by_id[review_id] = review
    evaluation_review_ids: set[str] = set()
    for path in static_evaluations:
        evaluation = read_json(path)
        review_id = str(evaluation.get("review_id") or "")
        review = review_by_id.get(review_id)
        try:
            score = float(evaluation.get("review_quality"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Formal partial candidate score differs for {role}/{candidate_id}"
            ) from exc
        if (
            review is None
            or review_id in evaluation_review_ids
            or evaluation.get("paper_id") != review.get("paper_id")
            or not 1.0 <= score <= 5.0
        ):
            raise ValueError(
                f"Formal partial candidate Evaluation differs for {role}/{candidate_id}"
            )
        evaluation_review_ids.add(review_id)
    if (
        phase == "candidate_static_review"
        and static_evaluation_bindings
    ) or (
        phase == "candidate_static_evaluation"
        and len(static_reviews) != FORMAL_STATIC_SIZE
    ):
        raise ValueError(
            f"Formal partial candidate evidence differs for {role}/{candidate_id}"
        )
    return memory_paths


def _validated_evolution_evidence_paths(
    *,
    artifact_root: Path,
    role_root: Path,
    role: str,
    formal_run_id: str,
    run_spec: dict[str, Any],
    pool: EvolutionPool,
) -> dict[str, Path]:
    """Re-derive every score and Reflector input that selected one winner."""
    static_split = read_json(run_spec["splits"]["static"]["path"])
    static_records = (
        static_split.get("records") if isinstance(static_split, dict) else None
    )
    if not isinstance(static_records, list):
        raise TypeError(f"Formal static evidence split is invalid for {role}")
    static_ids = [str(record.get("paper_id") or "") for record in static_records]
    rotating_split = read_json(run_spec["splits"]["rotating"]["path"])
    rotating_records = (
        rotating_split.get("records")
        if isinstance(rotating_split, dict)
        else None
    )
    if (
        len(static_ids) != FORMAL_STATIC_SIZE
        or "" in static_ids
        or len(set(static_ids)) != FORMAL_STATIC_SIZE
        or not isinstance(rotating_records, list)
        or len(rotating_records) < MAX_MUTATION_ATTEMPTS * FORMAL_ROTATING_BATCH_SIZE
    ):
        raise ValueError(f"Formal static evidence IDs differ for {role}")
    rotating_ids = [str(record.get("paper_id") or "") for record in rotating_records]
    if "" in rotating_ids or len(set(rotating_ids)) != len(rotating_ids):
        raise ValueError(f"Formal rotating evidence IDs differ for {role}")
    entry_values = pool.to_dict()["entries"]
    entries = {entry.candidate_id: entry for entry in pool.entries}
    entry_payloads = {str(value["candidate_id"]): value for value in entry_values}
    paths: dict[str, Path] = {}
    replayed_pool = EvolutionPool(role)

    for seed_name in SEED_NAMES:
        candidate_id = f"seed-{seed_name}"
        entry = entries.get(candidate_id)
        if entry is None:
            raise ValueError(f"Formal pool lacks {role} seed {seed_name}")
        validate_pool_entry_snapshot(entry)
        seed = run_spec["seed_programs"][seed_name]
        seed_snapshot = read_json(entry.memory_snapshot_path)
        expected_seed_lineage = [
            {
                "candidate_id": candidate_id,
                "parent_id": None,
                "change": {
                    "title": f"Initial {seed_name} seed",
                    "details": [],
                },
                "capability_manifest": seed["capability_manifest"],
            }
        ]
        if (
            entry.parent_id is not None
            or entry.seed_kind != seed_name
            or Path(entry.program_path).expanduser().resolve()
            != Path(seed["path"]).expanduser().resolve()
            or entry.program_sha256 != seed["sha256"]
            or file_sha256(entry.program_path) != entry.program_sha256
            or entry.capability_manifest != seed["capability_manifest"]
            or entry.capability_manifest
            != program_capability_manifest(path=entry.program_path)
            or seed_snapshot.get("program_version")
            != f"{formal_run_id}-{role.lower()}-seed-{seed_name}"
            or entry.lineage != expected_seed_lineage
        ):
            raise ValueError(f"Formal pool seed identity differs for {role}/{seed_name}")
        version = f"{formal_run_id}-{role.lower()}-seed-{seed_name}"
        review_run_id = f"{version}-static-review"
        evaluation_run_id = f"{version}-static-eval"
        review_bindings, reviews = _evidence_run_paths(
            artifact_root=artifact_root,
            category="reviews",
            run_id=review_run_id,
            paper_ids=static_ids,
            role=role,
            label=f"{role}.evidence.seed.{seed_name}.review",
            require_complete=True,
        )
        evaluation_bindings, evaluations = _evidence_run_paths(
            artifact_root=artifact_root,
            category="evaluations",
            run_id=evaluation_run_id,
            paper_ids=static_ids,
            role=role,
            label=f"{role}.evidence.seed.{seed_name}.evaluation",
            require_complete=True,
        )
        paths.update(review_bindings)
        paths.update(evaluation_bindings)
        score = candidate_static_mean(
            role=role,
            snapshot_path=entry.memory_snapshot_path,
            review_paths=reviews,
            evaluation_paths=evaluations,
        )
        if score != float(entry.static_score):
            raise ValueError(f"Formal seed evidence score differs for {role}/{seed_name}")
        replayed_pool.add(entry)

    coordinator = read_json(role_root / "coordinator.json")
    attempts = coordinator.get("attempts") if isinstance(coordinator, dict) else None
    expected_attempts = {f"{attempt:03d}" for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1)}
    if not isinstance(attempts, dict) or set(attempts) != expected_attempts:
        raise ValueError(f"Formal coordinator attempts are incomplete for {role}")
    for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1):
        key = f"{attempt:03d}"
        work_path = role_root / f"attempt-{key}.work.json"
        outcome_path = role_root / f"attempt-{key}.json"
        work = read_json(_required_file(work_path, f"the {role} attempt {key} work"))
        outcome = read_json(
            _required_file(outcome_path, f"the {role} attempt {key} outcome")
        )
        if not isinstance(work, dict) or not isinstance(outcome, dict):
            raise TypeError(f"Formal attempt evidence must be objects for {role}/{key}")
        unhashed_work = {
            name: value for name, value in work.items() if name != "work_id"
        }
        prefix = f"{formal_run_id}-{role.lower()}-a{attempt:03d}"
        expected_run_ids = {
            "rotating_reviews": f"{prefix}-rotating-review",
            "rotating_evaluations": f"{prefix}-rotating-eval",
            "candidate_static_reviews": f"{prefix}-static-review",
            "candidate_static_evaluations": f"{prefix}-static-eval",
        }
        expected_work_identity = {
            "protocol": FORMAL_ATTEMPT_WORK_PROTOCOL,
            "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
            "role": role,
            "run_id": formal_run_id,
            "attempt": attempt,
            "artifact_run_ids": expected_run_ids,
        }
        expected_static = {
            name: run_spec["splits"]["static"][name]
            for name in ("path", "sha256", "split_sha256")
        } | {
            "paper_ids": static_ids,
        }
        expected_rotating = {
            name: run_spec["splits"]["rotating"][name]
            for name in ("path", "sha256", "split_sha256")
        }
        start = (attempt - 1) * FORMAL_ROTATING_BATCH_SIZE
        batch_records = rotating_records[
            start : start + FORMAL_ROTATING_BATCH_SIZE
        ]
        expected_batch = {
            "schedule": "frozen-record-order-disjoint-5-v1",
            "paper_ids": [record["paper_id"] for record in batch_records],
            "paper_paths": [record["path"] for record in batch_records],
            "records_sha256": value_sha256(batch_records),
        }
        rendered_pool = (
            json.dumps(
                replayed_pool.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        expected_pool_before = {
            "path": str((role_root / "pool.json").resolve()),
            "sha256": hashlib.sha256(rendered_pool).hexdigest(),
        }
        if (
            {name: work.get(name) for name in expected_work_identity}
            != expected_work_identity
            or work.get("work_id") != value_sha256(unhashed_work)
            or work.get("canonical_evaluation")
            != run_spec["canonical_evaluation"]
            or work.get("pool_before") != expected_pool_before
            or work.get("static_split") != expected_static
            or work.get("rotating_split") != expected_rotating
            or work.get("rotating_batch") != expected_batch
        ):
            raise ValueError(f"Formal attempt work identity differs for {role}/{key}")
        state = attempts[key]
        if not isinstance(state, dict) or state.get("attempt_path") != str(
            outcome_path.resolve()
        ):
            raise ValueError(f"Formal coordinator attempt identity differs for {role}/{key}")
        if {
            "role": outcome.get("role"),
            "run_id": outcome.get("run_id"),
            "attempt": outcome.get("attempt"),
        } != {"role": role, "run_id": formal_run_id, "attempt": attempt}:
            raise ValueError(f"Formal attempt outcome identity differs for {role}/{key}")
        parent = replayed_pool.select_parent(seed=attempt)
        validate_pool_entry_snapshot(parent)
        parent_snapshot = read_json(parent.memory_snapshot_path)
        expected_parent = {
            "candidate_id": parent.candidate_id,
            "program_path": parent.program_path,
            "program_sha256": parent.program_sha256,
            "program_version": parent_snapshot.get("program_version"),
            "memory_snapshot_path": parent.memory_snapshot_path,
            "memory_snapshot_sha256": parent.memory_snapshot_sha256,
            "memory_snapshot_id": parent.memory_snapshot_id,
            "seed_kind": parent.seed_kind,
        }
        attempt_rotating_ids = list(expected_batch["paper_ids"])
        if work.get("parent") != expected_parent:
            raise ValueError(f"Formal attempt inputs differ for {role}/{key}")
        rotating_review_bindings, rotating_reviews = _evidence_run_paths(
            artifact_root=artifact_root,
            category="reviews",
            run_id=expected_run_ids["rotating_reviews"],
            paper_ids=attempt_rotating_ids,
            role=role,
            label=f"{role}.evidence.attempt.{key}.rotating_review",
            require_complete=True,
        )
        rotating_evaluation_bindings, rotating_evaluations = _evidence_run_paths(
            artifact_root=artifact_root,
            category="evaluations",
            run_id=expected_run_ids["rotating_evaluations"],
            paper_ids=attempt_rotating_ids,
            role=role,
            label=f"{role}.evidence.attempt.{key}.rotating_evaluation",
            require_complete=True,
        )
        paths.update(rotating_review_bindings)
        paths.update(rotating_evaluation_bindings)
        _validate_feedback_artifacts(
            work=work,
            review_paths=rotating_reviews,
            evaluation_paths=rotating_evaluations,
        )
        feedback = feedback_packet(
            parent=parent,
            review_paths=rotating_reviews,
            evaluation_paths=rotating_evaluations,
        )
        if outcome.get("feedback_sha256") != value_sha256(feedback):
            raise ValueError(f"Formal Reflector evidence differs for {role}/{key}")

        candidate_id = hashlib.sha256(
            (
                f"{formal_run_id}\n{role}\n{attempt}\n"
                f"{parent.candidate_id}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        expected_outcome_identity = {
            "protocol": "research-v2-evolution-attempt-v2",
            "run_id": formal_run_id,
            "attempt": attempt,
            "role": role,
            "candidate_id": candidate_id,
            "parent_id": parent.candidate_id,
            "completion_identity": run_spec["model_identity"],
            "parent_capability_manifest": parent.capability_manifest,
        }
        if {
            name: outcome.get(name) for name in expected_outcome_identity
        } != expected_outcome_identity:
            raise ValueError(f"Formal attempt outcome differs for {role}/{key}")
        plan_path = role_root / f"attempt-{key}.plan.json"
        plan = read_json(_required_file(plan_path, f"the {role} attempt {key} plan"))
        expected_plan = {
            "protocol": "research-v2-evolution-attempt-plan-v1",
            "run_id": formal_run_id,
            "attempt": attempt,
            "role": role,
            "parent_id": parent.candidate_id,
            "parent_program_sha256": parent.program_sha256,
            "candidate_id": candidate_id,
            "feedback_sha256": value_sha256(feedback),
            "completion_identity": run_spec["model_identity"],
        }
        if plan != expected_plan or outcome.get("plan_sha256") != value_sha256(plan):
            raise ValueError(f"Formal attempt plan differs for {role}/{key}")
        _validate_reflector_artifacts(
            role_root=role_root,
            key=key,
            parent_program_path=parent.program_path,
            feedback=feedback,
            plan_sha256=value_sha256(plan),
            completion_identity=run_spec["model_identity"],
            outcome=outcome,
        )

        status = str(state.get("status") or "")
        static_review_bindings, static_reviews = _evidence_run_paths(
            artifact_root=artifact_root,
            category="reviews",
            run_id=expected_run_ids["candidate_static_reviews"],
            paper_ids=static_ids,
            role=role,
            label=f"{role}.evidence.attempt.{key}.static_review",
            require_complete=status == "committed",
        )
        static_evaluation_bindings, static_evaluations = _evidence_run_paths(
            artifact_root=artifact_root,
            category="evaluations",
            run_id=expected_run_ids["candidate_static_evaluations"],
            paper_ids=static_ids,
            role=role,
            label=f"{role}.evidence.attempt.{key}.static_evaluation",
            require_complete=status == "committed",
        )
        paths.update(static_review_bindings)
        paths.update(static_evaluation_bindings)
        if status == "failed":
            if (
                outcome.get("status") != "failed"
                or state.get("candidate_id") != candidate_id
                or static_review_bindings
                or static_evaluation_bindings
            ):
                raise ValueError(f"Formal failed attempt evidence differs for {role}/{key}")
            continue
        if status == "committed":
            entry = entries.get(candidate_id)
            if entry is not None:
                validate_pool_entry_snapshot(entry)
            entry_snapshot = (
                read_json(entry.memory_snapshot_path)
                if entry is not None
                else {}
            )
            current_capability = (
                program_capability_manifest(path=entry.program_path)
                if entry is not None
                else None
            )
            if (
                outcome.get("status") != "proposed"
                or entry is None
                or state.get("candidate") != entry_payloads.get(candidate_id)
                or entry.parent_id != parent.candidate_id
                or entry.seed_kind != parent.seed_kind
                or Path(entry.program_path).expanduser().resolve()
                != (artifact_root / "programs" / role / candidate_id / "program.py").resolve()
                or entry.program_sha256 != outcome.get("program_sha256")
                or file_sha256(entry.program_path) != entry.program_sha256
                or str(Path(outcome.get("program_path") or "").expanduser().resolve())
                != entry.program_path
                or outcome.get("capability_manifest") != entry.capability_manifest
                or entry.capability_manifest != current_capability
                or entry_snapshot.get("program_version")
                != f"{formal_run_id}-{role.lower()}-{candidate_id}"
            ):
                raise ValueError(f"Formal committed attempt differs for {role}/{key}")
            score = candidate_static_mean(
                role=role,
                snapshot_path=entry.memory_snapshot_path,
                review_paths=static_reviews,
                evaluation_paths=static_evaluations,
            )
            if score != float(entry.static_score):
                raise ValueError(f"Formal candidate score differs for {role}/{key}")
            expected_lineage = [
                *parent.lineage,
                {
                    "candidate_id": candidate_id,
                    "parent_id": parent.candidate_id,
                    "change": outcome.get("change"),
                    "static_score": score,
                    "capability_manifest": current_capability,
                    "memory_snapshot_id": entry.memory_snapshot_id,
                },
            ]
            if entry.lineage != expected_lineage:
                raise ValueError(
                    f"Formal candidate lineage differs for {role}/{key}"
                )
            replayed_pool.add(entry)
            continue
        if status != "candidate_failed" or outcome.get("status") != "proposed":
            raise ValueError(f"Formal attempt is non-terminal for {role}/{key}: {status}")
        if not candidate_id or candidate_id in entries:
            raise ValueError(f"Formal failed candidate entered the pool for {role}/{key}")
        failure_path = role_root / f"attempt-{key}.candidate-failure.json"
        failure = read_json(
            _required_file(failure_path, f"the {role} candidate failure {key}")
        )
        failure_body = {
            name: value for name, value in failure.items() if name != "failure_id"
        }
        if (
            not isinstance(failure, dict)
            or failure.get("protocol") != FORMAL_CANDIDATE_FAILURE_PROTOCOL
            or failure.get("role") != role
            or failure.get("run_id") != formal_run_id
            or failure.get("attempt") != attempt
            or failure.get("candidate_id") != candidate_id
            or failure.get("parent_id") != parent.candidate_id
            or failure.get("work_id") != work["work_id"]
            or failure.get("failure_id") != value_sha256(failure_body)
            or state.get("failure_path") != str(failure_path.resolve())
            or state.get("failure_id") != failure.get("failure_id")
            or state.get("candidate_id") != candidate_id
            or state.get("phase") != failure.get("phase")
        ):
            raise ValueError(f"Formal candidate failure differs for {role}/{key}")
        failed_program = Path(str(failure.get("program_path") or "")).resolve()
        if (
            failed_program
            != (artifact_root / "programs" / role / candidate_id / "program.py").resolve()
            or str(Path(outcome.get("program_path") or "").expanduser().resolve())
            != str(failed_program)
            or outcome.get("program_sha256") != failure.get("program_sha256")
            or file_sha256(
                _required_file(failed_program, f"the {role} failed Program {key}")
            )
            != failure.get("program_sha256")
        ):
            raise ValueError(f"Formal failed Program differs for {role}/{key}")
        paths[f"{role}.evidence.attempt.{key}.failed_program"] = failed_program
        phase = failure.get("phase")
        paths.update(
            _validate_failed_candidate_evidence(
                artifact_root=artifact_root,
                role=role,
                formal_run_id=formal_run_id,
                candidate_id=candidate_id,
                parent_seed_kind=parent.seed_kind,
                program_path=failed_program,
                phase=phase,
                static_review_bindings=static_review_bindings,
                static_reviews=static_reviews,
                static_evaluation_bindings=static_evaluation_bindings,
                static_evaluations=static_evaluations,
            )
        )
    if replayed_pool.to_dict() != pool.to_dict():
        raise ValueError(f"Formal pool contains uncommitted candidates for {role}")
    return paths


def _expected_source_paths(
    *,
    formal_run_id: str,
    arguments: dict[str, Any],
) -> dict[str, Path]:
    pools, artifact_root = _formal_artifact_root(
        formal_run_id=formal_run_id,
        arguments=arguments,
    )
    values = {
        f"formal.resume_audit.{index:03d}": path
        for index, path in enumerate(
            _formal_resume_audit_paths(
                artifact_root=artifact_root,
                formal_run_id=formal_run_id,
            )
        )
    }
    values.update(
        {
            "formal.all_roles_summary": (
                artifact_root / "runs" / formal_run_id / "formal-evolution.json"
            ),
            "development.manifest": Path(
                arguments["development_manifest_path"]
            ).expanduser().resolve(),
            "development.s2_queries": (
                Path(arguments["development_manifest_path"])
                .expanduser()
                .resolve()
                .parent
                / "s2_title_queries.jsonl"
            ),
            "development.s2_pool": Path(
                arguments["frozen_s2_pools"]["development"]
            ).expanduser().resolve(),
            "coco_1300.manifest": Path(
                arguments["coco_manifest_path"]
            ).expanduser().resolve(),
            "coco_1300.s2_queries": (
                Path(arguments["coco_manifest_path"])
                .expanduser()
                .resolve()
                .parent
                / "s2_title_queries.jsonl"
            ),
            "coco_1300.s2_pool": Path(
                arguments["frozen_s2_pools"]["coco_1300"]
            ).expanduser().resolve(),
            "external_coco.runner": Path(
                arguments["external_coco_identity"]["runner"]["path"]
            ).expanduser().resolve(),
            "external_coco.scoring_spec": Path(
                arguments["coco_scoring_spec"]["path"]
            ).expanduser().resolve(),
        }
    )
    for role in ROLES:
        pool = pools[role]
        role_root = pool.parent
        run_spec = read_json(role_root / "run-spec.json")
        values.update(
            {
                f"{role}.pool": pool,
                f"{role}.coordinator": role_root / "coordinator.json",
                f"{role}.winner": role_root / "winner.json",
                f"{role}.run_spec": role_root / "run-spec.json",
                f"{role}.program": Path(arguments["programs"][role])
                .expanduser()
                .resolve(),
                f"{role}.memory_snapshot": Path(
                    arguments["memory_snapshots"][role]
                )
                .expanduser()
                .resolve(),
            }
        )
        canonical = run_spec.get("canonical_evaluation")
        if isinstance(canonical, dict) and canonical.get("path"):
            values[f"{role}.canonical_evaluation"] = Path(
                canonical["path"]
            ).expanduser().resolve()
        splits = run_spec.get("splits")
        if isinstance(splits, dict):
            for name, split in sorted(splits.items()):
                if isinstance(split, dict) and split.get("path"):
                    split_path = Path(
                        split["path"]
                    ).expanduser().resolve()
                    values[f"{role}.split.{name}"] = split_path
                    split_value = read_json(split_path)
                    records = (
                        split_value.get("records")
                        if isinstance(split_value, dict)
                        else None
                    )
                    if isinstance(records, list):
                        for record in records:
                            if isinstance(record, dict) and record.get("path"):
                                paper_id = str(record.get("paper_id") or "")
                                values[
                                    f"{role}.data.paper.{name}.{paper_id}"
                                ] = Path(record["path"]).expanduser().resolve()
                        if name == "canonical" and isinstance(canonical, dict):
                            for record in records:
                                if not isinstance(record, dict):
                                    continue
                                paper_id = str(record.get("paper_id") or "")
                                values[f"{role}.canonical_review.{paper_id}"] = (
                                    artifact_root
                                    / "reviews"
                                    / str(canonical.get("review_run_id") or "")
                                    / paper_id
                                    / f"{role}.json"
                                )
                                values[f"{role}.canonical_evaluation.{paper_id}"] = (
                                    artifact_root
                                    / "evaluations"
                                    / str(canonical.get("run_id") or "")
                                    / paper_id
                                    / f"{role}.json"
                                )
        seeds = run_spec.get("seed_programs")
        if isinstance(seeds, dict):
            for name, seed in sorted(seeds.items()):
                if isinstance(seed, dict) and seed.get("path"):
                    values[f"{role}.seed_program.{name}"] = Path(
                        seed["path"]
                    ).expanduser().resolve()
        role_pool = EvolutionPool.from_dict(read_json(pool))
        for entry in role_pool.entries:
            values[f"{role}.candidate.{entry.candidate_id}.program"] = Path(
                entry.program_path
            ).expanduser().resolve()
            if entry.memory_snapshot_path:
                values[f"{role}.candidate.{entry.candidate_id}.memory_snapshot"] = (
                    Path(entry.memory_snapshot_path).expanduser().resolve()
                )
        evidence_paths = _validated_evolution_evidence_paths(
            artifact_root=artifact_root,
            role_root=role_root,
            role=role,
            formal_run_id=formal_run_id,
            run_spec=run_spec,
            pool=role_pool,
        )
        duplicate_names = set(values).intersection(evidence_paths)
        if duplicate_names:
            raise ValueError(
                f"Formal source graph contains duplicate names: {sorted(duplicate_names)}"
            )
        values.update(evidence_paths)
        for attempt_path in sorted(role_root.glob("attempt-*")):
            if attempt_path.is_file():
                values[f"{role}.attempt.{attempt_path.name}"] = (
                    attempt_path.resolve()
                )
    return dict(sorted(values.items()))


def _formal_artifact_root(
    *,
    formal_run_id: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Path], Path]:
    pools = {
        role: Path(arguments["evolution_pools"][role]).expanduser().resolve()
        for role in ROLES
    }
    for role, pool in pools.items():
        expected_suffix = Path("evolution") / role / formal_run_id / "pool.json"
        if len(pool.parts) < len(expected_suffix.parts) or Path(
            *pool.parts[-len(expected_suffix.parts) :]
        ) != expected_suffix:
            raise ValueError(f"Formal source pool path differs for {role}")
    artifact_roots = {path.parents[3] for path in pools.values()}
    if len(artifact_roots) != 1:
        raise ValueError("Formal role pools must share one artifact root")
    return pools, next(iter(artifact_roots))


def _formal_resume_audit_paths(
    *,
    artifact_root: Path,
    formal_run_id: str,
) -> list[Path]:
    runtime_root = artifact_root / "runtime"
    expected_parent = runtime_root / formal_run_id.replace("-", "_")
    if runtime_root.exists() and (
        not runtime_root.is_dir()
        or runtime_root.is_symlink()
        or runtime_root.resolve() != runtime_root
    ):
        raise ValueError(
            "Formal resume audits require one canonical non-symlink runtime root"
        )
    audits: list[tuple[datetime, Path]] = []
    parents: set[Path] = set()
    for path in sorted(runtime_root.glob("*/resume_*.json")):
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or path.parent.resolve() != path.parent
            or not path.is_file()
        ):
            raise ValueError(
                f"Formal resume audit must be a regular non-symlink file: {path}"
            )
        filename_match = FORMAL_RESUME_AUDIT_FILENAME.fullmatch(path.name)
        if filename_match is None:
            raise ValueError(
                f"Formal resume audit filename timestamp is invalid: {path}"
            )
        try:
            value = read_json(path)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(
                f"Formal resume audit must contain readable JSON: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"Formal resume audit JSON must be an object: {path}")
        run_id = value.get("run_id")
        protocol = value.get("protocol")
        if path.parent == expected_parent and (
            protocol != FORMAL_RESUME_AUDIT_PROTOCOL or run_id != formal_run_id
        ):
            raise ValueError(
                "Formal resume audit canonical parent requires exact protocol "
                f"and run_id: {path}"
            )
        if run_id == formal_run_id and protocol != FORMAL_RESUME_AUDIT_PROTOCOL:
            raise ValueError(
                f"Formal resume audit protocol differs for run {formal_run_id}: {path}"
            )
        if protocol != FORMAL_RESUME_AUDIT_PROTOCOL or run_id != formal_run_id:
            continue
        recorded_at = value.get("recorded_at")
        if not isinstance(recorded_at, str) or not recorded_at.strip():
            raise ValueError(f"Formal resume audit recorded_at is missing: {path}")
        try:
            recorded_time = datetime.fromisoformat(recorded_at)
        except ValueError as exc:
            raise ValueError(
                f"Formal resume audit recorded_at is invalid: {path}"
            ) from exc
        if recorded_time.utcoffset() is None:
            raise ValueError(
                f"Formal resume audit recorded_at must be timezone-aware: {path}"
            )
        expected_filename_time = recorded_time.strftime("%Y%m%dT%H%M%S%z")
        if (
            recorded_time.microsecond != 0
            or filename_match.group("timestamp") != expected_filename_time
        ):
            raise ValueError(
                f"Formal resume audit filename timestamp differs from recorded_at: {path}"
            )
        resolved_parent = path.parent.resolve()
        parents.add(resolved_parent)
        audits.append((recorded_time.astimezone(timezone.utc), path.resolve()))
    if not audits:
        raise ValueError(
            "Formal freeze requires at least one resume audit for this resumed run"
        )
    if parents != {expected_parent}:
        raise ValueError(
            "Formal resume audits for one run must use its one canonical parent"
        )
    audits.sort(key=lambda item: item[0])
    for previous, current in zip(audits, audits[1:]):
        if current[0] <= previous[0]:
            raise ValueError(
                "Formal resume audit recorded_at timestamps must be strictly increasing"
            )
    return [path for _, path in audits]


def _git_repository_root(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip()).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Git returned an invalid repository root for {path}")
    return root


def _resolve_resume_commit(
    *,
    commit: Any,
    repository_role: str,
    repository_root: Path | None,
) -> str:
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError(
            f"Formal resume audit {repository_role} checkpoint must be 7-40 hex"
        )
    if repository_root is None:
        if len(commit) != 40:
            raise ValueError(
                "Formal resume audit requires a full Git SHA test double "
                f"for non-Git repository role {repository_role}"
            )
        return commit
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise ValueError(
            "Formal resume audit could not resolve Git checkpoint "
            f"{commit} in repository role {repository_role}"
        )
    return resolved


def _require_git_ancestor(
    *,
    repository_root: Path,
    ancestor: str,
    descendant: str,
    repository_role: str,
    label: str,
) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 1:
        raise ValueError(
            f"Formal resume audit {repository_role} {label} is not a linear "
            "ancestor chain"
        )
    if result.returncode != 0:
        raise ValueError(
            f"Formal resume audit could not verify {repository_role} {label}"
        )


def _resume_repository_bindings(
    *,
    artifact_root: Path,
    arguments: dict[str, Any],
) -> tuple[dict[str, Path | None], dict[str, str]]:
    external_identity = arguments.get("external_coco_identity")
    if not isinstance(external_identity, dict):
        raise ValueError("Formal resume audit requires external CoCo identity")
    external_root_value = external_identity.get("repository_root")
    if not isinstance(external_root_value, str) or not external_root_value.strip():
        raise ValueError("Formal resume audit requires external CoCo repository root")
    external_root = Path(external_root_value).expanduser().resolve()
    if not external_root.is_dir():
        raise ValueError("Formal resume audit external CoCo repository is unavailable")
    repositories = {
        "agentreview": _git_repository_root(artifact_root),
        "external_coco": _git_repository_root(external_root),
    }
    if (
        repositories["external_coco"] is not None
        and repositories["external_coco"] != external_root
    ):
        raise ValueError(
            "Formal resume audit external CoCo role must bind the repository root"
        )
    raw_heads = {
        "agentreview": arguments.get("code_commit"),
        "external_coco": external_identity.get("git_commit"),
    }
    heads: dict[str, str] = {}
    for role, raw_head in raw_heads.items():
        if not isinstance(raw_head, str) or not re.fullmatch(
            r"[0-9a-f]{40}", raw_head
        ):
            raise ValueError(
                f"Formal resume audit {role} bound HEAD must be one full Git SHA"
            )
        heads[role] = _resolve_resume_commit(
            commit=raw_head,
            repository_role=role,
            repository_root=repositories[role],
        )
    return repositories, heads


def _resume_audit_schema(checkpoints: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(checkpoints, dict):
        raise ValueError("Formal resume audit source_checkpoints must be an object")
    checkpoint_names = set(checkpoints)
    for name, schema in FORMAL_RESUME_AUDIT_SCHEMAS.items():
        if checkpoint_names == {key for key, _ in schema["checkpoints"]}:
            return name, schema
    raise ValueError("Formal resume audit checkpoint schema is not whitelisted")


def _validate_typed_resume_chain(
    *,
    audit_values: list[dict[str, Any]],
    audit_entries: list[dict[str, Any]],
    repositories: dict[str, Path | None],
    repository_heads: dict[str, str],
) -> None:
    schemas = [entry["schema"] for entry in audit_entries]
    if schemas != list(FORMAL_RESUME_AUDIT_SCHEMA_ORDER[: len(schemas)]):
        raise ValueError(
            "Formal resume audit checkpoint schemas must form the ordered typed chain"
        )
    entries_by_schema = {
        entry["schema"]: (value, entry)
        for value, entry in zip(audit_values, audit_entries, strict=True)
    }
    structured = entries_by_schema.get("structured_output_external_coco")
    legacy = entries_by_schema.get("legacy_identity_compatibility")
    original = entries_by_schema.get("original_process_context_fix_probe")
    if structured is not None:
        structured_value, _ = structured
        progress = structured_value.get("preserved_progress")
        if (
            not isinstance(progress, dict)
            or progress.get("next_clar_experience_item_ordinal") != 26
        ):
            raise ValueError(
                "Formal structured-output resume audit must preserve CLAR ordinal 26"
            )
    if legacy is None:
        return
    legacy_value, legacy_entry = legacy
    failure = legacy_value.get("failure")
    resumed = legacy_value.get("resumed_process")
    if not isinstance(failure, dict) or not isinstance(resumed, dict):
        raise ValueError("Formal legacy resume audit typed evidence is incomplete")
    if structured is None or original is None:
        raise ValueError("Formal legacy resume audit requires the complete prior chain")
    structured_value, structured_entry = structured
    original_value, original_entry = original
    if (
        structured_value["source_checkpoints"]["structured_output_fix_commit"]
        != legacy_value["source_checkpoints"]["structured_output_fix_commit"]
        or structured_entry["resolved_source_checkpoints"][
            "structured_output_fix_commit"
        ]
        != legacy_entry["resolved_source_checkpoints"][
            "structured_output_fix_commit"
        ]
    ):
        raise ValueError(
            "Formal resume audit structured-output checkpoint continuity differs"
        )
    next_ordinal = structured_value["preserved_progress"][
        "next_clar_experience_item_ordinal"
    ]
    before = resumed.get("clar_experience_checkpoint_before")
    after = resumed.get("clar_experience_checkpoint_after")
    if next_ordinal != before or before != 26:
        raise ValueError("Formal resume audit CLAR progress continuity differs")
    if after != 27 or after != before + 1:
        raise ValueError("Formal resume audit CLAR resumed progress must advance once")
    if failure.get("historical_process_started_at") != original_value.get(
        "recorded_at"
    ):
        raise ValueError("Formal resume audit historical process start differs")
    identity_commit = failure.get("identity_hardening_commit")
    resolved_identity = _resolve_resume_commit(
        commit=identity_commit,
        repository_role="agentreview",
        repository_root=repositories["agentreview"],
    )
    if resolved_identity in {
        original_entry["resume_commit"],
        structured_entry["resolved_source_checkpoints"]["pre_fix_checkpoint"],
        legacy_entry["resume_commit"],
    }:
        raise ValueError(
            "Formal resume audit identity-hardening checkpoint must be distinct"
        )
    legacy_entry["related_commits"] = {
        "failure.identity_hardening_commit": {
            "original": identity_commit,
            "resolved": resolved_identity,
            "repository_role": "agentreview",
        }
    }
    repository_root = repositories["agentreview"]
    if repository_root is not None:
        _require_git_ancestor(
            repository_root=repository_root,
            ancestor=original_entry["resume_commit"],
            descendant=resolved_identity,
            repository_role="agentreview",
            label="identity-hardening predecessor",
        )
        _require_git_ancestor(
            repository_root=repository_root,
            ancestor=resolved_identity,
            descendant=structured_entry["resolved_source_checkpoints"][
                "pre_fix_checkpoint"
            ],
            repository_role="agentreview",
            label="identity-hardening -> structured pre-fix",
        )
        _require_git_ancestor(
            repository_root=repository_root,
            ancestor=resolved_identity,
            descendant=legacy_entry["resume_commit"],
            repository_role="agentreview",
            label="identity-hardening successor",
        )
        _require_git_ancestor(
            repository_root=repository_root,
            ancestor=resolved_identity,
            descendant=repository_heads["agentreview"],
            repository_role="agentreview",
            label="identity-hardening HEAD binding",
        )


def _evolution_code_provenance(
    *,
    formal_run_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _, artifact_root = _formal_artifact_root(
        formal_run_id=formal_run_id,
        arguments=arguments,
    )
    repositories, repository_heads = _resume_repository_bindings(
        artifact_root=artifact_root,
        arguments=arguments,
    )
    audits: list[dict[str, Any]] = []
    audit_values: list[dict[str, Any]] = []
    repository_chains: dict[str, list[tuple[str, str]]] = {
        "agentreview": [],
        "external_coco": [],
    }
    previous_audit_sha256: str | None = None
    for path in _formal_resume_audit_paths(
        artifact_root=artifact_root,
        formal_run_id=formal_run_id,
    ):
        value = read_json(path)
        checkpoints = value.get("source_checkpoints") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("protocol") != FORMAL_RESUME_AUDIT_PROTOCOL
            or value.get("run_id") != formal_run_id
            or not isinstance(value.get("recorded_at"), str)
            or not value["recorded_at"].strip()
        ):
            raise ValueError("Formal resume audit identity is incomplete")
        schema_name, schema = _resume_audit_schema(checkpoints)
        ordered_checkpoints = {
            checkpoint: checkpoints[checkpoint]
            for checkpoint, _ in schema["checkpoints"]
        }
        checkpoint_roles = {
            checkpoint: repository_role
            for checkpoint, repository_role in schema["checkpoints"]
        }
        resolved_checkpoints = {
            checkpoint: _resolve_resume_commit(
                commit=ordered_checkpoints[checkpoint],
                repository_role=repository_role,
                repository_root=repositories[repository_role],
            )
            for checkpoint, repository_role in schema["checkpoints"]
        }
        for checkpoint, repository_role in schema["checkpoints"]:
            repository_chains[repository_role].append(
                (checkpoint, resolved_checkpoints[checkpoint])
            )
        audit_sha256 = file_sha256(path)
        audits.append(
            {
                "path": str(path),
                "sha256": audit_sha256,
                "previous_audit_sha256": previous_audit_sha256,
                "recorded_at": value["recorded_at"],
                "schema": schema_name,
                "source_checkpoints": ordered_checkpoints,
                "resolved_source_checkpoints": resolved_checkpoints,
                "checkpoint_repository_roles": checkpoint_roles,
                "resume_commit": resolved_checkpoints[schema["resume_checkpoint"]],
                "related_commits": {},
            }
        )
        audit_values.append(value)
        previous_audit_sha256 = audit_sha256
    _validate_typed_resume_chain(
        audit_values=audit_values,
        audit_entries=audits,
        repositories=repositories,
        repository_heads=repository_heads,
    )
    for repository_role, commits in repository_chains.items():
        repository_root = repositories[repository_role]
        if not commits:
            continue
        for previous, current in zip(commits, commits[1:]):
            allowed_shared_boundary = (
                previous[0] == "structured_output_fix_commit"
                and current[0] == "structured_output_fix_commit"
            )
            if previous[1] == current[1] and not allowed_shared_boundary:
                raise ValueError(
                    "Formal resume audit contains a duplicate checkpoint outside "
                    "the structured-output continuity boundary"
                )
            if repository_root is None:
                continue
            _require_git_ancestor(
                repository_root=repository_root,
                ancestor=previous[1],
                descendant=current[1],
                repository_role=repository_role,
                label=f"checkpoint {previous[0]} -> {current[0]}",
            )
        if repository_root is None:
            continue
        _require_git_ancestor(
            repository_root=repository_root,
            ancestor=commits[-1][1],
            descendant=repository_heads[repository_role],
            repository_role=repository_role,
            label="final checkpoint -> bound HEAD",
        )
    body = {
        "protocol": FORMAL_EVOLUTION_CODE_PROVENANCE_PROTOCOL,
        "mode": FORMAL_RESUME_AUDIT_MODE,
        "resume_audits": audits,
    }
    return {
        **body,
        "repository_heads": repository_heads,
        "chain_id": value_sha256(body),
    }


def _materialize_source_bindings(
    *,
    formal_run_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    files = {
        name: {
            "path": str(_required_file(path, name)),
            "sha256": file_sha256(path),
        }
        for name, path in _expected_source_paths(
            formal_run_id=formal_run_id,
            arguments=arguments,
        ).items()
    }
    return {
        "protocol": FORMAL_SOURCE_BINDINGS_PROTOCOL,
        "formal_run_id": formal_run_id,
        "files": files,
    }


def _validate_source_bindings(
    *,
    formal_run_id: str,
    arguments: dict[str, Any],
    bindings: Any,
) -> None:
    if not isinstance(bindings, dict) or {
        "protocol": bindings.get("protocol"),
        "formal_run_id": bindings.get("formal_run_id"),
    } != {
        "protocol": FORMAL_SOURCE_BINDINGS_PROTOCOL,
        "formal_run_id": formal_run_id,
    }:
        raise ValueError("Formal freeze source-binding identity mismatch")
    files = bindings.get("files")
    expected_paths = _expected_source_paths(
        formal_run_id=formal_run_id,
        arguments=arguments,
    )
    if not isinstance(files, dict) or set(files) != set(expected_paths):
        raise ValueError("Formal freeze source-binding fields differ")
    for name, expected_path in expected_paths.items():
        binding = files.get(name)
        expected = {
            "path": str(expected_path),
            "sha256": file_sha256(_required_file(expected_path, name)),
        }
        if binding != expected:
            raise ValueError(f"Formal freeze source binding changed: {name}")


def _validate_run_spec(
    *,
    path: Path,
    role: str,
    formal_run_id: str,
    pool_path: Path,
    model_identity: dict[str, Any],
    embedding_identity: dict[str, Any],
    development_s2_path: Path,
    require_bubblewrap: bool,
) -> dict[str, Any]:
    spec = read_json(path)
    if not isinstance(spec, dict):
        raise TypeError(f"Formal run spec must be an object: {path}")
    unhashed = {name: value for name, value in spec.items() if name != "run_spec_id"}
    expected = {
        "protocol": FORMAL_RUN_SPEC_PROTOCOL,
        "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
        "role": role,
        "run_id": formal_run_id,
        "pool_path": str(pool_path),
        "model_identity": model_identity,
        "embedding_identity": embedding_identity,
        "require_bubblewrap": require_bubblewrap,
        "s2_intervention": FORMAL_AUTONOMOUS_S2_INTERVENTION,
    }
    observed = {name: spec.get(name) for name in expected}
    if observed != expected:
        raise ValueError(
            f"Formal run spec identity mismatch for {role}: "
            f"observed={observed!r}, expected={expected!r}"
        )
    frozen_s2 = spec.get("frozen_s2")
    expected_s2 = {
        "path": str(development_s2_path),
        "sha256": file_sha256(development_s2_path),
        "pool_version": DEVELOPMENT_S2_VERSION,
        "paper_count": sum(SPLIT_SIZES.values()),
    }
    if frozen_s2 != expected_s2:
        raise ValueError(f"Formal development S2 identity mismatch for {role}")
    canonical = spec.get("canonical_evaluation")
    if not isinstance(canonical, dict) or set(canonical) != {
        "path",
        "sha256",
        "run_id",
        "review_run_id",
    }:
        raise ValueError(
            f"Formal canonical evaluation binding is incomplete for {role}"
        )
    canonical_path = _required_file(
        canonical["path"],
        f"the {role} canonical evaluation summary",
    )
    if canonical != validate_canonical_evaluation_summary(canonical_path):
        raise ValueError(
            f"Formal canonical evaluation identity mismatch for {role}"
        )
    splits = spec.get("splits")
    if not isinstance(splits, dict) or set(splits) != {
        "canonical",
        "static",
        "rotating",
    }:
        raise ValueError(f"Formal split bindings are incomplete for {role}")
    split_counts = {"canonical": 125, "static": 25, "rotating": 125}
    split_records: dict[str, list[dict[str, Any]]] = {}
    for name, split in splits.items():
        if not isinstance(split, dict) or set(split) != {
            "path",
            "sha256",
            "split_sha256",
            "paper_asset_set_sha256",
        }:
            raise ValueError(
                f"Formal {name} split binding is incomplete for {role}"
            )
        split_path = _required_file(split["path"], f"the {role} {name} split")
        split_value = read_json(split_path)
        records = split_value.get("records") if isinstance(split_value, dict) else None
        if (
            not isinstance(records, list)
            or split_value.get("count") != split_counts[name]
            or len(records) != split_counts[name]
            or split.get("sha256") != file_sha256(split_path)
            or split.get("split_sha256") != split_value.get("split_sha256")
        ):
            raise ValueError(f"Formal {name} split identity mismatch for {role}")
        paper_assets = []
        paper_ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise TypeError(f"Formal {name} split record must be an object")
            paper_id = str(record.get("paper_id") or "")
            paper_path = _required_file(
                record.get("path") or "",
                f"the {name} Paper {paper_id}",
            )
            paper = read_json(paper_path)
            if (
                not paper_id
                or paper_id in paper_ids
                or not isinstance(paper, dict)
                or paper.get("paper_id") != paper_id
                or not isinstance(paper.get("title"), str)
            ):
                raise ValueError(f"Formal {name} Paper identity mismatch: {paper_id}")
            paper_ids.add(paper_id)
            paper_assets.append(
                {
                    "paper_id": paper_id,
                    "path": str(paper_path),
                    "sha256": file_sha256(paper_path),
                    "title": paper["title"],
                }
            )
        if split.get("paper_asset_set_sha256") != value_sha256(paper_assets):
            raise ValueError(
                f"Formal {name} Paper asset-set identity mismatch for {role}"
            )
        split_records[name] = records
    seeds = spec.get("seed_programs")
    if not isinstance(seeds, dict) or set(seeds) != set(SEED_NAMES):
        raise ValueError(
            f"Formal seed Program bindings are incomplete for {role}"
        )
    for name, seed in seeds.items():
        if not isinstance(seed, dict) or set(seed) != {
            "path",
            "sha256",
            "capability_manifest",
        }:
            raise ValueError(
                f"Formal seed Program binding is incomplete for {role}"
            )
        seed_path = _required_file(
            seed["path"],
            f"the {role} {name} seed Program",
        )
        if seed != {
            "path": str(seed_path),
            "sha256": file_sha256(seed_path),
            "capability_manifest": program_capability_manifest(path=seed_path),
        }:
            raise ValueError(
                f"Formal seed Program identity mismatch for {role}: {name}"
            )
    expected_budget = {
        "seed_count": len(SEED_NAMES),
        "mutation_attempts": MAX_MUTATION_ATTEMPTS,
        "static_batch_size": FORMAL_STATIC_SIZE,
        "rotating_batch_size": FORMAL_ROTATING_BATCH_SIZE,
        "rotating_schedule": "frozen-record-order-disjoint-5-v1",
    }
    artifact_root = pool_path.parents[3]
    canonical_artifacts = []
    for record in split_records["canonical"]:
        paper_id = str(record["paper_id"])
        review_path = _required_file(
            artifact_root
            / "reviews"
            / canonical["review_run_id"]
            / paper_id
            / f"{role}.json",
            f"the canonical {role} Review for {paper_id}",
        )
        evaluation_path = _required_file(
            artifact_root
            / "evaluations"
            / canonical["run_id"]
            / paper_id
            / f"{role}.json",
            f"the canonical {role} evaluation for {paper_id}",
        )
        canonical_artifacts.append(
            {
                "review_path": str(review_path),
                "review_sha256": file_sha256(review_path),
                "evaluation_path": str(evaluation_path),
                "evaluation_sha256": file_sha256(evaluation_path),
            }
        )
    if (
        spec.get("search_budget") != expected_budget
        or spec.get("canonical_artifacts_sha256")
        != value_sha256(canonical_artifacts)
    ):
        raise ValueError(
            f"Formal search/canonical artifact identity mismatch for {role}"
        )
    if spec.get("run_spec_id") != value_sha256(unhashed):
        raise ValueError(f"Formal run spec hash mismatch for {role}")
    return spec


def _provenance_configs(model_identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "evaluator_config": {
            "prompt_version": INTERNAL_EVALUATOR_PROMPT_VERSION,
            "system_prompt_sha256": value_sha256(INTERNAL_EVALUATOR_SYSTEM_PROMPT),
            "max_tokens": EVALUATOR_MAX_TOKENS,
            "model_identity": dict(model_identity),
        },
        "reflector_config": {
            "prompt_protocol": REFLECTOR_PROMPT_PROTOCOL,
            "feedback_view_protocol": REFLECTOR_FEEDBACK_VIEW_PROTOCOL,
            "context_budget_protocol": REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
            "context_headroom_tokens": REFLECTOR_CONTEXT_HEADROOM_TOKENS,
            "max_tokens": REFLECTOR_MAX_TOKENS,
            "max_mutation_attempts": MAX_MUTATION_ATTEMPTS,
            "model_identity": dict(model_identity),
        },
        "summarizer_config": {
            "seed_kinds": list(SEED_NAMES),
            "summarizer_seed_kind": "llm_summarizer",
            "max_tokens": SUMMARIZER_MAX_TOKENS,
            "max_llm_calls_per_retrieval": 1,
            "model_identity": dict(model_identity),
        },
    }


def build_formal_freeze_arguments(
    *,
    config: ResearchConfig,
    formal_run_id: str,
    code_commit: str,
) -> dict[str, Any]:
    """Derive all freeze arguments from one completed all-role Formal run."""
    if config.require_bubblewrap is not True:
        raise ValueError("Formal freeze requires bubblewrap isolation")
    if (
        not formal_run_id.strip()
        or formal_run_id in {".", ".."}
        or Path(formal_run_id).name != formal_run_id
    ):
        raise ValueError("Formal freeze run_id must be one non-empty path-safe name")
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("Formal freeze code_commit must be one full Git SHA")
    output = config.artifact_path()
    summary_path = _required_file(
        output / "runs" / formal_run_id / "formal-evolution.json",
        "the all-role Formal summary",
    )
    summary = read_json(summary_path)
    expected_summary = {
        "protocol": FORMAL_ALL_ROLES_PROTOCOL,
        "run_id": formal_run_id,
        "role_order": list(ROLES),
        "status": "complete",
    }
    if not isinstance(summary, dict) or {
        name: summary.get(name) for name in expected_summary
    } != expected_summary:
        raise ValueError("Formal freeze requires a complete all-role summary")
    role_summaries = summary.get("roles")
    if not isinstance(role_summaries, dict) or set(role_summaries) != set(ROLES):
        raise ValueError("Formal freeze requires exactly five completed role summaries")

    development_s2 = _required_file(
        config.frozen_s2_path,
        "the frozen development S2 pool",
    )
    coco_s2 = _required_file(
        config.frozen_coco_s2_path,
        "the frozen CoCo-1300 S2 pool",
    )
    development_manifest = _required_file(
        output / "data" / "manifest.json",
        "the development manifest",
    )
    development_query_path = _required_file(
        output / "data" / "s2_title_queries.jsonl",
        "the development S2 query set",
    )
    coco_manifest = _required_file(
        output / "data" / "coco_1300" / "manifest.json",
        "the CoCo-1300 manifest",
    )
    coco_query_path = _required_file(
        output / "data" / "coco_1300" / "s2_title_queries.jsonl",
        "the CoCo-1300 S2 query set",
    )
    coco_scoring_spec_path = _required_file(
        config.coco_scoring_spec_path,
        "the frozen CoCo scoring spec",
    )
    coco_scoring_spec_value = validate_coco_scoring_spec(
        coco_scoring_spec_path,
        coco_root=config.coco_root,
    )
    if coco_scoring_spec_value.get("winner_run_id") != FORMAL_COCO_WINNER_RUN_ID:
        raise ValueError(
            "Formal freeze requires winner_run_id "
            f"{FORMAL_COCO_WINNER_RUN_ID}"
        )
    coco_scoring_spec = {
        "path": str(coco_scoring_spec_path),
        "sha256": file_sha256(coco_scoring_spec_path),
        "spec_id": coco_scoring_spec_value["spec_id"],
        "winner_run_id": coco_scoring_spec_value["winner_run_id"],
    }
    development_targets = _validate_development_manifest(
        development_manifest,
        query_path=development_query_path,
    )
    coco_targets = _validate_coco_manifest(
        coco_manifest,
        query_path=coco_query_path,
    )
    validate_frozen_s2_pool(
        pool_path=development_s2,
        expected_count=sum(SPLIT_SIZES.values()),
        pool_version=DEVELOPMENT_S2_VERSION,
        raw_limit=50,
        top_k=5,
        builder_sha256=S2_RECORD_BUILDER_SHA256,
        targets=development_targets,
        label="Development S2",
    )
    validate_frozen_s2_pool(
        pool_path=coco_s2,
        expected_count=COCO_1300_COUNT,
        pool_version=COCO_S2_VERSION,
        raw_limit=50,
        top_k=5,
        builder_sha256=S2_RECORD_BUILDER_SHA256,
        targets=coco_targets,
        label="CoCo-1300 S2",
    )
    model_identity = config.model.identity()
    embedding_identity = config.embedding_identity()
    programs: dict[str, str] = {}
    snapshots: dict[str, str] = {}
    pools: dict[str, str] = {}
    run_ids: dict[str, str] = {}
    shared_canonical_evaluation: dict[str, Any] | None = None
    shared_splits: dict[str, Any] | None = None
    for role in ROLES:
        role_root = output / "evolution" / role / formal_run_id
        pool_path = _required_file(role_root / "pool.json", f"the {role} pool")
        coordinator_path = _required_file(
            role_root / "coordinator.json",
            f"the {role} coordinator",
        )
        _required_file(role_root / "winner.json", f"the {role} winner")
        run_spec_path = _required_file(
            role_root / "run-spec.json",
            f"the {role} run spec",
        )
        pool = EvolutionPool.from_dict(read_json(pool_path))
        winner_artifact = _validate_completed_evolution(
            role=role,
            run_id=formal_run_id,
            pool_path=pool_path,
            pool=pool,
        )
        run_spec = _validate_run_spec(
            path=run_spec_path,
            role=role,
            formal_run_id=formal_run_id,
            pool_path=pool_path,
            model_identity=model_identity,
            embedding_identity=embedding_identity,
            development_s2_path=development_s2,
            require_bubblewrap=config.require_bubblewrap,
        )
        canonical_evaluation = run_spec["canonical_evaluation"]
        splits = run_spec["splits"]
        if shared_canonical_evaluation is None:
            shared_canonical_evaluation = canonical_evaluation
            shared_splits = splits
        elif (
            canonical_evaluation != shared_canonical_evaluation
            or splits != shared_splits
        ):
            raise ValueError(
                "Formal roles must share the exact canonical evaluation and splits"
            )
        winner = pool.best(require_snapshot=True)
        role_summary = role_summaries[role]
        expected_role_summary = {
            "status": "complete",
            "winner_id": winner_artifact["winner_id"],
            "coordinator_path": str(coordinator_path),
            "pool_path": str(pool_path),
        }
        if role_summary != expected_role_summary:
            raise ValueError(f"Formal all-role summary differs for {role}")
        programs[role] = str(Path(winner.program_path).expanduser().resolve())
        snapshots[role] = str(
            Path(winner.memory_snapshot_path).expanduser().resolve()
        )
        pools[role] = str(pool_path)
        run_ids[role] = formal_run_id

    arguments = {
        "programs": programs,
        "memory_snapshots": snapshots,
        "model_identity": model_identity,
        "embedding_identity": embedding_identity,
        "frozen_s2_pools": {
            "development": str(development_s2),
            "coco_1300": str(coco_s2),
        },
        "primary_meta_enabled": True,
        "require_bubblewrap": config.require_bubblewrap,
        "code_commit": code_commit,
        "development_manifest_path": str(development_manifest),
        "coco_manifest_path": str(coco_manifest),
        "evolution_run_ids": run_ids,
        "evolution_pools": pools,
        "external_coco_identity": _external_coco_identity(config),
        "coco_scoring_spec": coco_scoring_spec,
        **_provenance_configs(model_identity),
    }
    if set(arguments) != FREEZE_ARGUMENT_FIELDS:
        raise AssertionError("Formal freeze argument materialization is incomplete")
    return arguments


def materialize_formal_freeze_spec(
    *,
    config: ResearchConfig,
    formal_run_id: str,
    repository_root: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write one immutable spec after two identical semantic/source passes."""
    code_commit = clean_git_head(repository_root)
    arguments = build_formal_freeze_arguments(
        config=config,
        formal_run_id=formal_run_id,
        code_commit=code_commit,
    )
    evolution_code_provenance = _evolution_code_provenance(
        formal_run_id=formal_run_id,
        arguments=arguments,
    )
    source_bindings = _materialize_source_bindings(
        formal_run_id=formal_run_id,
        arguments=arguments,
    )
    confirmed_arguments = build_formal_freeze_arguments(
        config=config,
        formal_run_id=formal_run_id,
        code_commit=code_commit,
    )
    confirmed_bindings = _materialize_source_bindings(
        formal_run_id=formal_run_id,
        arguments=confirmed_arguments,
    )
    confirmed_code_provenance = _evolution_code_provenance(
        formal_run_id=formal_run_id,
        arguments=confirmed_arguments,
    )
    if (
        confirmed_arguments != arguments
        or confirmed_bindings != source_bindings
        or confirmed_code_provenance != evolution_code_provenance
    ):
        raise RuntimeError(
            "Formal freeze inputs changed between semantic validation passes"
        )
    _validate_source_bindings(
        formal_run_id=formal_run_id,
        arguments=arguments,
        bindings=source_bindings,
    )
    if _evolution_code_provenance(
        formal_run_id=formal_run_id,
        arguments=arguments,
    ) != evolution_code_provenance:
        raise RuntimeError("Formal Evolution code provenance changed")
    payload = {
        "protocol": FORMAL_FREEZE_SPEC_PROTOCOL,
        "formal_run_id": formal_run_id,
        "evolution_code_provenance": evolution_code_provenance,
        "freeze_arguments": arguments,
        "source_bindings": source_bindings,
    }
    payload["spec_id"] = value_sha256(payload)
    if clean_git_head(repository_root) != code_commit:
        raise RuntimeError("Git HEAD changed while materializing the formal freeze spec")
    atomic_write_json(output_path, payload)
    return payload


def validate_formal_freeze_spec(path: str | Path) -> dict[str, Any]:
    """Validate a formal spec envelope and every bound source file."""
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError("Freeze spec must be a JSON object")
    if value.get("protocol") != FORMAL_FREEZE_SPEC_PROTOCOL:
        raise ValueError("freeze-system requires a formal freeze spec envelope")
    if set(value) != {
        "protocol",
        "formal_run_id",
        "evolution_code_provenance",
        "freeze_arguments",
        "source_bindings",
        "spec_id",
    }:
        raise ValueError("Formal freeze spec envelope fields differ")
    unhashed = {name: item for name, item in value.items() if name != "spec_id"}
    if value.get("spec_id") != value_sha256(unhashed):
        raise ValueError("Formal freeze spec identity mismatch")
    formal_run_id = value.get("formal_run_id")
    if (
        not isinstance(formal_run_id, str)
        or not formal_run_id.strip()
        or formal_run_id in {".", ".."}
        or Path(formal_run_id).name != formal_run_id
    ):
        raise ValueError("Formal freeze spec run_id must be one path-safe name")
    arguments = value.get("freeze_arguments")
    if not isinstance(arguments, dict) or set(arguments) != FREEZE_ARGUMENT_FIELDS:
        raise ValueError("Formal freeze spec argument fields differ")
    if arguments.get("primary_meta_enabled") is not True:
        raise ValueError("Formal freeze spec requires primary Meta")
    if arguments.get("require_bubblewrap") is not True:
        raise ValueError("Formal freeze spec requires bubblewrap isolation")
    coco_scoring_spec = arguments.get("coco_scoring_spec")
    if (
        not isinstance(coco_scoring_spec, dict)
        or set(coco_scoring_spec)
        != {"path", "sha256", "spec_id", "winner_run_id"}
        or coco_scoring_spec.get("winner_run_id")
        != FORMAL_COCO_WINNER_RUN_ID
    ):
        raise ValueError("Formal freeze spec CoCo scoring binding differs")
    run_ids = arguments.get("evolution_run_ids")
    if not isinstance(run_ids, dict) or run_ids != {
        role: formal_run_id for role in ROLES
    }:
        raise ValueError("Formal freeze spec requires five role-local run IDs")
    external_coco_identity = arguments.get("external_coco_identity")
    if not isinstance(external_coco_identity, dict):
        raise ValueError("Formal freeze spec external CoCo identity differs")
    scoring_spec_value = validate_coco_scoring_spec(
        coco_scoring_spec["path"],
        coco_root=external_coco_identity.get("repository_root"),
    )
    _cross_check_external_coco_scoring_identity(
        external_coco_identity=external_coco_identity,
        scoring_spec=scoring_spec_value,
    )
    _validate_source_bindings(
        formal_run_id=formal_run_id,
        arguments=arguments,
        bindings=value.get("source_bindings"),
    )
    if value.get("evolution_code_provenance") != _evolution_code_provenance(
        formal_run_id=formal_run_id,
        arguments=arguments,
    ):
        raise ValueError("Formal Evolution code provenance changed")
    return value


def load_freeze_arguments(
    path: str | Path,
    *,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Load only a source-bound formal spec under its exact clean Git HEAD."""
    code_commit = clean_git_head(repository_root)
    value = validate_formal_freeze_spec(path)
    arguments = dict(value["freeze_arguments"])
    if arguments.get("code_commit") != code_commit:
        raise ValueError("Formal freeze spec code commit differs from current Git HEAD")
    if clean_git_head(repository_root) != code_commit:
        raise RuntimeError("Git HEAD changed while validating the formal freeze spec")
    return arguments


def freeze_formal_system(
    *,
    spec_path: str | Path,
    output_path: str | Path,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Freeze through a staged output and revalidate every source before commit."""
    spec_source = Path(spec_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    first_spec = validate_formal_freeze_spec(spec_source)
    arguments = load_freeze_arguments(
        spec_source,
        repository_root=repository_root,
    )
    spec_sha256 = file_sha256(spec_source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.staging.",
        dir=target.parent,
    ) as directory:
        staged_path = Path(directory) / "system.json"
        system = freeze_system(output_path=staged_path, **arguments)
        second_spec = validate_formal_freeze_spec(spec_source)
        second_arguments = load_freeze_arguments(
            spec_source,
            repository_root=repository_root,
        )
        if (
            second_spec != first_spec
            or second_arguments != arguments
            or file_sha256(spec_source) != spec_sha256
        ):
            raise RuntimeError("Formal freeze inputs changed while staging the system")
        reproducibility = system.get("reproducibility")
        if not isinstance(reproducibility, dict):
            raise TypeError("Frozen system reproducibility must be an object")
        reproducibility["formal_freeze_spec"] = {
            "protocol": FORMAL_FREEZE_SPEC_PROTOCOL,
            "path": str(spec_source),
            "sha256": spec_sha256,
            "spec_id": first_spec["spec_id"],
            "formal_run_id": first_spec["formal_run_id"],
            "source_bindings": first_spec["source_bindings"],
        }
        reproducibility["transition_code_commit"] = arguments["code_commit"]
        reproducibility["evolution_code_provenance"] = first_spec[
            "evolution_code_provenance"
        ]
        system.pop("system_id", None)
        system["system_id"] = value_sha256(system)
        atomic_write_json(target, system)
        return system


def validate_frozen_system_transition(
    *,
    frozen_system_path: str | Path,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Re-derive the complete frozen system from its bound formal spec."""
    code_commit = clean_git_head(repository_root)
    system_path = Path(frozen_system_path).expanduser().resolve()
    system_sha256 = file_sha256(system_path)
    system = read_json(system_path)
    if not isinstance(system, dict):
        raise TypeError("Frozen system must be a JSON object")
    body = {name: value for name, value in system.items() if name != "system_id"}
    if (
        system.get("protocol") != "research-v2-frozen-system-v2"
        or system.get("system_id") != value_sha256(body)
    ):
        raise ValueError("Frozen system identity mismatch")
    reproducibility = system.get("reproducibility")
    runtime = system.get("review_runtime")
    if not isinstance(reproducibility, dict) or not isinstance(runtime, dict):
        raise TypeError("Frozen system runtime/reproducibility must be objects")
    if reproducibility.get("code_commit") != code_commit:
        raise ValueError("Frozen system code commit differs from current Git HEAD")
    binding = reproducibility.get("formal_freeze_spec")
    expected_binding_fields = {
        "protocol",
        "path",
        "sha256",
        "spec_id",
        "formal_run_id",
        "source_bindings",
    }
    if not isinstance(binding, dict) or set(binding) != expected_binding_fields:
        raise ValueError("Frozen system lacks its formal freeze spec binding")
    spec_path = _required_file(binding["path"], "the bound formal freeze spec")
    spec = validate_formal_freeze_spec(spec_path)
    expected_binding = {
        "protocol": FORMAL_FREEZE_SPEC_PROTOCOL,
        "path": str(spec_path),
        "sha256": file_sha256(spec_path),
        "spec_id": spec["spec_id"],
        "formal_run_id": spec["formal_run_id"],
        "source_bindings": spec["source_bindings"],
    }
    if binding != expected_binding:
        raise ValueError("Frozen system formal freeze spec binding mismatch")
    with tempfile.TemporaryDirectory(prefix="formal-transition-validation.") as directory:
        expected = freeze_formal_system(
            spec_path=spec_path,
            output_path=Path(directory) / "expected-system.json",
            repository_root=repository_root,
        )
    if system != expected:
        raise ValueError(
            "Frozen system differs from the system re-derived from its formal spec"
        )
    if (
        file_sha256(system_path) != system_sha256
        or read_json(system_path) != system
    ):
        raise RuntimeError("Frozen system changed while it was being validated")
    if clean_git_head(repository_root) != code_commit:
        raise RuntimeError("Git HEAD changed while validating the frozen system")
    return system
