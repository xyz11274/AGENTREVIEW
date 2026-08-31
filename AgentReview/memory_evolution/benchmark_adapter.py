"""Outermost benchmark boundary for fully frozen Reviewer outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ReviewSubmission:
    paper_id: str
    review: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BenchmarkAdapter(Protocol):
    """A benchmark implementation that never contributes Candidate context."""

    name: str

    def evaluate(self, submission: ReviewSubmission) -> dict[str, Any]: ...


def evaluate_frozen_review(
    adapter: BenchmarkAdapter,
    submission: ReviewSubmission,
) -> dict[str, Any]:
    if not submission.paper_id or not submission.review.strip():
        raise ValueError("A benchmark submission requires paper_id and review text")
    result = adapter.evaluate(submission)
    if not isinstance(result, dict):
        raise TypeError("BenchmarkAdapter.evaluate() must return a dict")
    return {
        "benchmark": str(adapter.name),
        "paper_id": submission.paper_id,
        "result": result,
        "reviewer_received_benchmark_context": False,
    }


class CoCoMetricBenchmarkAdapter:
    """Evaluator-only bridge used by the sealed D path."""

    name = "CoCoReviewBench-held-out-manifest"

    def __init__(self, metric: Callable, gold: str, item: Any):
        self.metric = metric
        self.gold = str(gold)
        self.item = item

    def evaluate(self, submission: ReviewSubmission) -> dict[str, Any]:
        try:
            score = self.metric(
                submission.review,
                self.gold,
                submission.metadata,
            )
        except TypeError:
            score = self.metric(submission.review, self.gold)
        return {
            "score": max(0.0, min(1.0, float(score))),
            "structured_metrics": dict(getattr(self.metric, "last_details", {}) or {}),
            "evaluator_only_reference_used": True,
        }
