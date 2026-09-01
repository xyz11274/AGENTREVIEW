"""Stateless Meta synthesis and deterministic concat Final Reviews."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import (
    SPECIALIST_TASKS,
    build_meta_review_question,
    load_meta_synthesizer,
)

from .constants import META_MAX_TOKENS
from .io import atomic_write_json, read_json, value_sha256
from .llm import CompletionClient
from .models import FinalReview, Paper
from .review import frozen_paper_context


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_bundle(review_paths: list[str | Path]) -> tuple[str, dict[str, dict[str, Any]]]:
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


def _meta_output_valid(value: str) -> bool:
    forbidden = re.compile(r"(?im)^\s*(final\s+score|confidence)\s*:")
    return not forbidden.search(value)


def generate_final_review(
    *,
    paper_path: str | Path,
    review_paths: list[str | Path],
    mode: str,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient | None,
    model_identity: dict[str, Any] | None,
) -> dict[str, Any]:
    paper_id, by_role = _load_bundle(review_paths)
    paper = Paper.from_dict(read_json(paper_path))
    if paper.paper_id != paper_id:
        raise ValueError("Frozen Paper does not match the Specialist bundle")
    output = Path(output_root).expanduser().resolve()
    target = output / "final_reviews" / run_id / f"{paper_id}.json"
    meta_identity = None
    if mode == "concat":
        text = _concat(by_role)
    elif mode == "meta":
        if completion_client is None:
            raise ValueError("meta mode requires a completion client")
        synthesizer = load_meta_synthesizer()
        meta_identity = synthesizer.identity()
        question = build_meta_review_question(
            paper_id=paper_id,
            specialist_reviews={
                role: str(by_role[role].get("review_text") or "")
                for role in SPECIALIST_TASKS
            },
            paper_context=frozen_paper_context(paper, "META"),
        )
        messages = [
            {"role": "system", "content": synthesizer.system_prompt},
            {"role": "user", "content": question},
        ]
        errors = []
        text = ""
        for attempt in (1, 2):
            try:
                candidate = completion_client.complete(
                    messages,
                    max_tokens=META_MAX_TOKENS,
                    component="meta_synthesis",
                )
                atomic_write_json(
                    output / "final_reviews" / run_id / f"{paper_id}.meta.raw.{attempt}.json",
                    {
                        "paper_id": paper_id,
                        "attempt": attempt,
                        "raw_model_output": candidate,
                        "request_sha256": value_sha256(messages),
                        "created_at": _utc_now(),
                    },
                )
                if not _meta_output_valid(candidate):
                    raise ValueError("Meta output contains a forbidden score/confidence field")
                text = candidate
                break
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        if not text:
            failure = {
                "paper_id": paper_id,
                "mode": "meta",
                "status": "failed",
                "errors": errors,
                "created_at": _utc_now(),
            }
            atomic_write_json(
                output / "final_reviews" / run_id / f"{paper_id}.failure.json",
                failure,
            )
            raise RuntimeError(f"Meta synthesis failed; concat fallback is forbidden: {errors}")
    else:
        raise ValueError("mode must be meta or concat")
    source_ids = [str(by_role[role].get("review_id") or "") for role in SPECIALIST_TASKS]
    final_id = hashlib.sha256(
        f"{run_id}\n{paper_id}\n{mode}\n".encode("utf-8")
        + "\n".join(source_ids).encode("utf-8")
    ).hexdigest()
    final = FinalReview(
        final_review_id=final_id,
        paper_id=paper_id,
        mode=mode,
        review_text=text,
        source_review_ids=source_ids,
        meta_identity=meta_identity,
        run_id=run_id,
        created_at=_utc_now(),
    )
    payload = final.to_dict()
    if model_identity is not None and mode == "meta":
        payload["model_identity"] = dict(model_identity)
        payload["generation_config"] = {
            "temperature": 0.0,
            "enable_thinking": False,
            "max_tokens": META_MAX_TOKENS,
        }
    atomic_write_json(target, payload)
    return payload
