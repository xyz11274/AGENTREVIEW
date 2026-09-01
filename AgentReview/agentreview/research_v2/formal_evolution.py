"""Resumable role-local coordination over explicit Research v2 stage artifacts."""

from __future__ import annotations

import fcntl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
from .llm import CompletionClient
from .memory import SEED_NAMES, build_memory_snapshot, seed_program_path
from .review import SpecialistReviewPaperFailure, run_specialist_review

FORMAL_ROLE_EVOLUTION_PROTOCOL = "research-v2-formal-role-evolution-v1"
FORMAL_ATTEMPT_WORK_PROTOCOL = "research-v2-formal-attempt-work-v1"
FORMAL_WINNER_PROTOCOL = "research-v2-formal-role-winner-v1"
FORMAL_STATIC_SIZE = 25
FORMAL_ROTATING_POOL_SIZE = 125
FORMAL_ROTATING_BATCH_SIZE = 5
CANONICAL_EVALUATION_TASKS = 625
FORMAL_AUTONOMOUS_S2_INTERVENTION = {
    "protocol": "s2-intervention-v1",
    "mode": "program_autonomous",
    "label": "formal-primary-v1",
}


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


def _attempt_terminal(pool: EvolutionPool, attempt_artifact: dict[str, Any]) -> bool:
    if attempt_artifact.get("status") == "failed":
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
            if not previous_path.is_file() or not _attempt_terminal(
                pool, read_json(previous_path)
            ):
                raise ValueError(
                    f"Formal Evolution attempt {attempt} requires terminal attempt {previous}"
                )
        work_path = root / f"attempt-{attempt:03d}.work.json"
        if work_path.is_file():
            value = read_json(work_path)
            if value.get("protocol") != FORMAL_ATTEMPT_WORK_PROTOCOL:
                raise ValueError("Formal Evolution work artifact protocol mismatch")
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
    role = str(work.get("role") or "")
    run_id = str(work.get("run_id") or "")
    attempt = int(work.get("attempt") or 0)
    root = _coordinator_root(output_root, role, run_id)
    with _exclusive_lock(root / "coordinator.lock"):
        pool_source, pool = _pool(pool_path, role)
        state = _state(root, role=role, run_id=run_id)
        attempt_path = root / f"attempt-{attempt:03d}.json"
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
    ) -> str:
        with self._semaphore:
            return self.client.complete(
                messages,
                max_tokens=max_tokens,
                component=component,
            )


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
) -> list[Path]:
    snapshot = read_json(snapshot_path)
    snapshot_id = str(snapshot.get("snapshot_id") or "")

    def run(record: dict[str, Any]) -> Path:
        paper_id = str(record["paper_id"])
        final_path = output_root / "reviews" / run_id / paper_id / f"{role}.json"
        failure_path = final_path.with_name(f"{role}.paper_failure.json")
        if failure_path.is_file() and not final_path.is_file():
            return failure_path
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
            )
            return final_path
        except SpecialistReviewPaperFailure as exc:
            return _paper_failure_review(
                output_root=output_root,
                run_id=run_id,
                paper_id=paper_id,
                role=role,
                program_version=program_version,
                snapshot_id=snapshot_id,
                error=exc,
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
) -> list[Path]:
    if len(records) != len(review_paths):
        raise ValueError("Formal Evaluation batch inputs are not aligned")

    def run(item: tuple[dict[str, Any], str | Path]) -> Path:
        record, review_path = item
        review = read_json(review_path)
        if review.get("status") == "paper_failure":
            return _paper_failure_evaluation(
                output_root=output_root,
                run_id=run_id,
                review_path=review_path,
            )
        role = str(review["role"])
        final_path = (
            output_root
            / "evaluations"
            / run_id
            / str(record["paper_id"])
            / f"{role}.json"
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
            return failure_path

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
    with _exclusive_lock(output / "runs" / "formal-evolution-runner.lock"):
        canonical = validate_canonical_evaluation_summary(
            canonical_evaluation_summary_path
        )
        canonical_reviews, canonical_evaluations = _canonical_sources(
            output_root=output,
            canonical_split_path=canonical_split_path,
            canonical_summary=canonical,
            role=role,
        )
        _, static_split = _records(
            static_split_path,
            expected_count=FORMAL_STATIC_SIZE,
            label="static",
        )
        static_records = list(static_split["records"])
        if not pool_source.is_file():
            seed_snapshots = {}
            seed_reviews = {}
            seed_evaluations = {}
            for seed_name in SEED_NAMES:
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
                    completion_client=(
                        limited_client if seed_name == "experience_learner" else None
                    ),
                    require_bubblewrap=require_bubblewrap,
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
                )
                evaluation_paths = _run_evaluation_batch(
                    records=static_records,
                    review_paths=review_paths,
                    run_id=f"{version}-static-eval",
                    output_root=output,
                    completion_client=limited_client,
                    evaluator_identity=model_identity,
                    max_workers=max_workers,
                )
                seed_snapshots[seed_name] = snapshot_path
                seed_reviews[seed_name] = review_paths
                seed_evaluations[seed_name] = evaluation_paths
            initialize_pool(
                role=role,
                seed_static_evaluations=seed_evaluations,
                seed_static_reviews=seed_reviews,
                seed_memory_snapshots=seed_snapshots,
                pool_path=pool_source,
            )
        for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1):
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
            )
            rotating_evaluations = _run_evaluation_batch(
                records=rotating_records,
                review_paths=rotating_reviews,
                run_id=work["artifact_run_ids"]["rotating_evaluations"],
                output_root=output,
                completion_client=limited_client,
                evaluator_identity=model_identity,
                max_workers=max_workers,
            )
            progress = advance_role_attempt(
                work_path=root / f"attempt-{attempt:03d}.work.json",
                pool_path=pool_source,
                output_root=output,
                completion_client=limited_client,
                completion_identity=model_identity,
                rotating_review_paths=rotating_reviews,
                rotating_evaluation_paths=rotating_evaluations,
                require_bubblewrap=require_bubblewrap,
            )
            if progress["status"] in {"failed", "committed"}:
                continue
            candidate_id = str(progress["candidate_id"])
            candidate_program = str(progress["program_path"])
            candidate_version = f"{run_id}-{role.lower()}-{candidate_id}"
            candidate_snapshot = (
                output / "memories" / role / candidate_version / "snapshot.json"
            )
            build_memory_snapshot(
                role=role,
                program_path=candidate_program,
                program_version=candidate_version,
                review_paths=canonical_reviews,
                evaluation_paths=canonical_evaluations,
                output_path=candidate_snapshot,
                seed_kind=str(progress["seed_kind"]),
                embedding_client=embedding_client,
                completion_client=(
                    limited_client
                    if progress["seed_kind"] == "experience_learner"
                    else None
                ),
                require_bubblewrap=require_bubblewrap,
            )
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
            )
            static_evaluations = _run_evaluation_batch(
                records=static_records,
                review_paths=static_reviews,
                run_id=work["artifact_run_ids"]["candidate_static_evaluations"],
                output_root=output,
                completion_client=limited_client,
                evaluator_identity=model_identity,
                max_workers=max_workers,
            )
            advance_role_attempt(
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
        return read_json(root / "coordinator.json")
