"""Stateless Meta synthesis and deterministic concat Final Reviews."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import (
    META_OUTPUT_PROTOCOL_VERSION,
    META_RENDER_PROTOCOL_VERSION,
    SPECIALIST_TASKS,
    build_meta_review_question,
    load_meta_synthesizer,
)

from .constants import META_MAX_TOKENS
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient, EmptyCompletionOutput
from .models import FinalReview, Paper
from .review import frozen_paper_context

META_RAW_PROTOCOL = "research-v2-meta-raw-v2"
META_MAX_ATTEMPTS = 2
_RECOMMENDATIONS = {"Accept", "Weak Accept", "Borderline", "Weak Reject", "Reject"}


class MetaSynthesisFailure(RuntimeError):
    """Base class for an exhausted Meta synthesis invocation."""


class MetaSynthesisPaperFailure(MetaSynthesisFailure):
    """The endpoint responded, but no valid paper-local synthesis was produced."""


class MetaSynthesisInfrastructureFailure(MetaSynthesisFailure):
    """The completion transport or shared runtime failed."""


class MetaOutputValidationError(ValueError):
    """Structured Meta content violates a verifiable output contract."""

    def __init__(self, field: str, requirement: str):
        self.field = field
        self.requirement = requirement
        super().__init__(f"Meta output {field} requires {requirement}")

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": "meta_output_contract",
            "field": self.field,
            "requirement": self.requirement,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_bundle(
    review_paths: list[str | Path],
) -> tuple[str, dict[str, dict[str, Any]]]:
    values = [read_json(path) for path in review_paths]
    if len(values) != len(SPECIALIST_TASKS):
        raise ValueError("Final Review requires exactly five Specialist Reviews")
    papers = {str(value.get("paper_id") or "") for value in values}
    if len(papers) != 1:
        raise ValueError("Specialist Reviews belong to different papers")
    by_role = {str(value.get("role") or ""): value for value in values}
    if set(by_role) != set(SPECIALIST_TASKS):
        raise ValueError("Final Review requires QUAL/CLAR/SIGN/ORIG/POL exactly once")
    return papers.pop(), by_role


def _concat(by_role: dict[str, dict[str, Any]]) -> str:
    return "\n\n".join(
        f"# {role} Specialist Review\n\n{by_role[role]['review_text']}"
        for role in SPECIALIST_TASKS
    )


def _exact_fields(value: Any, fields: set[str], location: str) -> dict[str, Any]:
    if isinstance(value, dict) and set(value) == fields:
        return value
    raise MetaOutputValidationError(location, f"exactly these fields: {sorted(fields)}")


def _single_line(value: Any, location: str) -> str:
    if (
        isinstance(value, str)
        and value.strip()
        and value == value.strip()
        and len(value.splitlines()) == 1
    ):
        return value
    raise MetaOutputValidationError(location, "one nonempty text line with trimmed edges")


def _distinct_strings(value: Any, location: str) -> list[str]:
    if isinstance(value, list):
        strings = [
            _single_line(item, f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
        if len(strings) == len(set(strings)):
            return strings
    raise MetaOutputValidationError(location, "a list of distinct text strings")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MetaOutputValidationError(key, "a unique JSON object key")
        value[key] = item
    return value


def parse_meta_synthesis(
    raw: str,
    *,
    paper_context: str,
    source_review_ids_by_role: dict[str, str],
) -> dict[str, Any]:
    """Validate generated structure and bind its sources to frozen artifacts.

    This validates item boundaries and declared source identifiers. It does not
    infer semantic atomicity, factual correctness or downstream classifier labels.
    """
    sources = _exact_fields(
        source_review_ids_by_role,
        set(SPECIALIST_TASKS),
        "source_review_ids_by_role",
    )
    for role, review_id in sources.items():
        _single_line(review_id, f"source_review_ids_by_role.{role}")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MetaOutputValidationError("root", "one complete JSON object") from exc
    parsed = _exact_fields(parsed, {"sections", "recommendation"}, "root")
    sections = parsed["sections"]
    if isinstance(sections, list) and len(sections) == len(SPECIALIST_TASKS):
        pass
    else:
        raise MetaOutputValidationError(
            "sections", "five sections in QUAL/CLAR/SIGN/ORIG/POL order"
        )
    recommendation = _single_line(parsed["recommendation"], "recommendation")
    if recommendation in _RECOMMENDATIONS:
        pass
    else:
        raise MetaOutputValidationError("recommendation", f"one of {sorted(_RECOMMENDATIONS)}")

    _, separator, anchor_tail = paper_context.rpartition("=== SOURCE ANCHORS ===")
    anchor_text, end_separator, _ = anchor_tail.partition("=== END SOURCE ANCHORS ===")
    anchors: dict[str, str] = {}
    if separator and end_separator:
        for line in anchor_text.splitlines():
            match = re.match(r"^(SRC-\d+)(?=\s|\||\(|$)", line.strip())
            if match:
                anchors[match.group(1)] = line.strip()
    normalized_sections = []
    cited_anchors: dict[str, str] = {}
    opinion_count = 0
    for index, role in enumerate(SPECIALIST_TASKS):
        location = f"sections[{index}]"
        section = _exact_fields(sections[index], {"role", "opinions"}, location)
        if section["role"] == role and isinstance(section["opinions"], list):
            pass
        else:
            raise MetaOutputValidationError(location, f"role={role} and an opinions list")
        opinions = []
        for item_index, value in enumerate(section["opinions"]):
            item_location = f"{location}.opinions[{item_index}]"
            item = _exact_fields(
                value,
                {"statement", "source_roles", "evidence_anchors", "suggestions"},
                item_location,
            )
            statement = _single_line(item["statement"], f"{item_location}.statement")
            source_roles = _distinct_strings(item["source_roles"], f"{item_location}.source_roles")
            if source_roles and set(source_roles) <= set(sources):
                pass
            else:
                raise MetaOutputValidationError(
                    f"{item_location}.source_roles",
                    "one or more supplied Specialist roles",
                )
            evidence_anchors = _distinct_strings(
                item["evidence_anchors"], f"{item_location}.evidence_anchors"
            )
            if set(evidence_anchors) <= set(anchors):
                pass
            else:
                raise MetaOutputValidationError(
                    f"{item_location}.evidence_anchors",
                    "identifiers from the supplied Source Anchors table",
                )
            suggestions = _distinct_strings(
                item["suggestions"], f"{item_location}.suggestions"
            )
            opinions.append({
                "statement": statement,
                "source_roles": source_roles,
                "source_review_ids": [sources[role] for role in source_roles],
                "evidence_anchors": evidence_anchors,
                "suggestions": suggestions,
            })
            cited_anchors.update({anchor: anchors[anchor] for anchor in evidence_anchors})
        opinion_count += len(opinions)
        normalized_sections.append({"role": role, "opinions": opinions})
    if opinion_count > 0:
        return {
            "protocol": META_OUTPUT_PROTOCOL_VERSION,
            "rendering_protocol": META_RENDER_PROTOCOL_VERSION,
            "paper_context_sha256": value_sha256(paper_context),
            "source_review_ids_by_role": dict(sources),
            "source_anchors": cited_anchors,
            "sections": normalized_sections,
            "recommendation": recommendation,
        }
    raise MetaOutputValidationError("sections", "at least one supported review opinion")


def render_meta_synthesis(structured: dict[str, Any]) -> str:
    """Render every validated point and suggestion once in their original order."""
    paragraphs = ["# Final Review"]
    for section in structured["sections"]:
        if section["opinions"]:
            role = section["role"]
            paragraphs.append(f"## {role} findings")
            for index, opinion in enumerate(section["opinions"], 1):
                statement = "- " + opinion["statement"]
                if opinion["evidence_anchors"]:
                    statement += (
                        " Evidence anchors: "
                        + ", ".join(opinion["evidence_anchors"])
                        + "."
                    )
                paragraphs.append(statement)
                paragraphs.extend(
                    f"- Suggested revision for {role} opinion {index}: {suggestion}"
                    for suggestion in opinion["suggestions"]
                )
    paragraphs.append("Final recommendation: " + structured["recommendation"])
    return "\n\n".join(paragraphs)


def _final_review_id(
    *,
    run_id: str,
    paper_id: str,
    mode: str,
    source_review_ids: list[str],
    review_text: str,
) -> str:
    """Content-address a Final Review while excluding wall-clock metadata."""
    return hashlib.sha256(
        f"{run_id}\n{paper_id}\n{mode}\n".encode("utf-8")
        + "\n".join(source_review_ids).encode("utf-8")
        + f"\n{value_sha256(review_text)}".encode("utf-8")
    ).hexdigest()


def _load_resumable_meta_raw(
    path: Path,
    *,
    expected_identity: dict[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"Committed Meta raw artifact must be an object: {path}")
    observed = {name: value.get(name) for name in expected_identity}
    if observed != expected_identity:
        raise ValueError(
            f"Committed Meta raw identity mismatch at {path}: "
            f"observed={observed!r}, expected={expected_identity!r}"
        )
    if "raw_model_output" not in value:
        raise ValueError(f"Committed Meta raw artifact has no output field: {path}")
    if not isinstance(value.get("created_at"), str) or not value["created_at"].strip():
        raise ValueError(f"Committed Meta raw artifact has no creation time: {path}")
    return value


def _load_resumable_final(
    path: Path,
    *,
    expected_identity: dict[str, Any],
    expected_text: str | None,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"Committed Final Review must be an object: {path}")
    observed = {name: value.get(name) for name in expected_identity}
    if observed != expected_identity:
        raise ValueError(
            f"Committed Final Review identity mismatch at {path}: "
            f"observed={observed!r}, expected={expected_identity!r}"
        )
    text = value.get("review_text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Committed Final Review is empty: {path}")
    if expected_text is not None and text != expected_text:
        raise ValueError(f"Committed Final Review content identity mismatch: {path}")
    if not isinstance(value.get("created_at"), str) or not value["created_at"].strip():
        raise ValueError(f"Committed Final Review has no creation time: {path}")
    return value


def generate_final_review(
    *,
    paper_path: str | Path,
    review_paths: list[str | Path],
    mode: str,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient | None,
    model_identity: dict[str, Any] | None,
    max_tokens: int = META_MAX_TOKENS,
) -> dict[str, Any]:
    paper_id, by_role = _load_bundle(review_paths)
    paper = Paper.from_dict(read_json(paper_path))
    if paper.paper_id != paper_id:
        raise ValueError("Frozen Paper does not match the Specialist bundle")
    output = Path(output_root).expanduser().resolve()
    target = output / "final_reviews" / run_id / f"{paper_id}.json"
    source_ids = [
        str(by_role[role].get("review_id") or "") for role in SPECIALIST_TASKS
    ]
    meta_identity = None
    structured_meta = None
    generation_config = None
    created_at = _utc_now()
    if mode == "concat":
        text = _concat(by_role)
    elif mode == "meta":
        if completion_client is None:
            raise ValueError("meta mode requires a completion client")
        synthesizer = load_meta_synthesizer()
        meta_identity = synthesizer.identity()
        generation_config = {
            "temperature": (model_identity or {}).get("temperature", 0.0),
            "enable_thinking": (model_identity or {}).get("enable_thinking", False),
            "max_tokens": max_tokens,
        }
    else:
        raise ValueError("mode must be meta or concat")
    if mode == "meta":
        paper_context = frozen_paper_context(paper, "META")
        source_review_ids_by_role = dict(zip(SPECIALIST_TASKS, source_ids, strict=True))
        question = build_meta_review_question(
            paper_id=paper_id,
            specialist_reviews={
                role: str(by_role[role].get("review_text") or "")
                for role in SPECIALIST_TASKS
            },
            paper_context=paper_context,
        )
        messages = [
            {"role": "system", "content": synthesizer.system_prompt},
            {"role": "user", "content": question},
        ]
        errors = []
        validation_errors = []
        text = ""
        infrastructure_failed = False
        request_sha256 = value_sha256(messages)
        raw_identity = {
            "protocol": META_RAW_PROTOCOL,
            "paper_id": paper_id,
            "run_id": run_id,
            "source_review_ids": source_ids,
            "meta_identity": meta_identity,
            "model_identity": dict(model_identity or {}),
            "generation_config": generation_config,
            "request_sha256": request_sha256,
        }
        for attempt in range(1, META_MAX_ATTEMPTS + 1):
            raw_path = (
                output
                / "final_reviews"
                / run_id
                / f"{paper_id}.meta.raw.{attempt}.json"
            )
            raw = _load_resumable_meta_raw(
                raw_path,
                expected_identity={**raw_identity, "attempt": attempt},
            )
            if raw is None:
                if target.is_file():
                    continue
                try:
                    candidate = completion_client.complete(
                        messages,
                        max_tokens=max_tokens,
                        component="meta_synthesis",
                    )
                except EmptyCompletionOutput as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                except Exception as exc:
                    infrastructure_failed = True
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                raw = {
                    **raw_identity,
                    "attempt": attempt,
                    "raw_model_output": candidate,
                    "created_at": _utc_now(),
                }
                atomic_write_json(raw_path, raw)
            candidate = raw["raw_model_output"]
            if not isinstance(candidate, str) or not candidate.strip():
                errors.append(
                    "EmptyCompletionOutput: meta_synthesis returned empty output"
                )
                continue
            try:
                structured_meta = parse_meta_synthesis(
                    candidate,
                    paper_context=paper_context,
                    source_review_ids_by_role=source_review_ids_by_role,
                )
            except MetaOutputValidationError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                validation_errors.append({"attempt": attempt, **exc.to_dict()})
                continue
            text = render_meta_synthesis(structured_meta)
            created_at = str(raw["created_at"])
            break
        if not text:
            if target.is_file():
                raise ValueError(
                    f"Committed Meta Final Review has no matching raw output: {target}"
                )
            failure = {
                "paper_id": paper_id,
                "mode": "meta",
                "status": "failed",
                "errors": errors,
                "validation_errors": validation_errors,
                "created_at": _utc_now(),
            }
            atomic_write_json(
                output / "final_reviews" / run_id / f"{paper_id}.failure.json",
                failure,
                immutable=False,
            )
            failure_type = (
                MetaSynthesisInfrastructureFailure
                if infrastructure_failed
                else MetaSynthesisPaperFailure
            )
            raise failure_type(
                f"Meta synthesis requires a valid structured Final Review: {errors}"
            )
    final_id = _final_review_id(
        run_id=run_id,
        paper_id=paper_id,
        mode=mode,
        source_review_ids=source_ids,
        review_text=text,
    )
    expected_final_identity = {
        "schema_version": "research-core-v2-artifact-v1",
        "final_review_id": final_id,
        "paper_id": paper_id,
        "mode": mode,
        "source_review_ids": source_ids,
        "meta_identity": meta_identity,
        "run_id": run_id,
    }
    if mode == "meta" and model_identity is not None:
        expected_final_identity["model_identity"] = dict(model_identity)
        expected_final_identity["generation_config"] = generation_config
    if mode == "meta":
        expected_final_identity["structured_meta"] = structured_meta
    resumed = _load_resumable_final(
        target,
        expected_identity=expected_final_identity,
        expected_text=text,
    )
    if resumed is not None:
        return resumed
    final = FinalReview(
        final_review_id=final_id,
        paper_id=paper_id,
        mode=mode,
        review_text=text,
        source_review_ids=source_ids,
        meta_identity=meta_identity,
        run_id=run_id,
        created_at=created_at,
    )
    payload = final.to_dict()
    if mode == "meta":
        payload["structured_meta"] = structured_meta
    if model_identity is not None and mode == "meta":
        payload["model_identity"] = dict(model_identity)
        payload["generation_config"] = generation_config
    atomic_write_json(target, payload)
    return payload
