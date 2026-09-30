"""Primary Research Core v2 artifact values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .constants import ROLES, SCHEMA_VERSION


def _require_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")


@dataclass(frozen=True)
class Paper:
    paper_id: str
    title: str
    abstract: str
    text: str
    source: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.paper_id.strip():
            raise ValueError("Paper.paper_id is required")
        if not self.title.strip():
            raise ValueError("Paper.title is required")
        if not self.text.strip():
            raise ValueError("Paper.text is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Paper":
        paper = cls(
            paper_id=str(value.get("paper_id") or value.get("id") or ""),
            title=str(value.get("title") or ""),
            abstract=str(value.get("abstract") or ""),
            text=str(value.get("text") or ""),
            source=dict(value.get("source") or {}),
        )
        paper.validate()
        return paper


@dataclass(frozen=True)
class SpecialistReview:
    review_id: str
    paper_id: str
    role: str
    program_version: str
    memory_snapshot_id: str
    review_text: str
    raw_model_output: str
    reviewer_identity: dict[str, Any]
    model_identity: dict[str, Any]
    generation_config: dict[str, Any]
    retrieval_provenance: dict[str, Any]
    run_id: str
    created_at: str

    def validate(self) -> None:
        _require_role(self.role)
        for name in ("review_id", "paper_id", "program_version", "review_text", "run_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"SpecialistReview.{name} is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}


@dataclass(frozen=True)
class FinalReview:
    final_review_id: str
    paper_id: str
    mode: str
    review_text: str
    source_review_ids: list[str]
    meta_identity: dict[str, Any] | None
    run_id: str
    created_at: str

    def validate(self) -> None:
        if self.mode not in {"meta", "concat"}:
            raise ValueError("FinalReview.mode must be meta or concat")
        if len(self.source_review_ids) != len(ROLES):
            raise ValueError("FinalReview requires exactly five source Review IDs")
        if not self.review_text.strip():
            raise ValueError("FinalReview.review_text is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}
