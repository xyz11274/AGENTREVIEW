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
from .llm import CompletionClient, EmptyCompletionOutput
from .models import FinalReview, Paper
from .review import frozen_paper_context

META_RAW_PROTOCOL = "research-v2-meta-raw-v1"
META_MAX_ATTEMPTS = 2


class MetaSynthesisFailure(RuntimeError):
    """Base class for an exhausted Meta synthesis invocation."""


class MetaSynthesisPaperFailure(MetaSynthesisFailure):
    """The endpoint responded, but no valid paper-local synthesis was produced."""


class MetaSynthesisInfrastructureFailure(MetaSynthesisFailure):
    """The completion transport or shared runtime failed."""


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


def _meta_output_valid(value: str) -> bool:
    forbidden = re.compile(r"(?im)^\s*(final\s+score|confidence)\s*:")
    return bool(value.strip()) and not forbidden.search(value)


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
        raise ValueError(f"Committed concat Final Review content mismatch: {path}")
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
    final_id = hashlib.sha256(
        f"{run_id}\n{paper_id}\n{mode}\n".encode("utf-8")
        + "\n".join(source_ids).encode("utf-8")
    ).hexdigest()
    meta_identity = None
    generation_config = None
    expected_text = None
    if mode == "concat":
        expected_text = _concat(by_role)
    elif mode == "meta":
        if completion_client is None:
            raise ValueError("meta mode requires a completion client")
        synthesizer = load_meta_synthesizer()
        meta_identity = synthesizer.identity()
        generation_config = {
            "temperature": 0.0,
            "enable_thinking": False,
            "max_tokens": META_MAX_TOKENS,
        }
    else:
        raise ValueError("mode must be meta or concat")
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
    resumed = _load_resumable_final(
        target,
        expected_identity=expected_final_identity,
        expected_text=expected_text,
    )
    if resumed is not None:
        return resumed

    created_at = _utc_now()
    if mode == "concat":
        text = str(expected_text)
    else:
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
                try:
                    candidate = completion_client.complete(
                        messages,
                        max_tokens=META_MAX_TOKENS,
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
            if not _meta_output_valid(candidate):
                errors.append(
                    "ValueError: Meta output contains a forbidden score/confidence field"
                )
                continue
            text = candidate
            created_at = str(raw["created_at"])
            break
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
                immutable=False,
            )
            failure_type = (
                MetaSynthesisInfrastructureFailure
                if infrastructure_failed
                else MetaSynthesisPaperFailure
            )
            raise failure_type(
                f"Meta synthesis failed; concat fallback is forbidden: {errors}"
            )
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
    if model_identity is not None and mode == "meta":
        payload["model_identity"] = dict(model_identity)
        payload["generation_config"] = generation_config
    atomic_write_json(target, payload)
    return payload
