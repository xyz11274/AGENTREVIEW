"""M★-aligned role-local pool, Reflector, patcher and lineage."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentreview.reviewer_runtime.sandbox import SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .constants import REFLECTOR_MAX_TOKENS, ROLES, SCHEMA_VERSION
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient
from .memory import SEED_NAMES, seed_program_path, validate_memory_snapshot_artifact
from .models import InternalEvaluation, SpecialistReview
from .patcher import AppliedPatch, apply_v4a_patch
from .program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
    static_check,
)

MAX_MUTATION_ATTEMPTS = 20
REFLECTOR_PROMPT_PROTOCOL = "research-v2-reflector-prompt-v2"
REFLECTOR_FEEDBACK_VIEW_PROTOCOL = "research-v2-reflector-feedback-view-v1"
REFLECTOR_CONTEXT_BUDGET_PROTOCOL = "research-v2-reflector-context-budget-v1"
REFLECTOR_CONTEXT_HEADROOM_TOKENS = 512
EVALUATION_RECEIPT_PROTOCOL = "research-v2-candidate-evaluation-receipt-v1"


class ReflectorContextBudgetError(RuntimeError):
    """The measured Reflector request cannot preserve a usable output budget."""


@dataclass(frozen=True)
class PoolEntry:
    candidate_id: str
    role: str
    program_path: str
    program_sha256: str
    parent_id: str | None
    static_score: float
    seed_kind: str
    memory_snapshot_path: str = ""
    memory_snapshot_sha256: str = ""
    memory_snapshot_id: str = ""
    capability_manifest: dict[str, Any] = field(default_factory=dict)
    lineage: list[dict[str, Any]] = field(default_factory=list)
    evaluation_receipt: dict[str, Any] = field(default_factory=dict)

    @property
    def has_memory_snapshot(self) -> bool:
        return all(
            (
                self.memory_snapshot_path,
                self.memory_snapshot_sha256,
                self.memory_snapshot_id,
            )
        )


@dataclass
class EvolutionPool:
    role: str
    entries: list[PoolEntry] = field(default_factory=list)
    failure_policy_path: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown Specialist role: {self.role}")
        if any(entry.role != self.role for entry in self.entries):
            raise ValueError("Evolution pool contains a cross-role candidate")

    def add(self, entry: PoolEntry) -> None:
        if entry.role != self.role:
            raise ValueError("Evolution pool role mismatch")
        if any(value.candidate_id == entry.candidate_id for value in self.entries):
            raise ValueError(f"Duplicate candidate_id: {entry.candidate_id}")
        candidate_pool = EvolutionPool(self.role, [*self.entries, entry], self.failure_policy_path)
        candidate_pool._validate_selection()
        self.entries.append(entry)

    def _validate_selection(self, *, require_snapshot: bool = False) -> None:
        if self.failure_policy_path:
            from .failure_policy import validate_promotion_policy
            policy = read_json(self.failure_policy_path)
            validate_promotion_policy(policy)
            validate_pool_evaluation_receipts(self, expected_epoch=policy["evaluation_epoch"])
        for entry in self.entries:
            _finite_quality(entry.static_score)
        if require_snapshot or any(
            entry.memory_snapshot_path or entry.memory_snapshot_sha256
            or entry.memory_snapshot_id or entry.evaluation_receipt
            for entry in self.entries
        ):
            validate_pool_evaluation_receipts(self)

    def select_parent(
        self,
        *,
        temperature: float = 0.15,
        seed: int,
    ) -> PoolEntry:
        if not self.entries:
            raise RuntimeError("Evolution pool is empty")
        self._validate_selection()
        temperature = max(float(temperature), 1e-6)
        logits = [entry.static_score / temperature for entry in self.entries]
        offset = max(logits)
        weights = [math.exp(value - offset) for value in logits]
        return random.Random(seed).choices(self.entries, weights=weights, k=1)[0]

    def best(self, *, require_snapshot: bool = False) -> PoolEntry:
        if not self.entries:
            raise RuntimeError("Evolution pool is empty")
        self._validate_selection(require_snapshot=require_snapshot)
        winner = max(
            self.entries,
            key=lambda value: (value.static_score, value.candidate_id),
        )
        if file_sha256(winner.program_path) != winner.program_sha256:
            raise ValueError("Winner Program source no longer matches its pool entry")
        _validate_recorded_capabilities(
            program_path=winner.program_path,
            recorded=winner.capability_manifest,
        )
        if require_snapshot:
            validate_pool_entry_snapshot(winner)
        return winner

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": (
                "research-v2-role-pool-v3"
                if self.entries and all(entry.has_memory_snapshot for entry in self.entries)
                else "research-v2-role-pool-v2"
            ),
            "role": self.role,
            "entries": [asdict(entry) for entry in self.entries],
            **({"failure_policy_path": self.failure_policy_path} if self.failure_policy_path else {}),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvolutionPool":
        return cls(
            role=str(value.get("role") or ""),
            entries=[PoolEntry(**dict(item)) for item in value.get("entries") or []],
            failure_policy_path=str(value.get("failure_policy_path") or ""),
        )


def _finite_quality(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Static review_quality must be a finite number in [1, 5]")
    score = float(value)
    if not math.isfinite(score) or not 1.0 <= score <= 5.0:
        raise ValueError("Static review_quality must be a finite number in [1, 5]")
    return score


def _require_success(value: dict[str, Any]) -> None:
    if value.get("status") not in (None, "complete") or value.get("protocol") is not None:
        raise ValueError("Candidate scoring requires successful review/evaluation artifacts")


def _validate_scoring_artifact(value: dict[str, Any], model: type) -> None:
    _require_success(value)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Candidate scoring artifact schema mismatch")
    try:
        artifact = model(**{item.name: value[item.name] for item in fields(model)})
        artifact.validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Candidate scoring requires a valid {model.__name__}") from exc
    for name in ("paper_id", "role", "run_id", "created_at", "raw_model_output"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"Candidate scoring artifact requires {name}")
    identity_field = "model_identity" if model is SpecialistReview else "evaluator_identity"
    if not isinstance(value[identity_field], dict) or not value[identity_field]:
        raise ValueError(f"Candidate scoring artifact requires {identity_field}")
    if model is InternalEvaluation:
        _finite_quality(value["review_quality"])
        if not isinstance(value["prompt_version"], str) or not value["prompt_version"].strip():
            raise ValueError("Candidate Evaluation requires a prompt version")
        for name in ("major_missing_points", "factual_evidence_issues", "improvement_suggestions"):
            if not isinstance(value[name], list) or any(not isinstance(item, str) for item in value[name]):
                raise ValueError(f"Candidate Evaluation requires a list of strings for {name}")


def static_mean(evaluation_paths: list[str | Path]) -> float:
    if not evaluation_paths:
        raise ValueError("Static score requires at least one Internal Evaluation")
    artifacts = [read_json(path) for path in evaluation_paths]
    for artifact in artifacts:
        _require_success(artifact)
    values = [_finite_quality(value.get("review_quality")) for value in artifacts]
    return sum(values) / len(values)


def candidate_static_mean(
    *,
    role: str,
    snapshot_path: str | Path,
    review_paths: list[str | Path],
    evaluation_paths: list[str | Path],
) -> float:
    """Score one candidate only after binding Reviews to its exact snapshot."""
    return build_evaluation_receipt(
        role=role,
        snapshot_path=snapshot_path,
        review_paths=review_paths,
        evaluation_paths=evaluation_paths,
    )["mean_score"]


def build_evaluation_receipt(
    *,
    role: str,
    snapshot_path: str | Path,
    review_paths: list[str | Path],
    evaluation_paths: list[str | Path],
    epoch: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind complete successful scoring inputs to a cohort and scoring revision."""
    if type(epoch) is not int or epoch < 0:
        raise ValueError("Evaluation epoch must be a non-negative integer")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Evaluation metadata must be an object")
    if not review_paths or len(review_paths) != len(evaluation_paths):
        raise ValueError("Candidate static scoring requires aligned Review/Evaluation sets")
    _, snapshot = _snapshot_payload(snapshot_path)
    if snapshot.get("role") != role:
        raise ValueError("Candidate static scoring snapshot has a different role")
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    program_version = str(snapshot.get("program_version") or "")
    reviews = [read_json(path) for path in review_paths]
    for review in reviews:
        _validate_scoring_artifact(review, SpecialistReview)
    review_by_id = {str(value.get("review_id") or ""): value for value in reviews}
    if len(review_by_id) != len(reviews) or "" in review_by_id:
        raise ValueError("Candidate static Reviews have duplicate or empty identities")
    paper_ids = sorted(value["paper_id"] for value in reviews)
    if len(set(paper_ids)) != len(paper_ids):
        raise ValueError("Candidate static Reviews contain duplicate papers")
    if any(
        value.get("role") != role
        or value.get("memory_snapshot_id") != snapshot_id
        or value.get("program_version") != program_version
        for value in reviews
    ):
        raise ValueError("Candidate static Review is not bound to the scored snapshot")
    evaluations = [read_json(path) for path in evaluation_paths]
    for evaluation in evaluations:
        _validate_scoring_artifact(evaluation, InternalEvaluation)
    if any(value.get("role") != role for value in evaluations):
        raise ValueError("Candidate static Evaluation has a cross-role artifact")
    evaluation_review_ids = [str(value.get("review_id") or "") for value in evaluations]
    if len(set(evaluation_review_ids)) != len(evaluation_review_ids):
        raise ValueError("Candidate static Evaluations contain duplicate Reviews")
    if set(evaluation_review_ids) != set(review_by_id):
        raise ValueError("Candidate static Evaluations do not match the Review set")
    if len({value["evaluation_id"] for value in evaluations}) != len(evaluations):
        raise ValueError("Candidate static Evaluations contain duplicate identities")
    for value in evaluations:
        review = review_by_id[str(value["review_id"])]
        if value.get("paper_id") != review.get("paper_id"):
            raise ValueError("Candidate static Evaluation paper differs from its Review")
    scoring_identity = {
        "schema_version": SCHEMA_VERSION,
        "score_field": "review_quality",
        "evaluator_identity": evaluations[0]["evaluator_identity"],
        "prompt_version": evaluations[0]["prompt_version"],
    }
    if any(
        value["evaluator_identity"] != scoring_identity["evaluator_identity"]
        or value["prompt_version"] != scoring_identity["prompt_version"]
        for value in evaluations
    ):
        raise ValueError("Candidate static Evaluations use different scoring identities")
    review_sources = {
        value["review_id"]: Path(path).expanduser().resolve()
        for path, value in zip(review_paths, reviews, strict=True)
    }
    items = []
    for path, value in zip(evaluation_paths, evaluations, strict=True):
        review_source = review_sources[value["review_id"]]
        evaluation_source = Path(path).expanduser().resolve()
        items.append({
            "paper_id": value["paper_id"],
            "review_id": value["review_id"],
            "evaluation_id": value["evaluation_id"],
            "review_path": str(review_source),
            "review_sha256": file_sha256(review_source),
            "evaluation_path": str(evaluation_source),
            "evaluation_sha256": file_sha256(evaluation_source),
            "score": _finite_quality(value["review_quality"]),
        })
    items.sort(key=lambda value: value["paper_id"])
    receipt = {
        "protocol": EVALUATION_RECEIPT_PROTOCOL,
        "status": "complete",
        "role": role,
        "memory_snapshot_id": snapshot_id,
        "epoch": epoch,
        "metadata": dict(metadata or {}),
        "scoring_identity": scoring_identity,
        "paper_ids": paper_ids,
        "items": items,
        "mean_score": sum(value["score"] for value in items) / len(items),
    }
    receipt["receipt_id"] = value_sha256(receipt)
    return receipt


def validate_pool_evaluation_receipts(
    pool: EvolutionPool,
    *,
    expected_epoch: int | None = None,
    expected_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require one complete, current scoring cohort before using any candidate."""
    if not pool.entries:
        raise ValueError("Verified candidate pool is empty")
    cohort = None
    for entry in pool.entries:
        validate_pool_entry_snapshot(entry)
        receipt = entry.evaluation_receipt
        if not isinstance(receipt, dict) or receipt.get("protocol") != EVALUATION_RECEIPT_PROTOCOL:
            raise ValueError("Pool candidate requires a complete evaluation receipt")
        if receipt.get("status") != "complete":
            raise ValueError("Pool candidate evaluation is pending or incomplete")
        if receipt.get("receipt_id") != value_sha256({key: value for key, value in receipt.items() if key != "receipt_id"}):
            raise ValueError("Pool candidate evaluation receipt hash mismatch")
        if expected_epoch is not None and receipt.get("epoch") != expected_epoch:
            raise ValueError("Pool candidate evaluation epoch is stale")
        if expected_metadata is not None and receipt.get("metadata") != expected_metadata:
            raise ValueError("Pool candidate evaluation metadata is stale")
        items = receipt.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Pool candidate evaluation receipt has no scoring inputs")
        try:
            current = build_evaluation_receipt(
                role=entry.role,
                snapshot_path=entry.memory_snapshot_path,
                review_paths=[item["review_path"] for item in items],
                evaluation_paths=[item["evaluation_path"] for item in items],
                epoch=receipt["epoch"],
                metadata=receipt["metadata"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Pool candidate evaluation receipt inputs are invalid") from exc
        if current != receipt or _finite_quality(entry.static_score) != current["mean_score"]:
            raise ValueError("Pool candidate evaluation receipt differs from its scoring artifacts")
        current_cohort = {name: current[name] for name in ("epoch", "metadata", "scoring_identity", "paper_ids")}
        if cohort is not None and current_cohort != cohort:
            raise ValueError("Candidate pool has mixed evaluation cohorts or epochs")
        cohort = current_cohort
    return cohort


def _program_capabilities(program_path: str | Path) -> dict[str, Any]:
    """Return the verified candidate-visible capability identity for one Program."""
    source = Path(program_path).expanduser().resolve()
    errors = static_check(source.read_text(encoding="utf-8"))
    if errors:
        raise ValueError(f"Program capability static check failed: {errors}")
    manifest = dict(program_capability_manifest(path=source))
    required = {
        "interface_version",
        "program_sha256",
        "policy_ast_sha256",
        "memory_ast_sha256",
    }
    missing = sorted(required - set(manifest))
    unexpected = sorted(set(manifest) - required)
    if missing or unexpected:
        raise ValueError(
            "Program capability manifest fields differ from the formal protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if manifest["interface_version"] != REVIEW_PLAN_PROTOCOL:
        raise ValueError(
            "Program capability interface differs from the formal Review Plan protocol"
        )
    if manifest["program_sha256"] != file_sha256(source):
        raise ValueError("Program capability manifest source hash mismatch")
    return manifest


def _validate_recorded_capabilities(
    *,
    program_path: str | Path,
    recorded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate a recorded manifest while accepting legacy pool entries without one."""
    current = _program_capabilities(program_path)
    if recorded and dict(recorded) != current:
        raise ValueError("Recorded Program capability manifest no longer matches its source")
    return current


def _snapshot_payload(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    value = validate_memory_snapshot_artifact(read_json(source), path=source)
    return source, value


def snapshot_binding(
    *,
    snapshot_path: str | Path,
    role: str,
    program_path: str | Path,
    seed_kind: str,
) -> dict[str, str]:
    """Return the verified immutable Memory identity for one pool candidate."""
    source, snapshot = _snapshot_payload(snapshot_path)
    program = Path(program_path).expanduser().resolve()
    program_sha256 = file_sha256(program)
    expected = {
        "role": role,
        "program_sha256": program_sha256,
        "seed_kind": seed_kind,
    }
    observed = {name: snapshot.get(name) for name in expected}
    if observed != expected:
        raise ValueError(
            "Memory snapshot differs from its pool candidate: "
            f"observed={observed!r}, expected={expected!r}"
        )
    recorded_program_path = Path(str(snapshot.get("program_path") or "")).expanduser()
    if recorded_program_path.resolve() != program:
        raise ValueError("Memory snapshot Program path differs from its pool candidate")
    recorded_capabilities = snapshot.get("capability_manifest")
    if recorded_capabilities is not None:
        current_capabilities = _program_capabilities(program)
        if dict(recorded_capabilities) != current_capabilities:
            raise ValueError("Memory snapshot capability identity mismatch")
    return {
        "memory_snapshot_path": str(source),
        "memory_snapshot_sha256": file_sha256(source),
        "memory_snapshot_id": str(snapshot["snapshot_id"]),
    }


def validate_pool_entry_snapshot(entry: PoolEntry) -> dict[str, str]:
    """Verify that a pool entry still points at its scored Memory snapshot."""
    if not entry.has_memory_snapshot:
        raise ValueError(
            f"Pool candidate {entry.candidate_id!r} has no frozen Memory snapshot binding"
        )
    current = snapshot_binding(
        snapshot_path=entry.memory_snapshot_path,
        role=entry.role,
        program_path=entry.program_path,
        seed_kind=entry.seed_kind,
    )
    recorded = {
        "memory_snapshot_path": str(Path(entry.memory_snapshot_path).expanduser().resolve()),
        "memory_snapshot_sha256": entry.memory_snapshot_sha256,
        "memory_snapshot_id": entry.memory_snapshot_id,
    }
    if current != recorded:
        raise ValueError(
            f"Pool candidate {entry.candidate_id!r} Memory snapshot no longer matches"
        )
    return current


def initialize_pool(
    *,
    role: str,
    seed_static_evaluations: dict[str, list[str | Path]],
    pool_path: str | Path,
    seed_memory_snapshots: dict[str, str | Path] | None = None,
    seed_static_reviews: dict[str, list[str | Path]] | None = None,
    evaluation_epoch: int = 0,
    evaluation_metadata: dict[str, Any] | None = None,
    failure_policy_path: str | Path | None = None,
) -> EvolutionPool:
    """Create the three-seed role-local pool after their static evaluations exist."""
    if set(seed_static_evaluations) != set(SEED_NAMES):
        raise ValueError(f"Initial pool requires exactly these seeds: {SEED_NAMES}")
    if seed_memory_snapshots is not None and set(seed_memory_snapshots) != set(SEED_NAMES):
        raise ValueError(f"Initial pool snapshots require exactly these seeds: {SEED_NAMES}")
    if seed_static_reviews is not None and set(seed_static_reviews) != set(SEED_NAMES):
        raise ValueError(f"Initial pool Reviews require exactly these seeds: {SEED_NAMES}")
    if (seed_memory_snapshots is None) != (seed_static_reviews is None):
        raise ValueError(
            "Initial pool formal identity requires both seed snapshots and static Reviews"
        )
    pool = EvolutionPool(role, failure_policy_path=str(Path(failure_policy_path).resolve()) if failure_policy_path else "")
    for seed_name in SEED_NAMES:
        program = seed_program_path(seed_name).resolve()
        capability_manifest = _program_capabilities(program)
        memory_binding = (
            {}
            if seed_memory_snapshots is None
            else snapshot_binding(
                snapshot_path=seed_memory_snapshots[seed_name],
                role=role,
                program_path=program,
                seed_kind=seed_name,
            )
        )
        receipt = (
            {}
            if seed_memory_snapshots is None
            else build_evaluation_receipt(
                role=role,
                snapshot_path=seed_memory_snapshots[seed_name],
                review_paths=seed_static_reviews[seed_name],
                evaluation_paths=seed_static_evaluations[seed_name],
                epoch=evaluation_epoch,
                metadata=evaluation_metadata,
            )
        )
        static_score = receipt["mean_score"] if receipt else static_mean(seed_static_evaluations[seed_name])
        pool.add(
            PoolEntry(
                candidate_id=f"seed-{seed_name}",
                role=role,
                program_path=str(program),
                program_sha256=file_sha256(program),
                parent_id=None,
                static_score=static_score,
                seed_kind=seed_name,
                **memory_binding,
                capability_manifest=capability_manifest,
                evaluation_receipt=receipt,
                lineage=[
                    {
                        "candidate_id": f"seed-{seed_name}",
                        "parent_id": None,
                        "change": {"title": f"Initial {seed_name} seed", "details": []},
                        "capability_manifest": capability_manifest,
                    }
                ],
            )
        )
    target = Path(pool_path).expanduser().resolve()
    if target.is_file():
        existing = EvolutionPool.from_dict(read_json(target))
        if existing.to_dict() != pool.to_dict():
            raise ValueError("Existing initial pool differs from the requested seed state")
        return existing
    atomic_write_json(target, pool.to_dict())
    return pool


def feedback_packet(
    *,
    parent: PoolEntry,
    evaluation_paths: list[str | Path],
    review_paths: list[str | Path],
    maximum_cases: int = 5,
) -> dict[str, Any]:
    reviews = {str(value.get("review_id")): value for value in map(read_json, review_paths)}
    evaluations = [read_json(path) for path in evaluation_paths]
    if any(str(value.get("role") or "") != parent.role for value in reviews.values()):
        raise ValueError("Evolution feedback contains a cross-role Review")
    if any(str(value.get("role") or "") != parent.role for value in evaluations):
        raise ValueError("Evolution feedback contains a cross-role Internal Evaluation")
    evaluations.sort(key=lambda value: (float(value.get("review_quality", 5.0)), str(value.get("review_id"))))
    cases = []
    for value in evaluations[:maximum_cases]:
        review = reviews.get(str(value.get("review_id")))
        if review is None:
            raise ValueError("Internal Evaluation has no matching complete Review")
        retrieval = dict(review.get("retrieval_provenance") or {})
        memory = dict(retrieval.get("memory") or {})
        s2 = dict(retrieval.get("s2") or {})
        review_plan = {
            "s2_disposition": s2.get("s2_disposition"),
            "decision_label": s2.get("decision_label"),
            "selected_ids": s2.get("selected_ids"),
            "usage_instruction": s2.get("usage_instruction"),
            "context_order": s2.get("context_order"),
            "review_strategy": s2.get("review_strategy"),
        }
        cases.append(
            {
                "paper_id": review.get("paper_id"),
                "complete_review": review.get("review_text"),
                "memory_query": memory.get("query"),
                "memory_hit_log": memory.get("logs"),
                # The full deterministic traces are candidate feedback. Keeping the
                # complete structures prevents mutation from optimizing against IDs
                # while being blind to why and how evidence was used.
                "memory_trace": memory,
                "s2_result_ids": s2.get("result_ids") or s2.get("candidate_ids"),
                "review_plan": review_plan,
                "s2_decision": {
                    "capability_available": s2.get("capability_available"),
                    "intervention": s2.get("intervention"),
                    "s2_disposition": s2.get("s2_disposition"),
                    "decision_label": s2.get("decision_label"),
                    "usage_instruction": s2.get("usage_instruction"),
                    "context_order": s2.get("context_order"),
                    "review_strategy": s2.get("review_strategy"),
                },
                "s2_call_trace": {
                    "lookup_count": s2.get("lookup_count"),
                    "requested_limits": s2.get("requested_limits"),
                    "pool_path": s2.get("pool_path"),
                    "pool_sha256": s2.get("pool_sha256"),
                    "pool_version": s2.get("pool_version"),
                },
                "s2_candidates": {
                    "candidate_ids": s2.get("candidate_ids"),
                    "evidence": s2.get("candidate_evidence")
                    or s2.get("evidence"),
                },
                "s2_selected_ids": s2.get("selected_ids"),
                "s2_selected_evidence": s2.get("evidence"),
                "s2_injected_ids": s2.get("injected_ids"),
                "s2_dropped_ids": s2.get("dropped_ids"),
                "s2_trace": s2,
                "retrieval_assembly": dict(retrieval.get("assembly") or {}),
                "summarizer_output": memory.get("context")
                if memory.get("seed_kind") == "llm_summarizer"
                else None,
                "review_quality": value.get("review_quality"),
                "major_missing_points": value.get("major_missing_points"),
                "factual_evidence_issues": value.get("factual_evidence_issues"),
                "improvement_suggestions": value.get("improvement_suggestions"),
            }
        )
    return {
        "parent": {
            "candidate_id": parent.candidate_id,
            "program_sha256": parent.program_sha256,
            "static_mean_review_quality": parent.static_score,
            "seed_kind": parent.seed_kind,
            "capability_manifest": parent.capability_manifest
            or _program_capabilities(parent.program_path),
            "lineage": parent.lineage,
        },
        "rotating_failure_cases": cases,
    }


def _reflector_feedback_view(packet: dict[str, Any]) -> dict[str, Any]:
    """Render every feedback fact once while preserving both complete traces."""
    cases = []
    for value in list(packet.get("rotating_failure_cases") or []):
        cases.append(
            {
                "paper_id": value.get("paper_id"),
                "complete_review": value.get("complete_review"),
                "retrieval_trace": {
                    "memory": value.get("memory_trace"),
                    "s2": value.get("s2_trace"),
                    "assembly": value.get("retrieval_assembly"),
                },
                "internal_evaluation": {
                    "review_quality": value.get("review_quality"),
                    "major_missing_points": value.get("major_missing_points"),
                    "factual_evidence_issues": value.get(
                        "factual_evidence_issues"
                    ),
                    "improvement_suggestions": value.get(
                        "improvement_suggestions"
                    ),
                },
            }
        )
    return {
        "protocol": REFLECTOR_FEEDBACK_VIEW_PROTOCOL,
        "parent": packet.get("parent"),
        "rotating_failure_cases": cases,
    }


def _reflector_prompt(parent_code: str, packet: dict[str, Any]) -> str:
    feedback_view = _reflector_feedback_view(packet)
    return f"""You are the M★ Reflector for one Specialist Reviewer's evolvable Reviewer Program. Improve the Program using only the saved Review and Internal Evaluation feedback below.

The candidate-visible Program consists of Program instructions, KnowledgeItem, Query, KnowledgeBase.write/read, and plan_review(toolkit, query, memory_context). Preserve the M★ interfaces and the {REVIEW_PLAN_PROTOCOL} Review Plan protocol. plan_review autonomously decides whether S2 is useful, which returned evidence to select, how to use it, the context order, and the versioned Review strategy. It may call toolkit.retrieve_current_s2(limit=...) only for the current paper and only against the host-provided frozen pool. The capability performs no LLM or network call and accepts no paper identifier. plan_review itself must not call an LLM, embed, log, read time/datetime, depend on Memory instruction constants, or delegate to a top-level helper. Its query exposes only canonical query_text and reviewer_dimension. It may skip the capability, consult it and select nothing, or select a valid ordered subset of returned evidence.

Review Run constructs Query deterministically and uses a frozen read-only Memory snapshot. Generative KnowledgeItem creation is allowed only during offline Memory Build. Retrieval hit selection must remain deterministic. Only an explicit Summarizer Program may call one LLM after it freezes and logs hit IDs. Never access CoCo, human reviews, other roles, arbitrary/current paper source beyond the supplied Query, the frozen S2 pool directly, files, network, subprocesses, eval, or exec. Do not fabricate S2 evidence or candidate IDs.

Return exactly one commit message followed by one V4A patch for program.py:
*** Commit Message
Title: <one-line change>
- <root cause>
- <specific change>
*** Begin Patch
*** Update File: program.py
@@
 context
-old
+new
 context
*** End Patch

Feedback view (complete traces retained; repeated projections omitted):
{json.dumps(feedback_view, ensure_ascii=False, separators=(",", ":"))}

Parent program:
```python
{parent_code}
```
""".strip()


def _reflector_context_budget(
    *,
    messages: list[dict[str, str]],
    completion_client: CompletionClient,
    completion_identity: dict[str, Any],
) -> dict[str, Any]:
    counter = getattr(completion_client, "count_chat_tokens", None)
    measured = counter(messages) if callable(counter) else None
    if measured is None:
        if completion_identity.get("max_model_len") is not None:
            raise ReflectorContextBudgetError(
                "Formal Reflector requires count_chat_tokens for context gating"
            )
        return {
            "protocol": REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
            "method": "unmeasured-test-client",
            "input_tokens": None,
            "max_model_len": completion_identity.get("max_model_len"),
            "requested_max_tokens": REFLECTOR_MAX_TOKENS,
            "headroom_tokens": REFLECTOR_CONTEXT_HEADROOM_TOKENS,
            "max_tokens": REFLECTOR_MAX_TOKENS,
        }
    if isinstance(measured, int):
        input_tokens = measured
        measured_max_model_len = completion_identity.get("max_model_len")
    elif isinstance(measured, dict):
        input_tokens = int(measured.get("input_tokens") or 0)
        measured_max_model_len = measured.get("max_model_len")
    else:
        raise TypeError("count_chat_tokens must return a token-count object")
    max_model_len = int(completion_identity.get("max_model_len") or 0)
    if max_model_len <= 0:
        raise ReflectorContextBudgetError(
            "Reflector completion identity requires max_model_len"
        )
    if (
        measured_max_model_len is not None
        and int(measured_max_model_len) != max_model_len
    ):
        raise ReflectorContextBudgetError(
            "Reflector tokenizer endpoint max_model_len differs from completion identity"
        )
    required_tokens = (
        input_tokens
        + REFLECTOR_MAX_TOKENS
        + REFLECTOR_CONTEXT_HEADROOM_TOKENS
    )
    if required_tokens > max_model_len:
        raise ReflectorContextBudgetError(
            "Reflector prompt cannot preserve the fixed output token budget: "
            f"input={input_tokens}, context={max_model_len}, "
            f"headroom={REFLECTOR_CONTEXT_HEADROOM_TOKENS}, "
            f"fixed_output={REFLECTOR_MAX_TOKENS}"
        )
    return {
        "protocol": REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
        "method": "frozen-model-tokenize-endpoint",
        "input_tokens": input_tokens,
        "max_model_len": max_model_len,
        "requested_max_tokens": REFLECTOR_MAX_TOKENS,
        "headroom_tokens": REFLECTOR_CONTEXT_HEADROOM_TOKENS,
        "max_tokens": REFLECTOR_MAX_TOKENS,
    }


def _validate_recorded_context_budget(
    value: Any,
    *,
    completion_identity: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Committed Reflector response lacks context budget identity")
    budget = dict(value)
    expected_constants = {
        "protocol": REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
        "requested_max_tokens": REFLECTOR_MAX_TOKENS,
        "headroom_tokens": REFLECTOR_CONTEXT_HEADROOM_TOKENS,
    }
    if {name: budget.get(name) for name in expected_constants} != expected_constants:
        raise ValueError("Committed Reflector context budget identity mismatch")
    if budget.get("method") == "frozen-model-tokenize-endpoint":
        input_tokens = int(budget.get("input_tokens") or 0)
        max_model_len = int(budget.get("max_model_len") or 0)
        if max_model_len != int(completion_identity.get("max_model_len") or 0):
            raise ValueError("Committed Reflector context window identity mismatch")
        if (
            input_tokens
            + REFLECTOR_MAX_TOKENS
            + REFLECTOR_CONTEXT_HEADROOM_TOKENS
            > max_model_len
            or budget.get("max_tokens") != REFLECTOR_MAX_TOKENS
        ):
            raise ValueError("Committed Reflector context budget is invalid")
    elif budget.get("method") == "unmeasured-test-client":
        if budget.get("input_tokens") is not None or budget.get(
            "max_tokens"
        ) != REFLECTOR_MAX_TOKENS:
            raise ValueError("Committed unmeasured Reflector budget is invalid")
    else:
        raise ValueError("Committed Reflector context budget method is unknown")
    return budget


def _repair_prompt(parent_code: str, response: str, error: str) -> str:
    return f"""Repair one failed M★ V4A candidate. Preserve KnowledgeBase and the {REVIEW_PLAN_PROTOCOL} plan_review interface, including its self-contained deterministic policy, canonical query_text/reviewer_dimension input, and current-paper-only retrieve_current_s2 semantics. Use only the parent, failed patch, and compiler/protocol error below. Return exactly one valid V4A Update File patch for program.py and no Markdown fence.

Error:
{error}

Parent program:
{parent_code}

Failed response:
{response}
""".strip()


def _sample_dataclass(dataclass_type):
    from dataclasses import MISSING, fields

    values = {}
    for item in fields(dataclass_type):
        if item.default is not MISSING or item.default_factory is not MISSING:
            continue
        name = getattr(item.type, "__name__", str(item.type)).lower()
        if "list" in name:
            values[item.name] = []
        elif "bool" in name:
            values[item.name] = False
        elif "int" in name:
            values[item.name] = 1
        elif "float" in name:
            values[item.name] = 1.0
        else:
            values[item.name] = "sample"
    return dataclass_type(**values)


def validate_program(
    path: str | Path,
    *,
    require_bubblewrap: bool,
) -> list[str]:
    from agentreview.reviewer_runtime.sandbox import SandboxError
    from .failure_policy import failure_detail

    source = Path(path).expanduser().resolve()
    errors = static_check(source.read_text(encoding="utf-8"))
    if errors:
        return errors
    _program_capabilities(source)

    class SmokeLLM:
        def llm_completion(self, messages, **kwargs):
            return "smoke summary"

    toolkit = MemoryToolkit(
        llm_client=SmokeLLM(),
        max_llm_calls_per_operation=1,
        s2_retrieval_pool={
            "protocol-smoke-paper": [
                {"paperId": "protocol-smoke-s2", "title": "Frozen smoke evidence"}
            ]
        },
    )
    try:
        with SandboxSession(
            str(source),
            toolkit,
            timeout_seconds=10,
            require_bubblewrap=require_bubblewrap,
        ) as sandbox:
            program = sandbox.program_interface()
            item = _sample_dataclass(program.KnowledgeItem)
            query = _sample_dataclass(program.Query)
            with toolkit.operation("protocol_smoke_write", max_llm_calls=1):
                sandbox.write(item, "traceable Review Experience")
            with toolkit.operation("protocol_smoke_read", max_llm_calls=1):
                result = sandbox.read(query)
            if not isinstance(result, str):
                errors.append("KnowledgeBase.read() did not return str")
            elif len(result) > 6000:
                errors.append("KnowledgeBase.read() exceeded 6000 characters")
            with toolkit.paper_scope("protocol-smoke-paper"):
                with toolkit.operation(
                    "review_plan",
                    max_llm_calls=0,
                    allowed_capabilities={"retrieve_current_s2"},
                ):
                    plan = sandbox.plan_review(query, result)
                trace = toolkit.s2_trace_snapshot()
            calls = list(trace.get("calls") or [])
            selected_ids = list(plan.get("selected_s2_ids") or [])
            candidate_ids = [
                item
                for call in calls
                for item in list(call.get("result_ids") or [])
            ]
            expected_disposition = (
                "not_consulted"
                if not calls
                else "use"
                if selected_ids
                else "consulted_no_use"
            )
            if plan.get("s2_disposition") != expected_disposition:
                errors.append("plan_review disposition disagrees with capability trace")
            if not set(selected_ids).issubset(candidate_ids):
                errors.append("plan_review selected an ID absent from capability results")
    except SandboxError as exc:
        detail = failure_detail(exc, phase="candidate_validation")
        if detail["category"] == "candidate":
            errors.append(f"protocol smoke failed: {type(exc).__name__}: {exc}")
        else:
            raise
    return errors


def _write_program(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") == code:
            return
        raise FileExistsError(f"Candidate Program already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(code, encoding="utf-8")
    temporary.replace(path)


def _attempt_directory(
    *, output_root: str | Path, role: str, run_id: str
) -> Path:
    return (
        Path(output_root).expanduser().resolve()
        / "evolution"
        / role
        / run_id
    )


def _load_committed_attempt(
    *,
    path: Path,
    role: str,
    run_id: str,
    attempt: int,
    pool: EvolutionPool,
    feedback_sha256: str,
    completion_identity: dict[str, Any],
    evaluation_epoch: int = 0,
) -> dict[str, Any]:
    value = read_json(path)
    expected = {
        "protocol": "research-v2-evolution-attempt-v2",
        "run_id": run_id,
        "attempt": attempt,
        "role": role,
        "feedback_sha256": feedback_sha256,
        "completion_identity": completion_identity,
    }
    if {name: value.get(name) for name in expected} != expected:
        raise ValueError("Committed Evolution attempt identity mismatch")
    if value.get("evaluation_epoch", 0) != evaluation_epoch:
        raise ValueError("Committed Evolution attempt evaluation epoch mismatch")
    parents = {
        entry.candidate_id: entry
        for entry in pool.entries
        if entry.candidate_id == value.get("parent_id")
    }
    if len(parents) != 1:
        raise ValueError("Committed Evolution attempt parent is absent from the pool")
    parent = next(iter(parents.values()))
    parent_capabilities = _validate_recorded_capabilities(
        program_path=parent.program_path,
        recorded=parent.capability_manifest,
    )
    if value.get("parent_capability_manifest") != parent_capabilities:
        raise ValueError("Committed Evolution attempt parent identity mismatch")
    expected_candidate_id = _candidate_identity(run_id, role, attempt, parent.candidate_id, evaluation_epoch)
    if value.get("candidate_id") != expected_candidate_id:
        raise ValueError("Committed Evolution attempt candidate identity mismatch")
    if value.get("status") == "proposed":
        program_path = str(value.get("program_path") or "")
        if not program_path or file_sha256(program_path) != value.get("program_sha256"):
            raise ValueError("Committed Evolution candidate Program identity mismatch")
        capabilities = _validate_recorded_capabilities(
            program_path=program_path,
            recorded=dict(value.get("capability_manifest") or {}),
        )
        if value.get("capability_manifest") != capabilities:
            raise ValueError("Committed Evolution candidate capability mismatch")
    elif value.get("status") != "failed":
        raise ValueError("Committed Evolution attempt has an unknown status")
    return value


def _load_or_call_reflector(
    *,
    path: Path,
    messages: list[dict[str, str]],
    component: str,
    completion_client: CompletionClient,
    completion_identity: dict[str, Any],
    plan_sha256: str,
) -> str:
    request_sha256 = value_sha256(messages)
    if path.is_file():
        value = read_json(path)
        expected = {
            "plan_sha256": plan_sha256,
            "request_sha256": request_sha256,
            "component": component,
            "completion_identity": completion_identity,
            "prompt_protocol": REFLECTOR_PROMPT_PROTOCOL,
        }
        if {name: value.get(name) for name in expected} != expected:
            raise ValueError("Committed Reflector response identity mismatch")
        output = value.get("raw_model_output")
        if not isinstance(output, str) or not output.strip():
            raise ValueError("Committed Reflector response is empty")
        _validate_recorded_context_budget(
            value.get("context_budget"),
            completion_identity=completion_identity,
        )
        return output
    context_budget = _reflector_context_budget(
        messages=messages,
        completion_client=completion_client,
        completion_identity=completion_identity,
    )
    output = completion_client.complete(
        messages,
        max_tokens=int(context_budget["max_tokens"]),
        component=component,
    )
    atomic_write_json(
        path,
        {
            "plan_sha256": plan_sha256,
            "request_sha256": request_sha256,
            "component": component,
            "completion_identity": completion_identity,
            "prompt_protocol": REFLECTOR_PROMPT_PROTOCOL,
            "context_budget": context_budget,
            "raw_model_output": output,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return output


def _candidate_identity(run_id: str, role: str, attempt: int, parent_id: str, epoch: int) -> str:
    identity = f"{run_id}\n{role}\n{attempt}\n{parent_id}"
    if epoch:
        identity += f"\nevaluation-epoch:{epoch}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def propose_candidate(
    *,
    role: str,
    pool: EvolutionPool,
    evaluation_paths: list[str | Path],
    review_paths: list[str | Path],
    attempt: int,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    completion_identity: dict[str, Any] | None = None,
    require_bubblewrap: bool = True,
    before_repair: Callable[[str], None] | None = None,
    evaluation_epoch: int = 0,
    selected_parent_id: str | None = None,
) -> dict[str, Any]:
    """Consume exactly one attempt and save success or failure; no candidate is pooled yet."""
    if pool.role != role:
        raise ValueError("Evolution attempt role differs from its pool")
    pool._validate_selection()
    if type(evaluation_epoch) is int and evaluation_epoch >= 0:
        pass
    else:
        raise ValueError("Proposal evaluation epoch requires a nonnegative integer")
    if not 1 <= int(attempt) <= MAX_MUTATION_ATTEMPTS:
        raise ValueError(f"Evolution attempt must be in [1, {MAX_MUTATION_ATTEMPTS}]")
    frozen_completion_identity = dict(completion_identity or {})
    attempt_directory = _attempt_directory(
        output_root=output_root,
        role=role,
        run_id=run_id,
    )
    attempt_path = attempt_directory / f"attempt-{attempt:03d}.json"
    proposal_prefix = f"attempt-{attempt:03d}" + (f".e{evaluation_epoch:03d}" if evaluation_epoch else "")
    plan_path = attempt_directory / f"{proposal_prefix}.plan.json"
    plan = read_json(plan_path) if plan_path.is_file() else None
    if plan is None:
        if selected_parent_id is None:
            parent = pool.select_parent(seed=attempt)
        else:
            selected = [item for item in pool.entries if item.candidate_id == selected_parent_id]
            if len(selected) != 1:
                raise ValueError("Proposal requires its frozen selected parent")
            parent = selected[0]
    else:
        expected_plan_identity = {
            "protocol": "research-v2-evolution-attempt-plan-v1",
            "run_id": run_id,
            "attempt": attempt,
            "role": role,
        }
        if {name: plan.get(name) for name in expected_plan_identity} != expected_plan_identity:
            raise ValueError("Evolution attempt plan identity mismatch")
        parent = next(
            (
                entry
                for entry in pool.entries
                if entry.candidate_id == plan.get("parent_id")
            ),
            None,
        )
        if parent is None:
            raise ValueError("Evolution attempt plan parent is absent from the pool")
    if selected_parent_id is not None and parent.candidate_id != selected_parent_id:
        raise ValueError("Proposal plan requires its frozen selected parent")
    parent_path = Path(parent.program_path).expanduser().resolve()
    parent_capability_manifest = _validate_recorded_capabilities(
        program_path=parent_path,
        recorded=parent.capability_manifest,
    )
    if file_sha256(parent_path) != parent.program_sha256:
        raise ValueError("Parent Program source no longer matches its pool entry")
    parent_code = parent_path.read_text(encoding="utf-8")
    packet = feedback_packet(
        parent=parent,
        evaluation_paths=evaluation_paths,
        review_paths=review_paths,
    )
    feedback_sha256 = value_sha256(packet)
    if attempt_path.is_file():
        return _load_committed_attempt(
            path=attempt_path,
            role=role,
            run_id=run_id,
            attempt=attempt,
            pool=pool,
            feedback_sha256=feedback_sha256,
            completion_identity=frozen_completion_identity,
            evaluation_epoch=evaluation_epoch,
        )
    candidate_id = _candidate_identity(run_id, role, attempt, parent.candidate_id, evaluation_epoch)
    expected_plan = {
        "protocol": "research-v2-evolution-attempt-plan-v1",
        "run_id": run_id,
        "attempt": attempt,
        "role": role,
        "parent_id": parent.candidate_id,
        "parent_program_sha256": parent.program_sha256,
        "candidate_id": candidate_id,
        "feedback_sha256": feedback_sha256,
        "completion_identity": frozen_completion_identity,
        **({"evaluation_epoch": evaluation_epoch} if evaluation_epoch else {}),
    }
    if plan is not None:
        if plan != expected_plan:
            raise ValueError("Evolution attempt plan differs from current inputs")
    else:
        atomic_write_json(plan_path, expected_plan)
        plan = expected_plan
    plan_sha256 = value_sha256(plan)
    prompt = _reflector_prompt(parent_code, packet)
    raw = _load_or_call_reflector(
        path=attempt_directory / f"{proposal_prefix}.reflector.json",
        messages=[{"role": "user", "content": prompt}],
        component="reflector",
        completion_client=completion_client,
        completion_identity=frozen_completion_identity,
        plan_sha256=plan_sha256,
    )
    repair_used = False
    errors: list[str] = []
    applied: AppliedPatch | None = None
    program_path = (
        Path(output_root).expanduser().resolve()
        / "programs"
        / role
        / candidate_id
        / "program.py"
    )
    repair_path = attempt_directory / f"{proposal_prefix}.repair.json"
    repair_request_path = attempt_directory / f"{proposal_prefix}.repair-request.json"
    repair_pending = repair_request_path.is_file() or repair_path.is_file()
    if repair_pending:
        repair_request = read_json(repair_request_path)
        if (
            repair_request.get("protocol") == "research-v2-repair-request-v1"
            and repair_request.get("plan_sha256") == plan_sha256
            and repair_request.get("request_id") == value_sha256({
                key: value for key, value in repair_request.items() if key != "request_id"
            })
        ):
            repair_messages = repair_request["messages"]
        else:
            raise ValueError("Committed repair request identity mismatch")
    else:
        try:
            applied = apply_v4a_patch(parent_code, raw)
        except ValueError as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
        else:
            _write_program(program_path, applied.code)
            errors = validate_program(
                program_path,
                require_bubblewrap=require_bubblewrap,
            )
    if errors or repair_pending:
        if before_repair is not None:
            before_repair(candidate_id)
        repair_used = True
        if errors:
            repair_messages = [{
                "role": "user",
                "content": _repair_prompt(parent_code, raw, "\n".join(errors)),
            }]
            repair_request = {
                "protocol": "research-v2-repair-request-v1",
                "plan_sha256": plan_sha256,
                "messages": repair_messages,
            }
            repair_request["request_id"] = value_sha256(repair_request)
            atomic_write_json(repair_request_path, repair_request)
        repair_response = _load_or_call_reflector(
            path=repair_path,
            messages=repair_messages,
            component="reflector_repair",
            completion_client=completion_client,
            completion_identity=frozen_completion_identity,
            plan_sha256=plan_sha256,
        )
        try:
            applied = apply_v4a_patch(parent_code, repair_response)
        except ValueError as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
        else:
            if program_path.exists():
                program_path.unlink()
            _write_program(program_path, applied.code)
            errors = validate_program(
                program_path,
                require_bubblewrap=require_bubblewrap,
            )
            raw = raw + "\n\n--- EXPLICIT REPAIR ---\n\n" + repair_response
    status = "proposed" if applied is not None and not errors else "failed"
    capability_manifest = (
        _program_capabilities(program_path) if status == "proposed" else {}
    )
    outcome = {
        "protocol": "research-v2-evolution-attempt-v2",
        "run_id": run_id,
        "attempt": attempt,
        "role": role,
        "candidate_id": candidate_id,
        "parent_id": parent.candidate_id,
        "status": status,
        "repair_used": repair_used,
        "program_path": str(program_path) if status == "proposed" else "",
        "program_sha256": file_sha256(program_path) if status == "proposed" else "",
        "capability_manifest": capability_manifest,
        "parent_capability_manifest": parent_capability_manifest,
        "change": {
            "title": applied.title if applied else "",
            "details": applied.details if applied else [],
        },
        "errors": errors,
        "raw_reflector_output": raw,
        "feedback_sha256": feedback_sha256,
        "completion_identity": frozen_completion_identity,
        "plan_sha256": plan_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_epoch": evaluation_epoch,
    }
    atomic_write_json(attempt_path, outcome)
    return outcome


def commit_candidate(
    *,
    pool: EvolutionPool,
    attempt_path: str | Path,
    static_review_paths: list[str | Path],
    static_evaluation_paths: list[str | Path],
    pool_path: str | Path,
    memory_snapshot_path: str | Path,
    evaluation_epoch: int = 0,
    evaluation_metadata: dict[str, Any] | None = None,
) -> PoolEntry:
    pool._validate_selection()
    target = Path(pool_path).expanduser().resolve()
    if target.is_file():
        committed_pool = EvolutionPool.from_dict(read_json(target))
        if committed_pool.to_dict() != pool.to_dict():
            raise ValueError("Candidate commit received a stale in-memory pool")
    attempt = read_json(attempt_path)
    if attempt.get("status") != "proposed":
        raise ValueError("Only a successfully proposed candidate can enter the pool")
    if attempt.get("role") != pool.role:
        raise ValueError("Evolution attempt role differs from its pool")
    program_path = str(attempt.get("program_path") or "")
    if not program_path or file_sha256(program_path) != attempt.get("program_sha256"):
        raise ValueError("Evolution attempt Program source no longer matches its artifact")
    capability_manifest = _validate_recorded_capabilities(
        program_path=program_path,
        recorded=dict(attempt.get("capability_manifest") or {}),
    )
    if not attempt.get("capability_manifest"):
        raise ValueError("Evolution attempt has no Program capability manifest")
    receipt = build_evaluation_receipt(
        role=pool.role,
        snapshot_path=memory_snapshot_path,
        review_paths=static_review_paths,
        evaluation_paths=static_evaluation_paths,
        epoch=evaluation_epoch,
        metadata=evaluation_metadata,
    )
    score = receipt["mean_score"]
    parent = next(
        (
            value
            for value in pool.entries
            if value.candidate_id == attempt.get("parent_id")
        ),
        None,
    )
    if parent is None:
        raise ValueError("Evolution attempt parent is absent from the pool")
    parent_capability_manifest = _validate_recorded_capabilities(
        program_path=parent.program_path,
        recorded=parent.capability_manifest,
    )
    if file_sha256(parent.program_path) != parent.program_sha256:
        raise ValueError("Parent Program source no longer matches its pool entry")
    if dict(attempt.get("parent_capability_manifest") or {}) != parent_capability_manifest:
        raise ValueError("Evolution attempt parent capability identity mismatch")
    memory_binding = snapshot_binding(
        snapshot_path=memory_snapshot_path,
        role=pool.role,
        program_path=program_path,
        seed_kind=parent.seed_kind,
    )
    entry = PoolEntry(
        candidate_id=str(attempt["candidate_id"]),
        role=pool.role,
        program_path=str(attempt["program_path"]),
        program_sha256=str(attempt["program_sha256"]),
        parent_id=parent.candidate_id,
        static_score=score,
        seed_kind=parent.seed_kind,
        **memory_binding,
        capability_manifest=capability_manifest,
        evaluation_receipt=receipt,
        lineage=[
            *parent.lineage,
            {
                "candidate_id": attempt["candidate_id"],
                "parent_id": parent.candidate_id,
                "change": attempt.get("change"),
                "static_score": score,
                "capability_manifest": capability_manifest,
                "memory_snapshot_id": memory_binding["memory_snapshot_id"],
            },
        ],
    )
    existing = next(
        (
            value
            for value in pool.entries
            if value.candidate_id == entry.candidate_id
        ),
        None,
    )
    if existing is not None:
        if existing != entry:
            raise ValueError("Committed candidate differs from the existing pool entry")
        validate_pool_entry_snapshot(existing)
        return existing
    pool.add(entry)
    atomic_write_json(target, pool.to_dict(), immutable=False)
    return entry
