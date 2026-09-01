"""Bounded orchestration for final evaluation of frozen reviews."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar


ItemT = TypeVar("ItemT")
PreparedT = TypeVar("PreparedT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class EvaluationPipelineRun:
    """Ordered results plus auditable bounded-pipeline counters."""

    results: list[Any]
    stats: dict[str, Any]


def run_ordered_evaluation_pipeline(
    items: Iterable[ItemT],
    *,
    produce: Callable[[ItemT], PreparedT],
    score: Callable[[PreparedT], ResultT],
    max_inflight: int = 1,
) -> EvaluationPipelineRun:
    """Produce reviews on the caller thread and score them on one worker.

    ``max_inflight`` counts frozen reviews that have been submitted for scoring
    but have not yet been committed.  Results are always committed in input
    order.  A depth of one intentionally preserves the legacy serial path.
    """

    depth = int(max_inflight)
    if depth < 1:
        raise ValueError("Evaluation pipeline depth must be at least 1")

    results: list[ResultT] = []
    max_observed = 0
    produced_while_scoring = 0
    item_count = 0

    if depth == 1:
        for item in items:
            item_count += 1
            results.append(score(produce(item)))
            max_observed = 1
        return EvaluationPipelineRun(
            results=list(results),
            stats={
                "pipeline_version": "ordered-frozen-review-v1",
                "enabled": False,
                "score_workers": 1,
                "max_inflight_configured": depth,
                "max_inflight_observed": max_observed,
                "reviews_produced_while_scoring": 0,
                "item_count": item_count,
            },
        )

    pending: deque[Future[ResultT]] = deque()
    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="evaluation-score",
    ) as executor:
        for item in items:
            item_count += 1
            if any(future.running() for future in pending):
                produced_while_scoring += 1
            prepared = produce(item)
            pending.append(executor.submit(score, prepared))
            max_observed = max(max_observed, len(pending))
            if len(pending) >= depth:
                results.append(pending.popleft().result())
        while pending:
            results.append(pending.popleft().result())

    return EvaluationPipelineRun(
        results=list(results),
        stats={
            "pipeline_version": "ordered-frozen-review-v1",
            "enabled": True,
            "score_workers": 1,
            "max_inflight_configured": depth,
            "max_inflight_observed": max_observed,
            "reviews_produced_while_scoring": produced_while_scoring,
            "item_count": item_count,
        },
    )
