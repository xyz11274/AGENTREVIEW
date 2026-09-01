"""Stable reviewer task identities shared by every execution surface."""

from agentreview.reviewer_identity import (
    ALL_TASKS,
    META_TASK,
    SPECIALIST_TASKS,
    load_reviewer_suite,
)

__all__ = ["ALL_TASKS", "META_TASK", "SPECIALIST_TASKS", "reviewer_identity"]


def reviewer_identity(task_id: str) -> dict:
    """Return one Specialist's identity; Meta is not a Reviewer task."""
    return load_reviewer_suite().identity(task_id)
