"""Validated preparation of one Program-directed Specialist invocation.

This module is the authority boundary between an evolved Reviewer Program and
the immutable host runtime. The Program decides whether to consult current-paper
S2, which returned records to use, and how to assemble its Review context. The
host validates that decision, hydrates evidence from the frozen pool, and emits
an auditable prompt without allowing the Program to forge evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from agentreview.paper_context import format_canonical_paper_context, text_source_anchors
from agentreview.reviewer_identity import SPECIALIST_REQUEST_TEMPLATE, load_reviewer_suite

from .io import value_sha256
from .llm import CompletionClient
from .memory import retrieve_memory
from .models import Paper
from .program_checks import REVIEW_PLAN_PROTOCOL, program_capability_manifest
from .s2 import FrozenS2Store, format_s2_evidence

RETRIEVAL_PROVENANCE_PROTOCOL = "retrieval-provenance-v2"
S2_INTERVENTION_PROTOCOL = "s2-intervention-v1"
FORMAL_S2_INTERVENTION = {
    "protocol": S2_INTERVENTION_PROTOCOL,
    "mode": "program_autonomous",
    "label": "formal-primary-v1",
}
_S2_INTERVENTION_MODES = {"program_autonomous", "forced_off", "forced_consult"}
_S2_DISPOSITIONS = {"not_consulted", "consulted_no_use", "use"}
_CONTEXT_NAMES = ("memory", "s2", "strategy")
_MAX_SELECTED_S2 = 5
_TEXT_LIMITS = {
    "decision_label": 128,
    "usage_instruction": 1500,
    "review_strategy": 2000,
}


class ReviewPlanError(ValueError):
    """An evolved Review Plan violates the host protocol or frozen evidence."""


@dataclass(frozen=True)
class PreparedReviewInvocation:
    messages: list[dict[str, str]]
    retrieval_provenance: dict[str, Any]
    review_plan: dict[str, Any]


def frozen_paper_context(paper: Paper, role: str, *, max_chars: int | None = None) -> str:
    """Build the immutable paper-local context shared by Review and evaluation."""
    venue_year = str(paper.source.get("venue_year") or "")
    match = re.fullmatch(r"\s*(.*?)\s*[_ /-]?((?:19|20)\d{2})\s*", venue_year)
    venue = str(paper.source.get("venue") or paper.source.get("conference") or (match.group(1).rstrip("_ /-") if match else ""))
    year = str(paper.source.get("year") or (match.group(2) if match else ""))
    return format_canonical_paper_context(
        paper.text,
        {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "venue": venue,
            "year": year,
            "reviewer_role": role,
        },
        paper.source.get("source_anchors") or text_source_anchors(paper.text),
        max_chars=max_chars if max_chars is not None else paper.source.get("paper_context_max_chars"),
    )


def normalize_s2_intervention(value: dict[str, Any] | None) -> dict[str, str]:
    """Return the explicit intervention identity used by every Review artifact."""
    if value is None:
        return dict(FORMAL_S2_INTERVENTION)
    if not isinstance(value, dict):
        raise ReviewPlanError("s2_intervention must be a dict or None")
    required = {"protocol", "mode", "label"}
    if set(value) != required:
        raise ReviewPlanError(
            "s2_intervention fields must be exactly protocol, mode, and label"
        )
    if value.get("protocol") != S2_INTERVENTION_PROTOCOL:
        raise ReviewPlanError(
            f"s2_intervention protocol must be {S2_INTERVENTION_PROTOCOL!r}"
        )
    mode = value.get("mode")
    if mode not in _S2_INTERVENTION_MODES:
        raise ReviewPlanError(f"Unsupported s2_intervention mode: {mode!r}")
    label = value.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ReviewPlanError("s2_intervention label must be a non-empty string")
    return {
        "protocol": S2_INTERVENTION_PROTOCOL,
        "mode": str(mode),
        "label": label.strip(),
    }


def _s2_id(value: dict[str, Any]) -> str:
    return str(
        value.get("paperId")
        or value.get("paper_id")
        or value.get("id")
        or ""
    )


def _load_s2_capability(
    *,
    paper: Paper,
    frozen_s2_path: str | Path | None,
    frozen_s2_store: Any = None,
    intervention: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    mode = intervention["mode"]
    if frozen_s2_path is None:
        if mode != "forced_off":
            raise ReviewPlanError(
                "Program-autonomous S2 planning requires a Frozen S2 Pool"
            )
        return [], {
            "pool_path": None,
            "pool_sha256": None,
            "pool_version": None,
            "paper_id": paper.paper_id,
            "result_ids": [],
        }, False

    store = (
        frozen_s2_store
        if frozen_s2_store is not None
        else FrozenS2Store(frozen_s2_path)
    )
    store_path = Path(str(getattr(store, "path", ""))).expanduser().resolve()
    if store_path != Path(frozen_s2_path).expanduser().resolve():
        raise ReviewPlanError("Injected Frozen S2 store path differs from the request")
    values, provenance = store.retrieve(
        paper_id=paper.paper_id,
        target_title=paper.title,
        limit=_MAX_SELECTED_S2,
    )
    ids = [_s2_id(item) for item in values]
    if any(not value for value in ids):
        raise ReviewPlanError("Frozen S2 candidates require non-empty paper IDs")
    if len(ids) != len(set(ids)):
        raise ReviewPlanError("Frozen S2 candidates require unique paper IDs")
    return values, provenance, mode != "forced_off"


def _validated_plan(
    *,
    raw_plan: Any,
    s2_trace: dict[str, Any],
    intervention: dict[str, str],
) -> tuple[dict[str, Any], list[str], list[int]]:
    if not isinstance(raw_plan, dict):
        raise ReviewPlanError("plan_review() must return a dict")
    if raw_plan.get("protocol") != REVIEW_PLAN_PROTOCOL:
        raise ReviewPlanError(
            f"Review Plan protocol must be {REVIEW_PLAN_PROTOCOL!r}"
        )

    disposition = raw_plan.get("s2_disposition")
    if disposition not in _S2_DISPOSITIONS:
        raise ReviewPlanError(f"Invalid s2_disposition: {disposition!r}")
    selected_ids = raw_plan.get("selected_s2_ids")
    if not isinstance(selected_ids, list) or any(
        not isinstance(item, str) or not item or len(item) > 256
        for item in selected_ids
    ):
        raise ReviewPlanError(
            "selected_s2_ids must contain only bounded non-empty strings"
        )
    if len(selected_ids) > _MAX_SELECTED_S2:
        raise ReviewPlanError(
            f"selected_s2_ids exceeds the {_MAX_SELECTED_S2}-record capability limit"
        )
    if len(selected_ids) != len(set(selected_ids)):
        raise ReviewPlanError("selected_s2_ids must be unique")

    for field_name, maximum in _TEXT_LIMITS.items():
        value = raw_plan.get(field_name)
        if not isinstance(value, str):
            raise ReviewPlanError(f"{field_name} must be a string")
        if len(value) > maximum:
            raise ReviewPlanError(f"{field_name} exceeds {maximum} characters")
    if not raw_plan["decision_label"].strip():
        raise ReviewPlanError("decision_label must be non-empty")
    if disposition == "use" and not raw_plan["usage_instruction"].strip():
        raise ReviewPlanError(
            "s2_disposition='use' requires a non-empty usage_instruction"
        )

    context_order = raw_plan.get("context_order")
    if (
        not isinstance(context_order, list)
        or len(context_order) != len(_CONTEXT_NAMES)
        or tuple(sorted(context_order)) != tuple(sorted(_CONTEXT_NAMES))
    ):
        raise ReviewPlanError(
            "context_order must be a permutation of memory, s2, and strategy"
        )

    calls = s2_trace.get("calls") or []
    if not isinstance(calls, list) or len(calls) > 1:
        raise ReviewPlanError("retrieve_current_s2 may be called at most once")
    requested_limits: list[int] = []
    candidate_ids: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            raise ReviewPlanError("S2 capability trace contains a non-object call")
        requested_limit = call.get("requested_limit")
        if (
            isinstance(requested_limit, bool)
            or not isinstance(requested_limit, int)
            or not 1 <= requested_limit <= _MAX_SELECTED_S2
        ):
            raise ReviewPlanError("S2 capability trace contains an invalid limit")
        result_ids = call.get("result_ids")
        if not isinstance(result_ids, list) or any(
            not isinstance(item, str) or not item for item in result_ids
        ):
            raise ReviewPlanError("S2 capability trace contains invalid result IDs")
        requested_limits.append(requested_limit)
        candidate_ids.extend(result_ids)

    lookup_count = len(calls)
    expected_disposition = (
        "not_consulted"
        if lookup_count == 0
        else "use"
        if selected_ids
        else "consulted_no_use"
    )
    if disposition != expected_disposition:
        raise ReviewPlanError(
            "s2_disposition disagrees with the observed capability call and selection"
        )
    unknown = sorted(set(selected_ids) - set(candidate_ids))
    if unknown:
        raise ReviewPlanError(
            "Review Plan selected S2 IDs not returned by the capability: "
            + ", ".join(unknown)
        )
    if intervention["mode"] == "forced_consult" and lookup_count != 1:
        raise ReviewPlanError(
            "forced_consult diagnostic requires one S2 capability call"
        )

    plan = {
        "protocol": REVIEW_PLAN_PROTOCOL,
        "s2_disposition": disposition,
        "selected_s2_ids": list(selected_ids),
        "decision_label": raw_plan["decision_label"].strip(),
        "usage_instruction": raw_plan["usage_instruction"].strip(),
        "context_order": list(context_order),
        "review_strategy": raw_plan["review_strategy"].strip(),
    }
    return plan, candidate_ids, requested_limits


def prepare_review_invocation(
    *,
    paper: Paper,
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    completion_client: CompletionClient,
    frozen_s2_path: str | Path | None,
    frozen_s2_store: Any = None,
    s2_intervention: dict[str, Any] | None = None,
    embedding_client: Any = None,
    embedding_identity: dict[str, Any] | None = None,
    require_bubblewrap: bool = True,
    memory_enabled: bool = True,
    memory_runtime: dict[str, Any] | None = None,
) -> PreparedReviewInvocation:
    """Execute and validate one Program plan, then assemble the exact prompt."""
    intervention = normalize_s2_intervention(s2_intervention)
    available_evidence, pool_provenance, capability_available = _load_s2_capability(
        paper=paper,
        frozen_s2_path=frozen_s2_path,
        frozen_s2_store=frozen_s2_store,
        intervention=intervention,
    )
    exposed_evidence = available_evidence if capability_available else []
    memory_result = retrieve_memory(
        paper=paper,
        role=role,
        program_path=program_path,
        snapshot_path=snapshot_path,
        embedding_client=embedding_client,
        embedding_identity=embedding_identity,
        completion_client=completion_client,
        prepare_review_plan=True,
        s2_retrieval_pool={paper.paper_id: exposed_evidence},
        s2_retrieval_pool_sha256=str(pool_provenance.get("pool_sha256") or ""),
        require_bubblewrap=require_bubblewrap,
        memory_enabled=memory_enabled,
        memory_runtime=memory_runtime,
    )
    raw_plan = memory_result.pop("review_plan", None)
    s2_trace = dict(memory_result.pop("s2_trace", {}) or {})
    plan, candidate_ids, requested_limits = _validated_plan(
        raw_plan=raw_plan,
        s2_trace=s2_trace,
        intervention=intervention,
    )

    evidence_by_id = {_s2_id(item): dict(item) for item in exposed_evidence}
    selected_evidence = [evidence_by_id[value] for value in plan["selected_s2_ids"]]
    candidate_evidence = [evidence_by_id[value] for value in candidate_ids]
    selected_ids = list(plan["selected_s2_ids"])
    dropped_ids = [value for value in candidate_ids if value not in set(selected_ids)]
    s2_context = format_s2_evidence(selected_evidence)
    if capability_available:
        rendered_s2_context = s2_context or "(not selected by the Reviewer Program)"
    else:
        rendered_s2_context = (
            "(capability unavailable due to an explicit diagnostic intervention)"
        )

    memory_parts = ["[REVIEW EXPERIENCE]\n" + (memory_result["context"] or "(none)")]
    if memory_enabled:
        for key, heading in (
            ("instruction_response", "[MEMORY RESPONSE INSTRUCTION]"),
            ("always_on_knowledge", "[MEMORY ALWAYS-ON KNOWLEDGE]"),
        ):
            content = memory_result.get(key, "")
            if isinstance(content, str) and content.strip():
                memory_parts.append(heading + "\n" + content)
    context_blocks = {
        "memory": "\n\n".join(memory_parts),
        "s2": "[FROZEN S2 EVIDENCE]\n" + rendered_s2_context,
        "strategy": "\n".join(
            [
                "[VERSIONED REVIEW STRATEGY]",
                plan["review_strategy"] or "(none)",
                "",
                "[S2 USAGE INSTRUCTION]",
                plan["usage_instruction"] or "(none)",
            ]
        ),
    }
    paper_context = frozen_paper_context(paper, role)
    user_parts = [
        SPECIALIST_REQUEST_TEMPLATE.format(
            task_id=role,
            paper_context=paper_context,
        )
    ]
    user_parts.extend(context_blocks[name] for name in plan["context_order"])
    messages = [
        {
            "role": "system",
            "content": load_reviewer_suite().task(role).system_prompt,
        },
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    capability_manifest = dict(program_capability_manifest(path=program_path))
    s2_provenance = {
        "capability_available": capability_available,
        "intervention": intervention,
        "s2_disposition": plan["s2_disposition"],
        "decision_label": plan["decision_label"],
        "lookup_count": len(requested_limits),
        "requested_limits": requested_limits,
        "candidate_ids": candidate_ids,
        "selected_ids": selected_ids,
        "injected_ids": selected_ids,
        "dropped_ids": dropped_ids,
        "usage_instruction": plan["usage_instruction"],
        "context_order": plan["context_order"],
        "review_strategy": plan["review_strategy"],
        "candidate_evidence": candidate_evidence,
        "evidence": selected_evidence,
        "pool_path": pool_provenance.get("pool_path"),
        "pool_sha256": pool_provenance.get("pool_sha256"),
        "pool_version": pool_provenance.get("pool_version"),
        "context_sha256": value_sha256(s2_context),
        "capability_manifest": capability_manifest,
        "plan_sha256": value_sha256(plan),
    }
    provenance = {
        "protocol": RETRIEVAL_PROVENANCE_PROTOCOL,
        "memory": memory_result,
        "s2": s2_provenance,
        "assembly": {
            "context_order": plan["context_order"],
            "paper_context_sha256": value_sha256(paper_context),
            "prompt_sha256": value_sha256(messages),
        },
    }
    return PreparedReviewInvocation(
        messages=messages,
        retrieval_provenance=provenance,
        review_plan=plan,
    )
