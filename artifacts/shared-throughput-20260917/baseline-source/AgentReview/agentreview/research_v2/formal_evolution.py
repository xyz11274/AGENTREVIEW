"""Resumable role-local coordination over explicit Research v2 stage artifacts."""

from __future__ import annotations

import fcntl
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .constants import ROLES
from .evaluation import run_internal_evaluation
from .evolution import (
    MAX_MUTATION_ATTEMPTS,
    EvolutionPool,
    commit_candidate,
    initialize_pool,
    propose_candidate,
    validate_pool_entry_snapshot,
    build_evaluation_receipt,
    validate_pool_evaluation_receipts,
    validate_program,
    _load_or_call_reflector,
    _write_program,
)
from .failure_policy import (
    EvolutionPaused, EvolutionBudgetExhausted, FailureController, FailureLimits,
    controller_for_run, failure_detail, validate_promotion_policy,
)
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient, complete_with_response_format
from .memory import SEED_NAMES, build_memory_snapshot, seed_program_path
from .models import Paper
from .program_checks import program_capability_manifest
from .review import run_specialist_review
from .s2 import FrozenS2Store

FORMAL_ROLE_EVOLUTION_PROTOCOL = "research-v2-formal-role-evolution-v1"
FORMAL_ATTEMPT_WORK_PROTOCOL = "research-v2-formal-attempt-work-v1"
FORMAL_WINNER_PROTOCOL = "research-v2-formal-role-winner-v1"
FORMAL_RUN_SPEC_PROTOCOL = "research-v2-formal-role-run-spec-v1"
FORMAL_ALL_ROLES_PROTOCOL = "research-v2-formal-all-roles-run-v1"
FORMAL_CANDIDATE_FAILURE_PROTOCOL = "research-v2-formal-candidate-failure-v1"
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
        "status": "complete",
        "task_count": CANONICAL_EVALUATION_TASKS,
        "failed": 0,
    }
    supported_versions = {"research-v2-internal-evaluation-batch-v1", "research-v2-internal-evaluation-batch-v2"}
    if value.get("schema_version") not in supported_versions or {name: value.get(name) for name in expected} != expected:
        raise ValueError(
            "Formal Evolution requires a complete 625/625 canonical Internal "
            "Evaluation summary with zero failures"
        )
    completed = int(value.get("completed") or 0) + int(value.get("resumed") or 0)
    if completed != CANONICAL_EVALUATION_TASKS:
        raise ValueError("Canonical Internal Evaluation completion count differs from 625")
    if value["schema_version"] == "research-v2-internal-evaluation-batch-v2":
        tasks = value.get("task_results", [])
        identities = {(item.get("paper_id"), item.get("role")) for item in tasks}
        if len(tasks) != CANONICAL_EVALUATION_TASKS or len(identities) != CANONICAL_EVALUATION_TASKS or any(
            item.get("status") not in {"completed", "resumed"} for item in tasks
        ):
            raise ValueError("Canonical Evaluation requires a complete successful task manifest")
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


def _evaluation_metadata(static_split_path: str | Path) -> dict[str, Any]:
    from .evaluation import INTERNAL_EVALUATOR_PROMPT_VERSION
    return {
        "scoring_protocol": INTERNAL_EVALUATOR_PROMPT_VERSION,
        "scoring_implementation_sha256": file_sha256(Path(__file__).with_name("evaluation.py")),
        "static_split_sha256": file_sha256(static_split_path),
    }


def _epoch_run_id(run_id: str, epoch: int) -> str:
    return f"{run_id}-e{epoch:03d}" if epoch else run_id


def _effective_attempt(root: Path, attempt: int) -> tuple[Path, dict[str, Any]]:
    original = root / f"attempt-{attempt:03d}.json"
    repaired = root / f"attempt-{attempt:03d}.runtime-repair.json"
    if repaired.is_file():
        value = read_json(repaired)
        if value.get("status") == "proposed":
            if file_sha256(value["program_path"]) != value["program_sha256"]:
                raise ValueError("Repaired candidate Program requires its recorded hash")
            return repaired, value
    return original, read_json(original)


def _repair_runtime_candidate(
    *, root: Path, attempt: int, error: Exception, phase: str,
    controller: FailureController, completion_client: CompletionClient,
    completion_identity: dict[str, Any], require_bubblewrap: bool,
) -> bool:
    """Create one complete corrected Program, preserving every prior revision."""
    source, outcome = _effective_attempt(root, attempt)
    target = root / f"attempt-{attempt:03d}.runtime-repair.json"
    if target.is_file():
        return False
    candidate_id = outcome["candidate_id"]
    role = outcome["role"]
    if controller.reserve_repair(
        operation_id=f"{role}:{candidate_id}:runtime-repair",
        candidate_id=f"{role}:{candidate_id}",
    ) is False:
        return False
    code = Path(outcome["program_path"]).read_text(encoding="utf-8")
    request_path = root / f"attempt-{attempt:03d}.runtime-repair-request.json"
    if request_path.is_file():
        request = read_json(request_path)
        if request.get("source_sha256") != file_sha256(source) or request.get("request_id") != value_sha256({
            key: value for key, value in request.items() if key != "request_id"
        }):
            raise ValueError("Runtime repair requires its original request and candidate source")
    else:
        request = {
            "source_path": str(source), "source_sha256": file_sha256(source),
            "phase": phase, "error": f"{type(error).__name__}: {error}",
            "messages": [{"role": "user", "content": (
                "Repair this MemoryProgram using the execution error below. Preserve its public "
                "interface and permitted capabilities. Return the complete corrected Python source.\n\n"
                f"PHASE: {phase}\nERROR: {type(error).__name__}: {error}\n\nPROGRAM:\n{code}"
            )}],
        }
        request["request_id"] = value_sha256(request)
        atomic_write_json(request_path, request)
    raw = _load_or_call_reflector(
        path=root / f"attempt-{attempt:03d}.runtime-repair.raw.json",
        messages=request["messages"], component="reflector_repair", completion_client=completion_client,
        completion_identity=completion_identity,
        plan_sha256=request["request_id"],
    )
    corrected = raw.strip()
    if corrected.startswith("```python\n") and corrected.endswith("```"):
        corrected = corrected[len("```python\n"):-3].strip()
    elif corrected.startswith("```\n") and corrected.endswith("```"):
        corrected = corrected[4:-3].strip()
    program = Path(outcome["program_path"]).parent / "runtime-repair-001" / "program.py"
    _write_program(program, corrected + "\n")
    errors = validate_program(program, require_bubblewrap=require_bubblewrap)
    repaired = {
        **outcome, "status": "failed" if errors else "proposed",
        "program_path": str(program), "program_sha256": file_sha256(program),
        "capability_manifest": program_capability_manifest(path=program) if errors == [] else {},
        "runtime_repair": {"source_path": str(source), "source_sha256": file_sha256(source),
                           "phase": request["phase"], "error": request["error"], "revision": 1},
        "errors": errors,
    }
    atomic_write_json(target, repaired)
    return repaired["status"] == "proposed"


def _pool(path: str | Path, role: str) -> tuple[Path, EvolutionPool]:
    source = Path(path).expanduser().resolve()
    value = EvolutionPool.from_dict(read_json(source))
    if value.role != role:
        raise ValueError("Formal Evolution pool role mismatch")
    if not value.entries:
        raise ValueError("Formal Evolution pool is empty")
    for entry in value.entries:
        validate_pool_entry_snapshot(entry)
    validate_pool_evaluation_receipts(value)
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
    controller_for_run(output_root, run_id).require_running()
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
    build_evaluation_receipt(
        role=work["role"], snapshot_path=parent["memory_snapshot_path"],
        review_paths=review_paths, evaluation_paths=evaluation_paths,
    )


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
        "failure_policy": controller_for_run(root.parents[2], run_id).value,
    }


def _write_state(root: Path, state: dict[str, Any]) -> None:
    controller = controller_for_run(root.parents[2], state["run_id"])
    state["failure_policy"] = controller.value
    state["failure_policy_path"] = str(controller.path)
    state["champion"] = controller.value["champions"].get(state["role"])
    if controller.value["status"] in {"paused", "reevaluating", "ended"}:
        state["status"] = controller.value["status"]
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
    controller = controller_for_run(root.parents[2], run_id)
    state["failure_policy"] = controller.value
    if controller.value["status"] in {"paused", "reevaluating", "ended"}:
        state["status"] = controller.value["status"]
        return
    try:
        controller.require_running()
    except EvolutionBudgetExhausted:
        state["status"] = "ended"
        return
    if attempt != MAX_MUTATION_ATTEMPTS:
        return
    expected = {f"{index:03d}" for index in range(1, MAX_MUTATION_ATTEMPTS + 1)}
    attempts = state.get("attempts", {})
    if set(attempts) != expected or any(
        item.get("status") not in {"committed", "failed", "candidate_failed"}
        for item in attempts.values()
    ):
        raise ValueError("Winner publication requires the complete terminal attempt set")
    validate_pool_evaluation_receipts(pool, expected_epoch=controller.value["evaluation_epoch"])
    winner = pool.best(require_snapshot=True)
    winner_payload = {
        "protocol": FORMAL_WINNER_PROTOCOL,
        "role": role,
        "run_id": run_id,
        "pool_path": str(pool_source),
        "pool_sha256": file_sha256(pool_source),
        "winner": asdict(winner),
        "evaluation_epoch": controller.value["evaluation_epoch"],
    }
    winner_payload["winner_id"] = value_sha256(winner_payload)
    epoch = controller.value["evaluation_epoch"]
    atomic_write_json(root / f"winner.e{epoch:03d}.json", winner_payload)
    atomic_write_json(root / "winner.json", winner_payload, immutable=False)
    state["status"] = "complete"
    state["winner_id"] = winner_payload["winner_id"]
    controller.preserve_champion(role, asdict(winner))


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
        active_attempt_path, outcome = _effective_attempt(root, attempt)
        if outcome.get("status") != "proposed":
            raise ValueError("Candidate failure requires a successful proposal")
        if any(
            entry.candidate_id == outcome.get("candidate_id")
            for entry in pool.entries
        ):
            raise ValueError("A committed pool candidate cannot be marked failed")
        failure_path = _candidate_failure_path(root, attempt)
        if failure_path.is_file():
            failure = _load_candidate_failure(
                failure_path,
                work=work,
                outcome=outcome,
            )
        else:
            if file_sha256(pool_source) != work["pool_before"]["sha256"]:
                raise ValueError("Formal Evolution pool changed before candidate failure")
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
        controller = controller_for_run(output_root, run_id)
        controller.candidate_failed(
            operation_id=f"{role}:{outcome['candidate_id']}:failed",
            candidate_id=outcome["candidate_id"],
            detail=failure_detail(error, phase=phase),
        )
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
    controller = controller_for_run(output_root, run_id)
    controller.require_running()
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
        elif attempt_path.is_file():
            active_attempt_path, outcome = _effective_attempt(root, attempt)
            if outcome.get("status") == "proposed" and file_sha256(outcome["program_path"]) == outcome["program_sha256"]:
                _validate_feedback_artifacts(work=work, review_paths=rotating_review_paths, evaluation_paths=rotating_evaluation_paths)
            elif outcome.get("status") != "failed":
                raise ValueError("Candidate proposal requires its frozen Program identity")
        else:
            if file_sha256(pool_source) != work["pool_before"]["sha256"]:
                raise ValueError("Formal Evolution pool changed after attempt materialization")
            _validate_feedback_artifacts(work=work, review_paths=rotating_review_paths, evaluation_paths=rotating_evaluation_paths)
            outcome = propose_candidate(
                role=role, pool=pool, evaluation_paths=rotating_evaluation_paths,
                review_paths=rotating_review_paths, attempt=attempt, run_id=run_id,
                output_root=output_root, completion_client=completion_client,
                completion_identity=completion_identity, require_bubblewrap=require_bubblewrap,
                evaluation_epoch=controller.value["evaluation_epoch"],
                selected_parent_id=work["parent"]["candidate_id"],
                before_repair=lambda candidate_id: controller.reserve_repair(
                    operation_id=f"{role}:{candidate_id}:proposal-repair",
                    candidate_id=f"{role}:{candidate_id}",
                ),
            )
            if outcome.get("parent_id") != work["parent"]["candidate_id"]:
                raise ValueError("Reflector attempt selected a different frozen parent")
        key = f"{attempt:03d}"
        if outcome.get("status") == "failed":
            controller.candidate_failed(
                operation_id=f"{role}:{outcome.get('candidate_id')}:failed",
                candidate_id=str(outcome.get("candidate_id")),
                detail={"phase": "candidate_proposal", "errors": outcome.get("errors", [])},
            )
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
        controller.require_running()
        active_attempt_path, outcome = _effective_attempt(root, attempt)
        entry = commit_candidate(
            pool=pool,
            attempt_path=active_attempt_path,
            static_review_paths=candidate_static_review_paths,
            static_evaluation_paths=candidate_static_evaluation_paths,
            pool_path=pool_source,
            memory_snapshot_path=candidate_memory_snapshot_path,
            evaluation_epoch=controller.value["evaluation_epoch"],
            evaluation_metadata=_evaluation_metadata(work["static_split"]["path"]),
        )
        controller.preserve_champion(role, asdict(pool.best(require_snapshot=True)))
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


class RecordedStageFailure(RuntimeError):
    """Replay a classified failure while preserving the original attempt budget."""

    def __init__(self, record: dict[str, Any]):
        super().__init__(record["error"])
        self.failure_category = record["category"]
        self.failure_record = record


def _bounded_batch(items: list[Any], run: Callable[[Any], Path], max_workers: int) -> list[Path]:
    """Keep one bounded window active and stop submitting at the first failure."""
    if max_workers < 1:
        raise ValueError("Batch workers must be positive")
    results: dict[int, Path] = {}
    iterator = iter(enumerate(items))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {}
        for _ in range(min(max_workers, len(items))):
            index, item = next(iterator)
            pending[executor.submit(run, item)] = index
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Inspect the entire completed window before submitting more work.
                for future in done:
                    results[pending.pop(future)] = future.result()
                for _ in done:
                    next_item = next(iterator, None)
                    if next_item is None:
                        break
                    index, item = next_item
                    pending[executor.submit(run, item)] = index
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return [results[index] for index in range(len(items))]


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
) -> list[Path]:
    snapshot_id = read_json(snapshot_path)["snapshot_id"]
    progress_lock = threading.Lock()
    finished = 0

    def run(record: dict[str, Any]) -> Path:
        nonlocal finished
        paper_id = str(record["paper_id"])
        final_path = output_root / "reviews" / run_id / paper_id / f"{role}.json"
        failure_path = final_path.with_name(f"{role}.stage-failure.json")
        identity = {"run_id": run_id, "paper_id": paper_id, "role": role,
                    "program_version": program_version, "snapshot_id": snapshot_id}
        disposition = "resumed" if final_path.is_file() else "completed"
        try:
            if failure_path.is_file():
                failure = read_json(failure_path)
                if failure.get("identity") != identity:
                    raise ValueError("Review failure artifact identity mismatch")
                raise RecordedStageFailure(failure)
            try:
                run_specialist_review(
                    paper_path=record["path"], role=role, program_path=program_path,
                    snapshot_path=snapshot_path, run_id=run_id, output_root=output_root,
                    completion_client=completion_client, model_identity=model_identity,
                    program_version=program_version, frozen_s2_path=frozen_s2_path,
                    s2_intervention=FORMAL_AUTONOMOUS_S2_INTERVENTION,
                    embedding_client=embedding_client, embedding_identity=embedding_identity,
                    require_bubblewrap=require_bubblewrap,
                    _allow_legacy_missing_require_bubblewrap=(require_bubblewrap is True),
                )
            except Exception as exc:
                atomic_write_json(failure_path, {
                    "protocol": "research-v2-stage-failure-v1", "status": "failed",
                    "identity": identity, **failure_detail(exc, phase="candidate_review"),
                })
                raise
            return final_path
        except BaseException:
            disposition = "failed"
            raise
        finally:
            if progress is not None:
                with progress_lock:
                    finished += 1
                    current = finished
                progress({"event": "formal_review_progress", "run_id": run_id,
                          "role": role, "paper_id": paper_id, "disposition": disposition,
                          "finished": current, "total": len(records)})

    return _bounded_batch(records, run, max_workers)


def _run_evaluation_batch(
    *, records: list[dict[str, Any]], review_paths: list[str | Path], run_id: str,
    output_root: Path, completion_client: CompletionClient,
    evaluator_identity: dict[str, Any], max_workers: int,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    if len(records) != len(review_paths):
        raise ValueError("Formal Evaluation requires aligned paper and Review sets")
    progress_lock = threading.Lock()
    stop = threading.Event()
    finished = 0

    def run(item: tuple[dict[str, Any], str | Path]) -> Path:
        nonlocal finished
        record, review_path = item
        review = read_json(review_path)
        role = str(review["role"])
        final_path = output_root / "evaluations" / run_id / str(record["paper_id"]) / f"{role}.json"
        disposition = "resumed" if final_path.is_file() else "completed"
        try:
            if review.get("status", "complete") != "complete" or review.get("paper_id") != record["paper_id"]:
                raise ValueError("Formal Evaluation requires a complete matching Review")
            run_internal_evaluation(
                paper_path=record["path"], review_path=review_path, run_id=run_id,
                output_root=output_root, completion_client=completion_client,
                evaluator_identity=evaluator_identity,
                stop_requested=stop.is_set,
            )
            return final_path
        except BaseException:
            stop.set()
            disposition = "failed"
            raise
        finally:
            if progress is not None:
                with progress_lock:
                    finished += 1
                    current = finished
                progress({"event": "formal_evaluation_progress", "run_id": run_id,
                          "role": role, "paper_id": record["paper_id"], "disposition": disposition,
                          "finished": current, "total": len(records)})

    return _bounded_batch(list(zip(records, review_paths, strict=True)), run, max_workers)


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
    evaluation_epoch: int | None = None,
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
    spec_name = "run-spec.json" if evaluation_epoch is None else f"run-spec.e{evaluation_epoch:03d}.json"
    atomic_write_json(root / spec_name, run_spec)
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


def _run_formal_role_evolution(
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
    controller = controller_for_run(output, run_id)
    controller.require_running()
    epoch = controller.value["evaluation_epoch"]
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
                validate_pool_evaluation_receipts(completed_pool, expected_epoch=epoch)
                validate_promotion_policy(existing_state.get("failure_policy", {}))
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
                version = _epoch_run_id(f"{run_id}-{role.lower()}-seed-{seed_name}", epoch)
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
            seed_pool = initialize_pool(
                role=role,
                seed_static_evaluations=seed_evaluations,
                seed_static_reviews=seed_reviews,
                seed_memory_snapshots=seed_snapshots,
                pool_path=pool_source,
                evaluation_epoch=epoch,
                evaluation_metadata=_evaluation_metadata(static_split_path),
                failure_policy_path=controller.path,
            )
            controller.preserve_champion(role, asdict(seed_pool.best(require_snapshot=True)))
            emit("formal_seed_pool_complete", pool_path=str(pool_source))
        for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1):
            controller.require_running()
            state = _state(root, role=role, run_id=run_id)
            if state.get("attempts", {}).get(f"{attempt:03d}", {}).get("status") in {"committed", "failed", "candidate_failed"}:
                continue
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
                run_id=_epoch_run_id(work["artifact_run_ids"]["rotating_reviews"], epoch),
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
            rotating_evaluations = _run_evaluation_batch(
                records=rotating_records,
                review_paths=rotating_reviews,
                run_id=_epoch_run_id(work["artifact_run_ids"]["rotating_evaluations"], epoch),
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
            while True:
                active_path, active = _effective_attempt(root, attempt)
                revision = int(active.get("runtime_repair", {}).get("revision", 0))
                candidate_program = active["program_path"]
                candidate_version = _epoch_run_id(f"{run_id}-{role.lower()}-{candidate_id}-r{revision}", epoch)
                candidate_snapshot = output / "memories" / role / candidate_version / "snapshot.json"
                phase = "candidate_memory_build"
                try:
                    build_memory_snapshot(
                        role=role, program_path=candidate_program, program_version=candidate_version,
                        review_paths=canonical_reviews, evaluation_paths=canonical_evaluations,
                        output_path=candidate_snapshot, seed_kind=str(attempt_state["seed_kind"]),
                        embedding_client=embedding_client, embedding_identity=embedding_identity,
                        completion_client=limited_client if attempt_state["seed_kind"] == "experience_learner" else None,
                        completion_identity=model_identity if attempt_state["seed_kind"] == "experience_learner" else None,
                        require_bubblewrap=require_bubblewrap,
                    )
                    phase = "candidate_static_review"
                    static_reviews = _run_review_batch(
                        records=static_records, role=role, program_path=candidate_program,
                        snapshot_path=candidate_snapshot, program_version=candidate_version,
                        run_id=_epoch_run_id(work["artifact_run_ids"]["candidate_static_reviews"] + f"-r{revision}", epoch),
                        output_root=output, completion_client=limited_client, model_identity=model_identity,
                        frozen_s2_path=frozen_s2_path, embedding_client=embedding_client,
                        embedding_identity=embedding_identity, max_workers=max_workers,
                        require_bubblewrap=require_bubblewrap, progress=progress,
                    )
                    phase = "candidate_static_evaluation"
                    static_evaluations = _run_evaluation_batch(
                        records=static_records, review_paths=static_reviews,
                        run_id=_epoch_run_id(work["artifact_run_ids"]["candidate_static_evaluations"] + f"-r{revision}", epoch),
                        output_root=output, completion_client=limited_client, evaluator_identity=model_identity,
                        max_workers=max_workers, progress=progress,
                    )
                    committed = advance_role_attempt(
                        work_path=root / f"attempt-{attempt:03d}.work.json", pool_path=pool_source,
                        output_root=output, completion_client=limited_client, completion_identity=model_identity,
                        rotating_review_paths=rotating_reviews, rotating_evaluation_paths=rotating_evaluations,
                        candidate_memory_snapshot_path=candidate_snapshot,
                        candidate_static_review_paths=static_reviews,
                        candidate_static_evaluation_paths=static_evaluations,
                        require_bubblewrap=require_bubblewrap,
                    )
                    emit("formal_attempt_complete", attempt=attempt, status=committed["status"], candidate_id=candidate_id)
                    break
                except (EvolutionPaused, EvolutionBudgetExhausted):
                    raise
                except Exception as exc:
                    if failure_detail(exc, phase=phase)["category"] == "candidate":
                        repaired = _repair_runtime_candidate(
                            root=root, attempt=attempt, error=exc, phase=phase, controller=controller,
                            completion_client=limited_client, completion_identity=model_identity,
                            require_bubblewrap=require_bubblewrap,
                        )
                        if repaired:
                            continue
                        failed = record_candidate_failure(
                            work_path=root / f"attempt-{attempt:03d}.work.json", pool_path=pool_source,
                            output_root=output, phase=phase, error=exc,
                        )
                        emit("formal_attempt_complete", attempt=attempt, status=failed["status"], phase=phase)
                        break
                    raise
        result = _state(root, role=role, run_id=run_id)
        _, final_pool = _pool(pool_source, role)
        _record_winner_if_complete(root=root, pool_source=pool_source, pool=final_pool, role=role,
                                   run_id=run_id, attempt=MAX_MUTATION_ATTEMPTS, state=result)
        _write_state(root, result)
        emit(
            "formal_role_complete",
            resumed=False,
            winner_id=result.get("winner_id"),
        )
        return result


def run_formal_role_evolution(
    *, role: str, run_id: str, pool_path: str | Path,
    canonical_evaluation_summary_path: str | Path, canonical_split_path: str | Path,
    static_split_path: str | Path, rotating_split_path: str | Path,
    frozen_s2_path: str | Path, output_root: str | Path,
    completion_client: CompletionClient, model_identity: dict[str, Any],
    embedding_client: Any, embedding_identity: dict[str, Any], max_workers: int = 8,
    require_bubblewrap: bool = True, progress: ProgressCallback | None = None,
    failure_limits: FailureLimits | None = None,
    repair_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run within a persistent round gate, retaining the Champion during repair."""
    from .formal_recovery import affected_population, population_ids, recover_formal_round

    output = Path(output_root).resolve()
    root = _coordinator_root(output, role, run_id)
    controller = FailureController(output / "runs" / run_id / "failure-policy.json", limits=failure_limits)
    arguments = dict(
        run_id=run_id, canonical_evaluation_summary_path=canonical_evaluation_summary_path,
        canonical_split_path=canonical_split_path, static_split_path=static_split_path,
        rotating_split_path=rotating_split_path, frozen_s2_path=frozen_s2_path,
        output_root=output, completion_client=completion_client, model_identity=model_identity,
        embedding_client=embedding_client, embedding_identity=embedding_identity,
        max_workers=max_workers, require_bubblewrap=require_bubblewrap, progress=progress,
    )
    try:
        if controller.value["status"] == "reevaluating" and repair_evidence_path is None:
            repair_evidence_path = controller.value["repair_evidence"]["path"]
        if repair_evidence_path is not None and controller.value["status"] in {"paused", "reevaluating"}:
            recover_formal_round(**arguments, repair_evidence_path=repair_evidence_path)
        controller.require_running()
        return _run_formal_role_evolution(role=role, pool_path=pool_path, **arguments)
    except EvolutionBudgetExhausted:
        controller.end("budget_exhausted")
    except EvolutionPaused:
        pass
    except Exception as exc:
        detail = failure_detail(exc, phase=getattr(exc, "failure_phase", "formal_execution"))
        try:
            population = affected_population(output, run_id)
            discovered = population_ids(population)
        except Exception as capture_error:
            discovered = []
            detail["population_capture_error"] = f"{type(capture_error).__name__}: {capture_error}"
        # Reuse the original affected set during interrupted multi-pool recovery.
        affected = sorted(set(discovered) | set(controller_for_run(output, run_id).value["pending_candidates"]))
        controller.pause(operation_id=f"{role}:shared:{detail['fingerprint']}", detail=detail, affected=affected)
    for affected_role in ROLES:
        affected_root = _coordinator_root(output, affected_role, run_id)
        if affected_root == root or (affected_root / "coordinator.json").is_file():
            state = _state(affected_root, role=affected_role, run_id=run_id)
            _write_state(affected_root, state)
    result = _state(root, role=role, run_id=run_id)
    if progress is not None:
        progress({"event": "formal_role_stopped", "role": role, "status": result["status"]})
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
    failure_limits: FailureLimits | None = None,
    repair_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run all five roles serially while reusing one embedding/model client pair."""
    output = Path(output_root).expanduser().resolve()
    FailureController(output / "runs" / run_id / "failure-policy.json", limits=failure_limits)
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
                repair_evidence_path=repair_evidence_path,
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
        if result.get("status") != "complete":
            state["status"] = result.get("status", "paused")
            state["failed_role"] = role
            atomic_write_json(summary_path, state, immutable=False)
            return state
        atomic_write_json(summary_path, state, immutable=False)
    state["status"] = "complete"
    state["completed_at"] = _utc_now()
    state["updated_at"] = state["completed_at"]
    atomic_write_json(summary_path, state, immutable=False)
    return state
