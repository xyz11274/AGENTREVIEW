"""Review-only pipeline: frozen inputs to one immutable Specialist Review."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.paper_context import format_canonical_paper_context
from agentreview.reviewer_identity import SPECIALIST_REQUEST_TEMPLATE, load_reviewer_suite

from .constants import REVIEW_MAX_TOKENS, ROLES
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient
from .memory import retrieve_memory
from .models import Paper, SpecialistReview
from .s2 import FrozenS2Store, format_s2_evidence


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    for attempt in (1, 2):
        try:
            value = client.complete(
                messages,
                max_tokens=max_tokens,
                component=component,
            )
            if not value.strip():
                raise RuntimeError("empty model output")
            return value, attempt
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError(f"{component} failed twice with identical request: {errors}")


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
            "request_sha256": value_sha256(messages),
            "created_at": _utc_now(),
        }
        atomic_write_json(target_dir / f"{role}.failure.json", failure)
        raise
    raw_artifact = {
        "paper_id": paper.paper_id,
        "role": role,
        "run_id": run_id,
        "raw_model_output": raw_output,
        "request_sha256": value_sha256(messages),
        "attempts": attempts,
        "created_at": _utc_now(),
    }
    atomic_write_json(target_dir / f"{role}.raw.json", raw_artifact)
    review_id = hashlib.sha256(
        f"{run_id}\n{paper.paper_id}\n{role}\n{program_version}\n{snapshot.get('snapshot_id')}".encode("utf-8")
    ).hexdigest()
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
            "temperature": 0.0,
            "enable_thinking": False,
            "max_tokens": REVIEW_MAX_TOKENS,
            "attempts": attempts,
        },
        retrieval_provenance={
            "memory": memory,
            "s2": {**s2_provenance, "evidence": s2_values},
        },
        run_id=run_id,
        created_at=_utc_now(),
    )
    payload = review.to_dict()
    atomic_write_json(target_dir / f"{role}.json", payload)
    return payload
