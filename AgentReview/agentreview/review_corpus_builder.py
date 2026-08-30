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

from agentreview.single_paper_review_roles import (
    REVIEWER_SPECS,
    load_review_request_sections,
)

from .review_corpus import (
    ROLE_SUBDIMENSIONS,
    CanonicalFullReviewRecord,
    CorpusMaterialization,
    FrozenTokenizer,
    canonical_json_bytes,
    materialize_corpus,
)


BUILD_SCHEMA_VERSION = "no-memory-full-review-corpus-build-v2"
ROLE_ORDER = tuple(ROLE_SUBDIMENSIONS)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class CorpusPaper:
    paper_id: str
    canonical_paper_context: str
    structural_context_sha256: str
    paper_identity_sha256: str

    @classmethod
    def from_context(
        cls,
        *,
        paper_id: str,
        canonical_paper_context: str,
        structural_context: str = "",
    ) -> "CorpusPaper":
        if not paper_id or not canonical_paper_context:
            raise ValueError("Corpus paper id/context are required")
        return cls(
            paper_id=paper_id,
            canonical_paper_context=canonical_paper_context,
            structural_context_sha256=_sha(structural_context.encode("utf-8")),
            paper_identity_sha256=_sha(canonical_paper_context.encode("utf-8")),
        )


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
    prompt_identity_sha256: str
    request_identity_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "role": self.role,
            "messages": [dict(message) for message in self.messages],
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
    """Return Corpus-specific role prompts with one unambiguous output contract.

    The ordinary Specialist prompts intentionally request a human-readable
    review with scores.  Reusing that output contract here would conflict with
    the closed Canonical Full Review schema.  The Corpus keeps the same frozen
    role rubric and focus, while this prompt removes scoring, applicability and
    Markdown-output instructions.
    """

    sections = load_review_request_sections()
    prompts: Dict[str, str] = {}
    for spec in REVIEWER_SPECS:
        role = spec["dimension"]
        prompts[role] = (
            f"You are the no-memory specialist Corpus Reviewer for the {role} "
            "dimension of a machine-learning research paper. "
            f"Your expertise focus is {spec['focus']}.\n\n"
            "## Frozen role rubric\n"
            f"{sections[role]}\n\n"
            "## Corpus review rules\n"
            f"- Review only through the {role} lens and cover every fixed "
            "subdimension supplied by the request.\n"
            "- Use only the paper text and STRUCTURAL/Source Anchors in the "
            "request. No Review Memory, external evidence, Human/OpenReview "
            "reference, CoCo asset, or unstated outside fact is available.\n"
            "- Describe grounded strengths, weaknesses, limitations, and useful "
            "improvements. Do not reward criticism count and do not manufacture "
            "a weakness when the paper supports a positive assessment.\n"
            "- Do not output a score, recommendation, confidence, applicability "
            "decision, event code, or private reasoning.\n"
            "- For text-only figure/table evidence, judge only machine-readable "
            "captions, references, tables, or descriptions; never claim to have "
            "observed image pixels.\n"
            "- Any paper_anchor_ids must be copied exactly from the request's "
            "Source Anchor registry. An empty array is legal when no anchor can "
            "ground a section. Every non-empty paper_anchor_ids array must be "
            "lexicographically ascending and contain no duplicate. For example, "
            '["SRC-002","SRC-011","SRC-103"] is valid, while '
            '["SRC-011","SRC-002","SRC-103"] is invalid. Before returning, '
            "check this ordering independently in every section.\n"
            "- Return only the single strict JSON object required by the user "
            "message, with no Markdown fence, preface, or trailing text."
        )
    return prompts


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
    return (
        "Create the canonical no-memory specialist Full Review for the paper below.\n"
        "You have no Review Memory and no external evidence capability. Evaluate only "
        "the paper and visible structural/source anchors. Output exactly one JSON "
        "object: no markdown fence, reasoning preface, score, applicability decision, "
        "or extra field. Include every fixed role subdimension in the shown order, "
        "even when no weakness is found. paper_anchor_ids must be sorted and unique.\n\n"
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
    prompt_sha = _sha(canonical_json_bytes(list(messages)))
    request_sha = _sha(
        canonical_json_bytes(
            {
                "identity_type": "no_memory_corpus_reviewer_request",
                "schema_version": BUILD_SCHEMA_VERSION,
                "paper_id": paper.paper_id,
                "role": role,
                "messages_sha256": prompt_sha,
                "generation_identity_sha256": generation_identity_sha256,
            }
        )
    )
    return CorpusReviewerRequest(
        paper_id=paper.paper_id,
        role=role,
        messages=messages,
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
