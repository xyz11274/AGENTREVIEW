"""Bounded, resumable Internal Evaluation for the canonical Review corpus."""

from __future__ import annotations

import fcntl
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .constants import ROLES
from .evaluation import run_internal_evaluation
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
    records = list(split.get("records") or [])
    if options.limit:
        records = records[: options.limit]
    tasks = []
    for record in records:
        paper_id = str(record["paper_id"])
        paper_path = str(record["path"])
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
        tasks = _tasks(root, options)
        results: list[dict[str, Any]] = []

        def evaluate(task: dict[str, str]) -> dict[str, Any]:
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
                )
                return {
                    "paper_id": task["paper_id"],
                    "role": task["role"],
                    "status": "resumed" if resumed else "completed",
                    "evaluation_id": value["evaluation_id"],
                }
            except Exception as exc:
                return {
                    "paper_id": task["paper_id"],
                    "role": task["role"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        pending = iter(tasks)
        inflight = set()
        with ThreadPoolExecutor(max_workers=options.max_workers) as executor:
            while len(inflight) < options.max_workers:
                try:
                    inflight.add(executor.submit(evaluate, next(pending)))
                except StopIteration:
                    break
            while inflight:
                done, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
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
                    try:
                        inflight.add(executor.submit(evaluate, next(pending)))
                    except StopIteration:
                        pass

        results.sort(key=lambda item: (item["paper_id"], ROLES.index(item["role"])))
        failed = [item for item in results if item["status"] == "failed"]
        summary = {
            "schema_version": "research-v2-internal-evaluation-batch-v1",
            "stage": "evaluate-corpus",
            "review_run_id": options.review_run_id,
            "run_id": options.run_id,
            "max_workers": options.max_workers,
            "task_count": len(tasks),
            "completed": sum(item["status"] == "completed" for item in results),
            "resumed": sum(item["status"] == "resumed" for item in results),
            "failed": len(failed),
            "status": "complete" if not failed else "completed_with_failures",
            "task_results": results,
        }
        atomic_write_json(run_dir / "internal_evaluation.json", summary, immutable=False)
        return summary
