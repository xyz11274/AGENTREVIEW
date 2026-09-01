"""Resumable role-local coordination over explicit Research v2 stage artifacts."""

from __future__ import annotations

import fcntl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from agentreview.reviewer_runtime.sandbox import SandboxError

from .constants import ROLES
from .evaluation import run_internal_evaluation
from .evolution import (
    MAX_MUTATION_ATTEMPTS,
    EvolutionPool,
    commit_candidate,
    initialize_pool,
    propose_candidate,
    validate_pool_entry_snapshot,
)
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient, complete_with_response_format
from .memory import SEED_NAMES, build_memory_snapshot, seed_program_path
from .models import Paper
from .program_checks import program_capability_manifest
from .review import SpecialistReviewPaperFailure, run_specialist_review
from .review_preparation import ReviewPlanError
from .s2 import FrozenS2Store

FORMAL_ROLE_EVOLUTION_PROTOCOL = "research-v2-formal-role-evolution-v1"
FORMAL_ATTEMPT_WORK_PROTOCOL = "research-v2-formal-attempt-work-v1"
FORMAL_WINNER_PROTOCOL = "research-v2-formal-role-winner-v1"
FORMAL_RUN_SPEC_PROTOCOL = "research-v2-formal-role-run-spec-v1"
FORMAL_ALL_ROLES_PROTOCOL = "research-v2-formal-all-roles-run-v1"
FORMAL_CANDIDATE_FAILURE_PROTOCOL = "research-v2-formal-candidate-failure-v1"
FORMAL_PAPER_FAILURE_SCORE_PROTOCOL = (
    "research-v2-internal-evaluation-paper-failure-score-v1"
)
FORMAL_STATIC_SIZE = 25
FORMAL_ROTATING_POOL_SIZE = 125
FORMAL_ROTATING_BATCH_SIZE = 5
CANONICAL_EVALUATION_TASKS = 625
FORMAL_AUTONOMOUS_S2_INTERVENTION = {
    "protocol": "s2-intervention-v1",
    "mode": "program_autonomous",
    "label": "formal-primary-v1",
}

ProgressCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Formal Evolution coordinator is already active: {path}") from exc
        yield


def _records(path: str | Path, *, expected_count: int, label: str) -> tuple[Path, dict]:
    source = Path(path).expanduser().resolve()
    value = read_json(source)
    records = list(value.get("records") or [])
    if value.get("count") != expected_count or len(records) != expected_count:
        raise ValueError(
            f"Formal {label} split requires exactly {expected_count} records"
        )
    paper_ids = [str(item.get("paper_id") or "") for item in records]
    if "" in paper_ids or len(set(paper_ids)) != len(paper_ids):
        raise ValueError(f"Formal {label} split contains invalid paper identities")
    return source, value


def validate_canonical_evaluation_summary(path: str | Path) -> dict[str, Any]:
    """Require the complete 625-task canonical feedback corpus before Evolution."""
    source = Path(path).expanduser().resolve()
    value = read_json(source)
    expected = {
        "schema_version": "research-v2-internal-evaluation-batch-v1",
        "status": "complete",
        "task_count": CANONICAL_EVALUATION_TASKS,
        "failed": 0,
    }
    if {name: value.get(name) for name in expected} != expected:
        raise ValueError(
            "Formal Evolution requires a complete 625/625 canonical Internal "
            "Evaluation summary with zero failures"
        )
    completed = int(value.get("completed") or 0) + int(value.get("resumed") or 0)
    if completed != CANONICAL_EVALUATION_TASKS:
        raise ValueError("Canonical Internal Evaluation completion count differs from 625")
    return {
        "path": str(source),
        "sha256": file_sha256(source),
        "run_id": str(value.get("run_id") or ""),
        "review_run_id": str(value.get("review_run_id") or ""),
    }


def _coordinator_root(output_root: str | Path, role: str, run_id: str) -> Path:
    return (
        Path(output_root).expanduser().resolve()
        / "evolution"
        / role
        / run_id
    )


def _pool(path: str | Path, role: str) -> tuple[Path, EvolutionPool]:
    source = Path(path).expanduser().resolve()
    value = EvolutionPool.from_dict(read_json(source))
    if value.role != role:
        raise ValueError("Formal Evolution pool role mismatch")
    if not value.entries:
        raise ValueError("Formal Evolution pool is empty")
    for entry in value.entries:
        validate_pool_entry_snapshot(entry)
    return source, value


def _candidate_failure_path(root: Path, attempt: int) -> Path:
    return root / f"attempt-{attempt:03d}.candidate-failure.json"


def _load_candidate_failure(
    path: Path,
    *,
    work: dict[str, Any],
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = read_json(path)
    expected = {
        "protocol": FORMAL_CANDIDATE_FAILURE_PROTOCOL,
        "role": work["role"],
        "run_id": work["run_id"],
        "attempt": work["attempt"],
        "work_id": work["work_id"],
    }
    if {name: value.get(name) for name in expected} != expected:
        raise ValueError("Formal candidate-failure artifact identity mismatch")
    if outcome is not None and value.get("candidate_id") != outcome.get("candidate_id"):
        raise ValueError("Formal candidate-failure artifact candidate mismatch")
    unhashed = {name: item for name, item in value.items() if name != "failure_id"}
    if value.get("failure_id") != value_sha256(unhashed):
        raise ValueError("Formal candidate-failure artifact hash mismatch")
    program = Path(str(value.get("program_path") or "")).expanduser().resolve()
    if not program.is_file() or file_sha256(program) != value.get("program_sha256"):
        raise ValueError("Formal failed-candidate Program differs from its artifact")
    return value


def _attempt_terminal(
    pool: EvolutionPool,
    attempt_artifact: dict[str, Any],
    *,
    candidate_failure_path: Path | None = None,
    work: dict[str, Any] | None = None,
) -> bool:
    if attempt_artifact.get("status") == "failed":
        return True
    if candidate_failure_path is not None and candidate_failure_path.is_file():
        if work is None:
            raise ValueError("Formal candidate-failure validation requires attempt work")
        _load_candidate_failure(
            candidate_failure_path,
            work=work,
            outcome=attempt_artifact,
        )
        return True
    candidate_id = str(attempt_artifact.get("candidate_id") or "")
    return any(entry.candidate_id == candidate_id for entry in pool.entries)


def materialize_role_attempt(
    *,
    role: str,
    run_id: str,
    attempt: int,
    pool_path: str | Path,
    canonical_evaluation_summary_path: str | Path,
    static_split_path: str | Path,
    rotating_split_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Freeze the next parent and rotating batch without executing a model call."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    if not 1 <= int(attempt) <= MAX_MUTATION_ATTEMPTS:
        raise ValueError(
            f"Formal Evolution attempt must be in [1, {MAX_MUTATION_ATTEMPTS}]"
        )
    root = _coordinator_root(output_root, role, run_id)
    with _exclusive_lock(root / "coordinator.lock"):
        canonical = validate_canonical_evaluation_summary(
            canonical_evaluation_summary_path
        )
        static_source, static_split = _records(
            static_split_path,
            expected_count=FORMAL_STATIC_SIZE,
            label="static",
        )
        rotating_source, rotating_split = _records(
            rotating_split_path,
            expected_count=FORMAL_ROTATING_POOL_SIZE,
            label="rotating",
        )
        pool_source, pool = _pool(pool_path, role)
        for previous in range(1, attempt):
            previous_path = root / f"attempt-{previous:03d}.json"
            previous_work_path = root / f"attempt-{previous:03d}.work.json"
            if not previous_path.is_file() or not _attempt_terminal(
                pool,
                read_json(previous_path),
                candidate_failure_path=_candidate_failure_path(root, previous),
                work=(
                    read_json(previous_work_path)
                    if previous_work_path.is_file()
                    else None
                ),
            ):
                raise ValueError(
                    f"Formal Evolution attempt {attempt} requires terminal attempt {previous}"
                )
        work_path = root / f"attempt-{attempt:03d}.work.json"
        if work_path.is_file():
            value = read_json(work_path)
            if value.get("protocol") != FORMAL_ATTEMPT_WORK_PROTOCOL:
                raise ValueError("Formal Evolution work artifact protocol mismatch")
            unhashed = {name: item for name, item in value.items() if name != "work_id"}
            if value.get("work_id") != value_sha256(unhashed):
                raise ValueError("Formal Evolution work artifact hash mismatch")
            expected_identity = {
                "role": role,
                "run_id": run_id,
                "attempt": attempt,
                "canonical_evaluation": canonical,
            }
            if any(value.get(name) != expected for name, expected in expected_identity.items()):
                raise ValueError("Formal Evolution work artifact identity mismatch")
            expected_pool = {
                "path": str(pool_source),
            }
            if {
                name: value.get("pool_before", {}).get(name)
                for name in expected_pool
            } != expected_pool:
                raise ValueError("Formal Evolution work pool path mismatch")
            expected_static = {
                "path": str(static_source),
                "sha256": file_sha256(static_source),
                "split_sha256": static_split.get("split_sha256"),
                "paper_ids": [item["paper_id"] for item in static_split["records"]],
            }
            if value.get("static_split") != expected_static:
                raise ValueError("Formal Evolution work static split mismatch")
            expected_rotating = {
                "path": str(rotating_source),
                "sha256": file_sha256(rotating_source),
                "split_sha256": rotating_split.get("split_sha256"),
            }
            if value.get("rotating_split") != expected_rotating:
                raise ValueError("Formal Evolution work rotating split mismatch")
            start = (attempt - 1) * FORMAL_ROTATING_BATCH_SIZE
            rotating_records = list(rotating_split["records"])[
                start : start + FORMAL_ROTATING_BATCH_SIZE
            ]
            expected_batch = {
                "schedule": "frozen-record-order-disjoint-5-v1",
                "paper_ids": [item["paper_id"] for item in rotating_records],
                "paper_paths": [item["path"] for item in rotating_records],
                "records_sha256": value_sha256(rotating_records),
            }
            if value.get("rotating_batch") != expected_batch:
                raise ValueError("Formal Evolution work rotating batch mismatch")
            parent_id = str(value.get("parent", {}).get("candidate_id") or "")
            parent = next(
                (entry for entry in pool.entries if entry.candidate_id == parent_id),
                None,
            )
            if parent is None:
                raise ValueError("Formal Evolution work parent is absent from pool")
            expected_parent = {
                "candidate_id": parent.candidate_id,
                "program_path": parent.program_path,
                "program_sha256": parent.program_sha256,
                "program_version": read_json(parent.memory_snapshot_path).get(
                    "program_version"
                ),
                "memory_snapshot_path": parent.memory_snapshot_path,
                "memory_snapshot_sha256": parent.memory_snapshot_sha256,
                "memory_snapshot_id": parent.memory_snapshot_id,
                "seed_kind": parent.seed_kind,
            }
            if value.get("parent") != expected_parent:
                raise ValueError("Formal Evolution work parent identity mismatch")
            candidate_prefix = f"{run_id}-{role.lower()}-a{attempt:03d}"
            expected_run_ids = {
                "rotating_reviews": candidate_prefix + "-rotating-review",
                "rotating_evaluations": candidate_prefix + "-rotating-eval",
                "candidate_static_reviews": candidate_prefix + "-static-review",
                "candidate_static_evaluations": candidate_prefix + "-static-eval",
            }
            if value.get("artifact_run_ids") != expected_run_ids:
                raise ValueError("Formal Evolution work artifact run IDs mismatch")
            return value
        parent = pool.select_parent(seed=attempt)
        validate_pool_entry_snapshot(parent)
        start = (attempt - 1) * FORMAL_ROTATING_BATCH_SIZE
        stop = start + FORMAL_ROTATING_BATCH_SIZE
        rotating_records = list(rotating_split["records"])[start:stop]
        if len(rotating_records) != FORMAL_ROTATING_BATCH_SIZE:
            raise ValueError("Formal rotating schedule cannot materialize five papers")
        candidate_prefix = f"{run_id}-{role.lower()}-a{attempt:03d}"
        payload = {
            "protocol": FORMAL_ATTEMPT_WORK_PROTOCOL,
            "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
            "role": role,
            "run_id": run_id,
            "attempt": attempt,
            "pool_before": {
                "path": str(pool_source),
                "sha256": file_sha256(pool_source),
            },
            "parent": {
                "candidate_id": parent.candidate_id,
                "program_path": parent.program_path,
                "program_sha256": parent.program_sha256,
                "program_version": read_json(parent.memory_snapshot_path).get(
                    "program_version"
                ),
                "memory_snapshot_path": parent.memory_snapshot_path,
                "memory_snapshot_sha256": parent.memory_snapshot_sha256,
                "memory_snapshot_id": parent.memory_snapshot_id,
                "seed_kind": parent.seed_kind,
            },
            "canonical_evaluation": canonical,
            "static_split": {
                "path": str(static_source),
                "sha256": file_sha256(static_source),
                "split_sha256": static_split.get("split_sha256"),
                "paper_ids": [item["paper_id"] for item in static_split["records"]],
            },
            "rotating_split": {
                "path": str(rotating_source),
                "sha256": file_sha256(rotating_source),
                "split_sha256": rotating_split.get("split_sha256"),
            },
            "rotating_batch": {
                "schedule": "frozen-record-order-disjoint-5-v1",
                "paper_ids": [item["paper_id"] for item in rotating_records],
                "paper_paths": [item["path"] for item in rotating_records],
                "records_sha256": value_sha256(rotating_records),
            },
            "artifact_run_ids": {
                "rotating_reviews": candidate_prefix + "-rotating-review",
                "rotating_evaluations": candidate_prefix + "-rotating-eval",
                "candidate_static_reviews": candidate_prefix + "-static-review",
                "candidate_static_evaluations": candidate_prefix + "-static-eval",
            },
        }
        payload["work_id"] = value_sha256(payload)
        atomic_write_json(work_path, payload)
        return payload


def _validate_feedback_artifacts(
    *,
    work: dict[str, Any],
    review_paths: list[str | Path],
    evaluation_paths: list[str | Path],
) -> None:
    expected_papers = set(work["rotating_batch"]["paper_ids"])
    if len(review_paths) != FORMAL_ROTATING_BATCH_SIZE or len(evaluation_paths) != FORMAL_ROTATING_BATCH_SIZE:
        raise ValueError("Formal rotating feedback requires exactly five Reviews/Evaluations")
    reviews = [read_json(path) for path in review_paths]
    if {value.get("paper_id") for value in reviews} != expected_papers:
        raise ValueError("Formal rotating Reviews differ from the frozen batch")
    parent = work["parent"]
    if any(
        value.get("role") != work["role"]
        or value.get("memory_snapshot_id") != parent["memory_snapshot_id"]
        or value.get("program_version") != parent["program_version"]
        for value in reviews
    ):
        raise ValueError("Formal rotating Review differs from the selected parent")
    review_by_id = {value.get("review_id"): value for value in reviews}
    evaluations = [read_json(path) for path in evaluation_paths]
    if len(review_by_id) != FORMAL_ROTATING_BATCH_SIZE or {
        value.get("review_id") for value in evaluations
    } != set(review_by_id):
        raise ValueError("Formal rotating Evaluations do not match the Reviews")
    if any(
        value.get("role") != work["role"]
        or value.get("paper_id") != review_by_id[value.get("review_id")].get("paper_id")
        for value in evaluations
    ):
        raise ValueError("Formal rotating Evaluation identity mismatch")


def _state(root: Path, *, role: str, run_id: str) -> dict[str, Any]:
    path = root / "coordinator.json"
    if path.is_file():
        value = read_json(path)
        expected = {
            "protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
            "role": role,
            "run_id": run_id,
        }
        if {name: value.get(name) for name in expected} != expected:
            raise ValueError("Formal Evolution coordinator identity mismatch")
        return value
    return {
        "protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
        "role": role,
        "run_id": run_id,
        "attempts": {},
        "status": "running",
        "started_at": _utc_now(),
    }


def _write_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    atomic_write_json(root / "coordinator.json", state, immutable=False)


def _record_winner_if_complete(
    *,
    root: Path,
    pool_source: Path,
    pool: EvolutionPool,
    role: str,
    run_id: str,
    attempt: int,
    state: dict[str, Any],
) -> None:
    if attempt != MAX_MUTATION_ATTEMPTS:
        return
    winner = pool.best(require_snapshot=True)
    winner_payload = {
        "protocol": FORMAL_WINNER_PROTOCOL,
        "role": role,
        "run_id": run_id,
        "pool_path": str(pool_source),
        "pool_sha256": file_sha256(pool_source),
        "winner": asdict(winner),
    }
    winner_payload["winner_id"] = value_sha256(winner_payload)
    atomic_write_json(root / "winner.json", winner_payload)
    state["status"] = "complete"
    state["winner_id"] = winner_payload["winner_id"]


def record_candidate_failure(
    *,
    work_path: str | Path,
    pool_path: str | Path,
    output_root: str | Path,
    phase: str,
    error: Exception,
) -> dict[str, Any]:
    """Commit one deterministic candidate-local failure as a terminal attempt."""
    allowed_phases = {
        "candidate_memory_build",
        "candidate_static_review",
        "candidate_static_evaluation",
    }
    if phase not in allowed_phases:
        raise ValueError(f"Unknown formal candidate-failure phase: {phase}")
    source = Path(work_path).expanduser().resolve()
    work = read_json(source)
    if work.get("protocol") != FORMAL_ATTEMPT_WORK_PROTOCOL:
        raise ValueError("Formal Evolution work artifact protocol mismatch")
    unhashed = {name: item for name, item in work.items() if name != "work_id"}
    if work.get("work_id") != value_sha256(unhashed):
        raise ValueError("Formal Evolution work artifact hash mismatch")
    role = str(work.get("role") or "")
    run_id = str(work.get("run_id") or "")
    attempt = int(work.get("attempt") or 0)
    root = _coordinator_root(output_root, role, run_id)
    with _exclusive_lock(root / "coordinator.lock"):
        pool_source, pool = _pool(pool_path, role)
        attempt_path = root / f"attempt-{attempt:03d}.json"
        if not attempt_path.is_file():
            raise ValueError("Candidate failure requires a committed proposal artifact")
        outcome = read_json(attempt_path)
        if outcome.get("status") != "proposed":
            raise ValueError("Candidate failure requires a successful proposal")
        if any(
            entry.candidate_id == outcome.get("candidate_id")
            for entry in pool.entries
        ):
            raise ValueError("A committed pool candidate cannot be marked failed")
        if file_sha256(pool_source) != work["pool_before"]["sha256"]:
            raise ValueError("Formal Evolution pool changed before candidate failure")
        failure_path = _candidate_failure_path(root, attempt)
        if failure_path.is_file():
            failure = _load_candidate_failure(
                failure_path,
                work=work,
                outcome=outcome,
            )
        else:
            program_path = Path(str(outcome.get("program_path") or "")).resolve()
            failure = {
                "protocol": FORMAL_CANDIDATE_FAILURE_PROTOCOL,
                "role": role,
                "run_id": run_id,
                "attempt": attempt,
                "work_id": work["work_id"],
                "candidate_id": outcome["candidate_id"],
                "parent_id": outcome["parent_id"],
                "phase": phase,
                "program_path": str(program_path),
                "program_sha256": file_sha256(program_path),
                "error": f"{type(error).__name__}: {error}",
                "created_at": _utc_now(),
            }
            failure["failure_id"] = value_sha256(failure)
            atomic_write_json(failure_path, failure)
        state = _state(root, role=role, run_id=run_id)
        terminal = {
            "status": "candidate_failed",
            "attempt_path": str(attempt_path),
            "candidate_id": outcome["candidate_id"],
            "failure_path": str(failure_path),
            "failure_id": failure["failure_id"],
            "phase": failure["phase"],
        }
        state["attempts"][f"{attempt:03d}"] = terminal
        _record_winner_if_complete(
            root=root,
            pool_source=pool_source,
            pool=pool,
            role=role,
            run_id=run_id,
            attempt=attempt,
            state=state,
        )
        _write_state(root, state)
        return terminal


def advance_role_attempt(
    *,
    work_path: str | Path,
    pool_path: str | Path,
    output_root: str | Path,
    completion_client: CompletionClient,
    completion_identity: dict[str, Any],
    rotating_review_paths: list[str | Path],
    rotating_evaluation_paths: list[str | Path],
    candidate_memory_snapshot_path: str | Path | None = None,
    candidate_static_review_paths: list[str | Path] | None = None,
    candidate_static_evaluation_paths: list[str | Path] | None = None,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Advance one frozen attempt to proposed, failed, or committed state."""
    source = Path(work_path).expanduser().resolve()
    work = read_json(source)
    if work.get("protocol") != FORMAL_ATTEMPT_WORK_PROTOCOL:
        raise ValueError("Formal Evolution work artifact protocol mismatch")
    unhashed = {name: item for name, item in work.items() if name != "work_id"}
    if work.get("work_id") != value_sha256(unhashed):
        raise ValueError("Formal Evolution work artifact hash mismatch")
    role = str(work.get("role") or "")
    run_id = str(work.get("run_id") or "")
    attempt = int(work.get("attempt") or 0)
    root = _coordinator_root(output_root, role, run_id)
    with _exclusive_lock(root / "coordinator.lock"):
        pool_source, pool = _pool(pool_path, role)
        state = _state(root, role=role, run_id=run_id)
        attempt_path = root / f"attempt-{attempt:03d}.json"
        failure_path = _candidate_failure_path(root, attempt)
        if failure_path.is_file():
            if not attempt_path.is_file():
                raise ValueError(
                    "Formal candidate failure has no proposal artifact"
                )
            outcome = read_json(attempt_path)
            failure = _load_candidate_failure(
                failure_path,
                work=work,
                outcome=outcome,
            )
            terminal = {
                "status": "candidate_failed",
                "attempt_path": str(attempt_path),
                "candidate_id": outcome["candidate_id"],
                "failure_path": str(failure_path),
                "failure_id": failure["failure_id"],
                "phase": failure["phase"],
            }
            state["attempts"][f"{attempt:03d}"] = terminal
            _record_winner_if_complete(
                root=root,
                pool_source=pool_source,
                pool=pool,
                role=role,
                run_id=run_id,
                attempt=attempt,
                state=state,
            )
            _write_state(root, state)
            return terminal
        if attempt_path.is_file() and _attempt_terminal(pool, read_json(attempt_path)):
            outcome = read_json(attempt_path)
            if outcome.get("status") == "proposed":
                entry = next(
                    value
                    for value in pool.entries
                    if value.candidate_id == outcome.get("candidate_id")
                )
                committed = {
                    "status": "committed",
                    "attempt_path": str(attempt_path),
                    "candidate": asdict(entry),
                }
                state["attempts"][f"{attempt:03d}"] = committed
                _record_winner_if_complete(
                    root=root,
                    pool_source=pool_source,
                    pool=pool,
                    role=role,
                    run_id=run_id,
                    attempt=attempt,
                    state=state,
                )
                _write_state(root, state)
                return committed
        else:
            if file_sha256(pool_source) != work["pool_before"]["sha256"]:
                raise ValueError("Formal Evolution pool changed after attempt materialization")
            _validate_feedback_artifacts(
                work=work,
                review_paths=rotating_review_paths,
                evaluation_paths=rotating_evaluation_paths,
            )
            outcome = propose_candidate(
                role=role,
                pool=pool,
                evaluation_paths=rotating_evaluation_paths,
                review_paths=rotating_review_paths,
                attempt=attempt,
                run_id=run_id,
                output_root=output_root,
                completion_client=completion_client,
                completion_identity=completion_identity,
                require_bubblewrap=require_bubblewrap,
            )
            if outcome.get("parent_id") != work["parent"]["candidate_id"]:
                raise ValueError("Reflector attempt selected a different frozen parent")
        key = f"{attempt:03d}"
        if outcome.get("status") == "failed":
            state["attempts"][key] = {
                "status": "failed",
                "attempt_path": str(attempt_path),
                "candidate_id": outcome.get("candidate_id"),
            }
            _record_winner_if_complete(
                root=root,
                pool_source=pool_source,
                pool=pool,
                role=role,
                run_id=run_id,
                attempt=attempt,
                state=state,
            )
            _write_state(root, state)
            return state["attempts"][key]
        static_inputs = (
            candidate_memory_snapshot_path,
            candidate_static_review_paths,
            candidate_static_evaluation_paths,
        )
        if any(value is None for value in static_inputs):
            pending = {
                "status": "awaiting_candidate_static",
                "attempt_path": str(attempt_path),
                "candidate_id": outcome["candidate_id"],
                "program_path": outcome["program_path"],
                "seed_kind": work["parent"]["seed_kind"],
                "expected_static_paper_ids": work["static_split"]["paper_ids"],
            }
            state["attempts"][key] = pending
            _write_state(root, state)
            return pending
        assert candidate_memory_snapshot_path is not None
        assert candidate_static_review_paths is not None
        assert candidate_static_evaluation_paths is not None
        if len(candidate_static_review_paths) != FORMAL_STATIC_SIZE or len(candidate_static_evaluation_paths) != FORMAL_STATIC_SIZE:
            raise ValueError("Formal candidate static scoring requires exactly 25 tasks")
        static_reviews = [read_json(path) for path in candidate_static_review_paths]
        if {value.get("paper_id") for value in static_reviews} != set(
            work["static_split"]["paper_ids"]
        ):
            raise ValueError("Candidate static Reviews differ from the frozen static split")
        entry = commit_candidate(
            pool=pool,
            attempt_path=attempt_path,
            static_review_paths=candidate_static_review_paths,
            static_evaluation_paths=candidate_static_evaluation_paths,
            pool_path=pool_source,
            memory_snapshot_path=candidate_memory_snapshot_path,
        )
        committed = {
            "status": "committed",
            "attempt_path": str(attempt_path),
            "candidate": asdict(entry),
        }
        state["attempts"][key] = committed
        _record_winner_if_complete(
            root=root,
            pool_source=pool_source,
            pool=pool,
            role=role,
            run_id=run_id,
            attempt=attempt,
            state=state,
        )
        _write_state(root, state)
        return committed


class BoundedCompletionClient:
    """Share one completion client behind the formal global request limit."""

    def __init__(self, client: CompletionClient, maximum_inflight: int):
        if not 1 <= int(maximum_inflight) <= 8:
            raise ValueError("Formal 27B maximum_inflight must be in [1, 8]")
        self.client = client
        self._semaphore = threading.BoundedSemaphore(int(maximum_inflight))

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        component: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        with self._semaphore:
            return complete_with_response_format(
                self.client,
                messages,
                max_tokens=max_tokens,
                component=component,
                response_format=response_format,
            )

    def count_chat_tokens(
        self, messages: list[dict[str, str]]
    ) -> dict[str, int] | None:
        counter = getattr(self.client, "count_chat_tokens", None)
        if not callable(counter):
            return None
        with self._semaphore:
            return counter(messages)


def _artifact_paths(
    *,
    output_root: Path,
    run_id: str,
    records: list[dict[str, Any]],
    role: str,
    category: str,
) -> list[Path]:
    return [
        output_root / category / run_id / str(record["paper_id"]) / f"{role}.json"
        for record in records
    ]


def _paper_failure_review(
    *,
    output_root: Path,
    run_id: str,
    paper_id: str,
    role: str,
    program_version: str,
    snapshot_id: str,
    error: Exception,
) -> Path:
    identity = {
        "protocol": "research-v2-specialist-paper-failure-score-v1",
        "run_id": run_id,
        "paper_id": paper_id,
        "role": role,
        "program_version": program_version,
        "memory_snapshot_id": snapshot_id,
        "status": "paper_failure",
        "review_quality": 1.0,
    }
    review_id = value_sha256(identity)
    payload = {
        **identity,
        "review_id": review_id,
        "review_text": "",
        "failure": f"{type(error).__name__}: {error}",
        "retrieval_provenance": {"memory": {}, "s2": {}},
    }
    path = (
        output_root
        / "reviews"
        / run_id
        / paper_id
        / f"{role}.paper_failure.json"
    )
    atomic_write_json(path, payload)
    return path


def _load_paper_failure_review(
    *,
    path: Path,
    run_id: str,
    paper_id: str,
    role: str,
    program_version: str,
    snapshot_id: str,
) -> Path:
    value = read_json(path)
    identity = {
        "protocol": "research-v2-specialist-paper-failure-score-v1",
        "run_id": run_id,
        "paper_id": paper_id,
        "role": role,
        "program_version": program_version,
        "memory_snapshot_id": snapshot_id,
        "status": "paper_failure",
        "review_quality": 1.0,
    }
    if any(value.get(name) != expected for name, expected in identity.items()):
        raise ValueError("Formal Specialist paper-failure identity mismatch")
    if value.get("review_id") != value_sha256(identity):
        raise ValueError("Formal Specialist paper-failure hash mismatch")
    return path


def _paper_failure_evaluation(
    *, output_root: Path, run_id: str, review_path: str | Path
) -> Path:
    review = read_json(review_path)
    payload = {
        "protocol": "research-v2-internal-evaluation-paper-failure-score-v1",
        "evaluation_id": value_sha256(
            {
                "run_id": run_id,
                "review_id": review["review_id"],
                "review_quality": 1.0,
            }
        ),
        "review_id": review["review_id"],
        "paper_id": review["paper_id"],
        "role": review["role"],
        "review_quality": 1.0,
        "major_missing_points": ["Specialist Review generation failed twice."],
        "factual_evidence_issues": [],
        "improvement_suggestions": ["Inspect the saved paper-local failure artifact."],
        "status": "paper_failure_score",
        "run_id": run_id,
    }
    path = (
        output_root
        / "evaluations"
        / run_id
        / str(review["paper_id"])
        / f"{review['role']}.paper_failure.json"
    )
    atomic_write_json(path, payload)
    return path


_PAPER_LOCAL_EVALUATION_ERROR_PREFIXES = (
    "EmptyCompletionOutput:",
    "JSONDecodeError:",
    "ValueError: Internal Evaluator output must be a JSON object",
    "ValueError: Internal Evaluator fields mismatch:",
    "ValueError: review_quality must be in [1, 5]",
    "TypeError: major_missing_points must be a list of strings",
    "TypeError: factual_evidence_issues must be a list of strings",
    "TypeError: improvement_suggestions must be a list of strings",
)


def _paper_local_evaluation_failure(value: dict[str, Any]) -> bool:
    errors = value.get("errors")
    return (
        value.get("status") == "failed"
        and isinstance(errors, list)
        and bool(errors)
        and all(
            isinstance(error, str)
            and error.startswith(_PAPER_LOCAL_EVALUATION_ERROR_PREFIXES)
            for error in errors
        )
    )


def _internal_evaluation_failure_score(
    *,
    output_root: Path,
    run_id: str,
    review_path: str | Path,
    failure_path: str | Path,
) -> Path:
    review = read_json(review_path)
    source = Path(failure_path).expanduser().resolve()
    failure = read_json(source)
    if not _paper_local_evaluation_failure(failure):
        raise RuntimeError(
            "Internal Evaluation failure is not paper-local; formal run must stop"
        )
    identity = {
        "protocol": FORMAL_PAPER_FAILURE_SCORE_PROTOCOL,
        "run_id": run_id,
        "review_id": review["review_id"],
        "paper_id": review["paper_id"],
        "role": review["role"],
        "review_quality": 1.0,
        "status": "paper_failure_score",
        "failure_kind": "internal_evaluation",
        "source_failure_path": str(source),
        "source_failure_sha256": file_sha256(source),
    }
    payload = {
        **identity,
        "evaluation_id": value_sha256(identity),
        "major_missing_points": ["Internal Evaluator output failed validation twice."],
        "factual_evidence_issues": [],
        "improvement_suggestions": [
            "Inspect the saved paper-local Internal Evaluation failure artifact."
        ],
        "errors": list(failure["errors"]),
    }
    path = (
        output_root
        / "evaluations"
        / run_id
        / str(review["paper_id"])
        / f"{review['role']}.paper_failure.json"
    )
    atomic_write_json(path, payload)
    return path


def _load_internal_evaluation_failure_score(
    *,
    path: Path,
    run_id: str,
    review: dict[str, Any],
) -> Path:
    value = read_json(path)
    expected = {
        "protocol": FORMAL_PAPER_FAILURE_SCORE_PROTOCOL,
        "run_id": run_id,
        "review_id": review["review_id"],
        "paper_id": review["paper_id"],
        "role": review["role"],
        "review_quality": 1.0,
        "status": "paper_failure_score",
        "failure_kind": "internal_evaluation",
    }
    if {name: value.get(name) for name in expected} != expected:
        raise ValueError("Formal paper-failure score identity mismatch")
    identity = {
        name: value[name]
        for name in (
            "protocol",
            "run_id",
            "review_id",
            "paper_id",
            "role",
            "review_quality",
            "status",
            "failure_kind",
            "source_failure_path",
            "source_failure_sha256",
        )
    }
    if value.get("evaluation_id") != value_sha256(identity):
        raise ValueError("Formal paper-failure score hash mismatch")
    source = Path(str(value.get("source_failure_path") or "")).resolve()
    if not source.is_file() or file_sha256(source) != value.get(
        "source_failure_sha256"
    ):
        raise ValueError("Formal paper-failure source artifact differs")
    if not _paper_local_evaluation_failure(read_json(source)):
        raise ValueError("Formal paper-failure source is not paper-local")
    return path


_INFRASTRUCTURE_ERROR_MARKERS = (
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "InternalServerError",
    "RateLimitError",
    "ConnectionError",
    "ConnectError",
    "ReadTimeout",
    "OutOfMemoryError",
    "CUDA out of memory",
    "No space left on device",
    "bubblewrap was required",
)


def _is_candidate_local_failure(error: Exception) -> bool:
    if isinstance(error, ReviewPlanError):
        return True
    if isinstance(error, TypeError):
        return True
    if isinstance(error, TimeoutError):
        return True
    message = f"{type(error).__name__}: {error}"
    if any(marker in message for marker in _INFRASTRUCTURE_ERROR_MARKERS):
        return False
    if isinstance(error, SandboxError):
        return True
    return isinstance(error, ValueError) and any(
        marker in str(error)
        for marker in (
            "KnowledgeBase.read()",
            "Review Plan",
            "plan_review",
            "selected_s2_ids",
            "s2_disposition",
            "context_order",
        )
    )


def _run_review_batch(
    *,
    records: list[dict[str, Any]],
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    program_version: str,
    run_id: str,
    output_root: Path,
    completion_client: CompletionClient,
    model_identity: dict[str, Any],
    frozen_s2_path: str | Path,
    embedding_client: Any,
    embedding_identity: dict[str, Any],
    max_workers: int,
    require_bubblewrap: bool,
    progress: ProgressCallback | None = None,
    score_program_failures: bool = False,
) -> list[Path]:
    snapshot = read_json(snapshot_path)
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    progress_lock = threading.Lock()
    finished = 0

    def run(record: dict[str, Any]) -> Path:
        nonlocal finished
        paper_id = str(record["paper_id"])
        final_path = output_root / "reviews" / run_id / paper_id / f"{role}.json"
        failure_path = final_path.with_name(f"{role}.paper_failure.json")
        resumed = final_path.is_file() or failure_path.is_file()
        disposition = "resumed" if resumed else "completed"
        try:
            if failure_path.is_file() and not final_path.is_file():
                result = _load_paper_failure_review(
                    path=failure_path,
                    run_id=run_id,
                    paper_id=paper_id,
                    role=role,
                    program_version=program_version,
                    snapshot_id=snapshot_id,
                )
                disposition = "paper_failure_score"
            else:
                try:
                    run_specialist_review(
                        paper_path=record["path"],
                        role=role,
                        program_path=program_path,
                        snapshot_path=snapshot_path,
                        run_id=run_id,
                        output_root=output_root,
                        completion_client=completion_client,
                        model_identity=model_identity,
                        program_version=program_version,
                        frozen_s2_path=frozen_s2_path,
                        s2_intervention=FORMAL_AUTONOMOUS_S2_INTERVENTION,
                        embedding_client=embedding_client,
                        embedding_identity=embedding_identity,
                        require_bubblewrap=require_bubblewrap,
                        _allow_legacy_missing_require_bubblewrap=(
                            require_bubblewrap is True
                        ),
                    )
                    result = final_path
                except SpecialistReviewPaperFailure as exc:
                    disposition = "paper_failure_score"
                    result = _paper_failure_review(
                        output_root=output_root,
                        run_id=run_id,
                        paper_id=paper_id,
                        role=role,
                        program_version=program_version,
                        snapshot_id=snapshot_id,
                        error=exc,
                    )
                except Exception as exc:
                    if not score_program_failures or not _is_candidate_local_failure(
                        exc
                    ):
                        raise
                    disposition = "paper_failure_score"
                    result = _paper_failure_review(
                        output_root=output_root,
                        run_id=run_id,
                        paper_id=paper_id,
                        role=role,
                        program_version=program_version,
                        snapshot_id=snapshot_id,
                        error=exc,
                    )
            return result
        except BaseException:
            disposition = "error"
            raise
        finally:
            if progress is not None:
                with progress_lock:
                    finished += 1
                    current = finished
                progress(
                    {
                        "event": "formal_review_progress",
                        "run_id": run_id,
                        "role": role,
                        "paper_id": paper_id,
                        "disposition": disposition,
                        "finished": current,
                        "total": len(records),
                    }
                )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(run, records))


def _run_evaluation_batch(
    *,
    records: list[dict[str, Any]],
    review_paths: list[str | Path],
    run_id: str,
    output_root: Path,
    completion_client: CompletionClient,
    evaluator_identity: dict[str, Any],
    max_workers: int,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    if len(records) != len(review_paths):
        raise ValueError("Formal Evaluation batch inputs are not aligned")
    progress_lock = threading.Lock()
    finished = 0

    def run(item: tuple[dict[str, Any], str | Path]) -> Path:
        nonlocal finished
        record, review_path = item
        review = read_json(review_path)
        role = str(review["role"])
        final_path = (
            output_root
            / "evaluations"
            / run_id
            / str(record["paper_id"])
            / f"{role}.json"
        )
        score_path = final_path.with_name(f"{role}.paper_failure.json")
        resumed = final_path.is_file() or score_path.is_file()
        disposition = "resumed" if resumed else "completed"
        try:
            if review.get("status") == "paper_failure":
                disposition = "paper_failure_score"
                return _paper_failure_evaluation(
                    output_root=output_root,
                    run_id=run_id,
                    review_path=review_path,
                )
            if score_path.is_file():
                disposition = "paper_failure_score"
                return _load_internal_evaluation_failure_score(
                    path=score_path,
                    run_id=run_id,
                    review=review,
                )
            try:
                run_internal_evaluation(
                    paper_path=record["path"],
                    review_path=review_path,
                    run_id=run_id,
                    output_root=output_root,
                    completion_client=completion_client,
                    evaluator_identity=evaluator_identity,
                )
                return final_path
            except RuntimeError:
                failure_path = final_path.with_name(f"{role}.failure.json")
                if not failure_path.is_file():
                    raise
                disposition = "paper_failure_score"
                return _internal_evaluation_failure_score(
                    output_root=output_root,
                    run_id=run_id,
                    review_path=review_path,
                    failure_path=failure_path,
                )
        except BaseException:
            disposition = "error"
            raise
        finally:
            if progress is not None:
                with progress_lock:
                    finished += 1
                    current = finished
                progress(
                    {
                        "event": "formal_evaluation_progress",
                        "run_id": run_id,
                        "role": role,
                        "paper_id": record["paper_id"],
                        "disposition": disposition,
                        "finished": current,
                        "total": len(records),
                    }
                )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(run, zip(records, review_paths, strict=True)))


def _canonical_sources(
    *,
    output_root: Path,
    canonical_split_path: str | Path,
    canonical_summary: dict[str, Any],
    role: str,
) -> tuple[list[Path], list[Path]]:
    _, split = _records(
        canonical_split_path,
        expected_count=125,
        label="canonical",
    )
    review_paths = _artifact_paths(
        output_root=output_root,
        run_id=canonical_summary["review_run_id"],
        records=split["records"],
        role=role,
        category="reviews",
    )
    evaluation_paths = _artifact_paths(
        output_root=output_root,
        run_id=canonical_summary["run_id"],
        records=split["records"],
        role=role,
        category="evaluations",
    )
    missing = [
        str(path)
        for path in [*review_paths, *evaluation_paths]
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Formal Memory source artifacts are incomplete: {missing[:3]}"
        )
    return review_paths, evaluation_paths


def _validated_paper_assets(
    records: list[dict[str, Any]],
    *,
    label: str,
) -> list[dict[str, Any]]:
    values = []
    for record in records:
        source = Path(str(record.get("path") or "")).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Formal {label} paper asset is missing: {source}")
        paper = Paper.from_dict(read_json(source))
        if paper.paper_id != record.get("paper_id"):
            raise ValueError(f"Formal {label} split paper identity mismatch: {source}")
        values.append(
            {
                "paper_id": paper.paper_id,
                "path": str(source),
                "sha256": file_sha256(source),
                "title": paper.title,
            }
        )
    return values


def _preflight_formal_role_run(
    *,
    role: str,
    run_id: str,
    pool_source: Path,
    canonical_evaluation_summary_path: str | Path,
    canonical_split_path: str | Path,
    static_split_path: str | Path,
    rotating_split_path: str | Path,
    frozen_s2_path: str | Path,
    output_root: Path,
    model_identity: dict[str, Any],
    embedding_identity: dict[str, Any],
    require_bubblewrap: bool,
) -> tuple[
    dict[str, Any],
    list[Path],
    list[Path],
    list[dict[str, Any]],
]:
    """Validate every immutable formal input before the first model/GPU call."""
    root = _coordinator_root(output_root, role, run_id)
    expected_pool = root / "pool.json"
    if pool_source != expected_pool:
        raise ValueError(
            f"Formal role pool must use the canonical path: {expected_pool}"
        )
    canonical = validate_canonical_evaluation_summary(
        canonical_evaluation_summary_path
    )
    if not canonical["run_id"] or not canonical["review_run_id"]:
        raise ValueError("Canonical Internal Evaluation summary lacks run identities")
    canonical_source, canonical_split = _records(
        canonical_split_path,
        expected_count=125,
        label="canonical",
    )
    static_source, static_split = _records(
        static_split_path,
        expected_count=FORMAL_STATIC_SIZE,
        label="static",
    )
    rotating_source, rotating_split = _records(
        rotating_split_path,
        expected_count=FORMAL_ROTATING_POOL_SIZE,
        label="rotating",
    )
    split_records = {
        "canonical": list(canonical_split["records"]),
        "static": list(static_split["records"]),
        "rotating": list(rotating_split["records"]),
    }
    split_ids = {
        name: {str(record["paper_id"]) for record in records}
        for name, records in split_records.items()
    }
    if any(
        split_ids[left].intersection(split_ids[right])
        for left, right in (
            ("canonical", "static"),
            ("canonical", "rotating"),
            ("static", "rotating"),
        )
    ):
        raise ValueError("Formal canonical/static/rotating splits must be disjoint")
    paper_assets = {
        name: _validated_paper_assets(records, label=name)
        for name, records in split_records.items()
    }
    s2_source = Path(frozen_s2_path).expanduser().resolve()
    s2_store = FrozenS2Store(s2_source)
    expected_s2_ids = set().union(*split_ids.values())
    if set(s2_store.pool) != expected_s2_ids:
        missing = sorted(expected_s2_ids - set(s2_store.pool))
        extra = sorted(set(s2_store.pool) - expected_s2_ids)
        raise ValueError(
            "Formal development S2 Pool must exactly cover all 275 papers: "
            f"missing={missing[:3]}, extra={extra[:3]}"
        )
    pool_versions = set()
    for values in paper_assets.values():
        for paper in values:
            record = s2_store.pool[paper["paper_id"]]
            body = {name: value for name, value in record.items() if name != "record_sha256"}
            if record.get("record_sha256") != value_sha256(body):
                raise ValueError(
                    "Formal development S2 record identity mismatch for "
                    f"{paper['paper_id']}"
                )
            _, provenance = s2_store.retrieve(
                paper_id=paper["paper_id"],
                target_title=paper["title"],
                limit=5,
            )
            pool_versions.add(str(provenance.get("pool_version") or ""))
    if len(pool_versions) != 1 or "" in pool_versions:
        raise ValueError("Formal development S2 Pool has mixed/empty versions")
    canonical_reviews, canonical_evaluations = _canonical_sources(
        output_root=output_root,
        canonical_split_path=canonical_source,
        canonical_summary=canonical,
        role=role,
    )
    canonical_artifact_identity = [
        {
            "review_path": str(review_path),
            "review_sha256": file_sha256(review_path),
            "evaluation_path": str(evaluation_path),
            "evaluation_sha256": file_sha256(evaluation_path),
        }
        for review_path, evaluation_path in zip(
            canonical_reviews,
            canonical_evaluations,
            strict=True,
        )
    ]
    split_sources = {
        "canonical": (canonical_source, canonical_split),
        "static": (static_source, static_split),
        "rotating": (rotating_source, rotating_split),
    }
    seed_programs = {}
    for seed_name in SEED_NAMES:
        program = seed_program_path(seed_name).resolve()
        seed_programs[seed_name] = {
            "path": str(program),
            "sha256": file_sha256(program),
            "capability_manifest": program_capability_manifest(path=program),
        }
    run_spec = {
        "protocol": FORMAL_RUN_SPEC_PROTOCOL,
        "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
        "role": role,
        "run_id": run_id,
        "pool_path": str(pool_source),
        "canonical_evaluation": canonical,
        "canonical_artifacts_sha256": value_sha256(canonical_artifact_identity),
        "splits": {
            name: {
                "path": str(source),
                "sha256": file_sha256(source),
                "split_sha256": split.get("split_sha256"),
                "paper_asset_set_sha256": value_sha256(paper_assets[name]),
            }
            for name, (source, split) in split_sources.items()
        },
        "frozen_s2": {
            "path": str(s2_source),
            "sha256": file_sha256(s2_source),
            "pool_version": next(iter(pool_versions)),
            "paper_count": len(s2_store.pool),
        },
        "seed_programs": seed_programs,
        "model_identity": dict(model_identity),
        "embedding_identity": dict(embedding_identity),
        "s2_intervention": dict(FORMAL_AUTONOMOUS_S2_INTERVENTION),
        "require_bubblewrap": bool(require_bubblewrap),
        "search_budget": {
            "seed_count": len(SEED_NAMES),
            "mutation_attempts": MAX_MUTATION_ATTEMPTS,
            "static_batch_size": FORMAL_STATIC_SIZE,
            "rotating_batch_size": FORMAL_ROTATING_BATCH_SIZE,
            "rotating_schedule": "frozen-record-order-disjoint-5-v1",
        },
    }
    run_spec["run_spec_id"] = value_sha256(run_spec)
    atomic_write_json(root / "run-spec.json", run_spec)
    return canonical, canonical_reviews, canonical_evaluations, split_records["static"]


def preflight_formal_role_evolution(
    *,
    role: str,
    run_id: str,
    pool_path: str | Path,
    canonical_evaluation_summary_path: str | Path,
    canonical_split_path: str | Path,
    static_split_path: str | Path,
    rotating_split_path: str | Path,
    frozen_s2_path: str | Path,
    output_root: str | Path,
    model_identity: dict[str, Any],
    embedding_identity: dict[str, Any],
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Commit only the immutable run specification; execute no model/GPU call."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    output = Path(output_root).expanduser().resolve()
    pool_source = Path(pool_path).expanduser().resolve()
    root = _coordinator_root(output, role, run_id)
    with _exclusive_lock(output / "runs" / "formal-evolution-runner.lock"):
        _preflight_formal_role_run(
            role=role,
            run_id=run_id,
            pool_source=pool_source,
            canonical_evaluation_summary_path=canonical_evaluation_summary_path,
            canonical_split_path=canonical_split_path,
            static_split_path=static_split_path,
            rotating_split_path=rotating_split_path,
            frozen_s2_path=frozen_s2_path,
            output_root=output,
            model_identity=model_identity,
            embedding_identity=embedding_identity,
            require_bubblewrap=require_bubblewrap,
        )
    return read_json(root / "run-spec.json")


def run_formal_role_evolution(
    *,
    role: str,
    run_id: str,
    pool_path: str | Path,
    canonical_evaluation_summary_path: str | Path,
    canonical_split_path: str | Path,
    static_split_path: str | Path,
    rotating_split_path: str | Path,
    frozen_s2_path: str | Path,
    output_root: str | Path,
    completion_client: CompletionClient,
    model_identity: dict[str, Any],
    embedding_client: Any,
    embedding_identity: dict[str, Any],
    max_workers: int = 8,
    require_bubblewrap: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run or resume one role from three seeds through twenty attempts."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    if not 1 <= int(max_workers) <= 8:
        raise ValueError("Formal role runner max_workers must be in [1, 8]")
    output = Path(output_root).expanduser().resolve()
    pool_source = Path(pool_path).expanduser().resolve()
    root = _coordinator_root(output, role, run_id)
    limited_client = BoundedCompletionClient(completion_client, max_workers)

    def emit(event: str, **values: Any) -> None:
        if progress is not None:
            progress(
                {
                    "event": event,
                    "role": role,
                    "role_run_id": run_id,
                    **values,
                }
            )

    with _exclusive_lock(output / "runs" / "formal-evolution-runner.lock"):
        emit("formal_role_preflight_started")
        canonical, canonical_reviews, canonical_evaluations, static_records = (
            _preflight_formal_role_run(
                role=role,
                run_id=run_id,
                pool_source=pool_source,
                canonical_evaluation_summary_path=canonical_evaluation_summary_path,
                canonical_split_path=canonical_split_path,
                static_split_path=static_split_path,
                rotating_split_path=rotating_split_path,
                frozen_s2_path=frozen_s2_path,
                output_root=output,
                model_identity=model_identity,
                embedding_identity=embedding_identity,
                require_bubblewrap=require_bubblewrap,
            )
        )
        emit(
            "formal_role_preflight_complete",
            canonical_evaluation_sha256=canonical["sha256"],
            pending_pool=not pool_source.is_file(),
        )
        if pool_source.is_file() and (root / "coordinator.json").is_file():
            existing_state = _state(root, role=role, run_id=run_id)
            if existing_state.get("status") == "complete":
                _, completed_pool = _pool(pool_source, role)
                if not (root / "winner.json").is_file():
                    raise FileNotFoundError(
                        "Completed formal role coordinator has no winner artifact"
                    )
                completed_pool.best(require_snapshot=True)
                emit(
                    "formal_role_complete",
                    resumed=True,
                    winner_id=existing_state.get("winner_id"),
                )
                return existing_state
        if not pool_source.is_file():
            seed_snapshots = {}
            seed_reviews = {}
            seed_evaluations = {}
            for seed_name in SEED_NAMES:
                emit("formal_seed_started", seed_name=seed_name)
                program = seed_program_path(seed_name).resolve()
                version = f"{run_id}-{role.lower()}-seed-{seed_name}"
                snapshot_path = (
                    output / "memories" / role / version / "snapshot.json"
                )
                build_memory_snapshot(
                    role=role,
                    program_path=program,
                    program_version=version,
                    review_paths=canonical_reviews,
                    evaluation_paths=canonical_evaluations,
                    output_path=snapshot_path,
                    seed_kind=seed_name,
                    embedding_client=embedding_client,
                    embedding_identity=embedding_identity,
                    completion_client=(
                        limited_client if seed_name == "experience_learner" else None
                    ),
                    completion_identity=(
                        model_identity
                        if seed_name == "experience_learner"
                        else None
                    ),
                    require_bubblewrap=require_bubblewrap,
                )
                emit(
                    "formal_seed_memory_complete",
                    seed_name=seed_name,
                    snapshot_path=str(snapshot_path),
                )
                seed_review_run = f"{version}-static-review"
                review_paths = _run_review_batch(
                    records=static_records,
                    role=role,
                    program_path=program,
                    snapshot_path=snapshot_path,
                    program_version=version,
                    run_id=seed_review_run,
                    output_root=output,
                    completion_client=limited_client,
                    model_identity=model_identity,
                    frozen_s2_path=frozen_s2_path,
                    embedding_client=embedding_client,
                    embedding_identity=embedding_identity,
                    max_workers=max_workers,
                    require_bubblewrap=require_bubblewrap,
                    progress=progress,
                )
                evaluation_paths = _run_evaluation_batch(
                    records=static_records,
                    review_paths=review_paths,
                    run_id=f"{version}-static-eval",
                    output_root=output,
                    completion_client=limited_client,
                    evaluator_identity=model_identity,
                    max_workers=max_workers,
                    progress=progress,
                )
                seed_snapshots[seed_name] = snapshot_path
                seed_reviews[seed_name] = review_paths
                seed_evaluations[seed_name] = evaluation_paths
                emit("formal_seed_complete", seed_name=seed_name)
            initialize_pool(
                role=role,
                seed_static_evaluations=seed_evaluations,
                seed_static_reviews=seed_reviews,
                seed_memory_snapshots=seed_snapshots,
                pool_path=pool_source,
            )
            emit("formal_seed_pool_complete", pool_path=str(pool_source))
        for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1):
            emit("formal_attempt_started", attempt=attempt)
            work = materialize_role_attempt(
                role=role,
                run_id=run_id,
                attempt=attempt,
                pool_path=pool_source,
                canonical_evaluation_summary_path=canonical_evaluation_summary_path,
                static_split_path=static_split_path,
                rotating_split_path=rotating_split_path,
                output_root=output,
            )
            candidate_failure = _candidate_failure_path(root, attempt)
            if candidate_failure.is_file():
                terminal = advance_role_attempt(
                    work_path=root / f"attempt-{attempt:03d}.work.json",
                    pool_path=pool_source,
                    output_root=output,
                    completion_client=limited_client,
                    completion_identity=model_identity,
                    rotating_review_paths=[],
                    rotating_evaluation_paths=[],
                    require_bubblewrap=require_bubblewrap,
                )
                emit(
                    "formal_attempt_complete",
                    attempt=attempt,
                    status=terminal["status"],
                    resumed=True,
                )
                continue
            rotating_records = [
                {"paper_id": paper_id, "path": paper_path}
                for paper_id, paper_path in zip(
                    work["rotating_batch"]["paper_ids"],
                    work["rotating_batch"]["paper_paths"],
                    strict=True,
                )
            ]
            parent = work["parent"]
            rotating_reviews = _run_review_batch(
                records=rotating_records,
                role=role,
                program_path=parent["program_path"],
                snapshot_path=parent["memory_snapshot_path"],
                program_version=parent["program_version"],
                run_id=work["artifact_run_ids"]["rotating_reviews"],
                output_root=output,
                completion_client=limited_client,
                model_identity=model_identity,
                frozen_s2_path=frozen_s2_path,
                embedding_client=embedding_client,
                embedding_identity=embedding_identity,
                max_workers=max_workers,
                require_bubblewrap=require_bubblewrap,
                progress=progress,
                score_program_failures=True,
            )
            rotating_evaluations = _run_evaluation_batch(
                records=rotating_records,
                review_paths=rotating_reviews,
                run_id=work["artifact_run_ids"]["rotating_evaluations"],
                output_root=output,
                completion_client=limited_client,
                evaluator_identity=model_identity,
                max_workers=max_workers,
                progress=progress,
            )
            attempt_state = advance_role_attempt(
                work_path=root / f"attempt-{attempt:03d}.work.json",
                pool_path=pool_source,
                output_root=output,
                completion_client=limited_client,
                completion_identity=model_identity,
                rotating_review_paths=rotating_reviews,
                rotating_evaluation_paths=rotating_evaluations,
                require_bubblewrap=require_bubblewrap,
            )
            if attempt_state["status"] in {"failed", "committed"}:
                emit(
                    "formal_attempt_complete",
                    attempt=attempt,
                    status=attempt_state["status"],
                    resumed=attempt_state["status"] == "committed",
                )
                continue
            candidate_id = str(attempt_state["candidate_id"])
            candidate_program = str(attempt_state["program_path"])
            candidate_version = f"{run_id}-{role.lower()}-{candidate_id}"
            candidate_snapshot = (
                output / "memories" / role / candidate_version / "snapshot.json"
            )
            try:
                build_memory_snapshot(
                    role=role,
                    program_path=candidate_program,
                    program_version=candidate_version,
                    review_paths=canonical_reviews,
                    evaluation_paths=canonical_evaluations,
                    output_path=candidate_snapshot,
                    seed_kind=str(attempt_state["seed_kind"]),
                    embedding_client=embedding_client,
                    embedding_identity=embedding_identity,
                    completion_client=(
                        limited_client
                        if attempt_state["seed_kind"] == "experience_learner"
                        else None
                    ),
                    completion_identity=(
                        model_identity
                        if attempt_state["seed_kind"] == "experience_learner"
                        else None
                    ),
                    require_bubblewrap=require_bubblewrap,
                )
            except Exception as exc:
                if not _is_candidate_local_failure(exc):
                    raise
                failed = record_candidate_failure(
                    work_path=root / f"attempt-{attempt:03d}.work.json",
                    pool_path=pool_source,
                    output_root=output,
                    phase="candidate_memory_build",
                    error=exc,
                )
                emit(
                    "formal_attempt_complete",
                    attempt=attempt,
                    status=failed["status"],
                    phase=failed["phase"],
                )
                continue
            emit(
                "formal_candidate_memory_complete",
                attempt=attempt,
                candidate_id=candidate_id,
            )
            try:
                static_reviews = _run_review_batch(
                    records=static_records,
                    role=role,
                    program_path=candidate_program,
                    snapshot_path=candidate_snapshot,
                    program_version=candidate_version,
                    run_id=work["artifact_run_ids"]["candidate_static_reviews"],
                    output_root=output,
                    completion_client=limited_client,
                    model_identity=model_identity,
                    frozen_s2_path=frozen_s2_path,
                    embedding_client=embedding_client,
                    embedding_identity=embedding_identity,
                    max_workers=max_workers,
                    require_bubblewrap=require_bubblewrap,
                    progress=progress,
                )
            except Exception as exc:
                if not _is_candidate_local_failure(exc):
                    raise
                failed = record_candidate_failure(
                    work_path=root / f"attempt-{attempt:03d}.work.json",
                    pool_path=pool_source,
                    output_root=output,
                    phase="candidate_static_review",
                    error=exc,
                )
                emit(
                    "formal_attempt_complete",
                    attempt=attempt,
                    status=failed["status"],
                    phase=failed["phase"],
                )
                continue
            static_evaluations = _run_evaluation_batch(
                records=static_records,
                review_paths=static_reviews,
                run_id=work["artifact_run_ids"]["candidate_static_evaluations"],
                output_root=output,
                completion_client=limited_client,
                evaluator_identity=model_identity,
                max_workers=max_workers,
                progress=progress,
            )
            committed = advance_role_attempt(
                work_path=root / f"attempt-{attempt:03d}.work.json",
                pool_path=pool_source,
                output_root=output,
                completion_client=limited_client,
                completion_identity=model_identity,
                rotating_review_paths=rotating_reviews,
                rotating_evaluation_paths=rotating_evaluations,
                candidate_memory_snapshot_path=candidate_snapshot,
                candidate_static_review_paths=static_reviews,
                candidate_static_evaluation_paths=static_evaluations,
                require_bubblewrap=require_bubblewrap,
            )
            emit(
                "formal_attempt_complete",
                attempt=attempt,
                status=committed["status"],
                candidate_id=candidate_id,
            )
        result = read_json(root / "coordinator.json")
        emit(
            "formal_role_complete",
            resumed=False,
            winner_id=result.get("winner_id"),
        )
        return result


def run_formal_evolution_roles(
    *,
    run_id: str,
    canonical_evaluation_summary_path: str | Path,
    canonical_split_path: str | Path,
    static_split_path: str | Path,
    rotating_split_path: str | Path,
    frozen_s2_path: str | Path,
    output_root: str | Path,
    completion_client: CompletionClient,
    model_identity: dict[str, Any],
    embedding_client: Any,
    embedding_identity: dict[str, Any],
    max_workers: int = 8,
    require_bubblewrap: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run all five roles serially while reusing one embedding/model client pair."""
    output = Path(output_root).expanduser().resolve()
    summary_path = output / "runs" / run_id / "formal-evolution.json"
    if summary_path.is_file():
        state = read_json(summary_path)
        if state.get("protocol") != FORMAL_ALL_ROLES_PROTOCOL or state.get(
            "run_id"
        ) != run_id:
            raise ValueError("Formal all-role run summary identity mismatch")
    else:
        state = {
            "protocol": FORMAL_ALL_ROLES_PROTOCOL,
            "run_id": run_id,
            "role_order": list(ROLES),
            "roles": {},
            "started_at": _utc_now(),
        }
    state["status"] = "running"
    state.pop("error", None)
    state.pop("failed_role", None)
    atomic_write_json(summary_path, state, immutable=False)
    for role in ROLES:
        pool_path = _coordinator_root(output, role, run_id) / "pool.json"
        try:
            result = run_formal_role_evolution(
                role=role,
                run_id=run_id,
                pool_path=pool_path,
                canonical_evaluation_summary_path=canonical_evaluation_summary_path,
                canonical_split_path=canonical_split_path,
                static_split_path=static_split_path,
                rotating_split_path=rotating_split_path,
                frozen_s2_path=frozen_s2_path,
                output_root=output,
                completion_client=completion_client,
                model_identity=model_identity,
                embedding_client=embedding_client,
                embedding_identity=embedding_identity,
                max_workers=max_workers,
                require_bubblewrap=require_bubblewrap,
                progress=progress,
            )
        except BaseException as exc:
            state["status"] = "interrupted"
            state["failed_role"] = role
            state["error"] = f"{type(exc).__name__}: {exc}"
            state["updated_at"] = _utc_now()
            atomic_write_json(summary_path, state, immutable=False)
            raise
        state["roles"][role] = {
            "status": result.get("status"),
            "winner_id": result.get("winner_id"),
            "coordinator_path": str(
                _coordinator_root(output, role, run_id) / "coordinator.json"
            ),
            "pool_path": str(pool_path),
        }
        state["updated_at"] = _utc_now()
        atomic_write_json(summary_path, state, immutable=False)
    state["status"] = "complete"
    state["completed_at"] = _utc_now()
    state["updated_at"] = state["completed_at"]
    atomic_write_json(summary_path, state, immutable=False)
    return state
