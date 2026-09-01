"""Review-only pipeline: frozen inputs to one immutable Specialist Review."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.paper_context import format_canonical_paper_context
from agentreview.reviewer_identity import SPECIALIST_REQUEST_TEMPLATE, load_reviewer_suite

from .constants import ENABLE_THINKING, REVIEW_MAX_TOKENS, ROLES, TEMPERATURE
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient, EmptyCompletionOutput
from .memory import retrieve_memory
from .models import Paper, SpecialistReview
from .s2 import FrozenS2Store, format_s2_evidence


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SPECIALIST_INVOCATION_PROTOCOL = "research-v2-specialist-invocation-v1"
SPECIALIST_RETRY_PROTOCOL = "identical-request-at-most-two-v1"
SPECIALIST_MAX_ATTEMPTS = 2


class SpecialistReviewFailure(RuntimeError):
    """Base class for an exhausted Specialist Review invocation."""


class SpecialistReviewPaperFailure(SpecialistReviewFailure):
    """A request completed but produced no usable paper-local output."""


class SpecialistReviewInfrastructureFailure(SpecialistReviewFailure):
    """The completion transport or shared runtime failed."""


def specialist_generation_identity(
    model_identity: dict[str, Any],
) -> dict[str, Any]:
    """Return the frozen invocation policy shared by raw artifacts and run specs."""
    return {
        "protocol": SPECIALIST_INVOCATION_PROTOCOL,
        "model_identity": dict(model_identity),
        "temperature": model_identity.get("temperature", TEMPERATURE),
        "enable_thinking": model_identity.get("enable_thinking", ENABLE_THINKING),
        "max_tokens": REVIEW_MAX_TOKENS,
        "retry": {
            "protocol": SPECIALIST_RETRY_PROTOCOL,
            "max_attempts": SPECIALIST_MAX_ATTEMPTS,
            "sdk_max_retries": 0,
        },
    }


def _fallback_anchors(text: str, maximum: int = 40) -> list[dict[str, Any]]:
    anchors = []
    for start in range(0, min(len(text), 40000), 1000):
        excerpt = text[start : start + 500].replace("\n", " ").strip()
        if excerpt:
            anchors.append(
                {
                    "source_id": f"SRC-{len(anchors) + 1:03d}",
                    "page_idx": 0,
                    "type": "text",
                    "text": excerpt,
                }
            )
        if len(anchors) >= maximum:
            break
    return anchors


def frozen_paper_context(paper: Paper, role: str) -> str:
    return format_canonical_paper_context(
        paper.text,
        {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "venue": str(paper.source.get("conference") or ""),
            "year": "",
            "reviewer_role": role,
        },
        _fallback_anchors(paper.text),
    )


def _call_with_identical_retry(
    client: CompletionClient,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    component: str,
) -> tuple[str, int]:
    errors = []
    last_error: Exception | None = None
    infrastructure_failed = False
    for attempt in range(1, SPECIALIST_MAX_ATTEMPTS + 1):
        try:
            value = client.complete(
                messages,
                max_tokens=max_tokens,
                component=component,
            )
        except EmptyCompletionOutput as exc:
            last_error = exc
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        except Exception as exc:
            infrastructure_failed = True
            last_error = exc
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if isinstance(value, str) and value.strip():
            return value, attempt
        last_error = RuntimeError("empty or non-string model output")
        errors.append(f"{type(last_error).__name__}: {last_error}")
    failure_type = (
        SpecialistReviewInfrastructureFailure
        if infrastructure_failed
        else SpecialistReviewPaperFailure
    )
    raise failure_type(
        f"{component} failed {SPECIALIST_MAX_ATTEMPTS} times with identical request: "
        f"{errors}"
    ) from last_error


def _review_id(
    *,
    run_id: str,
    paper_id: str,
    role: str,
    program_version: str,
    snapshot_id: str,
) -> str:
    return hashlib.sha256(
        f"{run_id}\n{paper_id}\n{role}\n{program_version}\n{snapshot_id}".encode(
            "utf-8"
        )
    ).hexdigest()


def _load_resumable_raw_artifact(
    path: Path,
    *,
    paper_id: str,
    role: str,
    run_id: str,
    request_sha256: str,
    model_identity: dict[str, Any],
    generation_identity: dict[str, Any],
    program_version: str,
    snapshot_id: str,
) -> dict[str, Any] | None:
    """Return a committed model response after verifying its request identity."""
    if not path.is_file():
        return None
    value = read_json(path)
    expected = {
        "paper_id": paper_id,
        "role": role,
        "run_id": run_id,
        "request_sha256": request_sha256,
        "model_identity": model_identity,
        "generation_identity": generation_identity,
        "program_version": program_version,
        "memory_snapshot_id": snapshot_id,
    }
    observed = {name: value.get(name) for name in expected}
    if observed != expected:
        raise ValueError(
            f"Committed raw Review identity mismatch at {path}: "
            f"observed={observed!r}, expected={expected!r}"
        )
    raw_output = value.get("raw_model_output")
    attempts = value.get("attempts")
    created_at = value.get("created_at")
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise ValueError(f"Committed raw Review is empty at {path}")
    if attempts not in (1, 2):
        raise ValueError(f"Committed raw Review has invalid attempts at {path}")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError(f"Committed raw Review has no creation time at {path}")
    return value


def _save_failure_attempt(target_dir: Path, role: str, failure: dict[str, Any]) -> Path:
    """Preserve every failed invocation at a unique immutable path."""
    failure_id = value_sha256(failure)
    payload = {**failure, "failure_id": failure_id}
    return atomic_write_json(
        target_dir / "failures" / role / f"{failure_id}.json",
        payload,
    )


def run_specialist_review(
    *,
    paper_path: str | Path,
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    model_identity: dict[str, Any],
    program_version: str,
    review_strategy: str = "",
    s2_enabled: bool = True,
    frozen_s2_path: str | Path | None = None,
    embedding_client: Any = None,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Generate and save one full Review; this function has no evaluator hook."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    paper = Paper.from_dict(read_json(paper_path))
    snapshot = read_json(snapshot_path)
    if str(snapshot.get("program_version") or "") != program_version:
        raise ValueError("Review Program version differs from its Memory snapshot")
    memory = retrieve_memory(
        paper=paper,
        role=role,
        program_path=program_path,
        snapshot_path=snapshot_path,
        embedding_client=embedding_client,
        completion_client=completion_client,
        require_bubblewrap=require_bubblewrap,
    )
    s2_values: list[dict[str, Any]] = []
    s2_provenance: dict[str, Any] = {"enabled": False, "result_ids": []}
    if s2_enabled:
        if frozen_s2_path is None:
            raise ValueError("s2_enabled=true requires a Frozen S2 Pool")
        s2_values, s2_provenance = FrozenS2Store(frozen_s2_path).retrieve(
            paper_id=paper.paper_id,
            target_title=paper.title,
            limit=5,
        )
        s2_provenance["enabled"] = True
    paper_context = frozen_paper_context(paper, role)
    user_parts = [
        SPECIALIST_REQUEST_TEMPLATE.format(
            task_id=role,
            paper_context=paper_context,
        ),
        "[REVIEW EXPERIENCE]\n" + (memory["context"] or "(none)"),
        "[FROZEN S2 EVIDENCE]\n" + (format_s2_evidence(s2_values) or "(disabled or empty)"),
    ]
    if review_strategy.strip():
        user_parts.append("[VERSIONED REVIEW STRATEGY]\n" + review_strategy.strip())
    messages = [
        {
            "role": "system",
            "content": load_reviewer_suite().task(role).system_prompt,
        },
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    output = Path(output_root).expanduser().resolve()
    target_dir = output / "reviews" / run_id / paper.paper_id
    request_sha256 = value_sha256(messages)
    generation_identity = specialist_generation_identity(model_identity)
    raw_path = target_dir / f"{role}.raw.json"
    raw_artifact = _load_resumable_raw_artifact(
        raw_path,
        paper_id=paper.paper_id,
        role=role,
        run_id=run_id,
        request_sha256=request_sha256,
        model_identity=model_identity,
        generation_identity=generation_identity,
        program_version=program_version,
        snapshot_id=str(snapshot.get("snapshot_id") or ""),
    )
    if raw_artifact is None:
        try:
            raw_output, attempts = _call_with_identical_retry(
                completion_client,
                messages,
                max_tokens=REVIEW_MAX_TOKENS,
                component=f"specialist_review:{role}",
            )
        except Exception as exc:
            failure = {
                "paper_id": paper.paper_id,
                "role": role,
                "run_id": run_id,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "request_sha256": request_sha256,
                "model_identity": model_identity,
                "generation_identity": generation_identity,
                "program_version": program_version,
                "memory_snapshot_id": str(snapshot.get("snapshot_id") or ""),
                "created_at": _utc_now(),
            }
            _save_failure_attempt(target_dir, role, failure)
            raise
        raw_artifact = {
            "paper_id": paper.paper_id,
            "role": role,
            "run_id": run_id,
            "raw_model_output": raw_output,
            "request_sha256": request_sha256,
            "model_identity": model_identity,
            "generation_identity": generation_identity,
            "program_version": program_version,
            "memory_snapshot_id": str(snapshot.get("snapshot_id") or ""),
            "attempts": attempts,
            "created_at": _utc_now(),
        }
        atomic_write_json(raw_path, raw_artifact)
    raw_output = str(raw_artifact["raw_model_output"])
    attempts = int(raw_artifact["attempts"])
    review_id = _review_id(
        run_id=run_id,
        paper_id=paper.paper_id,
        role=role,
        program_version=program_version,
        snapshot_id=str(snapshot.get("snapshot_id") or ""),
    )
    review = SpecialistReview(
        review_id=review_id,
        paper_id=paper.paper_id,
        role=role,
        program_version=program_version,
        memory_snapshot_id=str(snapshot.get("snapshot_id") or ""),
        review_text=raw_output,
        raw_model_output=raw_output,
        reviewer_identity=load_reviewer_suite().identity(role),
        model_identity=dict(model_identity),
        generation_config={
            "temperature": generation_identity["temperature"],
            "enable_thinking": generation_identity["enable_thinking"],
            "max_tokens": generation_identity["max_tokens"],
            "retry": generation_identity["retry"],
            "attempts": attempts,
        },
        retrieval_provenance={
            "memory": memory,
            "s2": {**s2_provenance, "evidence": s2_values},
        },
        run_id=run_id,
        created_at=str(raw_artifact["created_at"]),
    )
    payload = review.to_dict()
    atomic_write_json(target_dir / f"{role}.json", payload)
    return payload
