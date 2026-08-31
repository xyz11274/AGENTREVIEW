"""Candidate-blind B-local manifest materialization for Core v1.

The module freezes one shared full paper/family pool and deterministic
role-local static/rotating membership.  It deliberately accepts no Candidate,
review, evaluator score, Human/OpenReview, or CoCo value.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from agentreview.review_corpus import ROLE_SUBDIMENSIONS, canonical_json_bytes


B_LOCAL_SCHEMA_VERSION = "core-v1-b-local-manifest-v1"
ROLE_ORDER = tuple(ROLE_SUBDIMENSIONS)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_identity(payload: Mapping[str, Any]) -> str:
    return _sha256(
        canonical_json_bytes(
            {
                "identity_type": "core_v1_b_local_manifest",
                "schema_version": B_LOCAL_SCHEMA_VERSION,
                "payload": dict(payload),
            }
        )
    )


def _require_sha(value: str, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a full lowercase SHA-256")
    return text


@dataclass(frozen=True)
class BLocalPaper:
    paper_id: str
    family_id: str
    reviewer_question: str
    paper_text: str
    structural_context: str
    cutoff: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BLocalPaper":
        expected = {
            "paper_id",
            "family_id",
            "reviewer_question",
            "paper_text",
            "structural_context",
            "cutoff",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("B-local paper fields mismatch")
        item = cls(**{key: str(value[key]) for key in expected})
        if not all((item.paper_id, item.family_id, item.reviewer_question, item.paper_text, item.cutoff)):
            raise ValueError("B-local paper identity/content/cutoff are required")
        return item

    @property
    def content_sha256(self) -> str:
        return _sha256(canonical_json_bytes(asdict(self)))

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class BLocalRoleView:
    role: str
    eligible_paper_ids: Tuple[str, ...]
    static_paper_ids: Tuple[str, ...]
    rotating_pool_paper_ids: Tuple[str, ...]
    rotating_schedule: Tuple[Tuple[str, ...], ...]
    full_pool_paper_ids: Tuple[str, ...]
    min_static_evaluable: int
    min_full_pool_evaluable: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "eligible_paper_ids": list(self.eligible_paper_ids),
            "static_paper_ids": list(self.static_paper_ids),
            "rotating_pool_paper_ids": list(self.rotating_pool_paper_ids),
            "rotating_schedule": [list(batch) for batch in self.rotating_schedule],
            "full_pool_paper_ids": list(self.full_pool_paper_ids),
            "min_static_evaluable": self.min_static_evaluable,
            "min_full_pool_evaluable": self.min_full_pool_evaluable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BLocalRoleView":
        expected = {
            "role",
            "eligible_paper_ids",
            "static_paper_ids",
            "rotating_pool_paper_ids",
            "rotating_schedule",
            "full_pool_paper_ids",
            "min_static_evaluable",
            "min_full_pool_evaluable",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("B-local role view fields mismatch")
        return cls(
            role=str(value["role"]),
            eligible_paper_ids=tuple(map(str, value["eligible_paper_ids"])),
            static_paper_ids=tuple(map(str, value["static_paper_ids"])),
            rotating_pool_paper_ids=tuple(map(str, value["rotating_pool_paper_ids"])),
            rotating_schedule=tuple(
                tuple(map(str, batch)) for batch in value["rotating_schedule"]
            ),
            full_pool_paper_ids=tuple(map(str, value["full_pool_paper_ids"])),
            min_static_evaluable=int(value["min_static_evaluable"]),
            min_full_pool_evaluable=int(value["min_full_pool_evaluable"]),
        )


@dataclass(frozen=True)
class BLocalManifest:
    papers: Tuple[BLocalPaper, ...]
    role_views: Tuple[BLocalRoleView, ...]
    eligibility_audit: Tuple[Dict[str, Any], ...]
    selection_seed: str
    attempts: int
    static_size: int
    rotating_size: int
    source_manifest_sha256: str
    rubric_protocol_version: str
    selection_algorithm: str = "sha256(role,seed,attempt,family_id),family_id"
    schema_version: str = B_LOCAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if tuple(view.role for view in self.role_views) != ROLE_ORDER:
            raise ValueError("B-local role order/coverage mismatch")
        paper_ids = tuple(item.paper_id for item in self.papers)
        family_ids = tuple(item.family_id for item in self.papers)
        if not paper_ids or len(set(paper_ids)) != len(paper_ids):
            raise ValueError("B-local paper IDs must be non-empty and unique")
        if len(set(family_ids)) != len(family_ids):
            raise ValueError("B-local v1 requires one selected paper per frozen family")
        _require_sha(self.source_manifest_sha256, "B-local source manifest SHA")

    @property
    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_manifest_sha256": self.source_manifest_sha256,
            "rubric_protocol_version": self.rubric_protocol_version,
            "selection_seed": self.selection_seed,
            "selection_algorithm": self.selection_algorithm,
            "attempts": self.attempts,
            "static_size": self.static_size,
            "rotating_size": self.rotating_size,
            "papers": [item.to_dict() for item in self.papers],
            "eligibility_audit": list(self.eligibility_audit),
            "role_views": [item.to_dict() for item in self.role_views],
        }

    @property
    def manifest_sha256(self) -> str:
        return _manifest_identity(self.canonical_payload)

    def role_view(self, role: str) -> BLocalRoleView:
        for item in self.role_views:
            if item.role == role:
                return item
        raise KeyError(f"Unknown B-local role: {role}")

    def paper(self, paper_id: str) -> BLocalPaper:
        for item in self.papers:
            if item.paper_id == paper_id:
                return item
        raise KeyError(f"Unknown B-local paper: {paper_id}")

    def write(self, path: str | Path) -> None:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "identity_type": "core_v1_b_local_manifest_envelope",
            "schema_version": self.schema_version,
            "manifest_sha256": self.manifest_sha256,
            "payload": self.canonical_payload,
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(canonical_json_bytes(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    @classmethod
    def load(cls, path: str | Path) -> "BLocalManifest":
        value = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        expected = {"identity_type", "schema_version", "manifest_sha256", "payload"}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("B-local manifest envelope fields mismatch")
        if (
            value["identity_type"] != "core_v1_b_local_manifest_envelope"
            or value["schema_version"] != B_LOCAL_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported B-local manifest identity/version")
        payload = value["payload"]
        fields = {
            "schema_version",
            "source_manifest_sha256",
            "rubric_protocol_version",
            "selection_seed",
            "selection_algorithm",
            "attempts",
            "static_size",
            "rotating_size",
            "papers",
            "eligibility_audit",
            "role_views",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("B-local manifest payload fields mismatch")
        papers = []
        for item in payload["papers"]:
            if not isinstance(item, dict) or set(item) != {
                "paper_id", "family_id", "reviewer_question", "paper_text",
                "structural_context", "cutoff", "content_sha256",
            }:
                raise ValueError("B-local serialized paper fields mismatch")
            raw = {key: item[key] for key in item if key != "content_sha256"}
            paper = BLocalPaper.from_mapping(raw)
            if paper.content_sha256 != item["content_sha256"]:
                raise ValueError("B-local paper content SHA mismatch")
            papers.append(paper)
        result = cls(
            papers=tuple(papers),
            role_views=tuple(BLocalRoleView.from_dict(item) for item in payload["role_views"]),
            eligibility_audit=tuple(dict(item) for item in payload["eligibility_audit"]),
            selection_seed=str(payload["selection_seed"]),
            attempts=int(payload["attempts"]),
            static_size=int(payload["static_size"]),
            rotating_size=int(payload["rotating_size"]),
            source_manifest_sha256=str(payload["source_manifest_sha256"]),
            rubric_protocol_version=str(payload["rubric_protocol_version"]),
            selection_algorithm=str(payload["selection_algorithm"]),
        )
        if result.canonical_payload != payload:
            raise ValueError("B-local manifest canonical payload mismatch")
        if result.manifest_sha256 != value["manifest_sha256"]:
            raise ValueError("B-local manifest SHA mismatch")
        return result


def _ranked_papers(
    papers: Sequence[BLocalPaper], *, role: str, seed: str, attempt: str
) -> Tuple[BLocalPaper, ...]:
    def key(item: BLocalPaper):
        digest = _sha256(
            canonical_json_bytes(
                {
                    "protocol": B_LOCAL_SCHEMA_VERSION,
                    "role": role,
                    "selection_seed": seed,
                    "attempt": attempt,
                    "family_id": item.family_id,
                }
            )
        )
        return digest, item.family_id, item.paper_id

    return tuple(sorted(papers, key=key))


def materialize_b_local_manifest(
    *,
    papers: Iterable[Mapping[str, Any]],
    eligibility: Mapping[str, Mapping[str, Mapping[str, Any]]],
    thresholds: Mapping[str, Mapping[str, int]],
    selection_seed: str,
    attempts: int,
    static_size: int = 25,
    rotating_size: int = 5,
    source_manifest_sha256: str,
    rubric_protocol_version: str,
) -> BLocalManifest:
    """Freeze B-local membership from Candidate-blind role eligibility facts."""
    values = tuple(
        sorted(
            (BLocalPaper.from_mapping(item) for item in papers),
            key=lambda item: (item.family_id, item.paper_id),
        )
    )
    if not selection_seed or not rubric_protocol_version:
        raise ValueError("B-local selection seed and rubric protocol are required")
    attempts = int(attempts)
    static_size = int(static_size)
    rotating_size = int(rotating_size)
    if attempts < 0 or static_size < 1 or rotating_size < 0:
        raise ValueError("B-local attempts/static/rotating sizes are invalid")
    paper_by_id = {item.paper_id: item for item in values}
    if len(paper_by_id) != len(values):
        raise ValueError("B-local paper IDs are duplicated")

    audit = []
    views = []
    for role in ROLE_ORDER:
        if set(eligibility.get(role, {})) != set(paper_by_id):
            raise ValueError(f"B-local eligibility coverage mismatch for {role}")
        eligible = []
        for paper_id in sorted(paper_by_id):
            decision = eligibility[role][paper_id]
            if not isinstance(decision, Mapping) or set(decision) != {
                "eligible", "reason_code"
            }:
                raise ValueError("B-local eligibility decision fields mismatch")
            if type(decision["eligible"]) is not bool or not str(decision["reason_code"]):
                raise TypeError("B-local eligibility decision is invalid")
            audit.append(
                {
                    "role": role,
                    "paper_id": paper_id,
                    "family_id": paper_by_id[paper_id].family_id,
                    "eligible": decision["eligible"],
                    "reason_code": str(decision["reason_code"]),
                }
            )
            if decision["eligible"]:
                eligible.append(paper_by_id[paper_id])
        ranked = _ranked_papers(eligible, role=role, seed=selection_seed, attempt="STATIC")
        if len(ranked) < static_size:
            raise ValueError(f"B-local {role} has fewer than static_size eligible families")
        static = ranked[:static_size]
        static_ids = {item.paper_id for item in static}
        rotating_pool = tuple(item for item in eligible if item.paper_id not in static_ids)
        if attempts and len(rotating_pool) < rotating_size:
            raise ValueError(f"B-local {role} rotating pool is too small")
        schedule = tuple(
            tuple(
                item.paper_id
                for item in _ranked_papers(
                    rotating_pool,
                    role=role,
                    seed=selection_seed,
                    attempt=f"ROTATING_{attempt}",
                )[:rotating_size]
            )
            for attempt in range(1, attempts + 1)
        )
        threshold = thresholds.get(role)
        if not isinstance(threshold, Mapping) or set(threshold) != {
            "min_static_evaluable", "min_full_pool_evaluable"
        }:
            raise ValueError(f"B-local thresholds are missing for {role}")
        min_static = threshold["min_static_evaluable"]
        min_full = threshold["min_full_pool_evaluable"]
        if (
            type(min_static) is not int
            or type(min_full) is not int
            or not 0 <= min_static <= static_size
            or not 0 <= min_full <= len(values)
        ):
            raise ValueError(f"B-local thresholds are invalid for {role}")
        views.append(
            BLocalRoleView(
                role=role,
                eligible_paper_ids=tuple(item.paper_id for item in ranked),
                static_paper_ids=tuple(item.paper_id for item in static),
                rotating_pool_paper_ids=tuple(
                    item.paper_id
                    for item in _ranked_papers(
                        rotating_pool,
                        role=role,
                        seed=selection_seed,
                        attempt="ROTATING_POOL",
                    )
                ),
                rotating_schedule=schedule,
                full_pool_paper_ids=tuple(item.paper_id for item in values),
                min_static_evaluable=min_static,
                min_full_pool_evaluable=min_full,
            )
        )
    return BLocalManifest(
        papers=values,
        role_views=tuple(views),
        eligibility_audit=tuple(audit),
        selection_seed=selection_seed,
        attempts=attempts,
        static_size=static_size,
        rotating_size=rotating_size,
        source_manifest_sha256=_require_sha(source_manifest_sha256, "source manifest SHA"),
        rubric_protocol_version=rubric_protocol_version,
    )
