"""Deterministic Full Review Corpus records and retrieval projections.

This module is deliberately model-free.  It owns the canonical schema, content
identities and the only allowed projection/fragmentation path.  Corpus callers
provide already-generated no-memory reviewer responses and a frozen tokenizer;
the module never reads an existing Memory corpus or calls an LLM.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Sequence, Tuple


CORPUS_SCHEMA_VERSION = "agentreview-full-review-corpus-v4"
PROJECTION_SCHEMA_VERSION = "agentreview-review-projection-v1"
FRAGMENT_SCHEMA_VERSION = "agentreview-review-fragment-v1"
DEFAULT_MAX_PROJECTION_TOKENS = 2048

ROLE_SUBDIMENSIONS: Dict[str, Tuple[str, ...]] = {
    "QUAL": ("QUAL-MET", "QUAL-EXP", "QUAL-REP", "QUAL-CMP", "QUAL-STA"),
    "CLAR": ("CLAR-WRT", "CLAR-NOT", "CLAR-FIG"),
    "SIGN": ("SIGN-BRD", "SIGN-DOM", "SIGN-SOT", "SIGN-IMP"),
    "ORIG": (
        "ORIG-PROB",
        "ORIG-MTH",
        "ORIG-ANL",
        "ORIG-EXP",
        "ORIG-COM",
        "ORIG-NEG",
    ),
    "POL": ("POL-ETH", "POL-DAT", "POL-ANO", "POL-PLG", "POL-IMP"),
}

_ANCHOR_RE = re.compile(r"^SRC-\d{3}(?:\(page=\d+\))?$")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _content_id(prefix: str, identity_type: str, version: str, payload: Any) -> str:
    identity = {
        "identity_type": identity_type,
        "schema_version": version,
        "payload": payload,
    }
    return prefix + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _strict_object(value: Any, *, fields: set[str], label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    actual = set(value)
    if actual != fields:
        raise ValueError(
            f"{label} fields mismatch: missing={sorted(fields - actual)}, "
            f"extra={sorted(actual - fields)}"
        )
    return value


def _anchor_tuple(
    values: Any, *, allowed_paper_anchor_ids: Sequence[str] | None = None
) -> Tuple[str, ...]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise TypeError("paper_anchor_ids must be a JSON string array")
    invalid = [value for value in values if not _ANCHOR_RE.fullmatch(value)]
    if invalid:
        raise ValueError(f"Invalid paper anchor ids: {invalid}")
    if len(values) != len(set(values)):
        raise ValueError("Duplicate paper anchor ids are forbidden")
    if allowed_paper_anchor_ids is not None:
        allowed = set(allowed_paper_anchor_ids)
        foreign = sorted(set(values) - allowed)
        if foreign:
            raise ValueError(f"Foreign or unknown paper anchor ids: {foreign}")
    # Anchor IDs are an unordered evidence set. Canonicalization, rather than
    # the model's incidental emission order, determines their stored bytes.
    # The exact raw model output remains immutable in the build audit.
    return tuple(sorted(values))


@dataclass(frozen=True)
class SubdimensionSection:
    subdimension_id: str
    review_text: str
    paper_anchor_ids: Tuple[str, ...]

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        allowed_paper_anchor_ids: Sequence[str] | None = None,
    ) -> "SubdimensionSection":
        item = _strict_object(
            value,
            fields={"subdimension_id", "review_text", "paper_anchor_ids"},
            label="subdimension section",
        )
        subdimension_id = item["subdimension_id"]
        review_text = item["review_text"]
        if not isinstance(subdimension_id, str) or not subdimension_id:
            raise TypeError("subdimension_id must be a non-empty string")
        if not isinstance(review_text, str) or not review_text.strip():
            raise TypeError("review_text must be a non-empty string")
        return cls(
            subdimension_id,
            review_text,
            _anchor_tuple(
                item["paper_anchor_ids"],
                allowed_paper_anchor_ids=allowed_paper_anchor_ids,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subdimension_id": self.subdimension_id,
            "review_text": self.review_text,
            "paper_anchor_ids": list(self.paper_anchor_ids),
        }


@dataclass(frozen=True)
class CanonicalFullReviewRecord:
    role: str
    paper_id: str
    subdimension_sections: Tuple[SubdimensionSection, ...]
    overall_assessment: str | None
    raw_response_sha256: str
    paper_identity_sha256: str
    structural_context_sha256: str
    reviewer_identity_sha256: str
    prompt_identity_sha256: str
    request_identity_sha256: str
    schema_version: str = CORPUS_SCHEMA_VERSION

    @property
    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "paper_id": self.paper_id,
            "subdimension_sections": [
                section.to_dict() for section in self.subdimension_sections
            ],
            "overall_assessment": self.overall_assessment,
        }

    @property
    def canonical_payload_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.canonical_payload)).hexdigest()

    @property
    def full_review_record_id(self) -> str:
        return _content_id(
            "fullrev_",
            "canonical_full_review_record",
            self.schema_version,
            self.to_dict(include_id=False),
        )

    def validate(self) -> None:
        if self.schema_version != CORPUS_SCHEMA_VERSION:
            raise ValueError(f"Unsupported Full Review schema: {self.schema_version}")
        expected = ROLE_SUBDIMENSIONS.get(self.role)
        if expected is None:
            raise ValueError(f"Unknown review role: {self.role}")
        if not self.paper_id:
            raise ValueError("paper_id is required")
        actual = tuple(section.subdimension_id for section in self.subdimension_sections)
        if actual != expected:
            raise ValueError(
                f"{self.role} subdimension order mismatch: expected={expected}, actual={actual}"
            )
        for name in (
            "raw_response_sha256",
            "paper_identity_sha256",
            "structural_context_sha256",
            "reviewer_identity_sha256",
            "prompt_identity_sha256",
            "request_identity_sha256",
        ):
            digest = getattr(self, name)
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.overall_assessment is not None and not isinstance(
            self.overall_assessment, str
        ):
            raise TypeError("overall_assessment must be a string or null")

    def to_dict(self, *, include_id: bool = True) -> Dict[str, Any]:
        self.validate()
        value = {
            "schema_version": self.schema_version,
            **self.canonical_payload,
            "raw_response_sha256": self.raw_response_sha256,
            "canonical_payload_sha256": self.canonical_payload_sha256,
            "paper_identity_sha256": self.paper_identity_sha256,
            "structural_context_sha256": self.structural_context_sha256,
            "reviewer_identity_sha256": self.reviewer_identity_sha256,
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "request_identity_sha256": self.request_identity_sha256,
        }
        if include_id:
            value["full_review_record_id"] = self.full_review_record_id
        return value

    @classmethod
    def from_model_response(
        cls,
        raw_response: bytes,
        *,
        expected_role: str,
        expected_paper_id: str,
        paper_identity_sha256: str,
        structural_context_sha256: str,
        reviewer_identity_sha256: str,
        prompt_identity_sha256: str,
        request_identity_sha256: str,
        allowed_paper_anchor_ids: Sequence[str] | None = None,
    ) -> "CanonicalFullReviewRecord":
        try:
            text = raw_response.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Reviewer response is not valid UTF-8") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Reviewer response must be one strict JSON object") from exc
        payload = _strict_object(
            payload,
            fields={"role", "paper_id", "subdimension_sections", "overall_assessment"},
            label="Full Review payload",
        )
        if payload["role"] != expected_role or payload["paper_id"] != expected_paper_id:
            raise ValueError("Full Review role/paper identity mismatch")
        if not isinstance(payload["subdimension_sections"], list):
            raise TypeError("subdimension_sections must be an array")
        sections = tuple(
            SubdimensionSection.from_value(
                value,
                allowed_paper_anchor_ids=allowed_paper_anchor_ids,
            )
            for value in payload["subdimension_sections"]
        )
        record = cls(
            role=expected_role,
            paper_id=expected_paper_id,
            subdimension_sections=sections,
            overall_assessment=payload["overall_assessment"],
            raw_response_sha256=hashlib.sha256(raw_response).hexdigest(),
            paper_identity_sha256=paper_identity_sha256,
            structural_context_sha256=structural_context_sha256,
            reviewer_identity_sha256=reviewer_identity_sha256,
            prompt_identity_sha256=prompt_identity_sha256,
            request_identity_sha256=request_identity_sha256,
        )
        record.validate()
        return record

    @classmethod
    def from_value(cls, value: Any) -> "CanonicalFullReviewRecord":
        if not isinstance(value, dict):
            raise TypeError("Full Review record must be an object")
        expected_fields = {
            "schema_version",
            "role",
            "paper_id",
            "subdimension_sections",
            "overall_assessment",
            "raw_response_sha256",
            "canonical_payload_sha256",
            "paper_identity_sha256",
            "structural_context_sha256",
            "reviewer_identity_sha256",
            "prompt_identity_sha256",
            "request_identity_sha256",
            "full_review_record_id",
        }
        _strict_object(value, fields=expected_fields, label="Full Review record")
        record = cls(
            role=value["role"],
            paper_id=value["paper_id"],
            subdimension_sections=tuple(
                SubdimensionSection.from_value(item)
                for item in value["subdimension_sections"]
            ),
            overall_assessment=value["overall_assessment"],
            raw_response_sha256=value["raw_response_sha256"],
            paper_identity_sha256=value["paper_identity_sha256"],
            structural_context_sha256=value["structural_context_sha256"],
            reviewer_identity_sha256=value["reviewer_identity_sha256"],
            prompt_identity_sha256=value["prompt_identity_sha256"],
            request_identity_sha256=value["request_identity_sha256"],
            schema_version=value["schema_version"],
        )
        record.validate()
        if value["canonical_payload_sha256"] != record.canonical_payload_sha256:
            raise ValueError("Full Review canonical payload SHA mismatch")
        if value["full_review_record_id"] != record.full_review_record_id:
            raise ValueError("Full Review record identity mismatch")
        return record


@dataclass(frozen=True)
class PrimaryProjection:
    projection_id: str
    full_review_record_id: str
    role: str
    subdimension_id: str
    review_text: str
    section_paper_anchor_ids: Tuple[str, ...]
    materialization_identity: str
    schema_version: str = PROJECTION_SCHEMA_VERSION

    @classmethod
    def from_record(
        cls,
        record: CanonicalFullReviewRecord,
        section: SubdimensionSection,
        *,
        materialization_identity: str,
    ) -> "PrimaryProjection":
        payload = {
            "full_review_record_id": record.full_review_record_id,
            "role": record.role,
            "subdimension_id": section.subdimension_id,
            "review_text": section.review_text,
            "section_paper_anchor_ids": list(section.paper_anchor_ids),
            "materialization_identity": materialization_identity,
        }
        return cls(
            projection_id=_content_id(
                "proj_", "primary_review_projection", PROJECTION_SCHEMA_VERSION, payload
            ),
            full_review_record_id=record.full_review_record_id,
            role=record.role,
            subdimension_id=section.subdimension_id,
            review_text=section.review_text,
            section_paper_anchor_ids=section.paper_anchor_ids,
            materialization_identity=materialization_identity,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "section_paper_anchor_ids": list(self.section_paper_anchor_ids),
        }


@dataclass(frozen=True)
class ProjectionFragment:
    projection_fragment_id: str
    projection_id: str
    full_review_record_id: str
    role: str
    subdimension_id: str
    fragment_ordinal: int
    text: str
    character_start: int
    character_end: int
    token_count: int
    inherited_section_anchor_ids: Tuple[str, ...]
    explicit_fragment_anchor_ids: Tuple[str, ...]
    materialization_identity: str
    schema_version: str = FRAGMENT_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "inherited_section_anchor_ids": list(self.inherited_section_anchor_ids),
            "explicit_fragment_anchor_ids": list(self.explicit_fragment_anchor_ids),
        }


class FrozenTokenizer:
    """Small deterministic tokenizer adapter used by the fragmenter.

    ``encode`` must return the same token sequence as the frozen embedding
    tokenizer.  Prefix cuts are found by deterministic binary search, avoiding a
    dependency on optional offset-mapping support.
    """

    def __init__(self, encode: Callable[[str], Sequence[Any]], identity: str):
        if not identity:
            raise ValueError("Tokenizer identity is required")
        self._encode = encode
        self.identity = identity

    def count(self, text: str) -> int:
        return len(self._encode(text))

    def largest_prefix(self, text: str, max_tokens: int) -> int:
        if not text:
            return 0
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        low, high = 1, len(text)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if self.count(text[:middle]) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError("Tokenizer cannot fit one character in max_tokens")
        return best


def _boundary_units(text: str, kind: str) -> Iterator[Tuple[int, int]]:
    if kind == "paragraph":
        pattern = re.compile(r".*?(?:\n[ \t]*\n|\Z)", re.DOTALL)
    elif kind == "sentence":
        pattern = re.compile(r".*?(?:[.!?。！？](?:\s+|\Z)|\Z)", re.DOTALL)
    else:
        raise ValueError(f"Unknown boundary kind: {kind}")
    for match in pattern.finditer(text):
        if match.end() > match.start():
            yield match.start(), match.end()


def _split_range(
    text: str,
    start: int,
    end: int,
    *,
    tokenizer: FrozenTokenizer,
    max_tokens: int,
    kind: str,
) -> list[Tuple[int, int]]:
    value = text[start:end]
    if tokenizer.count(value) <= max_tokens:
        return [(start, end)]
    if kind in {"paragraph", "sentence"}:
        next_kind = "sentence" if kind == "paragraph" else "token"
        units = [(start + left, start + right) for left, right in _boundary_units(value, kind)]
        output: list[Tuple[int, int]] = []
        pending_start: int | None = None
        pending_end: int | None = None
        for unit_start, unit_end in units:
            if tokenizer.count(text[unit_start:unit_end]) > max_tokens:
                if pending_start is not None:
                    output.append((pending_start, pending_end or pending_start))
                    pending_start = pending_end = None
                output.extend(
                    _split_range(
                        text,
                        unit_start,
                        unit_end,
                        tokenizer=tokenizer,
                        max_tokens=max_tokens,
                        kind=next_kind,
                    )
                )
                continue
            if pending_start is None:
                pending_start, pending_end = unit_start, unit_end
            elif tokenizer.count(text[pending_start:unit_end]) <= max_tokens:
                pending_end = unit_end
            else:
                output.append((pending_start, pending_end or pending_start))
                pending_start, pending_end = unit_start, unit_end
        if pending_start is not None:
            output.append((pending_start, pending_end or pending_start))
        return output

    output = []
    cursor = start
    while cursor < end:
        length = tokenizer.largest_prefix(text[cursor:end], max_tokens)
        output.append((cursor, cursor + length))
        cursor += length
    return output


def fragment_projection(
    projection: PrimaryProjection,
    *,
    tokenizer: FrozenTokenizer,
    max_projection_tokens: int = DEFAULT_MAX_PROJECTION_TOKENS,
) -> Tuple[ProjectionFragment, ...]:
    if max_projection_tokens <= 0:
        raise ValueError("max_projection_tokens must be positive")
    text = projection.review_text
    ranges = _split_range(
        text,
        0,
        len(text),
        tokenizer=tokenizer,
        max_tokens=max_projection_tokens,
        kind="paragraph",
    )
    if not ranges or ranges[0][0] != 0 or ranges[-1][1] != len(text):
        raise RuntimeError("Fragment ranges do not cover the projection")
    if any(left[1] != right[0] for left, right in zip(ranges, ranges[1:])):
        raise RuntimeError("Fragment ranges are not contiguous")
    fragments = []
    for ordinal, (start, end) in enumerate(ranges):
        fragment_text = text[start:end]
        explicit = tuple(
            sorted(
                set(re.findall(r"SRC-\d{3}(?:\(page=\d+\))?", fragment_text))
                & set(projection.section_paper_anchor_ids)
            )
        )
        payload = {
            "projection_id": projection.projection_id,
            "full_review_record_id": projection.full_review_record_id,
            "role": projection.role,
            "subdimension_id": projection.subdimension_id,
            "fragment_ordinal": ordinal,
            "text": fragment_text,
            "character_start": start,
            "character_end": end,
            "token_count": tokenizer.count(fragment_text),
            "inherited_section_anchor_ids": list(projection.section_paper_anchor_ids),
            "explicit_fragment_anchor_ids": list(explicit),
            "materialization_identity": projection.materialization_identity,
            "tokenizer_identity": tokenizer.identity,
            "max_projection_tokens": max_projection_tokens,
        }
        fragments.append(
            ProjectionFragment(
                projection_fragment_id=_content_id(
                    "frag_", "review_projection_fragment", FRAGMENT_SCHEMA_VERSION, payload
                ),
                projection_id=projection.projection_id,
                full_review_record_id=projection.full_review_record_id,
                role=projection.role,
                subdimension_id=projection.subdimension_id,
                fragment_ordinal=ordinal,
                text=fragment_text,
                character_start=start,
                character_end=end,
                token_count=payload["token_count"],
                inherited_section_anchor_ids=projection.section_paper_anchor_ids,
                explicit_fragment_anchor_ids=explicit,
                materialization_identity=projection.materialization_identity,
            )
        )
    return tuple(fragments)


@dataclass(frozen=True)
class CorpusMaterialization:
    records: Tuple[CanonicalFullReviewRecord, ...]
    projections: Tuple[PrimaryProjection, ...]
    fragments: Tuple[ProjectionFragment, ...]
    materialization_identity: str
    tokenizer_identity: str
    max_projection_tokens: int

    @property
    def manifest(self) -> Dict[str, Any]:
        records = [record.to_dict() for record in self.records]
        projections = [projection.to_dict() for projection in self.projections]
        fragments = [fragment.to_dict() for fragment in self.fragments]
        payload = {
            "schema_version": CORPUS_SCHEMA_VERSION,
            "materialization_identity": self.materialization_identity,
            "tokenizer_identity": self.tokenizer_identity,
            "max_projection_tokens": self.max_projection_tokens,
            "record_count": len(records),
            "projection_count": len(projections),
            "fragment_count": len(fragments),
            "record_ids": [record["full_review_record_id"] for record in records],
            "projection_ids": [item["projection_id"] for item in projections],
            "fragment_ids": [item["projection_fragment_id"] for item in fragments],
        }
        payload["corpus_manifest_sha256"] = hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest()
        return payload

    def write(self, directory: str | Path) -> None:
        target = Path(directory).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        values = {
            "full_reviews.jsonl": [record.to_dict() for record in self.records],
            "projections.jsonl": [item.to_dict() for item in self.projections],
            "fragments.jsonl": [item.to_dict() for item in self.fragments],
        }
        for name, records in values.items():
            path = target / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("wb") as handle:
                for record in records:
                    handle.write(canonical_json_bytes(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        manifest_path = target / "manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        with temporary.open("wb") as handle:
            handle.write(canonical_json_bytes(self.manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)

    @classmethod
    def load(cls, directory: str | Path) -> "CorpusMaterialization":
        source = Path(directory).expanduser().resolve()

        def jsonl(name: str) -> list[Dict[str, Any]]:
            output = []
            with (source / name).open("rb") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line.decode("utf-8", errors="strict"))
                    except Exception as exc:
                        raise ValueError(
                            f"Invalid frozen Corpus JSONL {name}:{line_number}"
                        ) from exc
                    if not isinstance(value, dict):
                        raise TypeError(f"Expected object in {name}:{line_number}")
                    output.append(value)
            return output

        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        required_manifest = {
            "schema_version",
            "materialization_identity",
            "tokenizer_identity",
            "max_projection_tokens",
            "record_count",
            "projection_count",
            "fragment_count",
            "record_ids",
            "projection_ids",
            "fragment_ids",
            "corpus_manifest_sha256",
        }
        _strict_object(manifest, fields=required_manifest, label="Corpus manifest")
        supplied_sha = manifest["corpus_manifest_sha256"]
        manifest_payload = dict(manifest)
        manifest_payload.pop("corpus_manifest_sha256")
        if supplied_sha != hashlib.sha256(canonical_json_bytes(manifest_payload)).hexdigest():
            raise ValueError("Corpus manifest SHA mismatch")

        records = tuple(
            CanonicalFullReviewRecord.from_value(value)
            for value in jsonl("full_reviews.jsonl")
        )
        projection_fields = {
            "projection_id",
            "full_review_record_id",
            "role",
            "subdimension_id",
            "review_text",
            "section_paper_anchor_ids",
            "materialization_identity",
            "schema_version",
        }
        projections = []
        for value in jsonl("projections.jsonl"):
            _strict_object(value, fields=projection_fields, label="Primary Projection")
            projection = PrimaryProjection(
                **{
                    **value,
                    "section_paper_anchor_ids": _anchor_tuple(
                        value["section_paper_anchor_ids"]
                    ),
                }
            )
            record = next(
                (item for item in records if item.full_review_record_id == projection.full_review_record_id),
                None,
            )
            if record is None:
                raise ValueError("Projection references an unknown Full Review")
            section = next(
                (item for item in record.subdimension_sections if item.subdimension_id == projection.subdimension_id),
                None,
            )
            expected = (
                PrimaryProjection.from_record(
                    record,
                    section,
                    materialization_identity=manifest["materialization_identity"],
                )
                if section is not None
                else None
            )
            if expected != projection:
                raise ValueError("Primary Projection content/identity mismatch")
            projections.append(projection)

        fragment_fields = {
            "projection_fragment_id",
            "projection_id",
            "full_review_record_id",
            "role",
            "subdimension_id",
            "fragment_ordinal",
            "text",
            "character_start",
            "character_end",
            "token_count",
            "inherited_section_anchor_ids",
            "explicit_fragment_anchor_ids",
            "materialization_identity",
            "schema_version",
        }
        fragments = []
        by_projection = {value.projection_id: value for value in projections}
        for value in jsonl("fragments.jsonl"):
            _strict_object(value, fields=fragment_fields, label="Projection Fragment")
            fragment = ProjectionFragment(
                **{
                    **value,
                    "inherited_section_anchor_ids": _anchor_tuple(
                        value["inherited_section_anchor_ids"]
                    ),
                    "explicit_fragment_anchor_ids": _anchor_tuple(
                        value["explicit_fragment_anchor_ids"]
                    ),
                }
            )
            projection = by_projection.get(fragment.projection_id)
            if projection is None:
                raise ValueError("Fragment references an unknown Projection")
            payload = {
                "projection_id": fragment.projection_id,
                "full_review_record_id": fragment.full_review_record_id,
                "role": fragment.role,
                "subdimension_id": fragment.subdimension_id,
                "fragment_ordinal": fragment.fragment_ordinal,
                "text": fragment.text,
                "character_start": fragment.character_start,
                "character_end": fragment.character_end,
                "token_count": fragment.token_count,
                "inherited_section_anchor_ids": list(fragment.inherited_section_anchor_ids),
                "explicit_fragment_anchor_ids": list(fragment.explicit_fragment_anchor_ids),
                "materialization_identity": fragment.materialization_identity,
                "tokenizer_identity": manifest["tokenizer_identity"],
                "max_projection_tokens": manifest["max_projection_tokens"],
            }
            expected_id = _content_id(
                "frag_", "review_projection_fragment", FRAGMENT_SCHEMA_VERSION, payload
            )
            if expected_id != fragment.projection_fragment_id:
                raise ValueError("Projection Fragment content/identity mismatch")
            if fragment.text != projection.review_text[
                fragment.character_start : fragment.character_end
            ]:
                raise ValueError("Projection Fragment offsets mismatch")
            fragments.append(fragment)

        result = cls(
            records=records,
            projections=tuple(projections),
            fragments=tuple(fragments),
            materialization_identity=manifest["materialization_identity"],
            tokenizer_identity=manifest["tokenizer_identity"],
            max_projection_tokens=int(manifest["max_projection_tokens"]),
        )
        if result.manifest != manifest:
            raise ValueError("Frozen Corpus manifest/content mismatch")
        return result


def materialize_corpus(
    records: Iterable[CanonicalFullReviewRecord],
    *,
    tokenizer: FrozenTokenizer,
    max_projection_tokens: int = DEFAULT_MAX_PROJECTION_TOKENS,
    materialization_identity: str = "review-corpus-materialization-v1",
) -> CorpusMaterialization:
    ordered_records = tuple(records)
    keys = [(record.paper_id, record.role) for record in ordered_records]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate paper × role Full Review record")
    projections = []
    fragments = []
    for record in ordered_records:
        record.validate()
        for section in record.subdimension_sections:
            projection = PrimaryProjection.from_record(
                record, section, materialization_identity=materialization_identity
            )
            projections.append(projection)
            fragments.extend(
                fragment_projection(
                    projection,
                    tokenizer=tokenizer,
                    max_projection_tokens=max_projection_tokens,
                )
            )
    return CorpusMaterialization(
        records=ordered_records,
        projections=tuple(projections),
        fragments=tuple(fragments),
        materialization_identity=materialization_identity,
        tokenizer_identity=tokenizer.identity,
        max_projection_tokens=max_projection_tokens,
    )
