"""Offline Internal Evaluation used only as M★ Evolution feedback."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentreview.reviewer_identity import load_reviewer_suite

from .constants import EVALUATOR_MAX_TOKENS, ROLES
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient
from .models import InternalEvaluation, Paper
from .review import frozen_paper_context
from .s2 import format_s2_evidence

INTERNAL_EVALUATOR_PROMPT_VERSION = "internal-evaluator-v1"
EVALUATION_MAX_ATTEMPTS = 3
EVALUATION_DETERMINISTIC_REPEAT_LIMIT = 2
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


class EvaluationFailure(RuntimeError):
    """A shared evaluation failure pauses the experiment with its evidence."""

    failure_category = "shared_evaluation"

    def __init__(self, failure_record: dict[str, Any]):
        self.failure_record = failure_record
        super().__init__(
            f"Evaluation paused: {failure_record.get('failure_reason', 'shared_evaluation')}"
        )


class EvaluationCancelled(RuntimeError):
    """A peer failure stopped this task before its next evaluator request."""


class InterruptedEvaluationAttempt(RuntimeError):
    """An evaluator request was reserved before an interrupted process exited."""


def evaluation_failure_detail(exc: Exception, *, stage: str = "request") -> dict[str, Any]:
    """Classify one failure and identify consecutive equivalent failures."""
    error_type = type(exc).__name__
    message = str(exc)
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    transient_status = isinstance(status, int) and (status in {408, 409, 425, 429} or 500 <= status <= 599)
    transient_type = isinstance(exc, (TimeoutError, ConnectionError, InterruptedEvaluationAttempt)) or error_type in {
        "APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError",
        "ReadTimeout", "ConnectTimeout", "ConnectError", "RemoteProtocolError",
    }
    if stage == "input" or isinstance(exc, (FileNotFoundError, PermissionError, FileExistsError)):
        kind = "data"
    elif stage == "parse":
        kind = "deterministic"
    elif transient_status or transient_type or (
        isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError)
    ):
        kind = "transient"
    else:
        kind = "deterministic"
    return {
        "kind": kind,
        "stage": stage,
        "error_type": error_type,
        "message": message,
        "status_code": status if isinstance(status, int) else None,
        "fingerprint": value_sha256({
            "kind": kind, "stage": stage, "error_type": error_type,
            "message": " ".join(message.split()), "status_code": status if isinstance(status, int) else None,
        }),
    }


def _raise_persisted_failure(path: Path, failure: dict[str, Any]) -> None:
    try:
        atomic_write_json(path, failure)
    except Exception as exc:
        failure = {**failure, "persistence_failure": evaluation_failure_detail(exc, stage="input")}
    raise EvaluationFailure(failure)


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
    if type(parsed["review_quality"]) not in (int, float):
        raise TypeError("review_quality must be a JSON number")
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
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    try:
        return _run_internal_evaluation(
            paper_path=paper_path, review_path=review_path, run_id=run_id,
            output_root=output_root, completion_client=completion_client,
            evaluator_identity=evaluator_identity, stop_requested=stop_requested,
        )
    except (EvaluationFailure, EvaluationCancelled):
        raise
    except Exception as exc:
        identity = {
            "run_id": run_id, "paper_path": str(Path(paper_path).resolve()),
            "review_path": str(Path(review_path).resolve()), "evaluator_identity": evaluator_identity,
        }
        detail = evaluation_failure_detail(exc, stage="input")
        failure = {
            "schema_version": "research-v2-evaluation-failure-v2",
            **identity, "status": "paused", "failure_category": "shared_evaluation",
            "failure_reason": "input_data_error", "attempts": [],
            "errors": [f"{detail['error_type']}: {detail['message']}"],
            "failure": detail, "created_at": _utc_now(),
        }
        path = Path(output_root).resolve() / "evaluations" / run_id / "input_failures" / f"{value_sha256(identity)}.json"
        if path.is_file():
            existing = read_json(path)
            if existing.get("failure", {}).get("fingerprint") == detail["fingerprint"]:
                raise EvaluationFailure(existing) from exc
        _raise_persisted_failure(path, failure)


def _run_internal_evaluation(
    *,
    paper_path: str | Path,
    review_path: str | Path,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    evaluator_identity: dict[str, Any],
    stop_requested: Callable[[], bool] | None,
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
    identity = {
        "evaluation_id": expected_evaluation_id, "paper_id": paper.paper_id,
        "role": role, "review_id": review.get("review_id"), "run_id": run_id,
        "request_sha256": value_sha256(messages), "evaluator_identity": evaluator_identity,
        "prompt_version": INTERNAL_EVALUATOR_PROMPT_VERSION,
    }
    retry_policy = {
        "max_attempts": EVALUATION_MAX_ATTEMPTS,
        "deterministic_repeat_limit": EVALUATION_DETERMINISTIC_REPEAT_LIMIT,
    }
    first_reservation = target_dir / f"{role}.attempt.1.json"
    if first_reservation.is_file():
        reserved_policy = read_json(first_reservation).get("retry_policy")
        if isinstance(reserved_policy, dict) and set(reserved_policy) == set(retry_policy):
            retry_policy = reserved_policy
        else:
            raise ValueError("Evaluation attempt must record its retry policy")
    if any(type(value) is not int or value < 1 for value in retry_policy.values()):
        raise ValueError("Evaluation retry limits must be positive")
    failure_path = target_dir / f"{role}.failure.json"
    if failure_path.is_file():
        failure = read_json(failure_path)
        if any(failure.get(key) != value for key, value in identity.items()):
            raise ValueError("Existing Evaluation failure identity must match the request")
        raise EvaluationFailure(failure)

    attempts = []
    previous_fingerprint = None
    repeated = 0
    for attempt in range(1, retry_policy["max_attempts"] + 1):
        raw_path = target_dir / f"{role}.raw.{attempt}.json"
        reservation_path = target_dir / f"{role}.attempt.{attempt}.json"
        expected_raw_identity = {**identity, "attempt": attempt}
        if raw_path.is_file():
            raw_artifact = read_json(raw_path)
            if any(raw_artifact.get(key) != value for key, value in expected_raw_identity.items()):
                raise ValueError("Existing Evaluation raw identity must match the request")
        else:
            if stop_requested is not None and stop_requested():
                raise EvaluationCancelled("Shared evaluation batch is paused")
            if reservation_path.is_file():
                reservation = read_json(reservation_path)
                if any(reservation.get(key) != value for key, value in expected_raw_identity.items()):
                    raise ValueError("Existing Evaluation attempt identity must match the request")
                detail = evaluation_failure_detail(InterruptedEvaluationAttempt("Reserved request has no committed response"))
                raw_artifact = {**expected_raw_identity, "status": "failed", "failure": detail, "created_at": reservation["created_at"]}
            else:
                atomic_write_json(reservation_path, {
                    **expected_raw_identity, "retry_policy": retry_policy, "created_at": _utc_now(),
                })
                try:
                    raw = completion_client.complete(
                        messages, max_tokens=EVALUATOR_MAX_TOKENS, component=f"internal_evaluator:{role}",
                    )
                    raw_artifact = {
                        **expected_raw_identity, "status": "completed", "raw_model_output": raw, "created_at": _utc_now(),
                    }
                except Exception as exc:
                    raw_artifact = {
                        **expected_raw_identity, "status": "failed",
                        "failure": evaluation_failure_detail(exc), "created_at": _utc_now(),
                    }
            atomic_write_json(raw_path, raw_artifact)

        committed_at = str(raw_artifact["created_at"])
        detail = raw_artifact.get("failure")
        if detail is None:
            raw = raw_artifact.get("raw_model_output")
            try:
                parsed = _parse_json_object(raw)
                break
            except Exception as exc:
                detail = evaluation_failure_detail(exc, stage="parse")
        attempt_record = {"attempt": attempt, "raw_path": str(raw_path), "failure": detail}
        atomic_write_json(target_dir / f"{role}.error.{attempt}.json", attempt_record)
        attempts.append(attempt_record)
        repeated = repeated + 1 if detail["fingerprint"] == previous_fingerprint else 1
        previous_fingerprint = detail["fingerprint"]
        reason = None
        if detail["kind"] == "data":
            reason = "input_data_error"
        elif detail["kind"] == "deterministic" and repeated >= retry_policy["deterministic_repeat_limit"]:
            reason = "repeated_deterministic_error"
        elif attempt == retry_policy["max_attempts"]:
            reason = "attempt_budget_exhausted"
        if reason is not None:
            _raise_persisted_failure(failure_path, {
                "schema_version": "research-v2-evaluation-failure-v2", **identity,
                "status": "paused", "failure_category": "shared_evaluation", "failure_reason": reason,
                "retry_policy": retry_policy, "attempts": attempts,
                "errors": [f"{item['failure']['error_type']}: {item['failure']['message']}" for item in attempts],
                "created_at": _utc_now(),
            })
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
