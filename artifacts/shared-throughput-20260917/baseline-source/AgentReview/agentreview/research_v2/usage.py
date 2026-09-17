"""Derived S2 usage summaries and diagnostic factorial attribution."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .components import (
    FACTORIAL_CELL_SOURCES,
    REQUIRED_SHARED_FACTORIAL_IDENTITY,
    validate_factorial_components,
)
from .io import file_sha256, read_json, value_sha256

S2_USAGE_SUMMARY_PROTOCOL = "research-v2-s2-usage-summary-v1"
FACTORIAL_ATTRIBUTION_PROTOCOL = "research-v2-factorial-attribution-v1"
VALIDATED_FACTORIAL_ATTRIBUTION_PROTOCOL = (
    "research-v2-validated-factorial-attribution-v1"
)
FACTORIAL_CELL_EVALUATION_PROTOCOL = "research-v2-factorial-cell-evaluation-v1"
S2_INTERVENTION_PROTOCOL = "s2-intervention-v1"
S2_DISPOSITIONS = frozenset({"not_consulted", "consulted_no_use", "use"})
S2_INTERVENTION_MODES = frozenset(
    {"program_autonomous", "forced_off", "forced_consult"}
)
_FACTORIAL_EVALUATION_IDENTITY_FIELDS = frozenset(
    {
        "run_id",
        "artifact_path",
        "artifact_sha256",
        "evaluation_id",
        "paper_ids_sha256",
        "reviewer_identity",
        "evaluator_identity",
        "generation_identity",
        "intervention_mode",
    }
)

_S2_REQUIRED_FIELDS = frozenset(
    {
        "capability_available",
        "intervention",
        "s2_disposition",
        "decision_label",
        "lookup_count",
        "requested_limits",
        "candidate_ids",
        "selected_ids",
        "injected_ids",
        "usage_instruction",
        "context_order",
        "evidence",
        "pool_version",
        "pool_sha256",
        "context_sha256",
    }
)


def _require_string_list(value: Any, *, field: str, source: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{source}: retrieval_provenance.s2.{field} must be list[str]")
    return value


def _validated_trace(review: Mapping[str, Any], source: Path) -> dict[str, Any]:
    retrieval = review.get("retrieval_provenance")
    if not isinstance(retrieval, Mapping):
        raise TypeError(f"{source}: retrieval_provenance must be an object")

    memory = retrieval.get("memory")
    if not isinstance(memory, Mapping):
        raise TypeError(f"{source}: retrieval_provenance.memory must be an object")
    if not isinstance(memory.get("logs"), list):
        raise TypeError(f"{source}: retrieval_provenance.memory.logs must be a list")
    if not isinstance(memory.get("context"), str):
        raise TypeError(
            f"{source}: retrieval_provenance.memory.context must be a string"
        )

    s2 = retrieval.get("s2")
    if not isinstance(s2, Mapping):
        raise TypeError(f"{source}: retrieval_provenance.s2 must be an object")
    missing = sorted(_S2_REQUIRED_FIELDS - set(s2))
    if missing:
        raise ValueError(f"{source}: S2 provenance v2 fields missing: {missing}")

    capability_available = s2["capability_available"]
    if not isinstance(capability_available, bool):
        raise TypeError(
            f"{source}: retrieval_provenance.s2.capability_available must be bool"
        )

    intervention = s2["intervention"]
    if not isinstance(intervention, Mapping):
        raise TypeError(
            f"{source}: retrieval_provenance.s2.intervention must be an object"
        )
    if intervention.get("protocol") != S2_INTERVENTION_PROTOCOL:
        raise ValueError(f"{source}: unsupported S2 intervention protocol")
    intervention_mode = intervention.get("mode")
    if intervention_mode not in S2_INTERVENTION_MODES:
        raise ValueError(f"{source}: unsupported S2 intervention mode")
    if (
        not isinstance(intervention.get("label"), str)
        or not intervention["label"].strip()
    ):
        raise ValueError(f"{source}: S2 intervention label must be a non-empty string")

    disposition = s2["s2_disposition"]
    if disposition not in S2_DISPOSITIONS:
        raise ValueError(f"{source}: unsupported S2 disposition: {disposition!r}")
    if not isinstance(s2["decision_label"], str):
        raise TypeError(f"{source}: S2 decision_label must be a string")
    if not isinstance(s2["usage_instruction"], str):
        raise TypeError(f"{source}: S2 usage_instruction must be a string")

    lookup_count = s2["lookup_count"]
    if isinstance(lookup_count, bool) or not isinstance(lookup_count, int):
        raise TypeError(f"{source}: S2 lookup_count must be an integer")
    if lookup_count < 0:
        raise ValueError(f"{source}: S2 lookup_count cannot be negative")

    requested_limits = s2["requested_limits"]
    if not isinstance(requested_limits, list) or any(
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        for limit in requested_limits
    ):
        raise TypeError(f"{source}: S2 requested_limits must be positive integers")
    if len(requested_limits) != lookup_count:
        raise ValueError(f"{source}: S2 requested_limits must match lookup_count")

    candidate_ids = _require_string_list(
        s2["candidate_ids"], field="candidate_ids", source=source
    )
    selected_ids = _require_string_list(
        s2["selected_ids"], field="selected_ids", source=source
    )
    injected_ids = _require_string_list(
        s2["injected_ids"], field="injected_ids", source=source
    )
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"{source}: S2 candidate_ids must be unique")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError(f"{source}: S2 selected_ids must be unique")
    if len(injected_ids) != len(set(injected_ids)):
        raise ValueError(f"{source}: S2 injected_ids must be unique")
    if not set(selected_ids).issubset(candidate_ids):
        raise ValueError(f"{source}: S2 selected_ids must come from candidate_ids")
    if not set(injected_ids).issubset(selected_ids):
        raise ValueError(f"{source}: S2 injected_ids must come from selected_ids")

    if not isinstance(s2["context_order"], list) or sorted(s2["context_order"]) != [
        "memory",
        "s2",
        "strategy",
    ]:
        raise ValueError(
            f"{source}: S2 context_order must be a context-lane permutation"
        )
    if not isinstance(s2["evidence"], list):
        raise TypeError(f"{source}: S2 evidence must be a list")
    for field in ("pool_version", "pool_sha256"):
        if s2[field] is not None and not isinstance(s2[field], str):
            raise TypeError(f"{source}: S2 {field} must be a string or null")
    if not isinstance(s2["context_sha256"], str):
        raise TypeError(f"{source}: S2 context_sha256 must be a string")

    if disposition == "not_consulted" and (
        lookup_count or candidate_ids or selected_ids or injected_ids
    ):
        raise ValueError(
            f"{source}: not_consulted S2 trace contains retrieval activity"
        )
    if disposition == "consulted_no_use" and (
        lookup_count == 0 or selected_ids or injected_ids
    ):
        raise ValueError(f"{source}: consulted_no_use S2 trace is inconsistent")
    if disposition == "use" and (
        lookup_count == 0 or not selected_ids or not injected_ids
    ):
        raise ValueError(f"{source}: use S2 trace is inconsistent")

    return {
        "role": str(review.get("role") or ""),
        "program_version": str(review.get("program_version") or ""),
        "decision_label": str(s2["decision_label"]),
        "capability_available": capability_available,
        "intervention_mode": str(intervention_mode),
        "intervention_label": str(intervention["label"]),
        "disposition": str(disposition),
        "lookup_count": lookup_count,
        "candidate_count": len(candidate_ids),
        "selected_count": len(selected_ids),
        "injected_count": len(injected_ids),
    }


def _empty_accumulator() -> dict[str, int]:
    return {
        "total_reviews": 0,
        "eligible_reviews": 0,
        "lookup_reviews": 0,
        "injection_reviews": 0,
        "consulted_unused_reviews": 0,
        "empty_result_reviews": 0,
        "selected_count": 0,
    }


def _accumulate(
    accumulator: dict[str, int],
    trace: Mapping[str, Any],
    *,
    expected_intervention_label: str | None,
) -> None:
    accumulator["total_reviews"] += 1
    eligible = (
        bool(trace["capability_available"])
        and (trace["intervention_mode"] == "program_autonomous")
        and (
            expected_intervention_label is None
            or trace["intervention_label"] == expected_intervention_label
        )
    )
    if not eligible:
        return
    accumulator["eligible_reviews"] += 1
    looked_up = int(trace["lookup_count"]) > 0
    accumulator["lookup_reviews"] += int(looked_up)
    accumulator["injection_reviews"] += int(int(trace["injected_count"]) > 0)
    accumulator["consulted_unused_reviews"] += int(
        trace["disposition"] == "consulted_no_use"
    )
    accumulator["empty_result_reviews"] += int(
        looked_up and int(trace["candidate_count"]) == 0
    )
    accumulator["selected_count"] += int(trace["selected_count"])


def _rate(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _render_metrics(accumulator: Mapping[str, int]) -> dict[str, int | float]:
    total = int(accumulator["total_reviews"])
    eligible = int(accumulator["eligible_reviews"])
    selected_count = int(accumulator["selected_count"])
    return {
        "total_reviews": total,
        "eligible_reviews": eligible,
        "ineligible_reviews": total - eligible,
        "eligible_rate": _rate(eligible, total),
        "lookup_reviews": int(accumulator["lookup_reviews"]),
        "lookup_rate": _rate(int(accumulator["lookup_reviews"]), eligible),
        "injection_reviews": int(accumulator["injection_reviews"]),
        "injection_rate": _rate(int(accumulator["injection_reviews"]), eligible),
        "consulted_unused_reviews": int(accumulator["consulted_unused_reviews"]),
        "consulted_unused_rate": _rate(
            int(accumulator["consulted_unused_reviews"]), eligible
        ),
        "empty_result_reviews": int(accumulator["empty_result_reviews"]),
        "empty_result_rate": _rate(int(accumulator["empty_result_reviews"]), eligible),
        "selected_count": selected_count,
        "mean_selected_count": _rate(selected_count, eligible),
    }


def _grouped_metrics(
    traces: Iterable[Mapping[str, Any]],
    field: str,
    *,
    expected_intervention_label: str | None,
) -> dict[str, dict[str, int | float]]:
    groups: dict[str, dict[str, int]] = {}
    for trace in traces:
        key = str(trace[field])
        accumulator = groups.setdefault(key, _empty_accumulator())
        _accumulate(
            accumulator,
            trace,
            expected_intervention_label=expected_intervention_label,
        )
    return {key: _render_metrics(groups[key]) for key in sorted(groups)}


def summarize_s2_usage(
    review_paths: Iterable[str | Path],
    *,
    expected_intervention_label: str | None = "formal-primary-v1",
) -> dict[str, Any]:
    """Summarize autonomous S2 use from immutable provenance-v2 Reviews.

    Every rate except ``eligible_rate`` uses eligible Reviews as its denominator.
    Eligibility requires the capability to be available, the intervention mode to be
    ``program_autonomous``, and (by default) the formal Primary label. Pass an explicit
    development label to summarize Evolution runs, or ``None`` only for a deliberately
    pooled autonomous report. Forced diagnostics remain visible but cannot affect rates.
    """
    traces: list[dict[str, Any]] = []
    for value in review_paths:
        source = Path(value).expanduser().resolve()
        review = read_json(source)
        if not isinstance(review, Mapping):
            raise TypeError(f"{source}: Specialist Review must be a JSON object")
        traces.append(_validated_trace(review, source))

    overall = _empty_accumulator()
    intervention_counts = {mode: 0 for mode in sorted(S2_INTERVENTION_MODES)}
    intervention_label_counts: dict[str, int] = {}
    for trace in traces:
        _accumulate(
            overall,
            trace,
            expected_intervention_label=expected_intervention_label,
        )
        intervention_counts[str(trace["intervention_mode"])] += 1
        label = str(trace["intervention_label"])
        intervention_label_counts[label] = intervention_label_counts.get(label, 0) + 1

    return {
        "protocol": S2_USAGE_SUMMARY_PROTOCOL,
        "rate_denominator": "eligible_reviews",
        "eligibility": {
            "capability_available": True,
            "intervention_mode": "program_autonomous",
            "intervention_label": expected_intervention_label,
        },
        "overall": _render_metrics(overall),
        "intervention_samples": intervention_counts,
        "intervention_label_samples": {
            key: intervention_label_counts[key]
            for key in sorted(intervention_label_counts)
        },
        "by_role": _grouped_metrics(
            traces,
            "role",
            expected_intervention_label=expected_intervention_label,
        ),
        "by_program_version": _grouped_metrics(
            traces,
            "program_version",
            expected_intervention_label=expected_intervention_label,
        ),
        "by_decision_label": _grouped_metrics(
            traces,
            "decision_label",
            expected_intervention_label=expected_intervention_label,
        ),
    }


def _finite_score(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError(f"{name} must be finite")
    return score


def factorial_attribution(
    baseline_memory_baseline_s2_policy: float,
    evolved_memory_baseline_s2_policy: float,
    baseline_memory_evolved_s2_policy: float,
    evolved_memory_evolved_s2_policy: float,
) -> dict[str, Any]:
    """Compute balanced 2x2 Memory and autonomous-S2-policy effects.

    Inputs are comparable aggregate outcome scores from four frozen-component runs.
    The function performs no I/O and does not treat forced S2 interventions as cells.
    """
    y00 = _finite_score(
        baseline_memory_baseline_s2_policy,
        name="baseline_memory_baseline_s2_policy",
    )
    y10 = _finite_score(
        evolved_memory_baseline_s2_policy,
        name="evolved_memory_baseline_s2_policy",
    )
    y01 = _finite_score(
        baseline_memory_evolved_s2_policy,
        name="baseline_memory_evolved_s2_policy",
    )
    y11 = _finite_score(
        evolved_memory_evolved_s2_policy,
        name="evolved_memory_evolved_s2_policy",
    )
    memory_at_baseline_s2 = y10 - y00
    memory_at_evolved_s2 = y11 - y01
    s2_at_baseline_memory = y01 - y00
    s2_at_evolved_memory = y11 - y10
    return {
        "protocol": FACTORIAL_ATTRIBUTION_PROTOCOL,
        "cells": {
            "baseline_memory__baseline_s2_policy": y00,
            "evolved_memory__baseline_s2_policy": y10,
            "baseline_memory__evolved_s2_policy": y01,
            "evolved_memory__evolved_s2_policy": y11,
        },
        "effects": {
            "memory_main_effect": (memory_at_baseline_s2 + memory_at_evolved_s2) / 2.0,
            "s2_policy_main_effect": (s2_at_baseline_memory + s2_at_evolved_memory)
            / 2.0,
            "interaction": memory_at_evolved_s2 - memory_at_baseline_s2,
        },
        "simple_effects": {
            "memory_at_baseline_s2_policy": memory_at_baseline_s2,
            "memory_at_evolved_s2_policy": memory_at_evolved_s2,
            "s2_policy_at_baseline_memory": s2_at_baseline_memory,
            "s2_policy_at_evolved_memory": s2_at_evolved_memory,
        },
    }


def validated_factorial_attribution(
    cell_results: Mapping[str, Mapping[str, Any]],
    *,
    factorial_components: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    """Validate four frozen-component cells before computing causal contrasts."""
    materialized = validate_factorial_components(factorial_components)
    materialization_id = str(materialized["factorial_components_id"])
    materialized_cells = materialized["cells"]
    materialized_shared = materialized["shared_identity"]
    materialized_role = str(materialized["role"])
    if set(cell_results) != set(FACTORIAL_CELL_SOURCES):
        raise ValueError(
            "Factorial attribution requires exactly the four registered cells"
        )
    normalized: dict[str, dict[str, Any]] = {}
    shared_identity: dict[str, Any] | None = None
    for cell in FACTORIAL_CELL_SOURCES:
        value = cell_results[cell]
        if not isinstance(value, Mapping):
            raise TypeError(f"Factorial cell {cell} must be an object")
        if value.get("cell") != cell:
            raise ValueError(f"Factorial cell identity mismatch: {cell}")
        observed_shared = value.get("shared_identity")
        if not isinstance(observed_shared, Mapping):
            raise TypeError(f"Factorial cell {cell} lacks shared_identity")
        missing_shared = sorted(
            REQUIRED_SHARED_FACTORIAL_IDENTITY - set(observed_shared)
        )
        unexpected_shared = sorted(
            set(observed_shared) - REQUIRED_SHARED_FACTORIAL_IDENTITY
        )
        if missing_shared or unexpected_shared:
            raise ValueError(
                f"Factorial cell {cell} shared identity differs from protocol: "
                f"missing={missing_shared}, unexpected={unexpected_shared}"
            )
        if dict(observed_shared) != materialized_shared:
            raise ValueError(
                f"Factorial cell {cell} differs from materialized shared identity"
            )
        if shared_identity is None:
            shared_identity = dict(observed_shared)
        elif dict(observed_shared) != shared_identity:
            raise ValueError("Factorial cells do not share evaluation identity")
        memory_hash = value.get("memory_ast_sha256")
        policy_hash = value.get("policy_ast_sha256")
        if not isinstance(memory_hash, str) or not memory_hash:
            raise ValueError(f"Factorial cell {cell} lacks Memory identity")
        if not isinstance(policy_hash, str) or not policy_hash:
            raise ValueError(f"Factorial cell {cell} lacks S2 policy identity")
        spec = materialized_cells[cell]
        manifest = spec["capability_manifest"]
        if value.get("factorial_components_id") != materialization_id:
            raise ValueError(f"Factorial cell {cell} materialization mismatch")
        if value.get("role") != materialized_role:
            raise ValueError(f"Factorial cell {cell} role mismatch")
        if value.get("program_sha256") != spec["program_sha256"]:
            raise ValueError(f"Factorial cell {cell} Program identity mismatch")
        if value.get("snapshot_id") != spec["snapshot_id"]:
            raise ValueError(f"Factorial cell {cell} snapshot identity mismatch")
        if value.get("snapshot_sha256") != spec["snapshot_sha256"]:
            raise ValueError(f"Factorial cell {cell} snapshot file identity mismatch")
        memory_state_sha256 = value.get("memory_state_sha256")
        if memory_state_sha256 != spec["memory_state_sha256"]:
            raise ValueError(f"Factorial cell {cell} Memory state identity mismatch")
        if memory_hash != manifest["memory_ast_sha256"]:
            raise ValueError(f"Factorial cell {cell} Memory AST identity mismatch")
        if policy_hash != manifest["policy_ast_sha256"]:
            raise ValueError(f"Factorial cell {cell} S2 policy AST identity mismatch")

        evaluation_identity = value.get("evaluation_identity")
        if not isinstance(evaluation_identity, Mapping):
            raise TypeError(f"Factorial cell {cell} lacks evaluation_identity")
        missing_evaluation = sorted(
            _FACTORIAL_EVALUATION_IDENTITY_FIELDS - set(evaluation_identity)
        )
        unexpected_evaluation = sorted(
            set(evaluation_identity) - _FACTORIAL_EVALUATION_IDENTITY_FIELDS
        )
        if missing_evaluation or unexpected_evaluation:
            raise ValueError(
                f"Factorial cell {cell} evaluation identity fields differ from "
                f"protocol: missing={missing_evaluation}, "
                f"unexpected={unexpected_evaluation}"
            )
        if (
            not isinstance(evaluation_identity.get("run_id"), str)
            or not str(evaluation_identity["run_id"]).strip()
        ):
            raise ValueError(f"Factorial cell {cell} lacks evaluation run identity")
        evaluation_sha256 = evaluation_identity.get("artifact_sha256")
        if (
            not isinstance(evaluation_sha256, str)
            or len(evaluation_sha256) != 64
            or any(
                character not in "0123456789abcdef" for character in evaluation_sha256
            )
        ):
            raise ValueError(
                f"Factorial cell {cell} evaluation artifact hash is invalid"
            )
        evaluation_path = (
            Path(str(evaluation_identity.get("artifact_path") or ""))
            .expanduser()
            .resolve()
        )
        if file_sha256(evaluation_path) != evaluation_sha256:
            raise ValueError(f"Factorial cell {cell} evaluation artifact file mismatch")
        evaluation_artifact = read_json(evaluation_path)
        if not isinstance(evaluation_artifact, Mapping):
            raise TypeError(
                f"Factorial cell {cell} evaluation artifact must be an object"
            )
        evaluation_id = evaluation_artifact.get("evaluation_id")
        evaluation_unhashed = {
            name: item
            for name, item in evaluation_artifact.items()
            if name != "evaluation_id"
        }
        if (
            evaluation_artifact.get("protocol") != FACTORIAL_CELL_EVALUATION_PROTOCOL
            or not isinstance(evaluation_id, str)
            or evaluation_id != value_sha256(evaluation_unhashed)
            or evaluation_identity.get("evaluation_id") != evaluation_id
        ):
            raise ValueError(
                f"Factorial cell {cell} evaluation artifact identity mismatch"
            )
        if evaluation_identity.get("intervention_mode") != "program_autonomous":
            raise ValueError(
                f"Factorial cell {cell} must use autonomous S2 policy execution"
            )
        for identity_name in (
            "paper_ids_sha256",
            "reviewer_identity",
            "evaluator_identity",
            "generation_identity",
        ):
            if evaluation_identity.get(identity_name) != materialized_shared.get(
                identity_name
            ):
                raise ValueError(
                    f"Factorial cell {cell} evaluation {identity_name} mismatch"
                )
        expected_evaluation_artifact = {
            "cell": cell,
            "role": materialized_role,
            "run_id": evaluation_identity["run_id"],
            "factorial_components_id": materialization_id,
            "program_sha256": spec["program_sha256"],
            "snapshot_id": spec["snapshot_id"],
            "memory_state_sha256": spec["memory_state_sha256"],
            "paper_ids_sha256": materialized_shared["paper_ids_sha256"],
            "reviewer_identity": materialized_shared["reviewer_identity"],
            "evaluator_identity": materialized_shared["evaluator_identity"],
            "generation_identity": materialized_shared["generation_identity"],
            "intervention_mode": "program_autonomous",
        }
        for identity_name, expected in expected_evaluation_artifact.items():
            if evaluation_artifact.get(identity_name) != expected:
                raise ValueError(
                    f"Factorial cell {cell} evaluation artifact {identity_name} mismatch"
                )
        artifact_score = _finite_score(
            evaluation_artifact.get("score"),
            name=f"{cell}.evaluation_artifact.score",
        )
        result_score = _finite_score(value.get("score"), name=f"{cell}.score")
        if artifact_score != result_score:
            raise ValueError(
                f"Factorial cell {cell} score differs from evaluation artifact"
            )
        normalized[cell] = {
            "score": artifact_score,
            "memory_ast_sha256": memory_hash,
            "policy_ast_sha256": policy_hash,
            "memory_state_sha256": memory_state_sha256,
            "evaluation_identity": dict(evaluation_identity),
        }

    y00 = normalized["baseline_memory__baseline_s2_policy"]
    y10 = normalized["evolved_memory__baseline_s2_policy"]
    y01 = normalized["baseline_memory__evolved_s2_policy"]
    y11 = normalized["evolved_memory__evolved_s2_policy"]
    if not (
        y00["memory_ast_sha256"] == y01["memory_ast_sha256"]
        and y10["memory_ast_sha256"] == y11["memory_ast_sha256"]
        and y00["memory_state_sha256"] == y01["memory_state_sha256"]
        and y10["memory_state_sha256"] == y11["memory_state_sha256"]
        and y00["memory_ast_sha256"] != y10["memory_ast_sha256"]
        and y00["memory_state_sha256"] != y10["memory_state_sha256"]
    ):
        raise ValueError("Factorial cells do not form baseline/evolved Memory pairs")
    if not (
        y00["policy_ast_sha256"] == y10["policy_ast_sha256"]
        and y01["policy_ast_sha256"] == y11["policy_ast_sha256"]
        and y00["policy_ast_sha256"] != y01["policy_ast_sha256"]
    ):
        raise ValueError("Factorial cells do not form baseline/evolved S2 policy pairs")

    report = factorial_attribution(
        y00["score"],
        y10["score"],
        y01["score"],
        y11["score"],
    )
    return {
        **report,
        "protocol": VALIDATED_FACTORIAL_ATTRIBUTION_PROTOCOL,
        "factorial_components_id": materialization_id,
        "shared_identity": shared_identity,
        "component_identities": {
            cell: {
                "memory_ast_sha256": normalized[cell]["memory_ast_sha256"],
                "policy_ast_sha256": normalized[cell]["policy_ast_sha256"],
                "memory_state_sha256": normalized[cell]["memory_state_sha256"],
                "evaluation_identity": normalized[cell]["evaluation_identity"],
            }
            for cell in FACTORIAL_CELL_SOURCES
        },
    }
