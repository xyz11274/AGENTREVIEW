"""Model-free, deterministic read-only Review Memory.

The module owns exact-flat ranking, role isolation, atomic budgets and the
paper-local retrieval ledger.  It intentionally exposes no LLM completion
capability and has no write path back into the frozen Corpus.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple

from .review_corpus import (
    CanonicalFullReviewRecord,
    CorpusMaterialization,
    PrimaryProjection,
    ProjectionFragment,
    canonical_json_bytes,
)


INDEX_SCHEMA_VERSION = "agentreview-review-memory-exact-flat-v1"
LEDGER_SCHEMA_VERSION = "agentreview-retrieved-memory-ledger-v1"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(prefix: str, identity_type: str, payload: Any) -> str:
    value = {
        "identity_type": identity_type,
        "schema_version": INDEX_SCHEMA_VERSION,
        "payload": payload,
    }
    return prefix + _sha256(canonical_json_bytes(value))


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def canonical_normalized_float32(values: Sequence[float]) -> Tuple[Tuple[float, ...], bytes]:
    if not values:
        raise ValueError("Embedding vector cannot be empty")
    vector = tuple(_float32(value) for value in values)
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Embedding vector contains a non-finite value")
    squared = 0.0
    for value in vector:
        squared += float(value) * float(value)
    norm = math.sqrt(squared)
    if norm == 0.0:
        raise ValueError("Embedding vector norm cannot be zero")
    normalized = tuple(_float32(float(value) / norm) for value in vector)
    raw = b"".join(struct.pack("<f", value) for value in normalized)
    return normalized, raw


def exact_inner_product(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions differ")
    total = 0.0
    for index in range(len(left)):
        total += float(left[index]) * float(right[index])
    return total


@dataclass(frozen=True)
class IndexedFragment:
    projection_fragment_id: str
    vector: Tuple[float, ...]
    vector_bytes: bytes
    vector_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "projection_fragment_id": self.projection_fragment_id,
            "vector_base64": base64.b64encode(self.vector_bytes).decode("ascii"),
            "vector_byte_length": len(self.vector_bytes),
            "vector_sha256": self.vector_sha256,
        }


@dataclass(frozen=True)
class ReviewMemoryIndex:
    role: str
    records: Tuple[IndexedFragment, ...]
    embedding_identity: str
    source_manifest_sha256: str
    index_identity: str
    dimension: int
    schema_version: str = INDEX_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        role: str,
        fragments: Iterable[ProjectionFragment],
        embed: Callable[[Sequence[str]], Sequence[Sequence[float]]],
        embedding_identity: str,
        source_manifest_sha256: str,
    ) -> "ReviewMemoryIndex":
        selected = tuple(fragment for fragment in fragments if fragment.role == role)
        if not selected:
            raise ValueError(f"No Review Memory fragments for role {role}")
        vectors = list(embed([fragment.text for fragment in selected]))
        if len(vectors) != len(selected):
            raise ValueError("Embedding result cardinality mismatch")
        indexed = []
        dimension = None
        for fragment, values in zip(selected, vectors):
            vector, raw = canonical_normalized_float32(values)
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise ValueError("Embedding dimension changed within one index")
            indexed.append(
                IndexedFragment(
                    projection_fragment_id=fragment.projection_fragment_id,
                    vector=vector,
                    vector_bytes=raw,
                    vector_sha256=_sha256(raw),
                )
            )
        payload = {
            "role": role,
            "embedding_identity": embedding_identity,
            "source_manifest_sha256": source_manifest_sha256,
            "dimension": dimension,
            "records": [record.to_dict() for record in indexed],
            "ranking": "binary64_exact_inner_product_then_fragment_id",
        }
        return cls(
            role=role,
            records=tuple(indexed),
            embedding_identity=embedding_identity,
            source_manifest_sha256=source_manifest_sha256,
            index_identity=_identity("reviewindex_", "review_memory_index", payload),
            dimension=int(dimension or 0),
        )

    def query(
        self,
        query_vector: Sequence[float],
        *,
        allowed_fragment_ids: set[str] | None = None,
    ) -> Tuple[Tuple[str, float], ...]:
        normalized, _ = canonical_normalized_float32(query_vector)
        if len(normalized) != self.dimension:
            raise ValueError("Query embedding dimension mismatch")
        scored = []
        for record in self.records:
            if allowed_fragment_ids is not None and record.projection_fragment_id not in allowed_fragment_ids:
                continue
            scored.append(
                (
                    record.projection_fragment_id,
                    exact_inner_product(normalized, record.vector),
                )
            )
        scored.sort(key=lambda value: (-value[1], value[0]))
        return tuple(scored)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "embedding_identity": self.embedding_identity,
            "source_manifest_sha256": self.source_manifest_sha256,
            "index_identity": self.index_identity,
            "dimension": self.dimension,
            "records": [record.to_dict() for record in self.records],
            "ranking": "binary64_exact_inner_product_then_fragment_id",
        }

    def write(self, path: str | Path) -> None:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(canonical_json_bytes(self.to_dict()))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    @classmethod
    def load(cls, path: str | Path) -> "ReviewMemoryIndex":
        value = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        fields = {
            "schema_version",
            "role",
            "embedding_identity",
            "source_manifest_sha256",
            "index_identity",
            "dimension",
            "records",
            "ranking",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Review Memory index schema mismatch")
        if value["schema_version"] != INDEX_SCHEMA_VERSION:
            raise ValueError("Unsupported Review Memory index version")
        if value["ranking"] != "binary64_exact_inner_product_then_fragment_id":
            raise ValueError("Review Memory ranking identity mismatch")
        dimension = int(value["dimension"])
        records = []
        for item in value["records"]:
            expected = {
                "projection_fragment_id",
                "vector_base64",
                "vector_byte_length",
                "vector_sha256",
            }
            if not isinstance(item, dict) or set(item) != expected:
                raise ValueError("Review Memory vector record schema mismatch")
            try:
                raw = base64.b64decode(item["vector_base64"], validate=True)
            except Exception as exc:
                raise ValueError("Invalid Review Memory vector Base64") from exc
            if len(raw) != item["vector_byte_length"] or _sha256(raw) != item["vector_sha256"]:
                raise ValueError("Review Memory vector bytes/SHA mismatch")
            if len(raw) != dimension * 4:
                raise ValueError("Review Memory vector dimension mismatch")
            vector = tuple(
                struct.unpack("<f", raw[offset : offset + 4])[0]
                for offset in range(0, len(raw), 4)
            )
            if any(not math.isfinite(number) for number in vector):
                raise ValueError("Review Memory vector contains a non-finite value")
            records.append(
                IndexedFragment(
                    projection_fragment_id=item["projection_fragment_id"],
                    vector=vector,
                    vector_bytes=raw,
                    vector_sha256=item["vector_sha256"],
                )
            )
        payload = {
            "role": value["role"],
            "embedding_identity": value["embedding_identity"],
            "source_manifest_sha256": value["source_manifest_sha256"],
            "dimension": dimension,
            "records": [record.to_dict() for record in records],
            "ranking": value["ranking"],
        }
        expected_id = _identity("reviewindex_", "review_memory_index", payload)
        if expected_id != value["index_identity"]:
            raise ValueError("Review Memory index identity mismatch")
        return cls(
            role=value["role"],
            records=tuple(records),
            embedding_identity=value["embedding_identity"],
            source_manifest_sha256=value["source_manifest_sha256"],
            index_identity=value["index_identity"],
            dimension=dimension,
        )


@dataclass(frozen=True)
class ReviewMemoryBudget:
    max_api_calls: int
    max_delivered_fragments: int
    max_full_record_hydrations: int
    max_delivered_tokens: int
    max_top_k: int
    max_memory_query_tokens_per_call: int

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class BudgetState:
    api_calls: int = 0
    delivered_fragments: int = 0
    full_record_hydrations: int = 0
    delivered_tokens: int = 0


@dataclass(frozen=True)
class RetrievalOccurrence:
    ordinal: int
    operation: str
    source_content_kind: str
    source_id: str
    full_review_record_id: str
    projection_id: str | None
    subdimension_id: str | None
    content: str
    paper_anchor_ids: Tuple[str, ...]
    token_count: int
    query_sha256: str | None = None
    similarity_hex: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "paper_anchor_ids": list(self.paper_anchor_ids)}


@dataclass(frozen=True)
class MemoryCallResult:
    disposition: str
    delivered: Tuple[RetrievalOccurrence, ...]
    budget_state: BudgetState


class ReviewMemoryStore:
    """Immutable Corpus and role indices shared by fresh paper sessions."""

    def __init__(
        self,
        materialization: CorpusMaterialization,
        indices: Mapping[str, ReviewMemoryIndex],
        *,
        embed_query: Callable[[str], Sequence[float]],
        token_count: Callable[[str], int],
        query_token_count: Callable[[str], int] | None = None,
    ):
        self.materialization = materialization
        self.indices = dict(indices)
        self.embed_query = embed_query
        self.token_count = token_count
        self.query_token_count = query_token_count or token_count
        self.records = {
            value.full_review_record_id: value for value in materialization.records
        }
        self.projections = {
            value.projection_id: value for value in materialization.projections
        }
        self.fragments = {
            value.projection_fragment_id: value for value in materialization.fragments
        }
        for role, index in self.indices.items():
            if role != index.role:
                raise ValueError("Review Memory index role key mismatch")
            unknown = {
                record.projection_fragment_id for record in index.records
            } - set(self.fragments)
            if unknown:
                raise ValueError(f"Review Memory index contains unknown fragments: {sorted(unknown)}")

    def open_session(
        self,
        *,
        candidate_id: str,
        paper_id: str,
        role: str,
        program_identity: str,
        budget: ReviewMemoryBudget,
    ) -> "ReviewMemorySession":
        if role not in self.indices:
            raise ValueError(f"No Review Memory index for role {role}")
        return ReviewMemorySession(
            self,
            candidate_id=candidate_id,
            paper_id=paper_id,
            role=role,
            program_identity=program_identity,
            budget=budget,
        )


class ReviewMemorySession:
    """Fresh Candidate × paper × role read session with atomic accounting."""

    def __init__(
        self,
        store: ReviewMemoryStore,
        *,
        candidate_id: str,
        paper_id: str,
        role: str,
        program_identity: str,
        budget: ReviewMemoryBudget,
    ):
        if not all((candidate_id, paper_id, role, program_identity)):
            raise ValueError("Review Memory session identities are required")
        budget.validate()
        self.store = store
        self.candidate_id = candidate_id
        self.paper_id = paper_id
        self.role = role
        self.program_identity = program_identity
        self.budget = budget
        self.state = BudgetState()
        self._ledger: list[RetrievalOccurrence] = []
        self.closed = False

    @property
    def ledger(self) -> Tuple[RetrievalOccurrence, ...]:
        return tuple(self._ledger)

    @property
    def ledger_sha256(self) -> str:
        return _sha256(
            canonical_json_bytes(
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "candidate_id": self.candidate_id,
                    "paper_id": self.paper_id,
                    "role": self.role,
                    "program_identity": self.program_identity,
                    "occurrences": [item.to_dict() for item in self._ledger],
                    "budget_state": asdict(self.state),
                }
            )
        )

    def close(self) -> None:
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("Review Memory paper session is closed")

    def _call_available(self) -> bool:
        return self.state.api_calls < self.budget.max_api_calls

    def _with_call(self, *, delivered_fragments: int, hydrations: int, tokens: int) -> bool:
        if not self._call_available():
            return False
        next_calls = self.state.api_calls + 1
        fits = (
            self.state.delivered_fragments + delivered_fragments
            <= self.budget.max_delivered_fragments
            and self.state.full_record_hydrations + hydrations
            <= self.budget.max_full_record_hydrations
            and self.state.delivered_tokens + tokens <= self.budget.max_delivered_tokens
        )
        if fits:
            self.state = BudgetState(
                api_calls=next_calls,
                delivered_fragments=self.state.delivered_fragments + delivered_fragments,
                full_record_hydrations=self.state.full_record_hydrations + hydrations,
                delivered_tokens=self.state.delivered_tokens + tokens,
            )
        else:
            self.state = BudgetState(
                api_calls=next_calls,
                delivered_fragments=self.state.delivered_fragments,
                full_record_hydrations=self.state.full_record_hydrations,
                delivered_tokens=self.state.delivered_tokens,
            )
        return fits

    def _exhausted(self) -> MemoryCallResult:
        return MemoryCallResult("REVIEW_MEMORY_BUDGET_EXHAUSTED", (), self.state)

    def vector_search(
        self,
        query: str,
        *,
        subdimension_filter: str | None = None,
        top_k: int = 5,
    ) -> MemoryCallResult:
        self._ensure_open()
        try:
            query_bytes = query.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_QUERY_UTF8") from exc
        if not query_bytes:
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_QUERY_EMPTY")
        if (
            int(self.store.query_token_count(query))
            > self.budget.max_memory_query_tokens_per_call
        ):
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_QUERY_TOKEN_BUDGET")
        if not isinstance(top_k, int) or top_k < 0 or top_k > self.budget.max_top_k:
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_TOP_K")
        allowed = None
        if subdimension_filter is not None:
            if not isinstance(subdimension_filter, str) or not subdimension_filter.startswith(
                self.role + "-"
            ):
                if self._call_available():
                    self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
                raise ValueError("INVALID_REVIEW_MEMORY_SUBDIMENSION_FILTER")
            allowed = {
                fragment.projection_fragment_id
                for fragment in self.store.fragments.values()
                if fragment.role == self.role
                and fragment.subdimension_id == subdimension_filter
            }
        ranked = self.store.indices[self.role].query(
            self.store.embed_query(query), allowed_fragment_ids=allowed
        )[:top_k]
        token_cost = sum(
            self.store.token_count(self.store.fragments[fragment_id].text)
            for fragment_id, _ in ranked
        )
        if not self._with_call(
            delivered_fragments=len(ranked), hydrations=0, tokens=token_cost
        ):
            return self._exhausted()
        query_sha = _sha256(query_bytes)
        delivered = []
        for fragment_id, similarity in ranked:
            fragment = self.store.fragments[fragment_id]
            occurrence = RetrievalOccurrence(
                ordinal=len(self._ledger),
                operation="vector_search",
                source_content_kind="PROJECTION_FRAGMENT",
                source_id=fragment.projection_fragment_id,
                full_review_record_id=fragment.full_review_record_id,
                projection_id=fragment.projection_id,
                subdimension_id=fragment.subdimension_id,
                content=fragment.text,
                paper_anchor_ids=fragment.explicit_fragment_anchor_ids,
                token_count=self.store.token_count(fragment.text),
                query_sha256=query_sha,
                similarity_hex=float(similarity).hex(),
            )
            self._ledger.append(occurrence)
            delivered.append(occurrence)
        return MemoryCallResult("SUCCESS", tuple(delivered), self.state)

    def get_projection(self, projection_id: str) -> MemoryCallResult:
        self._ensure_open()
        projection = self.store.projections.get(projection_id)
        if projection is None or projection.role != self.role:
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_PROJECTION_ID")
        token_cost = self.store.token_count(projection.review_text)
        if not self._with_call(delivered_fragments=0, hydrations=0, tokens=token_cost):
            return self._exhausted()
        occurrence = RetrievalOccurrence(
            ordinal=len(self._ledger),
            operation="get_projection",
            source_content_kind="PRIMARY_PROJECTION",
            source_id=projection.projection_id,
            full_review_record_id=projection.full_review_record_id,
            projection_id=projection.projection_id,
            subdimension_id=projection.subdimension_id,
            content=projection.review_text,
            paper_anchor_ids=projection.section_paper_anchor_ids,
            token_count=token_cost,
        )
        self._ledger.append(occurrence)
        return MemoryCallResult("SUCCESS", (occurrence,), self.state)

    def get_full_review_record(self, full_review_record_id: str) -> MemoryCallResult:
        self._ensure_open()
        record = self.store.records.get(full_review_record_id)
        if record is None or record.role != self.role:
            if self._call_available():
                self._with_call(delivered_fragments=0, hydrations=0, tokens=0)
            raise ValueError("INVALID_REVIEW_MEMORY_FULL_RECORD_ID")
        content = canonical_json_bytes(record.canonical_payload).decode("utf-8")
        token_cost = self.store.token_count(content)
        if not self._with_call(delivered_fragments=0, hydrations=1, tokens=token_cost):
            return self._exhausted()
        anchors = tuple(
            sorted(
                {
                    anchor
                    for section in record.subdimension_sections
                    for anchor in section.paper_anchor_ids
                }
            )
        )
        occurrence = RetrievalOccurrence(
            ordinal=len(self._ledger),
            operation="get_full_review_record",
            source_content_kind="FULL_REVIEW_RECORD",
            source_id=record.full_review_record_id,
            full_review_record_id=record.full_review_record_id,
            projection_id=None,
            subdimension_id=None,
            content=content,
            paper_anchor_ids=anchors,
            token_count=token_cost,
        )
        self._ledger.append(occurrence)
        return MemoryCallResult("SUCCESS", (occurrence,), self.state)
