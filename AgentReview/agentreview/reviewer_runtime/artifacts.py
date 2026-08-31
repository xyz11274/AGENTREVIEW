"""Stable candidate review JSONL contract, independent of optimizer provenance."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import ReviewResult
from .tasks import reviewer_identity

CANDIDATE_REVIEW_SCHEMA_VERSION = "agentreview-candidate-review-v2"


@dataclass(frozen=True)
class CandidateReviewRecord:
    paper_id: str
    task_id: str
    review_text: str
    reviewer_identity: dict[str, Any]
    memory_program_identity: dict[str, Any]
    memory_program_sha256: str
    model: str
    revision: str
    run_identity: str
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CANDIDATE_REVIEW_SCHEMA_VERSION

    @classmethod
    def from_result(cls, result: ReviewResult) -> CandidateReviewRecord:
        return cls(
            paper_id=result.paper_id,
            task_id=result.task_id,
            review_text=result.review_text,
            reviewer_identity=dict(result.reviewer_identity),
            memory_program_identity=dict(result.memory_program_identity),
            memory_program_sha256=result.memory_program_sha256,
            model=result.model,
            revision=result.revision,
            run_identity=result.run_identity,
            provenance=dict(result.provenance),
        )

    @classmethod
    def from_value(cls, value: Any) -> CandidateReviewRecord:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("Candidate review record must be an object")
        known = {
            "paper_id",
            "task_id",
            "review_text",
            "reviewer_identity",
            "memory_program_identity",
            "memory_program_sha256",
            "model",
            "revision",
            "run_identity",
            "provenance",
            "schema_version",
        }
        extra = set(value) - known
        if extra:
            raise ValueError(f"Unknown candidate review fields: {sorted(extra)}")
        record = cls(
            paper_id=str(value.get("paper_id") or ""),
            task_id=str(value.get("task_id") or ""),
            review_text=str(value.get("review_text") or ""),
            reviewer_identity=dict(value.get("reviewer_identity") or {}),
            memory_program_identity=dict(value.get("memory_program_identity") or {}),
            memory_program_sha256=str(value.get("memory_program_sha256") or ""),
            model=str(value.get("model") or ""),
            revision=str(value.get("revision") or ""),
            run_identity=str(value.get("run_identity") or ""),
            provenance=dict(value.get("provenance") or {}),
            schema_version=str(
                value.get("schema_version") or CANDIDATE_REVIEW_SCHEMA_VERSION
            ),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if self.schema_version != CANDIDATE_REVIEW_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported candidate review schema: {self.schema_version}"
            )
        required_text = {
            "paper_id": self.paper_id,
            "task_id": self.task_id,
            "review_text": self.review_text,
            "memory_program_sha256": self.memory_program_sha256,
            "model": self.model,
            "revision": self.revision,
            "run_identity": self.run_identity,
        }
        missing = [name for name, value in required_text.items() if not value.strip()]
        if missing:
            raise ValueError(f"Candidate review required fields are empty: {missing}")
        expected_reviewer_identity = reviewer_identity(self.task_id)
        if self.reviewer_identity == expected_reviewer_identity:
            pass
        else:
            raise ValueError(
                f"reviewer_identity differs from frozen task {self.task_id}"
            )
        if not self.memory_program_identity:
            raise ValueError("memory_program_identity is required")
        if len(self.memory_program_sha256) != 64:
            raise ValueError("memory_program_sha256 must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def iter_candidate_reviews(path: str | Path) -> Iterator[CandidateReviewRecord]:
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                yield CandidateReviewRecord.from_value(value)
            except Exception as exc:
                raise ValueError(
                    f"Invalid candidate review at {source}:{line_number}: {exc}"
                ) from exc


class CandidateReviewWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def append(self, value: CandidateReviewRecord | ReviewResult) -> None:
        record = (
            CandidateReviewRecord.from_result(value)
            if isinstance(value, ReviewResult)
            else CandidateReviewRecord.from_value(value.to_dict())
        )
        payload = (
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload)

    def write_all(self, values: Iterable[CandidateReviewRecord | ReviewResult]) -> None:
        for value in values:
            self.append(value)
