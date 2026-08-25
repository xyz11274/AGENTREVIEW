"""Public request/result values for the optimizer-agnostic reviewer runtime."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass
class Episode:
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "Episode":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                id=str(value.get("id", "")),
                text=str(value.get("text", "")),
                metadata=dict(value.get("metadata") or {}),
            )
        return cls(
            id=str(getattr(value, "id", "")),
            text=str(getattr(value, "text", "")),
            metadata=dict(getattr(value, "metadata", {}) or {}),
        )


@dataclass
class ReviewRequest:
    """One paper reviewed by one fixed reviewer task."""

    paper_id: str
    task_id: str
    question: str
    episodes: Iterable[Any] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)
    model: str = ""
    revision: str = ""
    run_identity: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)
    read_operation: str = "memory_read"

    def validate(self) -> None:
        if not self.paper_id:
            raise ValueError("ReviewRequest.paper_id is required")
        if not self.task_id:
            raise ValueError("ReviewRequest.task_id is required")
        if not self.question.strip():
            raise ValueError("ReviewRequest.question is required")


@dataclass
class ReviewResult:
    paper_id: str
    task_id: str
    review_text: str
    query: Dict[str, Any]
    memory_context: str
    reviewer_identity: Dict[str, Any]
    memory_program_identity: Dict[str, Any]
    memory_program_sha256: str
    model: str
    revision: str
    run_identity: str
    provenance: Dict[str, Any] = field(default_factory=dict)
    runtime_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
