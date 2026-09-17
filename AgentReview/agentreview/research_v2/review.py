"""Review-only pipeline: frozen inputs to one immutable Specialist Review."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import SPECIALIST_REQUEST_TEMPLATE, load_reviewer_suite

from .constants import ENABLE_THINKING, REVIEW_MAX_TOKENS, ROLES, TEMPERATURE
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient, EmptyCompletionOutput
from .memory import retrieve_memory, seed_program_path
from .models import Paper, SpecialistReview
from .review_preparation import (
    frozen_paper_context,
    normalize_s2_intervention,
    prepare_review_invocation,
)
from .s2 import FrozenS2Store, format_s2_evidence


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SPECIALIST_INVOCATION_PROTOCOL = "research-v2-specialist-invocation-v1"
SPECIALIST_RETRY_PROTOCOL = "identical-request-at-most-two-v1"
REVIEW_REQUEST_MATERIALIZATION_PROTOCOL = "research-v2-review-request-v1"
SPECIALIST_MAX_ATTEMPTS = 2


class SpecialistReviewFailure(RuntimeError):
    """Base class for an exhausted Specialist Review invocation."""


class SpecialistReviewPaperFailure(SpecialistReviewFailure):
    """A request completed but produced no usable paper-local output."""


class SpecialistReviewInfrastructureFailure(SpecialistReviewFailure):
    """The completion transport or shared runtime failed."""


def specialist_generation_identity(
    model_identity: dict[str, Any],
    *, max_tokens: int = REVIEW_MAX_TOKENS,
) -> dict[str, Any]:
    """Return the frozen invocation policy shared by raw artifacts and run specs."""
    return {
        "protocol": SPECIALIST_INVOCATION_PROTOCOL,
        "completion_contract": "complete-stop-v1",
        "model_identity": dict(model_identity),
        "temperature": model_identity.get("temperature", TEMPERATURE),
        "enable_thinking": model_identity.get("enable_thinking", ENABLE_THINKING),
        "max_tokens": max_tokens,
        "retry": {
            "protocol": SPECIALIST_RETRY_PROTOCOL,
            "max_attempts": SPECIALIST_MAX_ATTEMPTS,
            "sdk_max_retries": 0,
        },
    }


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
    review_plan_identity_sha256: str = "",
) -> str:
    values = f"{run_id}\n{paper_id}\n{role}\n{program_version}\n{snapshot_id}"
    if review_plan_identity_sha256:
        values += f"\n{review_plan_identity_sha256}"
    return hashlib.sha256(
        values.encode("utf-8")
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
    review_plan_identity: dict[str, Any] | None = None,
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
    if review_plan_identity is not None:
        expected["review_plan_identity"] = review_plan_identity
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


def _review_request_input_identity(
    *,
    paper_path: str | Path,
    paper: Paper,
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    snapshot_id: str,
    program_version: str,
    frozen_s2_path: str | Path | None,
    frozen_s2_sha256: str | None,
    s2_intervention: dict[str, Any] | None,
    model_identity: dict[str, Any],
    embedding_identity: dict[str, Any] | None,
    require_bubblewrap: bool,
) -> dict[str, Any]:
    if not isinstance(require_bubblewrap, bool):
        raise TypeError("Review request require_bubblewrap must be a boolean")
    paper_source = Path(paper_path).expanduser().resolve()
    program_source = Path(program_path).expanduser().resolve()
    snapshot_source = Path(snapshot_path).expanduser().resolve()
    s2_source = (
        Path(frozen_s2_path).expanduser().resolve()
        if frozen_s2_path is not None
        else None
    )
    s2_sha256 = (
        frozen_s2_sha256 or file_sha256(s2_source)
        if s2_source is not None
        else None
    )
    return {
        "paper_id": paper.paper_id,
        "paper_path": str(paper_source),
        "paper_sha256": file_sha256(paper_source),
        "role": role,
        "program_path": str(program_source),
        "program_sha256": file_sha256(program_source),
        "program_version": program_version,
        "model_identity": dict(model_identity),
        "embedding_identity": dict(embedding_identity or {}),
        "require_bubblewrap": require_bubblewrap,
        "snapshot_path": str(snapshot_source),
        "snapshot_sha256": file_sha256(snapshot_source),
        "snapshot_id": snapshot_id,
        "frozen_s2_path": str(s2_source) if s2_source is not None else None,
        "frozen_s2_sha256": s2_sha256,
        "s2_intervention": normalize_s2_intervention(s2_intervention),
    }


def _materialize_review_request(
    *,
    path: Path,
    input_identity: dict[str, Any],
    messages: list[dict[str, str]],
    retrieval_provenance: dict[str, Any],
    review_plan: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "protocol": REVIEW_REQUEST_MATERIALIZATION_PROTOCOL,
        "input_identity": input_identity,
        "messages": messages,
        "retrieval_provenance": retrieval_provenance,
        "review_plan": review_plan,
        "request_sha256": value_sha256(messages),
    }
    payload = {**body, "preparation_sha256": value_sha256(body)}
    atomic_write_json(path, payload)
    return payload


def _review_request_input_identity_matches(
    *,
    protocol: Any,
    stored: Any,
    expected: dict[str, Any],
    allow_legacy_missing_require_bubblewrap: bool,
) -> bool:
    if stored == expected:
        return True
    if (
        allow_legacy_missing_require_bubblewrap is not True
        or protocol != REVIEW_REQUEST_MATERIALIZATION_PROTOCOL
        or expected.get("require_bubblewrap") is not True
        or not isinstance(stored, dict)
        or "require_bubblewrap" in stored
    ):
        return False
    historical_expected = dict(expected)
    historical_expected.pop("require_bubblewrap")
    return stored == historical_expected


def _load_review_request(
    path: Path,
    *,
    input_identity: dict[str, Any],
    allow_legacy_missing_require_bubblewrap: bool,
) -> dict[str, Any]:
    value = read_json(path)
    if value.get("protocol") != REVIEW_REQUEST_MATERIALIZATION_PROTOCOL:
        raise ValueError(f"Review request protocol mismatch at {path}")
    if not _review_request_input_identity_matches(
        protocol=value.get("protocol"),
        stored=value.get("input_identity"),
        expected=input_identity,
        allow_legacy_missing_require_bubblewrap=(
            allow_legacy_missing_require_bubblewrap
        ),
    ):
        raise ValueError(f"Review request input identity mismatch at {path}")
    body = {name: item for name, item in value.items() if name != "preparation_sha256"}
    if value.get("preparation_sha256") != value_sha256(body):
        raise ValueError(f"Review request materialization hash mismatch at {path}")
    messages = value.get("messages")
    if not isinstance(messages, list) or value.get("request_sha256") != value_sha256(
        messages
    ):
        raise ValueError(f"Review request prompt hash mismatch at {path}")
    provenance = value.get("retrieval_provenance")
    plan = value.get("review_plan")
    if not isinstance(provenance, dict) or not isinstance(plan, dict):
        raise ValueError(f"Review request plan/provenance is invalid at {path}")
    assembly = provenance.get("assembly")
    s2 = provenance.get("s2")
    if (
        not isinstance(assembly, dict)
        or assembly.get("prompt_sha256") != value["request_sha256"]
        or not isinstance(s2, dict)
        or s2.get("plan_sha256") != value_sha256(plan)
    ):
        raise ValueError(f"Review request provenance identity mismatch at {path}")
    return value


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
    frozen_s2_path: str | Path | None = None,
    frozen_s2_store: Any = None,
    s2_intervention: dict[str, Any] | None = None,
    embedding_client: Any = None,
    embedding_identity: dict[str, Any] | None = None,
    require_bubblewrap: bool = True,
    _canonical_bootstrap_s2_enabled: bool | None = None,
    _allow_legacy_missing_require_bubblewrap: bool = False,
    memory_enabled: bool = True,
    memory_runtime: dict[str, Any] | None = None,
    max_tokens: int = REVIEW_MAX_TOKENS,
) -> dict[str, Any]:
    """Generate and save one full Review; this function has no evaluator hook."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    paper = Paper.from_dict(read_json(paper_path))
    snapshot = read_json(snapshot_path)
    if str(snapshot.get("program_version") or "") != program_version:
        raise ValueError("Review Program version differs from its Memory snapshot")
    if embedding_client is not None and not embedding_identity:
        raise ValueError(
            "Review preparation with an embedding client requires embedding_identity"
        )
    output = Path(output_root).expanduser().resolve()
    target_dir = output / "reviews" / run_id / paper.paper_id
    frozen_s2_sha256 = None
    if frozen_s2_store is not None:
        store_path = (
            Path(str(getattr(frozen_s2_store, "path", ""))).expanduser().resolve()
        )
        expected_s2_path = (
            Path(frozen_s2_path).expanduser().resolve()
            if frozen_s2_path is not None
            else None
        )
        if expected_s2_path is None or store_path != expected_s2_path:
            raise ValueError("Injected Frozen S2 store path differs from Review input")
        frozen_s2_sha256 = str(getattr(frozen_s2_store, "sha256", ""))
        if not frozen_s2_sha256:
            raise ValueError("Injected Frozen S2 store lacks a content identity")
    review_plan_identity = None
    review_plan_identity_sha256 = ""
    if _canonical_bootstrap_s2_enabled is None:
        input_identity = _review_request_input_identity(
            paper_path=paper_path,
            paper=paper,
            role=role,
            program_path=program_path,
            snapshot_path=snapshot_path,
            snapshot_id=str(snapshot.get("snapshot_id") or ""),
            program_version=program_version,
            frozen_s2_path=frozen_s2_path,
            frozen_s2_sha256=frozen_s2_sha256,
            s2_intervention=s2_intervention,
            model_identity=model_identity,
            embedding_identity=embedding_identity,
            require_bubblewrap=require_bubblewrap,
        )
        if memory_runtime is not None or memory_enabled is False or max_tokens != REVIEW_MAX_TOKENS:
            from agentreview.paper_context import CANONICAL_PAPER_CONTEXT_VERSION
            input_identity.update({"memory_enabled": memory_enabled, "memory_runtime": memory_runtime,
                                   "max_tokens": max_tokens, "paper_context_version": CANONICAL_PAPER_CONTEXT_VERSION})
        request_path = target_dir / f"{role}.request.json"
        if request_path.is_file():
            request_materialization = _load_review_request(
                request_path,
                input_identity=input_identity,
                allow_legacy_missing_require_bubblewrap=(
                    _allow_legacy_missing_require_bubblewrap
                ),
            )
        else:
            try:
                prepared = prepare_review_invocation(
                    paper=paper,
                    role=role,
                    program_path=program_path,
                    snapshot_path=snapshot_path,
                    frozen_s2_path=frozen_s2_path,
                    frozen_s2_store=frozen_s2_store,
                    s2_intervention=s2_intervention,
                    embedding_client=embedding_client,
                    embedding_identity=embedding_identity,
                    completion_client=completion_client,
                    require_bubblewrap=require_bubblewrap,
                    memory_enabled=memory_enabled,
                    memory_runtime=memory_runtime,
                )
            except Exception as exc:
                _save_failure_attempt(
                    target_dir,
                    role,
                    {
                        "paper_id": paper.paper_id,
                        "role": role,
                        "run_id": run_id,
                        "status": "failed",
                        "phase": "review_preparation",
                        "error": f"{type(exc).__name__}: {exc}",
                        "input_identity": input_identity,
                        "created_at": _utc_now(),
                    },
                )
                raise
            request_materialization = _materialize_review_request(
                path=request_path,
                input_identity=input_identity,
                messages=prepared.messages,
                retrieval_provenance=prepared.retrieval_provenance,
                review_plan=prepared.review_plan,
            )
        messages = list(request_materialization["messages"])
        retrieval_provenance = dict(
            request_materialization["retrieval_provenance"]
        )
        s2_provenance = dict(retrieval_provenance["s2"])
        review_plan_identity = {
            "intervention": dict(s2_provenance["intervention"]),
            "capability_manifest": dict(s2_provenance["capability_manifest"]),
            "plan_sha256": str(s2_provenance["plan_sha256"]),
            "preparation_sha256": str(
                request_materialization["preparation_sha256"]
            ),
        }
        review_plan_identity_sha256 = value_sha256(review_plan_identity)
    else:
        if program_version != "canonical-no-memory-v1":
            raise ValueError(
                "The fixed Review path is reserved for canonical no-Memory bootstrap"
            )
        if Path(program_path).expanduser().resolve() != seed_program_path(
            "no_memory"
        ).resolve():
            raise ValueError(
                "Canonical bootstrap requires the frozen no-Memory seed Program"
            )
        if s2_intervention is not None:
            raise ValueError("Canonical bootstrap does not accept s2_intervention")
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
        if _canonical_bootstrap_s2_enabled:
            if frozen_s2_path is None:
                raise ValueError("canonical bootstrap with S2 requires its frozen pool")
            s2_store = (
                frozen_s2_store
                if frozen_s2_store is not None
                else FrozenS2Store(frozen_s2_path)
            )
            s2_values, s2_provenance = s2_store.retrieve(
                paper_id=paper.paper_id,
                target_title=paper.title,
                limit=5,
            )
            s2_provenance["enabled"] = True
        user_parts = [
            SPECIALIST_REQUEST_TEMPLATE.format(
                task_id=role,
                paper_context=frozen_paper_context(paper, role),
            ),
            "[REVIEW EXPERIENCE]\n" + (memory["context"] or "(none)"),
            "[FROZEN S2 EVIDENCE]\n"
            + (format_s2_evidence(s2_values) or "(disabled or empty)"),
        ]
        messages = [
            {
                "role": "system",
                "content": load_reviewer_suite().task(role).system_prompt,
            },
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
        retrieval_provenance = {
            "memory": memory,
            "s2": {**s2_provenance, "evidence": s2_values},
        }
    request_sha256 = value_sha256(messages)
    generation_identity = specialist_generation_identity(model_identity, max_tokens=max_tokens)
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
        review_plan_identity=review_plan_identity,
    )
    if raw_artifact is None:
        try:
            raw_output, attempts = _call_with_identical_retry(
                completion_client,
                messages,
                max_tokens=max_tokens,
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
            if review_plan_identity is not None:
                failure["review_plan_identity"] = review_plan_identity
                failure["review_plan_identity_sha256"] = review_plan_identity_sha256
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
        if review_plan_identity is not None:
            raw_artifact["review_plan_identity"] = review_plan_identity
            raw_artifact["review_plan_identity_sha256"] = review_plan_identity_sha256
        atomic_write_json(raw_path, raw_artifact)
    raw_output = str(raw_artifact["raw_model_output"])
    attempts = int(raw_artifact["attempts"])
    review_id = _review_id(
        run_id=run_id,
        paper_id=paper.paper_id,
        role=role,
        program_version=program_version,
        snapshot_id=str(snapshot.get("snapshot_id") or ""),
        review_plan_identity_sha256=review_plan_identity_sha256,
    )
    generation_config = {
        "completion_contract": generation_identity["completion_contract"],
        "temperature": generation_identity["temperature"],
        "enable_thinking": generation_identity["enable_thinking"],
        "max_tokens": generation_identity["max_tokens"],
        "retry": generation_identity["retry"],
        "attempts": attempts,
    }
    if review_plan_identity is not None:
        generation_config.update(
            {
                "request_sha256": request_sha256,
                "preparation_sha256": review_plan_identity["preparation_sha256"],
            }
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
        generation_config=generation_config,
        retrieval_provenance=retrieval_provenance,
        run_id=run_id,
        created_at=str(raw_artifact["created_at"]),
    )
    payload = review.to_dict()
    atomic_write_json(target_dir / f"{role}.json", payload)
    return payload
