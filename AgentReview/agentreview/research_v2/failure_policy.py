"""Durable failure budgets and publication gates for an evolution round."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
from functools import wraps
from pathlib import Path
from typing import Any

from .io import atomic_write_json, file_sha256, read_json, value_sha256


FAILURE_POLICY_PROTOCOL = "research-v2-failure-policy-v1"


def _transaction(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        if self._transaction_active:
            return method(self, *args, **kwargs)
        with self.path.with_suffix(".lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self.value = read_json(self.path)
            self._transaction_active = True
            try:
                return method(self, *args, **kwargs)
            finally:
                self._transaction_active = False
    return invoke


class EvolutionPaused(RuntimeError):
    """Shared inputs require repair and a complete reevaluation epoch."""


class EvolutionBudgetExhausted(RuntimeError):
    """The round has reached its recorded failure or repair limit."""


@dataclass(frozen=True)
class FailureLimits:
    candidate_repairs: int = 1
    round_repairs: int = 10
    candidate_failures: int = 5
    consecutive_candidate_failures: int | None = None

    def __post_init__(self) -> None:
        if self.consecutive_candidate_failures is not None and (type(self.consecutive_candidate_failures) is not int or self.consecutive_candidate_failures < 1):
            raise ValueError("Consecutive candidate failure limit requires a positive integer")
        for value in (self.candidate_repairs, self.round_repairs, self.candidate_failures):
            if type(value) is int and value > 0:
                continue
            raise ValueError("Failure budgets require positive integer limits")


def failure_detail(error: Exception, *, phase: str) -> dict[str, Any]:
    """Attribute known candidate contracts; treat shared and unknown faults globally."""
    from agentreview.reviewer_runtime.sandbox import SandboxError
    from .review import SpecialistReviewPaperFailure
    from .review_preparation import ReviewPlanError

    message = f"{type(error).__name__}: {error}"
    category = "shared_runtime"
    transient_names = {
        "TimeoutError", "ConnectionError", "APIConnectionError", "APITimeoutError",
        "RateLimitError", "InternalServerError", "ConnectError", "ReadTimeout",
    }
    shared_markers = ("CUDA", "OutOfMemory", "No space left", "Read-only file system", "PermissionError", "bubblewrap was required", *transient_names, "APIStatusError")
    transient = type(error).__name__ in transient_names
    recorded_category = getattr(error, "failure_category", "")
    if recorded_category in {"candidate", "shared_data", "shared_evaluation", "shared_runtime"}:
        category = recorded_category
    elif "evaluation" in phase:
        category = "shared_evaluation"
    elif isinstance(error, (FileNotFoundError, KeyError)) or "FileNotFoundError:" in message or "preflight" in phase:
        category = "shared_data"
    elif transient or any(marker in message for marker in shared_markers):
        category = "shared_runtime"
    elif isinstance(error, (ReviewPlanError, SpecialistReviewPaperFailure, SandboxError)):
        category = "candidate"
    elif isinstance(error, ValueError) and any(
        marker in str(error)
        for marker in ("KnowledgeBase.read()", "Review Plan", "plan_review", "selected_s2_ids", "s2_disposition", "context_order")
    ):
        category = "candidate"
    record = {"category": category, "phase": phase, "error": message, "transient": transient}
    record["fingerprint"] = value_sha256({"category": category, "phase": phase, "error": message})
    evaluator_record = getattr(error, "failure_record", None)
    if isinstance(evaluator_record, dict):
        record["evaluation_failure"] = evaluator_record
    return record


def validate_promotion_policy(value: dict[str, Any]) -> None:
    """Require a live, fully evaluated round with remaining repair capacity."""
    if value.get("protocol") != FAILURE_POLICY_PROTOCOL:
        raise ValueError("Promotion requires a recorded failure policy")
    if value.get("status") not in {"running", "complete"}:
        raise EvolutionPaused(f"Promotion requires a ready round; status={value.get('status')}")
    if value.get("pending_candidates"):
        raise EvolutionPaused("Promotion requires complete reevaluation of every affected candidate")
    for name in ("repair_budget", "candidate_failure_budget"):
        budget = value.get(name, {})
        used, limit = budget.get("used"), budget.get("limit")
        inclusive = name == "repair_budget" and value.get("counting_contract") == "terminal-events-v2"
        if type(used) is int and type(limit) is int and 0 <= used and (used <= limit if inclusive else used < limit):
            continue
        raise EvolutionBudgetExhausted(f"Promotion requires available {name}")
    epoch = value.get("evaluation_epoch")
    if type(epoch) is int and epoch >= 0:
        return
    raise ValueError("Promotion requires a valid evaluation epoch")


def validate_frozen_policy(value):
    """A bounded early stop may freeze its already validated Champion, never admit a new candidate."""
    if value.get("status") != "ended" or value.get("counting_contract") != "terminal-events-v2":
        return validate_promotion_policy(value)
    if value.get("pending_candidates") or not value.get("champions"):
        raise EvolutionPaused("Terminal freeze requires an intact validated comparison")
    if value.get("end_reason") not in {"candidate_failure_budget", "repair_budget"}:
        raise EvolutionPaused("Only a recorded bounded search stop can freeze its Champion")
    for name in ("repair_budget", "candidate_failure_budget"):
        budget = value[name]
        if not 0 <= budget["used"] <= budget["limit"]:
            raise ValueError("Invalid terminal failure accounting")


class FailureController:
    """Persist each budget reservation before the corresponding operation starts."""

    def __init__(self, path: str | Path, *, limits: FailureLimits | None = None):
        self.path = Path(path).expanduser().resolve()
        self._transaction_active = False
        limits = limits or FailureLimits()
        if self.path.is_file():
            self.value = read_json(self.path)
            if self.value.get("protocol") != FAILURE_POLICY_PROTOCOL:
                raise ValueError("Evolution failure policy protocol mismatch")
            return
        self.value = {
            "protocol": FAILURE_POLICY_PROTOCOL,
            "status": "running",
            "evaluation_epoch": 0,
            "pending_candidates": [],
            "repair_budget": {"limit": limits.round_repairs, "used": 0},
            "candidate_failure_budget": {"limit": limits.candidate_failures, "used": 0},
            "candidate_repair_limit": limits.candidate_repairs,
            "candidate_repairs": {},
            "events": {},
            "champions": {},
        }
        if limits.consecutive_candidate_failures is not None:
            self.value.update(counting_contract="terminal-events-v2",
                consecutive_failures={"limit": limits.consecutive_candidate_failures, "used": 0})
        try:
            atomic_write_json(self.path, self.value)
        except FileExistsError:
            self.value = read_json(self.path)
            if self.value.get("protocol") != FAILURE_POLICY_PROTOCOL:
                raise ValueError("Evolution failure policy protocol mismatch")

    def save(self) -> None:
        atomic_write_json(self.path, self.value, immutable=False)

    @_transaction
    def require_running(self) -> None:
        if self.value["status"] == "ended":
            raise EvolutionBudgetExhausted("Evolution round ended at its recorded budget")
        for name in ("repair_budget", "candidate_failure_budget"):
            budget = self.value[name]
            if name == "repair_budget" and self.value.get("counting_contract") == "terminal-events-v2" and budget["used"] == budget["limit"]:
                continue  # The tenth reserved repair may finish; an eleventh cannot start.
            if budget["used"] >= budget["limit"]:
                self.end(name)
                raise EvolutionBudgetExhausted(f"Evolution round reached {name}")
        validate_promotion_policy(self.value)

    @_transaction
    def preserve_champion(self, role: str, champion: dict[str, Any]) -> None:
        previous = self.value["champions"].get(role)
        if previous is None or champion["static_score"] > previous["static_score"]:
            self.value["champions"][role] = champion
        self.save()

    @_transaction
    def reserve_repair(self, *, operation_id: str, candidate_id: str | None = None) -> bool:
        """Return a persisted repair reservation; replay consumes the same slot."""
        if operation_id in self.value["events"]:
            event = self.value["events"][operation_id]
            if event.get("kind") == "repair" and event.get("candidate_id") == candidate_id:
                return True
            raise ValueError("Repair replay requires its original candidate identity")
        if self.value["status"] == "ended":
            raise EvolutionBudgetExhausted("Evolution round repair budget is exhausted")
        if candidate_id is not None:
            used = int(self.value["candidate_repairs"].get(candidate_id, 0))
            if used >= self.value["candidate_repair_limit"]:
                return False
        budget = self.value["repair_budget"]
        if budget["used"] >= budget["limit"]:
            self.end("repair_budget")
            raise EvolutionBudgetExhausted("Evolution round repair budget is exhausted")
        budget["used"] += 1
        if candidate_id is not None:
            self.value["candidate_repairs"][candidate_id] = used + 1
        self.value["events"][operation_id] = {"kind": "repair", "candidate_id": candidate_id}
        self.save()
        return True

    @_transaction
    def candidate_failed(self, *, operation_id: str, candidate_id: str, detail: dict[str, Any]) -> None:
        if operation_id in self.value["events"]:
            return
        self.value["events"][operation_id] = {"kind": "candidate_failure", "candidate_id": candidate_id, **detail}
        budget = self.value["candidate_failure_budget"]
        budget["used"] += 1
        streak = self.value.get("consecutive_failures")
        if streak is not None:
            streak["used"] += 1
        if budget["used"] >= budget["limit"]:
            self.value["status"] = "ended"
            self.value["end_reason"] = "candidate_failure_budget"
        elif streak is not None and streak["used"] >= streak["limit"]:
            self.value["status"] = "paused_candidate"
            self.value["pause_reason"] = "consecutive_candidate_failures"
        self.save()

    @_transaction
    def candidate_succeeded(self, *, candidate_id: str) -> None:
        if "consecutive_failures" not in self.value:
            return
        key = f"candidate-success:{candidate_id}"
        if key in self.value["events"]:
            return
        self.value["events"][key] = {"kind": "candidate_success", "candidate_id": candidate_id}
        self.value["consecutive_failures"]["used"] = 0
        self.save()

    @_transaction
    def resume_candidate_diagnosis(self, *, evidence_path: str | Path) -> None:
        if self.value["status"] != "paused_candidate":
            raise EvolutionPaused("Candidate diagnosis requires its recorded pause")
        record = read_json(evidence_path)
        if any(not isinstance(record.get(k), str) or not record[k].strip() for k in ("cause", "repair", "validation")):
            raise ValueError("Candidate diagnosis requires cause, repair and validation")
        identity = {"path": str(Path(evidence_path).resolve()), "sha256": file_sha256(evidence_path),
                    "repair_id":value_sha256({key:record[key].strip() for key in ("cause","repair","validation")})}
        if any(old.get("repair_id") == identity["repair_id"] for old in self.value.get("candidate_diagnoses", [])):
            raise ValueError("A new candidate diagnosis requires new verified repair evidence")
        # Count the intervention against the same cumulative repair ceiling.
        self.reserve_repair(operation_id="candidate-diagnosis:" + value_sha256({"evidence": identity, "failures": self.value["candidate_failure_budget"]["used"]}))
        self.value.setdefault("candidate_diagnoses", []).append(identity)
        self.value["status"] = "running"
        self.save()  # Neither total failures nor the current streak is erased.

    @_transaction
    def pause(self, *, operation_id: str, detail: dict[str, Any], affected: list[str]) -> None:
        if self.value["status"] == "ended":
            return
        self.value["events"].setdefault(operation_id, {"kind": "shared_failure", **detail})
        self.value["status"] = "paused"
        self.value["population_capture_failed"] = bool(detail.get("population_capture_error"))
        self.value["pending_candidates"] = sorted(set(self.value["pending_candidates"]) | set(affected))
        self.save()

    @_transaction
    def begin_recovery(self, *, repair_evidence_path: str | Path, affected_candidates: list[str] | None = None) -> int:
        """Bind a concrete repair record and allocate a fresh evaluation namespace."""
        evidence = Path(repair_evidence_path).expanduser().resolve()
        record = read_json(evidence)
        for field in ("cause", "repair", "validation"):
            if isinstance(record.get(field), str) and record[field].strip():
                continue
            raise ValueError(f"Shared repair evidence requires {field}")
        identity = {
            "path": str(evidence), "sha256": file_sha256(evidence),
            "repair_id": value_sha256({name: record[name].strip() for name in ("cause", "repair", "validation")}),
        }
        previous = self.value.get("repair_evidence", {})
        if self.value["status"] == "reevaluating" and previous.get("repair_id") == identity["repair_id"]:
            return self.value["evaluation_epoch"]
        if self.value["status"] != "paused":
            raise ValueError("Shared recovery starts from a paused round")
        previous_repairs = [previous, *self.value.get("repair_evidence_history", [])]
        if any(item.get("repair_id") == identity["repair_id"] or item.get("sha256") == identity["sha256"] for item in previous_repairs):
            raise ValueError("A failed reevaluation requires evidence of a new verified repair")
        if self.value.get("population_capture_failed") and affected_candidates is None:
            raise ValueError("Repaired population data requires a complete candidate inventory")
        operation_id = "shared-repair:" + value_sha256({"evidence": identity, "epoch": self.value["evaluation_epoch"]})
        self.reserve_repair(operation_id=operation_id)
        self.value["evaluation_epoch"] += 1
        self.value["status"] = "reevaluating"
        self.value["repair_evidence"] = identity
        self.value.setdefault("repair_evidence_history", []).append(identity)
        if self.value.get("population_capture_failed"):
            self.value["pending_candidates"] = sorted(set(affected_candidates))
            self.value["population_capture_failed"] = False
        self.save()
        return self.value["evaluation_epoch"]

    @_transaction
    def finish_recovery(self, *, completed_candidates: list[str], champions: dict[str, Any] | None = None) -> None:
        expected = set(self.value["pending_candidates"])
        if self.value["status"] == "reevaluating" and expected == set(completed_candidates):
            for name in ("repair_budget", "candidate_failure_budget"):
                budget = self.value[name]
                if name == "repair_budget" and self.value.get("counting_contract") == "terminal-events-v2" and budget["used"] == budget["limit"]:
                    continue
                if budget["used"] >= budget["limit"]:
                    self.end(name)
                    raise EvolutionBudgetExhausted("Recovery exhausted the round budget; Champion retained")
            if champions is not None:
                self.value.setdefault("champion_history", []).append(self.value["champions"])
                self.value["champions"] = champions
            self.value["pending_candidates"] = []
            self.value["status"] = "running"
            self.save()
            return
        raise EvolutionPaused("Recovery requires complete results for the affected candidate set")

    @_transaction
    def end(self, reason: str) -> None:
        self.value["status"] = "ended"
        self.value["end_reason"] = reason
        self.save()


def controller_for_run(output_root: str | Path, run_id: str) -> FailureController:
    return FailureController(Path(output_root).resolve() / "runs" / run_id / "failure-policy.json")
