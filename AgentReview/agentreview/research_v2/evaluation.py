"""Offline Internal Evaluation used only as M★ Evolution feedback."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import load_reviewer_suite

from .constants import EVALUATOR_MAX_TOKENS, ROLES
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient
from .models import InternalEvaluation, Paper
from .review import frozen_paper_context
from .s2 import format_s2_evidence

INTERNAL_EVALUATOR_PROMPT_VERSION = "internal-evaluator-v1"
INTERNAL_EVALUATOR_SYSTEM_PROMPT = """You are an offline peer-review quality evaluator used only for self-improvement research. Evaluate the supplied Specialist Review against the Frozen Paper, the frozen Specialist role, and exactly the evidence shown to that Reviewer. Do not infer Program identity and do not use human reviews, external benchmark data, other Reviewer outputs, or hidden reasoning.

Return only one JSON object with exactly these fields:
{
  "review_quality": <number from 1 to 5>,
  "major_missing_points": [<strings>],
  "factual_evidence_issues": [<strings>],
  "improvement_suggestions": [<strings>]
}

Use review_quality as one holistic assessment. Do not invent a weighted reward, checklist score, or benchmark proxy."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _escape_invalid_json_string_backslashes(value: str) -> str:
    """Preserve literal LaTeX backslashes emitted inside JSON strings.

    Review text commonly contains sequences such as ``\\mathcal`` or ``\\Sigma``.
    They are valid text but invalid JSON escapes.  This scanner changes only an
    invalid backslash inside a quoted JSON string into an escaped literal
    backslash; valid JSON escapes and all structure outside strings stay exact.
    """
    output: list[str] = []
    in_string = False
    index = 0
    hexadecimal = set("0123456789abcdefABCDEF")
    while index < len(value):
        character = value[index]
        if character == '"':
            in_string = not in_string
            output.append(character)
            index += 1
            continue
        if character != "\\" or not in_string:
            output.append(character)
            index += 1
            continue

        following = value[index + 1] if index + 1 < len(value) else ""
        if following in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
            output.extend((character, following))
            index += 2
            continue
        if (
            following == "u"
            and index + 5 < len(value)
            and all(item in hexadecimal for item in value[index + 2 : index + 6])
        ):
            output.append(value[index : index + 6])
            index += 6
            continue

        # Two backslashes encode one literal backslash in the repaired JSON.
        output.extend(("\\", "\\"))
        index += 1
    return "".join(output)


def _parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        repaired = _escape_invalid_json_string_backslashes(value)
        if repaired == value:
            raise
        parsed = json.loads(repaired)
    if not isinstance(parsed, dict):
        raise ValueError("Internal Evaluator output must be a JSON object")
    expected = {
        "review_quality",
        "major_missing_points",
        "factual_evidence_issues",
        "improvement_suggestions",
    }
    if set(parsed) != expected:
        raise ValueError(
            f"Internal Evaluator fields mismatch: expected={sorted(expected)}, got={sorted(parsed)}"
        )
    score = float(parsed["review_quality"])
    if not 1.0 <= score <= 5.0:
        raise ValueError("review_quality must be in [1, 5]")
    parsed["review_quality"] = score
    for key in expected - {"review_quality"}:
        if not isinstance(parsed[key], list) or not all(
            isinstance(item, str) for item in parsed[key]
        ):
            raise TypeError(f"{key} must be a list of strings")
    return parsed


def run_internal_evaluation(
    *,
    paper_path: str | Path,
    review_path: str | Path,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    evaluator_identity: dict[str, Any],
) -> dict[str, Any]:
    paper = Paper.from_dict(read_json(paper_path))
    review = read_json(review_path)
    role = str(review.get("role") or "")
    if role not in ROLES or review.get("paper_id") != paper.paper_id:
        raise ValueError("Internal Evaluation paper/role mismatch")
    retrieval = dict(review.get("retrieval_provenance") or {})
    memory = dict(retrieval.get("memory") or {})
    s2 = dict(retrieval.get("s2") or {})
    user_prompt = "\n\n".join(
        [
            "[FROZEN SPECIALIST ROLE]\n" + load_reviewer_suite().task(role).system_prompt,
            "[FROZEN PAPER]\n" + frozen_paper_context(paper, role),
            "[COMPLETE SPECIALIST REVIEW]\n" + str(review.get("review_text") or ""),
            "[EXACT REVIEW EXPERIENCE SHOWN]\n" + str(memory.get("context") or "(none)"),
            "[EXACT FROZEN S2 EVIDENCE SHOWN]\n"
            + (format_s2_evidence(list(s2.get("evidence") or [])) or "(none)"),
        ]
    )
    messages = [
        {"role": "system", "content": INTERNAL_EVALUATOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    output = Path(output_root).expanduser().resolve()
    target_dir = output / "evaluations" / run_id / paper.paper_id
    final_path = target_dir / f"{role}.json"
    expected_evaluation_id = hashlib.sha256(
        f"{run_id}\n{review.get('review_id')}\n{INTERNAL_EVALUATOR_PROMPT_VERSION}".encode(
            "utf-8"
        )
    ).hexdigest()
    if final_path.is_file():
        existing = read_json(final_path)
        expected_identity = {
            "evaluation_id": expected_evaluation_id,
            "review_id": review.get("review_id"),
            "paper_id": paper.paper_id,
            "role": role,
            "evaluator_identity": evaluator_identity,
            "prompt_version": INTERNAL_EVALUATOR_PROMPT_VERSION,
            "run_id": run_id,
        }
        if any(existing.get(key) != value for key, value in expected_identity.items()):
            raise ValueError("Existing Internal Evaluation identity does not match request")
        InternalEvaluation(
            evaluation_id=str(existing["evaluation_id"]),
            review_id=str(existing["review_id"]),
            paper_id=str(existing["paper_id"]),
            role=str(existing["role"]),
            review_quality=float(existing["review_quality"]),
            major_missing_points=list(existing["major_missing_points"]),
            factual_evidence_issues=list(existing["factual_evidence_issues"]),
            improvement_suggestions=list(existing["improvement_suggestions"]),
            raw_model_output=str(existing["raw_model_output"]),
            evaluator_identity=dict(existing["evaluator_identity"]),
            prompt_version=str(existing["prompt_version"]),
            run_id=str(existing["run_id"]),
            created_at=str(existing["created_at"]),
        ).validate()
        return existing
    errors = []
    parsed = None
    raw = ""
    committed_at = ""
    for attempt in (1, 2):
        try:
            raw_path = target_dir / f"{role}.raw.{attempt}.json"
            if raw_path.is_file():
                raw_artifact = read_json(raw_path)
                expected_raw_identity = {
                    "paper_id": paper.paper_id,
                    "role": role,
                    "attempt": attempt,
                    "request_sha256": value_sha256(messages),
                }
                if any(
                    raw_artifact.get(key) != value
                    for key, value in expected_raw_identity.items()
                ):
                    raise ValueError(
                        "Existing Internal Evaluation raw identity does not match request"
                    )
                raw = str(raw_artifact.get("raw_model_output") or "")
                committed_at = str(raw_artifact.get("created_at") or "")
            else:
                raw = completion_client.complete(
                    messages,
                    max_tokens=EVALUATOR_MAX_TOKENS,
                    component=f"internal_evaluator:{role}",
                )
                committed_at = _utc_now()
                atomic_write_json(
                    raw_path,
                    {
                        "paper_id": paper.paper_id,
                        "role": role,
                        "attempt": attempt,
                        "raw_model_output": raw,
                        "request_sha256": value_sha256(messages),
                        "created_at": committed_at,
                    },
                )
            parsed = _parse_json_object(raw)
            break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    if parsed is None:
        failure = {
            "paper_id": paper.paper_id,
            "role": role,
            "review_id": review.get("review_id"),
            "status": "failed",
            "errors": errors,
            "review_quality": 1.0,
            "created_at": _utc_now(),
        }
        atomic_write_json(target_dir / f"{role}.failure.json", failure)
        raise RuntimeError(f"Internal Evaluation failed twice: {errors}")
    artifact = InternalEvaluation(
        evaluation_id=expected_evaluation_id,
        review_id=str(review.get("review_id") or ""),
        paper_id=paper.paper_id,
        role=role,
        review_quality=parsed["review_quality"],
        major_missing_points=parsed["major_missing_points"],
        factual_evidence_issues=parsed["factual_evidence_issues"],
        improvement_suggestions=parsed["improvement_suggestions"],
        raw_model_output=raw,
        evaluator_identity=dict(evaluator_identity),
        prompt_version=INTERNAL_EVALUATOR_PROMPT_VERSION,
        run_id=run_id,
        created_at=committed_at,
    )
    payload = artifact.to_dict()
    atomic_write_json(final_path, payload)
    return payload
