"""Stable reviewer task identities shared by every optimizer."""

from typing import Tuple

from agentreview.single_paper_review_roles import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    REVIEWER_SPECS,
)


SPECIALIST_TASKS: Tuple[str, ...] = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
META_TASK = "META"
ALL_TASKS: Tuple[str, ...] = SPECIALIST_TASKS + (META_TASK,)


def reviewer_identity(task_id: str) -> dict:
    """Return the immutable identity of one fixed AgentReview reviewer."""
    if task_id == META_TASK:
        return {
            "name": "Final Meta-Reviewer",
            "task_id": META_TASK,
            "rubric_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        }
    for spec in REVIEWER_SPECS:
        if spec["dimension"] == task_id:
            return {
                "name": spec["name"],
                "task_id": task_id,
                "rubric_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
            }
    raise ValueError(f"Unknown review task: {task_id}")
