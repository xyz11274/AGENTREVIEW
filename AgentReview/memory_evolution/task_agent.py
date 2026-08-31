"""Backward-compatible fixed reviewer TaskAgent import path."""

from agentreview.reviewer_runtime.task_agent import (
    TaskAgent,
    _parse_json_object,
    build_task_agent,
    dataclass_schema,
)

__all__ = [
    "TaskAgent",
    "_parse_json_object",
    "build_task_agent",
    "dataclass_schema",
]
