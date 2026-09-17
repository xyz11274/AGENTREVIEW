"""Bounded, resumable Internal Evaluation for the canonical Review corpus."""

from __future__ import annotations

import fcntl
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .constants import ROLES
from .evaluation import EvaluationCancelled, EvaluationFailure, evaluation_failure_detail, run_internal_evaluation
from .io import atomic_write_json, read_json
from .llm import CompletionClient


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class EvaluationBatchOptions:
    review_run_id: str
    run_id: str
    max_workers: int = 8
    limit: int = 0

    def validate(self) -> None:
        if not self.review_run_id or not self.run_id:
            raise ValueError("Evaluation batch run identities are required")
        if not 1 <= int(self.max_workers) <= 8:
            raise ValueError("Evaluation batch max_workers must be in [1, 8]")
        if int(self.limit) < 0:
            raise ValueError("Evaluation batch limit must be non-negative")


def _tasks(output_root: Path, options: EvaluationBatchOptions) -> list[dict[str, str]]:
    split = read_json(output_root / "data" / "splits" / "canonical_review_corpus.json")
    records = split.get("records")
    if not isinstance(records, list) or len(records) == 0:
        raise ValueError("Canonical Evaluation corpus must contain paper records")
    if options.limit:
        records = records[: options.limit]
    tasks = []
    for record in records:
        paper_id = str(record["paper_id"])
        paper_path = str(record["path"])
        if not Path(paper_path).is_file():
            raise FileNotFoundError(f"Canonical Paper is missing: {paper_path}")
        for role in ROLES:
            review_path = (
                output_root / "reviews" / options.review_run_id / paper_id / f"{role}.json"
            )
            if not review_path.is_file():
                raise FileNotFoundError(f"Canonical Review is missing: {review_path}")
            tasks.append(
                {
                    "paper_id": paper_id,
                    "paper_path": paper_path,
                    "role": role,
                    "review_path": str(review_path),
                }
            )
    return tasks


def _summary(
    options: EvaluationBatchOptions,
    evaluator_identity: dict[str, Any],
    task_count: int,
    submitted: int,
    results: list[dict[str, Any]],
    paused: bool,
) -> dict[str, Any]:
    ordered = sorted(results, key=lambda item: (item["paper_id"], ROLES.index(item["role"])))
    failed = [item for item in ordered if item["status"] == "failed"]
    summary = {
        "schema_version": "research-v2-internal-evaluation-batch-v2",
        "stage": "evaluate-corpus", "review_run_id": options.review_run_id,
        "run_id": options.run_id, "max_workers": options.max_workers,
        "evaluator_identity": evaluator_identity, "task_count": task_count,
        "completed": sum(item["status"] == "completed" for item in ordered),
        "resumed": sum(item["status"] == "resumed" for item in ordered),
        "failed": len(failed),
        "cancelled": sum(item["status"] == "cancelled" for item in ordered),
        "pending": task_count - submitted,
        "inflight": submitted - len(results),
        "status": "paused" if paused else "complete",
        "task_results": ordered,
    }
    if paused:
        summary["failure_category"] = "shared_evaluation"
        if failed:
            summary["failure_record"] = failed[0]["failure_record"]
    return summary


def run_evaluation_batch(
    *,
    output_root: str | Path,
    options: EvaluationBatchOptions,
    completion_client: CompletionClient,
    evaluator_identity: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    options.validate()
    root = Path(output_root).expanduser().resolve()
    run_dir = root / "runs" / options.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / "internal-evaluation.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Internal Evaluation run is already active: {options.run_id}") from exc
        summary_path = run_dir / "internal_evaluation.json"
        if summary_path.is_file():
            existing = read_json(summary_path)
            if existing.get("status") == "paused":
                if existing.get("review_run_id") != options.review_run_id or existing.get("evaluator_identity") != evaluator_identity:
                    raise ValueError("Paused Evaluation batch identities must match the request")
                return existing
        try:
            tasks = _tasks(root, options)
        except Exception as exc:
            detail = evaluation_failure_detail(exc, stage="input")
            summary = {
                "schema_version": "research-v2-internal-evaluation-batch-v2",
                "stage": "evaluate-corpus", "review_run_id": options.review_run_id,
                "run_id": options.run_id, "max_workers": options.max_workers,
                "evaluator_identity": evaluator_identity, "task_count": 0,
                "completed": 0, "resumed": 0, "failed": 0, "cancelled": 0, "pending": 0,
                "status": "paused", "failure_category": "shared_evaluation",
                "failure_record": {"failure_reason": "input_data_error", "failure": detail},
                "task_results": [],
            }
            atomic_write_json(summary_path, summary, immutable=False)
            return summary
        results: list[dict[str, Any]] = []
        paused = Event()

        def evaluate(task: dict[str, str]) -> dict[str, Any]:
            if paused.is_set():
                return {"paper_id": task["paper_id"], "role": task["role"], "status": "cancelled"}
            final_path = (
                root / "evaluations" / options.run_id / task["paper_id"] / f"{task['role']}.json"
            )
            resumed = final_path.is_file()
            try:
                value = run_internal_evaluation(
                    paper_path=task["paper_path"],
                    review_path=task["review_path"],
                    run_id=options.run_id,
                    output_root=root,
                    completion_client=completion_client,
                    evaluator_identity=evaluator_identity,
                    stop_requested=paused.is_set,
                )
                return {
                    "paper_id": task["paper_id"],
                    "role": task["role"],
                    "status": "resumed" if resumed else "completed",
                    "evaluation_id": value["evaluation_id"],
                }
            except EvaluationCancelled:
                return {"paper_id": task["paper_id"], "role": task["role"], "status": "cancelled"}
            except Exception as exc:
                paused.set()
                failure_record = exc.failure_record if isinstance(exc, EvaluationFailure) else {
                    "failure_reason": "input_data_error", "failure": evaluation_failure_detail(exc, stage="input"),
                }
                return {
                    "paper_id": task["paper_id"],
                    "role": task["role"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "failure_category": "shared_evaluation",
                    "failure_record": failure_record,
                }

        pending = iter(tasks)
        inflight = {}
        submitted = 0
        with ThreadPoolExecutor(max_workers=options.max_workers) as executor:
            def fill_workers() -> None:
                nonlocal submitted
                while len(inflight) < options.max_workers and not paused.is_set():
                    try:
                        task = next(pending)
                    except StopIteration:
                        break
                    inflight[executor.submit(evaluate, task)] = task
                    submitted += 1

            fill_workers()
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    task = inflight.pop(future)
                    result = (
                        {"paper_id": task["paper_id"], "role": task["role"], "status": "cancelled"}
                        if future.cancelled() else future.result()
                    )
                    results.append(result)
                    event = {
                        "event": "internal_evaluation_progress",
                        "run_id": options.run_id,
                        "finished": len(results),
                        "total": len(tasks),
                        **result,
                    }
                    if progress is not None:
                        progress(event)
                if paused.is_set():
                    atomic_write_json(
                        summary_path,
                        _summary(options, evaluator_identity, len(tasks), submitted, results, True),
                        immutable=False,
                    )
                    for future in inflight:
                        future.cancel()
                else:
                    fill_workers()

        summary = _summary(options, evaluator_identity, len(tasks), submitted, results, paused.is_set())
        atomic_write_json(summary_path, summary, immutable=False)
        return summary
