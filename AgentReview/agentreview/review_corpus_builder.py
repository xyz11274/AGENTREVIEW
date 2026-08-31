"""Independent no-memory build stage for the frozen A Review Corpus.

The builder has deliberately no Review Memory or External Evidence dependency.
Its only generative capability is the frozen Reviewer completion used to create
one canonical Full Review per paper × role.  Projection and embedding begin only
after every canonical review exists.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple

from .corpus_role_prompts_v4 import (
    CORPUS_ROLE_PROMPT_VERSION,
    corpus_role_prompts,
)
from .corpus_semantic_audit import semantic_boundary_violations
from .review_corpus import (
    ROLE_SUBDIMENSIONS,
    CanonicalFullReviewRecord,
    CorpusMaterialization,
    FrozenTokenizer,
    canonical_json_bytes,
    materialize_corpus,
)


BUILD_SCHEMA_VERSION = "no-memory-full-review-corpus-build-v4"
STRUCTURAL_CONTEXT_SCHEMA_VERSION = "corpus-paper-structural-context-v1"
RESPONSE_FORMAT_SCHEMA_VERSION = "canonical-full-review-response-format-v4"
ROLE_ORDER = tuple(ROLE_SUBDIMENSIONS)

_SOURCE_ANCHOR_LINE_RE = re.compile(
    r"^(?P<anchor_id>SRC-\d{3}(?:\(page=\d+\))?)"
    r"\s*\|\s*page=(?P<page>\d+)"
    r"\s*\|\s*type=(?P<content_type>[^|\r\n]+?)"
    r"\s*\|\s*text=(?P<text>.*)$"
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class CorpusPaper:
    paper_id: str
    canonical_paper_context: str
    structural_context_sha256: str
    structural_context_canonical_json: bytes
    allowed_paper_anchor_ids: Tuple[str, ...]
    paper_identity_sha256: str

    @classmethod
    def from_context(
        cls,
        *,
        paper_id: str,
        canonical_paper_context: str,
    ) -> "CorpusPaper":
        if not paper_id or not canonical_paper_context:
            raise ValueError("Corpus paper id/context are required")
        structural_context = _structural_context_payload(
            paper_id=paper_id,
            canonical_paper_context=canonical_paper_context,
        )
        structural_bytes = canonical_json_bytes(structural_context)
        return cls(
            paper_id=paper_id,
            canonical_paper_context=canonical_paper_context,
            structural_context_sha256=_sha(structural_bytes),
            structural_context_canonical_json=structural_bytes,
            allowed_paper_anchor_ids=tuple(
                item["anchor_id"] for item in structural_context["source_anchors"]
            ),
            paper_identity_sha256=_sha(canonical_paper_context.encode("utf-8")),
        )


def _line_records(text: str) -> Tuple[Dict[str, Any], ...]:
    records = []
    char_cursor = 0
    byte_cursor = 0
    for ordinal, raw_line in enumerate(text.splitlines(keepends=True)):
        content = raw_line.rstrip("\r\n")
        content_bytes = content.encode("utf-8")
        raw_bytes = raw_line.encode("utf-8")
        records.append(
            {
                "ordinal": ordinal,
                "content": content,
                "char_start": char_cursor,
                "char_end": char_cursor + len(content),
                "byte_start": byte_cursor,
                "byte_end": byte_cursor + len(content_bytes),
            }
        )
        char_cursor += len(raw_line)
        byte_cursor += len(raw_bytes)
    if not records and text:
        raise RuntimeError("Structural line materialization failed")
    return tuple(records)


def _structural_context_payload(
    *, paper_id: str, canonical_paper_context: str
) -> Dict[str, Any]:
    """Project deterministic paper-derived structure without semantic judgment."""

    lines = _line_records(canonical_paper_context)
    context_protocol = ""
    metadata = []
    sections = []
    anchors = []
    source_anchor_preamble = []
    in_metadata = False
    in_anchors = False
    for line in lines:
        content = line["content"]
        if line["ordinal"] == 0 and content.startswith("Context protocol:"):
            context_protocol = content.split(":", 1)[1].strip()
        if content == "=== CANDIDATE-VISIBLE METADATA ===":
            in_metadata = True
        elif content == "=== END CANDIDATE-VISIBLE METADATA ===":
            in_metadata = False
        elif in_metadata and ":" in content:
            key, value = content.split(":", 1)
            metadata.append({"key": key.strip(), "value": value.strip()})

        if content == "=== SOURCE ANCHORS ===":
            in_anchors = True
        elif content == "=== END SOURCE ANCHORS ===":
            in_anchors = False
        elif in_anchors and content:
            match = _SOURCE_ANCHOR_LINE_RE.fullmatch(content)
            if match is None:
                if content == "Structured source anchors from the original paper extraction:":
                    source_anchor_preamble.append(
                        {
                            "text": content,
                            "line_ordinal": line["ordinal"],
                            "char_start": line["char_start"],
                            "char_end": line["char_end"],
                            "byte_start": line["byte_start"],
                            "byte_end": line["byte_end"],
                        }
                    )
                    continue
                raise ValueError(
                    f"Malformed Source Anchor line for {paper_id}: {content[:120]!r}"
                )
            groups = match.groupdict()
            anchors.append(
                {
                    "anchor_id": groups["anchor_id"],
                    "page": int(groups["page"]),
                    "content_type": groups["content_type"].strip(),
                    "line_ordinal": line["ordinal"],
                    "char_start": line["char_start"],
                    "char_end": line["char_end"],
                    "byte_start": line["byte_start"],
                    "byte_end": line["byte_end"],
                    "text_sha256": _sha(groups["text"].encode("utf-8")),
                    "text_char_length": len(groups["text"]),
                    "text_byte_length": len(groups["text"].encode("utf-8")),
                }
            )

        heading = re.fullmatch(r"(?P<marks>#{1,6})\s+(?P<title>.+)", content)
        is_boundary = content.startswith("===") and content.endswith("===")
        if heading or is_boundary:
            sections.append(
                {
                    "kind": "MARKDOWN_HEADING" if heading else "CONTEXT_BOUNDARY",
                    "level": len(heading.group("marks")) if heading else 0,
                    "label": heading.group("title") if heading else content,
                    "line_ordinal": line["ordinal"],
                    "char_start": line["char_start"],
                    "char_end": line["char_end"],
                    "byte_start": line["byte_start"],
                    "byte_end": line["byte_end"],
                }
            )
    if not context_protocol:
        raise ValueError(f"Corpus paper {paper_id} lacks Context protocol identity")
    metadata_map = {item["key"]: item["value"] for item in metadata}
    if metadata_map.get("paper_id") not in (None, paper_id):
        raise ValueError(
            f"Corpus paper metadata identity mismatch: {metadata_map.get('paper_id')} != {paper_id}"
        )
    anchor_ids = [item["anchor_id"] for item in anchors]
    if len(anchor_ids) != len(set(anchor_ids)):
        raise ValueError(f"Duplicate Source Anchor IDs in paper context: {paper_id}")
    if not anchors:
        raise ValueError(f"Corpus paper lacks Source Anchor registry: {paper_id}")
    return {
        "schema_version": STRUCTURAL_CONTEXT_SCHEMA_VERSION,
        "paper_id": paper_id,
        "context_protocol": context_protocol,
        "canonical_context_sha256": _sha(canonical_paper_context.encode("utf-8")),
        "canonical_context_char_length": len(canonical_paper_context),
        "canonical_context_byte_length": len(canonical_paper_context.encode("utf-8")),
        "metadata": metadata,
        "sections": sections,
        "source_anchor_preamble": source_anchor_preamble,
        "source_anchors": anchors,
    }


@dataclass(frozen=True)
class RawCorpusAttempt:
    paper_id: str
    role: str
    attempt_index: int
    assistant_content: bytes
    raw_provider_response: bytes
    finish_reason: str | None
    usage: Mapping[str, Any]
    request_identity_sha256: str
    valid: bool
    error: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": BUILD_SCHEMA_VERSION,
            "paper_id": self.paper_id,
            "role": self.role,
            "attempt_index": self.attempt_index,
            "assistant_content_base64": base64.b64encode(self.assistant_content).decode("ascii"),
            "assistant_content_sha256": _sha(self.assistant_content),
            "raw_provider_response_base64": base64.b64encode(
                self.raw_provider_response
            ).decode("ascii"),
            "raw_provider_response_sha256": _sha(self.raw_provider_response),
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
            "request_identity_sha256": self.request_identity_sha256,
            "valid": self.valid,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RawCorpusAttempt":
        required = {
            "schema_version",
            "paper_id",
            "role",
            "attempt_index",
            "assistant_content_base64",
            "assistant_content_sha256",
            "raw_provider_response_base64",
            "raw_provider_response_sha256",
            "finish_reason",
            "usage",
            "request_identity_sha256",
            "valid",
            "error",
        }
        if set(value) != required or value["schema_version"] != BUILD_SCHEMA_VERSION:
            raise ValueError("Raw Corpus attempt schema mismatch")
        assistant = base64.b64decode(value["assistant_content_base64"], validate=True)
        provider = base64.b64decode(
            value["raw_provider_response_base64"], validate=True
        )
        if _sha(assistant) != value["assistant_content_sha256"]:
            raise ValueError("Raw Corpus assistant content SHA mismatch")
        if _sha(provider) != value["raw_provider_response_sha256"]:
            raise ValueError("Raw Corpus provider response SHA mismatch")
        return cls(
            paper_id=value["paper_id"],
            role=value["role"],
            attempt_index=value["attempt_index"],
            assistant_content=assistant,
            raw_provider_response=provider,
            finish_reason=value["finish_reason"],
            usage=value["usage"],
            request_identity_sha256=value["request_identity_sha256"],
            valid=value["valid"],
            error=value["error"],
        )


class CorpusBuildFailure(RuntimeError):
    """A paper×role exhausted its one identical semantic retry."""

    def __init__(self, paper_id: str, role: str, attempts: Sequence[RawCorpusAttempt]):
        super().__init__(
            f"Corpus build fail-fast: two illegal Reviewer outputs for "
            f"{paper_id} × {role}"
        )
        self.paper_id = paper_id
        self.role = role
        self.attempts = tuple(attempts)


@dataclass(frozen=True)
class CorpusCompletionResponse:
    """Transport adapter result retained by the authoritative Corpus audit."""

    assistant_content: bytes
    raw_provider_response: bytes
    finish_reason: str | None = None
    usage: Mapping[str, Any] = None

    def __post_init__(self):
        if not isinstance(self.assistant_content, bytes):
            raise TypeError("Corpus assistant content must be bytes")
        if not isinstance(self.raw_provider_response, bytes):
            raise TypeError("Corpus raw provider response must be bytes")
        object.__setattr__(self, "usage", dict(self.usage or {}))


def _completion_response(value: Any) -> CorpusCompletionResponse:
    if isinstance(value, CorpusCompletionResponse):
        return value
    content = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return CorpusCompletionResponse(
        assistant_content=content,
        raw_provider_response=content,
        finish_reason=None,
        usage={},
    )


@dataclass(frozen=True)
class CorpusReviewerRequest:
    """Exact model-visible request used by the Corpus Builder.

    Request materialization and live generation share this construction seam so
    capacity planning cannot drift from production prompting.
    """

    paper_id: str
    role: str
    messages: Tuple[Dict[str, str], ...]
    response_format: Mapping[str, Any]
    response_format_sha256: str
    prompt_identity_sha256: str
    request_identity_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "role": self.role,
            "messages": [dict(message) for message in self.messages],
            "response_format": dict(self.response_format),
            "response_format_sha256": self.response_format_sha256,
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "request_identity_sha256": self.request_identity_sha256,
        }


@dataclass(frozen=True)
class CorpusBuildResult:
    records: Tuple[CanonicalFullReviewRecord, ...]
    raw_attempts: Tuple[RawCorpusAttempt, ...]
    expected_paper_count: int

    @property
    def manifest(self) -> Dict[str, Any]:
        payload = {
            "schema_version": BUILD_SCHEMA_VERSION,
            "expected_paper_count": self.expected_paper_count,
            "role_order": list(ROLE_ORDER),
            "expected_record_count": self.expected_paper_count * len(ROLE_ORDER),
            "canonical_record_count": len(self.records),
            "raw_attempt_count": len(self.raw_attempts),
            "record_ids": [record.full_review_record_id for record in self.records],
            "no_memory": True,
            "external_evidence_enabled": False,
        }
        payload["build_manifest_sha256"] = _sha(canonical_json_bytes(payload))
        return payload

    def materialize(
        self,
        *,
        tokenizer: FrozenTokenizer,
        max_projection_tokens: int = 2048,
    ) -> CorpusMaterialization:
        if len(self.records) != self.expected_paper_count * len(ROLE_ORDER):
            raise RuntimeError("Corpus cannot materialize before all reviews commit")
        return materialize_corpus(
            self.records,
            tokenizer=tokenizer,
            max_projection_tokens=max_projection_tokens,
        )

    def write_audit(self, directory: str | Path) -> None:
        target = Path(directory).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        values = {
            "build_manifest.json": canonical_json_bytes(self.manifest),
            "canonical_full_reviews.jsonl": b"".join(
                canonical_json_bytes(item.to_dict()) + b"\n"
                for item in self.records
            ),
            "raw_attempts.jsonl": b"".join(
                canonical_json_bytes(item.to_dict()) + b"\n"
                for item in self.raw_attempts
            ),
        }
        for name, content in values.items():
            path = target / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)


def default_role_system_prompts() -> Dict[str, str]:
    """Return the versioned Core-v1 Corpus prompts, not legacy review text."""

    prompts = corpus_role_prompts()
    if set(prompts) != set(ROLE_ORDER):
        raise RuntimeError("Versioned Corpus prompt role set mismatch")
    return prompts


def corpus_response_schema(*, paper_id: str, role: str) -> Dict[str, Any]:
    """Build the strict per-paper, per-role JSON representation schema."""

    if role not in ROLE_SUBDIMENSIONS:
        raise ValueError(f"Unknown Corpus response-schema role: {role}")
    anchor_schema = {
        "type": "array",
        "items": {
            "type": "string",
            "pattern": r"^SRC-\d{3}(?:\(page=\d+\))?$",
        },
    }
    section_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["subdimension_id", "review_text", "paper_anchor_ids"],
        "properties": {
            "subdimension_id": {
                "type": "string",
                "enum": list(ROLE_SUBDIMENSIONS[role]),
            },
            "review_text": {"type": "string", "minLength": 1},
            "paper_anchor_ids": anchor_schema,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "role",
            "paper_id",
            "subdimension_sections",
            "overall_assessment",
        ],
        "properties": {
            "role": {"type": "string", "const": role},
            "paper_id": {"type": "string", "const": paper_id},
            "subdimension_sections": {
                "type": "array",
                "minItems": len(ROLE_SUBDIMENSIONS[role]),
                "maxItems": len(ROLE_SUBDIMENSIONS[role]),
                "items": section_schema,
            },
            "overall_assessment": {"type": ["string", "null"]},
        },
    }


def corpus_response_format(*, paper_id: str, role: str) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"canonical_full_review_{role.lower()}_v4",
            "strict": True,
            "schema": corpus_response_schema(paper_id=paper_id, role=role),
        },
    }


def _role_paper_context(paper: CorpusPaper, role: str) -> str:
    return re.sub(
        r"(?m)^reviewer_role:\s*.*$",
        f"reviewer_role: {role}",
        paper.canonical_paper_context,
    )


def _reviewer_user_prompt(paper: CorpusPaper, role: str) -> str:
    sections = [
        {
            "subdimension_id": subdimension,
            "review_text": "non-empty grounded review text",
            "paper_anchor_ids": [],
        }
        for subdimension in ROLE_SUBDIMENSIONS[role]
    ]
    example = {
        "role": role,
        "paper_id": paper.paper_id,
        "subdimension_sections": sections,
        "overall_assessment": "optional role-level assessment or null",
    }
    role_input_boundary = ""
    if role == "CLAR":
        role_input_boundary = (
            "\nCURRENT INPUT MODALITY (MANDATORY): This request contains text-only "
            "Markdown and no figure/image pixels. For CLAR-FIG, assess only "
            "machine-readable tables, caption text, figure/table references, and "
            "explicit textual descriptions. Do not state or imply that a figure/image "
            "is clear, legible, well-labeled, informative, visually useful, or visually "
            "misleading. A caption's description is not observation of the image. Apply "
            "this restriction to overall_assessment as well. Use the observed text object "
            "as the grammatical subject: for example, write that caption text is detailed, "
            "a Markdown table is readable, or a figure reference is consistent. Never "
            "write that figures or images are well-labeled or informative. If pixels are "
            "absent, do not infer the presence or absence of colors, axes, fonts, layouts, "
            "or other pixel-level properties; only assess whether the visible caption or "
            "textual description contains information explicitly present in the Markdown.\n"
        )
    return (
        "Create the canonical no-memory specialist Full Review for the paper below.\n"
        "You have no Review Memory and no external evidence capability. Evaluate only "
        "the paper and visible structural/source anchors. Output exactly one JSON "
        "object: no markdown fence, reasoning preface, score, applicability decision, "
        "or extra field. Include every fixed role subdimension in the shown order, "
        "even when no weakness is found. paper_anchor_ids must contain no duplicates; "
        "their emission order is semantically irrelevant and is canonicalized by the "
        "strict parser. For strict JSON transport, review_text and overall_assessment "
        "must not contain any backslash character or LaTeX command; write mathematical "
        "symbols with Unicode or plain words (for example, write 'theta', not a LaTeX "
        "command).\n\n"
        + role_input_boundary
        + "\n"
        "STRICT OUTPUT SHAPE:\n"
        + json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        + "\n\nCANONICAL PAPER INPUT:\n"
        + _role_paper_context(paper, role)
    )


def build_corpus_reviewer_request(
    paper: CorpusPaper,
    role: str,
    *,
    generation_identity_sha256: str,
    role_system_prompts: Mapping[str, str] | None = None,
) -> CorpusReviewerRequest:
    """Construct the exact request consumed by live no-memory generation."""

    if role not in ROLE_SUBDIMENSIONS:
        raise ValueError(f"Unknown Corpus Reviewer role: {role}")
    prompts = dict(role_system_prompts or default_role_system_prompts())
    if set(prompts) != set(ROLE_ORDER):
        raise ValueError("Corpus role system prompt set mismatch")
    if len(generation_identity_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in generation_identity_sha256
    ):
        raise ValueError("Corpus generation identity must be lowercase SHA-256")
    messages = (
        {"role": "system", "content": prompts[role]},
        {"role": "user", "content": _reviewer_user_prompt(paper, role)},
    )
    response_format = corpus_response_format(paper_id=paper.paper_id, role=role)
    response_format_sha = _sha(canonical_json_bytes(response_format))
    prompt_sha = _sha(canonical_json_bytes(list(messages)))
    request_sha = _sha(
        canonical_json_bytes(
            {
                "identity_type": "no_memory_corpus_reviewer_request",
                "schema_version": BUILD_SCHEMA_VERSION,
                "paper_id": paper.paper_id,
                "role": role,
                "messages_sha256": prompt_sha,
                "response_format_sha256": response_format_sha,
                "generation_identity_sha256": generation_identity_sha256,
            }
        )
    )
    return CorpusReviewerRequest(
        paper_id=paper.paper_id,
        role=role,
        messages=messages,
        response_format=response_format,
        response_format_sha256=response_format_sha,
        prompt_identity_sha256=prompt_sha,
        request_identity_sha256=request_sha,
    )


class NoMemoryCorpusBuilder:
    def __init__(
        self,
        *,
        complete: Callable[[Sequence[Mapping[str, str]]], str | bytes] | None = None,
        complete_by_role: Mapping[
            str, Callable[[Sequence[Mapping[str, str]]], str | bytes]
        ]
        | None = None,
        reviewer_identity_sha256: str,
        generation_identity_sha256: str,
        role_system_prompts: Mapping[str, str] | None = None,
        max_workers: int = 1,
    ):
        self.complete = complete
        self.complete_by_role = dict(complete_by_role or {})
        self.reviewer_identity_sha256 = reviewer_identity_sha256
        self.generation_identity_sha256 = generation_identity_sha256
        self.role_system_prompts = dict(
            role_system_prompts or default_role_system_prompts()
        )
        self.max_workers = max(1, int(max_workers))
        for digest in (reviewer_identity_sha256, generation_identity_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Corpus Reviewer identities must be lowercase SHA-256")
        if set(self.role_system_prompts) != set(ROLE_ORDER):
            raise ValueError("Corpus role system prompt set mismatch")
        if self.complete is None and set(self.complete_by_role) != set(ROLE_ORDER):
            raise ValueError("Corpus build requires one shared or five role completions")

    def _one(self, paper: CorpusPaper, role: str):
        request = build_corpus_reviewer_request(
            paper,
            role,
            generation_identity_sha256=self.generation_identity_sha256,
            role_system_prompts=self.role_system_prompts,
        )
        messages = request.messages
        prompt_sha = request.prompt_identity_sha256
        request_sha = request.request_identity_sha256
        attempts = []
        completion = self.complete_by_role.get(role, self.complete)
        if completion is None:
            raise RuntimeError(f"Corpus completion is unavailable for {role}")
        for attempt_index in range(2):
            response = _completion_response(completion(messages))
            raw = response.assistant_content
            try:
                if response.finish_reason == "length":
                    raise ValueError("Corpus Reviewer response was truncated")
                if not raw:
                    raise ValueError("Corpus Reviewer response was empty")
                record = CanonicalFullReviewRecord.from_model_response(
                    raw,
                    expected_role=role,
                    expected_paper_id=paper.paper_id,
                    paper_identity_sha256=_sha(
                        _role_paper_context(paper, role).encode("utf-8")
                    ),
                    structural_context_sha256=paper.structural_context_sha256,
                    reviewer_identity_sha256=self.reviewer_identity_sha256,
                    prompt_identity_sha256=prompt_sha,
                    request_identity_sha256=request_sha,
                    allowed_paper_anchor_ids=paper.allowed_paper_anchor_ids,
                )
                boundary_violations = semantic_boundary_violations(record)
                if boundary_violations:
                    codes = ",".join(
                        f"{item.subdimension_id}:{item.code}"
                        for item in boundary_violations
                    )
                    raise ValueError(
                        "Corpus Reviewer response violated frozen semantic "
                        f"boundaries: {codes}"
                    )
            except Exception as exc:
                attempts.append(
                    RawCorpusAttempt(
                        paper.paper_id,
                        role,
                        attempt_index,
                        raw,
                        response.raw_provider_response,
                        response.finish_reason,
                        response.usage,
                        request_sha,
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            attempts.append(
                RawCorpusAttempt(
                    paper.paper_id,
                    role,
                    attempt_index,
                    raw,
                    response.raw_provider_response,
                    response.finish_reason,
                    response.usage,
                    request_sha,
                    True,
                    "",
                )
            )
            return record, tuple(attempts)
        raise CorpusBuildFailure(paper.paper_id, role, attempts)

    def build_one(
        self, paper: CorpusPaper, role: str
    ) -> Tuple[CanonicalFullReviewRecord, Tuple[RawCorpusAttempt, ...]]:
        """Build one authoritative item for a checkpointed sequential runner."""

        return self._one(paper, role)

    def build(
        self,
        papers: Iterable[CorpusPaper],
        *,
        expected_paper_count: int = 125,
    ) -> CorpusBuildResult:
        ordered_papers = tuple(papers)
        if len(ordered_papers) != expected_paper_count:
            raise ValueError(
                f"Expected {expected_paper_count} A papers, got {len(ordered_papers)}"
            )
        if len({paper.paper_id for paper in ordered_papers}) != len(ordered_papers):
            raise ValueError("Duplicate A paper id in Corpus input")
        jobs = [(paper_index, role_index, paper, role)
                for paper_index, paper in enumerate(ordered_papers)
                for role_index, role in enumerate(ROLE_ORDER)]
        completed = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._one, paper, role): (paper_index, role_index)
                for paper_index, role_index, paper, role in jobs
            }
            for future in as_completed(futures):
                completed[futures[future]] = future.result()
        records = []
        attempts = []
        for key in sorted(completed):
            record, raw_attempts = completed[key]
            records.append(record)
            attempts.extend(raw_attempts)
        expected_records = expected_paper_count * len(ROLE_ORDER)
        if len(records) != expected_records:
            raise RuntimeError("Corpus build did not produce the complete canonical set")
        return CorpusBuildResult(tuple(records), tuple(attempts), expected_paper_count)
