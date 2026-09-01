"""Resumable bounded runner for the canonical no-Memory Review corpus."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentreview.reviewer_identity import (
    SPECIALIST_REQUEST_TEMPLATE,
    load_reviewer_suite,
)

from .config import ResearchConfig
from .constants import (
    ROLES,
    SCHEMA_VERSION,
)
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient
from .memory import build_memory_snapshot, seed_program_path
from .models import Paper
from .review import (
    SpecialistReviewPaperFailure,
    _review_id,
    frozen_paper_context,
    run_specialist_review,
    specialist_generation_identity,
)
from .s2 import FrozenS2Store, format_s2_evidence

CANONICAL_PROGRAM_VERSION = "canonical-no-memory-v1"
DEFAULT_FORMAL_RUN_ID = "canonical-bootstrap-v1"
ProgressCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BootstrapInfrastructureFailure(RuntimeError):
    """A shared bootstrap dependency failed and remaining work was preserved."""


@contextmanager
def _exclusive_run_lock(run_path: Path):
    """Hold a non-blocking process lock for one run identity."""
    lock_path = run_path.with_name("bootstrap.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"bootstrap run is already active for {run_path.parent.name!r}"
            ) from exc
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class BootstrapCorpusOptions:
    """Operational choices for one canonical bootstrap run."""

    run_id: str = DEFAULT_FORMAL_RUN_ID
    limit: int = 0
    max_workers: int = 8
    s2_enabled: bool = True

    def validate(self, config: ResearchConfig) -> None:
        if not self.run_id.strip():
            raise ValueError("bootstrap run_id is required")
        if Path(self.run_id).name != self.run_id or self.run_id in {".", ".."}:
            raise ValueError("bootstrap run_id must be one path-safe name")
        if self.limit < 0:
            raise ValueError("bootstrap limit must be non-negative")
        if self.max_workers <= 0:
            raise ValueError("bootstrap max_workers must be positive")
        if self.run_id == DEFAULT_FORMAL_RUN_ID and (
            self.limit > 0 or not self.s2_enabled
        ):
            raise ValueError(
                "limited or no-S2 bootstrap smoke requires an explicit non-formal "
                "--run-id"
            )
        configured_limit = int(config.model.max_num_seqs)
        if self.max_workers > configured_limit:
            raise ValueError(
                "bootstrap max_workers exceeds the configured model max_num_seqs: "
                f"{self.max_workers} > {configured_limit}"
            )


@dataclass(frozen=True)
class _Task:
    index: int
    paper: Paper
    paper_path: Path
    role: str
    snapshot_path: Path
    snapshot_id: str


def _load_records(
    output_root: Path,
    limit: int,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Paper, Path]]]:
    split_path = output_root / "data" / "splits" / "canonical_review_corpus.json"
    split = read_json(split_path)
    if split.get("split") != "canonical_review_corpus":
        raise ValueError(f"Canonical split identity mismatch at {split_path}")
    records = split.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"Canonical split has no records at {split_path}")
    if split.get("count") != len(records):
        raise ValueError(f"Canonical split count mismatch at {split_path}")
    if split.get("split_sha256") != value_sha256(records):
        raise ValueError(f"Canonical split hash mismatch at {split_path}")
    selected = records[:limit] if limit > 0 else records
    loaded: list[tuple[dict[str, Any], Paper, Path]] = []
    paper_ids: set[str] = set()
    for record in selected:
        if not isinstance(record, dict):
            raise ValueError("Canonical split record must be an object")
        record_id = str(record.get("paper_id") or "")
        paper_path = Path(str(record.get("path") or "")).expanduser().resolve()
        paper_value = read_json(paper_path)
        if not isinstance(paper_value, dict):
            raise ValueError(f"Canonical Paper must be an object at {paper_path}")
        paper_asset_id = str(paper_value.get("paper_asset_id") or "")
        record_asset_id = str(record.get("paper_asset_id") or "")
        computed_asset_id = value_sha256(
            {
                name: value
                for name, value in paper_value.items()
                if name != "paper_asset_id"
            }
        )
        if (
            not paper_asset_id
            or record_asset_id != paper_asset_id
            or computed_asset_id != paper_asset_id
        ):
            raise ValueError(
                f"Canonical Paper asset identity mismatch at {paper_path}: "
                f"record={record_asset_id!r}, artifact={paper_asset_id!r}, "
                f"computed={computed_asset_id!r}"
            )
        paper = Paper.from_dict(paper_value)
        if paper.paper_id != record_id:
            raise ValueError(
                f"Canonical Paper identity mismatch: record={record_id!r}, "
                f"artifact={paper.paper_id!r}"
            )
        if paper.paper_id in paper_ids:
            raise ValueError(f"Canonical split has duplicate paper_id: {paper.paper_id}")
        paper_ids.add(paper.paper_id)
        loaded.append((record, paper, paper_path))
    return split, loaded


def _verify_snapshot(
    path: Path,
    *,
    role: str,
    program_path: Path,
    program_sha256: str,
) -> dict[str, Any]:
    value = read_json(path)
    expected = {
        "role": role,
        "program_version": CANONICAL_PROGRAM_VERSION,
        "program_path": str(program_path),
        "program_sha256": program_sha256,
        "seed_kind": "no_memory",
        "source_review_ids": [],
        "source_evaluation_ids": [],
        "items": [],
    }
    observed = {name: value.get(name) for name in expected}
    if observed != expected:
        raise ValueError(
            f"Canonical snapshot identity mismatch at {path}: "
            f"observed={observed!r}, expected={expected!r}"
        )
    snapshot_id = value.get("snapshot_id")
    unhashed = {name: item for name, item in value.items() if name != "snapshot_id"}
    if not isinstance(snapshot_id, str) or snapshot_id != value_sha256(unhashed):
        raise ValueError(f"Canonical snapshot hash mismatch at {path}")
    if not isinstance(value.get("snapshot"), dict):
        raise ValueError(f"Canonical snapshot state is invalid at {path}")
    return value


def _ensure_snapshots(
    *,
    output_root: Path,
    program_path: Path,
    program_sha256: str,
    require_bubblewrap: bool,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    snapshots: dict[str, tuple[Path, dict[str, Any]]] = {}
    for role in ROLES:
        path = (
            output_root
            / "memories"
            / role
            / CANONICAL_PROGRAM_VERSION
            / "snapshot.json"
        )
        if path.is_file():
            value = _verify_snapshot(
                path,
                role=role,
                program_path=program_path,
                program_sha256=program_sha256,
            )
        else:
            build_memory_snapshot(
                role=role,
                program_path=program_path,
                program_version=CANONICAL_PROGRAM_VERSION,
                review_paths=[],
                evaluation_paths=[],
                output_path=path,
                seed_kind="no_memory",
                embedding_client=None,
                completion_client=None,
                require_bubblewrap=require_bubblewrap,
            )
            value = _verify_snapshot(
                path,
                role=role,
                program_path=program_path,
                program_sha256=program_sha256,
            )
        snapshots[role] = (path, value)
    return snapshots


def _expected_memory(
    *,
    task: _Task,
    program_sha256: str,
) -> dict[str, Any]:
    query_text = "\n".join(
        value
        for value in (task.paper.title.strip(), task.paper.abstract.strip())
        if value
    )
    return {
        "query": {
            "query_text": query_text,
            "reviewer_dimension": task.role,
        },
        "context": "",
        "logs": [{"event": "retrieval_hits", "payload": []}],
        "snapshot_id": task.snapshot_id,
        "program_version": CANONICAL_PROGRAM_VERSION,
        "program_sha256": program_sha256,
        "seed_kind": "no_memory",
    }


def _expected_s2_by_paper(
    *,
    loaded_records: list[tuple[dict[str, Any], Paper, Path]],
    s2_enabled: bool,
    s2_path: Path | None,
) -> dict[str, dict[str, Any]]:
    if not s2_enabled:
        return {
            paper.paper_id: {"enabled": False, "result_ids": [], "evidence": []}
            for _, paper, _ in loaded_records
        }
    assert s2_path is not None
    store = FrozenS2Store(s2_path)
    expected = {}
    for _, paper, _ in loaded_records:
        evidence, provenance = store.retrieve(
            paper_id=paper.paper_id,
            target_title=paper.title,
            limit=5,
        )
        expected[paper.paper_id] = {
            **provenance,
            "enabled": True,
            "evidence": evidence,
        }
    return expected


def _request_messages(
    *,
    task: _Task,
    memory: dict[str, Any],
    s2: dict[str, Any],
) -> list[dict[str, str]]:
    user_parts = [
        SPECIALIST_REQUEST_TEMPLATE.format(
            task_id=task.role,
            paper_context=frozen_paper_context(task.paper, task.role),
        ),
        "[REVIEW EXPERIENCE]\n" + (str(memory.get("context") or "") or "(none)"),
        "[FROZEN S2 EVIDENCE]\n"
        + (format_s2_evidence(list(s2.get("evidence") or [])) or "(disabled or empty)"),
    ]
    return [
        {
            "role": "system",
            "content": load_reviewer_suite().task(task.role).system_prompt,
        },
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _verify_saved_review(
    *,
    task: _Task,
    output_root: Path,
    run_id: str,
    model_identity: dict[str, Any],
    generation_identity: dict[str, Any],
    program_sha256: str,
    expected_s2: dict[str, Any],
) -> dict[str, Any]:
    review_path = output_root / "reviews" / run_id / task.paper.paper_id / f"{task.role}.json"
    raw_path = review_path.with_name(f"{task.role}.raw.json")
    review = read_json(review_path)
    raw = read_json(raw_path)
    expected_review_id = _review_id(
        run_id=run_id,
        paper_id=task.paper.paper_id,
        role=task.role,
        program_version=CANONICAL_PROGRAM_VERSION,
        snapshot_id=task.snapshot_id,
    )
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "review_id": expected_review_id,
        "paper_id": task.paper.paper_id,
        "role": task.role,
        "program_version": CANONICAL_PROGRAM_VERSION,
        "memory_snapshot_id": task.snapshot_id,
        "reviewer_identity": load_reviewer_suite().identity(task.role),
        "model_identity": model_identity,
        "run_id": run_id,
    }
    observed_identity = {name: review.get(name) for name in expected_identity}
    if observed_identity != expected_identity:
        raise ValueError(
            f"Review identity mismatch at {review_path}: "
            f"observed={observed_identity!r}, expected={expected_identity!r}"
        )
    expected_memory = _expected_memory(task=task, program_sha256=program_sha256)
    expected_retrieval = {"memory": expected_memory, "s2": expected_s2}
    if review.get("retrieval_provenance") != expected_retrieval:
        raise ValueError(f"Review retrieval provenance mismatch at {review_path}")
    raw_output = review.get("raw_model_output")
    if (
        not isinstance(raw_output, str)
        or not raw_output.strip()
        or review.get("review_text") != raw_output
        or raw.get("raw_model_output") != raw_output
    ):
        raise ValueError(f"Review output mismatch at {review_path}")
    attempts = raw.get("attempts")
    expected_generation = {
        "temperature": generation_identity["temperature"],
        "enable_thinking": generation_identity["enable_thinking"],
        "max_tokens": generation_identity["max_tokens"],
        "retry": generation_identity["retry"],
        "attempts": attempts,
    }
    if attempts not in (1, 2) or review.get("generation_config") != expected_generation:
        raise ValueError(f"Review generation config mismatch at {review_path}")
    expected_raw_identity = {
        "paper_id": task.paper.paper_id,
        "role": task.role,
        "run_id": run_id,
        "model_identity": model_identity,
        "generation_identity": generation_identity,
        "program_version": CANONICAL_PROGRAM_VERSION,
        "memory_snapshot_id": task.snapshot_id,
    }
    observed_raw_identity = {name: raw.get(name) for name in expected_raw_identity}
    if observed_raw_identity != expected_raw_identity:
        raise ValueError(f"Raw Review identity mismatch at {raw_path}")
    messages = _request_messages(task=task, memory=expected_memory, s2=expected_s2)
    if raw.get("request_sha256") != value_sha256(messages):
        raise ValueError(f"Raw Review request hash mismatch at {raw_path}")
    if (
        not isinstance(review.get("created_at"), str)
        or not review["created_at"].strip()
        or raw.get("created_at") != review["created_at"]
    ):
        raise ValueError(f"Review creation time is missing at {review_path}")
    return review


def _verify_partial_raw(
    *,
    task: _Task,
    output_root: Path,
    run_id: str,
    model_identity: dict[str, Any],
    generation_identity: dict[str, Any],
    program_sha256: str,
    expected_s2: dict[str, Any],
) -> None:
    raw_path = output_root / "reviews" / run_id / task.paper.paper_id / f"{task.role}.raw.json"
    if not raw_path.is_file():
        return
    raw = read_json(raw_path)
    expected_identity = {
        "paper_id": task.paper.paper_id,
        "role": task.role,
        "run_id": run_id,
        "model_identity": model_identity,
        "generation_identity": generation_identity,
        "program_version": CANONICAL_PROGRAM_VERSION,
        "memory_snapshot_id": task.snapshot_id,
    }
    if {name: raw.get(name) for name in expected_identity} != expected_identity:
        raise ValueError(f"Partial raw Review identity mismatch at {raw_path}")
    expected_memory = _expected_memory(task=task, program_sha256=program_sha256)
    messages = _request_messages(task=task, memory=expected_memory, s2=expected_s2)
    if raw.get("request_sha256") != value_sha256(messages):
        raise ValueError(f"Partial raw Review request hash mismatch at {raw_path}")
    if not isinstance(raw.get("raw_model_output"), str) or not raw["raw_model_output"].strip():
        raise ValueError(f"Partial raw Review output is empty at {raw_path}")
    if raw.get("attempts") not in (1, 2):
        raise ValueError(f"Partial raw Review attempts are invalid at {raw_path}")
    if not isinstance(raw.get("created_at"), str) or not raw["created_at"].strip():
        raise ValueError(f"Partial raw Review creation time is invalid at {raw_path}")


def _run_spec(
    *,
    split: dict[str, Any],
    loaded_records: list[tuple[dict[str, Any], Paper, Path]],
    program_path: Path,
    program_sha256: str,
    snapshots: dict[str, tuple[Path, dict[str, Any]]],
    model_identity: dict[str, Any],
    generation_identity: dict[str, Any],
    s2_enabled: bool,
    s2_path: Path | None,
) -> dict[str, Any]:
    selected_records = [record for record, _, _ in loaded_records]
    return {
        "protocol": "research-v2-canonical-bootstrap-v2",
        "canonical_split_sha256": split["split_sha256"],
        "selected_records_sha256": value_sha256(selected_records),
        "paper_ids": [paper.paper_id for _, paper, _ in loaded_records],
        "roles": list(ROLES),
        "program": {
            "version": CANONICAL_PROGRAM_VERSION,
            "path": str(program_path),
            "sha256": program_sha256,
        },
        "snapshots": {
            role: {
                "path": str(path),
                "snapshot_id": value["snapshot_id"],
            }
            for role, (path, value) in snapshots.items()
        },
        "model_identity": model_identity,
        "review_generation": generation_identity,
        "s2": {
            "enabled": s2_enabled,
            "path": str(s2_path) if s2_path is not None else None,
            "sha256": file_sha256(s2_path) if s2_path is not None else None,
        },
    }


def _validate_existing_run_record(path: Path, run_id: str, spec_sha256: str) -> str | None:
    if not path.is_file():
        return None
    value = read_json(path)
    if value.get("run_id") != run_id:
        raise ValueError(f"Existing run record identity mismatch at {path}")
    stage = value.get("stage")
    if stage not in (None, "bootstrap-corpus"):
        raise ValueError(f"run_id {run_id!r} already belongs to stage {stage!r}")
    existing_spec = value.get("run_spec_sha256")
    if existing_spec is not None and existing_spec != spec_sha256:
        raise ValueError(
            f"run_id {run_id!r} is bound to a different canonical bootstrap spec"
        )
    if existing_spec is None and not {
        "completed_review_ids",
        "failures",
        "snapshots",
    }.issubset(value):
        raise ValueError(f"run_id {run_id!r} already has an unrelated run record")
    started_at = value.get("started_at")
    return started_at if isinstance(started_at, str) and started_at.strip() else None


def _result_payload(
    *,
    run_id: str,
    spec: dict[str, Any],
    spec_sha256: str,
    runtime_config: dict[str, Any],
    max_workers: int,
    snapshots: dict[str, tuple[Path, dict[str, Any]]],
    task_results: list[dict[str, Any]],
    started_at: str,
    status: str,
    infrastructure_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    successful = [
        value for value in task_results if value["status"] in {"completed", "resumed"}
    ]
    failures = [value for value in task_results if value["status"] == "failed"]
    pending = [value for value in task_results if value["status"] == "pending"]
    now = _utc_now()
    return {
        "schema_version": "research-v2-bootstrap-run-v2",
        "stage": "bootstrap-corpus",
        "run_id": run_id,
        "status": status,
        "run_spec": spec,
        "run_spec_sha256": spec_sha256,
        "runtime_config": dict(runtime_config),
        "max_workers": max_workers,
        "task_count": len(task_results),
        "completed_review_ids": [value["review_id"] for value in successful],
        "generated_review_ids": [
            value["review_id"] for value in successful if value["status"] == "completed"
        ],
        "resumed_review_ids": [
            value["review_id"] for value in successful if value["status"] == "resumed"
        ],
        "failures": [
            {
                "paper_id": value["paper_id"],
                "role": value["role"],
                "error": value["error"],
            }
            for value in failures
        ],
        "snapshots": {role: str(path) for role, (path, _) in snapshots.items()},
        "progress": {
            "finished": len(successful) + len(failures),
            "completed": sum(value["status"] == "completed" for value in task_results),
            "resumed": sum(value["status"] == "resumed" for value in task_results),
            "failed": len(failures),
            "pending": len(pending),
        },
        "task_results": task_results,
        "infrastructure_failures": list(infrastructure_failures or []),
        "started_at": started_at,
        "updated_at": now,
        "completed_at": (
            now if status in {"complete", "completed_with_failures"} else None
        ),
    }


def run_canonical_bootstrap(
    *,
    config: ResearchConfig,
    options: BootstrapCorpusOptions,
    completion_client: CompletionClient,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run or resume all canonical no-Memory Reviews through one deep interface."""
    options.validate(config)
    output_root = config.artifact_path()
    run_path = output_root / "runs" / options.run_id / "run.json"
    with _exclusive_run_lock(run_path):
        return _run_canonical_bootstrap_locked(
            config=config,
            options=options,
            completion_client=completion_client,
            progress=progress,
            output_root=output_root,
            run_path=run_path,
        )


def _run_canonical_bootstrap_locked(
    *,
    config: ResearchConfig,
    options: BootstrapCorpusOptions,
    completion_client: CompletionClient,
    progress: ProgressCallback | None,
    output_root: Path,
    run_path: Path,
) -> dict[str, Any]:
    split, loaded_records = _load_records(output_root, options.limit)
    program_path = seed_program_path("no_memory").resolve()
    program_sha256 = file_sha256(program_path)
    snapshots = _ensure_snapshots(
        output_root=output_root,
        program_path=program_path,
        program_sha256=program_sha256,
        require_bubblewrap=config.require_bubblewrap,
    )
    s2_path: Path | None = None
    if options.s2_enabled:
        if not config.frozen_s2_path:
            raise ValueError("canonical bootstrap with S2 requires frozen_s2_path")
        s2_path = Path(config.frozen_s2_path).expanduser().resolve()
    expected_s2 = _expected_s2_by_paper(
        loaded_records=loaded_records,
        s2_enabled=options.s2_enabled,
        s2_path=s2_path,
    )
    model_identity = config.model.identity()
    generation_identity = specialist_generation_identity(model_identity)
    runtime_config = config.model.runtime_config()
    spec = _run_spec(
        split=split,
        loaded_records=loaded_records,
        program_path=program_path,
        program_sha256=program_sha256,
        snapshots=snapshots,
        model_identity=model_identity,
        generation_identity=generation_identity,
        s2_enabled=options.s2_enabled,
        s2_path=s2_path,
    )
    spec_sha256 = value_sha256(spec)
    started_at = _validate_existing_run_record(
        run_path,
        options.run_id,
        spec_sha256,
    ) or _utc_now()

    tasks: list[_Task] = []
    for _, paper, paper_path in loaded_records:
        for role in ROLES:
            snapshot_path, snapshot = snapshots[role]
            tasks.append(
                _Task(
                    index=len(tasks),
                    paper=paper,
                    paper_path=paper_path,
                    role=role,
                    snapshot_path=snapshot_path,
                    snapshot_id=str(snapshot["snapshot_id"]),
                )
            )

    task_results: list[dict[str, Any]] = [
        {"paper_id": task.paper.paper_id, "role": task.role, "status": "pending"}
        for task in tasks
    ]
    pending: list[_Task] = []
    artifact_conflicts = []
    for task in tasks:
        review_path = (
            output_root
            / "reviews"
            / options.run_id
            / task.paper.paper_id
            / f"{task.role}.json"
        )
        try:
            if review_path.is_file():
                review = _verify_saved_review(
                    task=task,
                    output_root=output_root,
                    run_id=options.run_id,
                    model_identity=model_identity,
                    generation_identity=generation_identity,
                    program_sha256=program_sha256,
                    expected_s2=expected_s2[task.paper.paper_id],
                )
                task_results[task.index] = {
                    "paper_id": task.paper.paper_id,
                    "role": task.role,
                    "status": "resumed",
                    "review_id": review["review_id"],
                }
            else:
                _verify_partial_raw(
                    task=task,
                    output_root=output_root,
                    run_id=options.run_id,
                    model_identity=model_identity,
                    generation_identity=generation_identity,
                    program_sha256=program_sha256,
                    expected_s2=expected_s2[task.paper.paper_id],
                )
                pending.append(task)
        except Exception as exc:
            artifact_conflicts.append(
                f"{task.paper.paper_id}/{task.role}: {type(exc).__name__}: {exc}"
            )
    if artifact_conflicts:
        raise ValueError(
            "Canonical bootstrap found conflicting committed artifacts:\n"
            + "\n".join(artifact_conflicts)
        )

    initial = _result_payload(
        run_id=options.run_id,
        spec=spec,
        spec_sha256=spec_sha256,
        runtime_config=runtime_config,
        max_workers=options.max_workers,
        snapshots=snapshots,
        task_results=task_results,
        started_at=started_at,
        status="running" if pending else "complete",
    )
    atomic_write_json(run_path, initial, immutable=False)
    if progress is not None:
        progress(
            {
                "event": "bootstrap_plan",
                "run_id": options.run_id,
                "total": len(tasks),
                "resumed": len(tasks) - len(pending),
                "pending": len(pending),
                "max_workers": options.max_workers,
            }
        )
    if not pending:
        if progress is not None:
            progress(
                {
                    "event": "bootstrap_complete",
                    "run_id": options.run_id,
                    **initial["progress"],
                }
            )
        return initial

    def execute(task: _Task) -> dict[str, Any]:
        return run_specialist_review(
            paper_path=task.paper_path,
            role=task.role,
            program_path=program_path,
            snapshot_path=task.snapshot_path,
            run_id=options.run_id,
            output_root=output_root,
            completion_client=completion_client,
            model_identity=model_identity,
            program_version=CANONICAL_PROGRAM_VERSION,
            frozen_s2_path=s2_path,
            embedding_client=None,
            require_bubblewrap=config.require_bubblewrap,
            _canonical_bootstrap_s2_enabled=options.s2_enabled,
        )

    next_pending = 0
    in_flight: dict[Future[dict[str, Any]], _Task] = {}
    infrastructure_failures: list[dict[str, Any]] = []
    first_infrastructure_error: Exception | None = None
    stop_submitting = False
    executor = ThreadPoolExecutor(
        max_workers=options.max_workers,
        thread_name_prefix="canonical-review",
    )
    try:
        while next_pending < len(pending) and len(in_flight) < options.max_workers:
            task = pending[next_pending]
            next_pending += 1
            in_flight[executor.submit(execute, task)] = task
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: in_flight[item].index):
                task = in_flight.pop(future)
                try:
                    review = future.result()
                    task_results[task.index] = {
                        "paper_id": task.paper.paper_id,
                        "role": task.role,
                        "status": "completed",
                        "review_id": review["review_id"],
                    }
                    disposition = "completed"
                except SpecialistReviewPaperFailure as exc:
                    task_results[task.index] = {
                        "paper_id": task.paper.paper_id,
                        "role": task.role,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    disposition = "failed"
                except Exception as exc:
                    if first_infrastructure_error is None:
                        first_infrastructure_error = exc
                    stop_submitting = True
                    disposition = "infrastructure_failed"
                    infrastructure_failures.append(
                        {
                            "paper_id": task.paper.paper_id,
                            "role": task.role,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                checkpoint = _result_payload(
                    run_id=options.run_id,
                    spec=spec,
                    spec_sha256=spec_sha256,
                    runtime_config=runtime_config,
                    max_workers=options.max_workers,
                    snapshots=snapshots,
                    task_results=task_results,
                    started_at=started_at,
                    status=(
                        "infrastructure_failed"
                        if infrastructure_failures
                        else "running"
                    ),
                    infrastructure_failures=infrastructure_failures,
                )
                atomic_write_json(run_path, checkpoint, immutable=False)
                if progress is not None:
                    progress(
                        {
                            "event": "bootstrap_progress",
                            "run_id": options.run_id,
                            "paper_id": task.paper.paper_id,
                            "role": task.role,
                            "disposition": disposition,
                            **checkpoint["progress"],
                            "total": len(tasks),
                        }
                    )
            while (
                not stop_submitting
                and next_pending < len(pending)
                and len(in_flight) < options.max_workers
            ):
                next_task = pending[next_pending]
                next_pending += 1
                in_flight[executor.submit(execute, next_task)] = next_task
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    if infrastructure_failures:
        interrupted = _result_payload(
            run_id=options.run_id,
            spec=spec,
            spec_sha256=spec_sha256,
            runtime_config=runtime_config,
            max_workers=options.max_workers,
            snapshots=snapshots,
            task_results=task_results,
            started_at=started_at,
            status="infrastructure_failed",
            infrastructure_failures=infrastructure_failures,
        )
        atomic_write_json(run_path, interrupted, immutable=False)
        if progress is not None:
            progress(
                {
                    "event": "bootstrap_infrastructure_failure",
                    "run_id": options.run_id,
                    **interrupted["progress"],
                }
            )
        raise BootstrapInfrastructureFailure(
            "canonical bootstrap stopped after a shared infrastructure failure; "
            f"resume with the same run_id after recovery: {infrastructure_failures[0]}"
        ) from first_infrastructure_error

    final_status = (
        "completed_with_failures"
        if any(value["status"] == "failed" for value in task_results)
        else "complete"
    )
    result = _result_payload(
        run_id=options.run_id,
        spec=spec,
        spec_sha256=spec_sha256,
        runtime_config=runtime_config,
        max_workers=options.max_workers,
        snapshots=snapshots,
        task_results=task_results,
        started_at=started_at,
        status=final_status,
    )
    atomic_write_json(run_path, result, immutable=False)
    if progress is not None:
        progress(
            {
                "event": "bootstrap_complete",
                "run_id": options.run_id,
                **result["progress"],
            }
        )
    return result
