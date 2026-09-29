"""M★ search over one shared MemoryProgram, using complete final CoCo fitness."""

from __future__ import annotations

import fcntl
import copy
import math
import json
import random
from pathlib import Path
from typing import Any

from .failure_policy import (FailureController, FailureLimits, EvolutionBudgetExhausted,
                             EvolutionPaused, failure_detail, validate_promotion_policy)
from .io import atomic_write_json, read_json, value_sha256
from .memory import SEED_NAMES

PROTOCOL = "research-v2-shared-mstar-v1"


class CandidateProposalFailure(ValueError):
    """A classified proposal contract enters the candidate's bounded repair path."""

    failure_category = "candidate"


def select_parent(entries: list[dict], *, temperature: float, random_seed: int | None = None, rng=None) -> dict:
    """Sample every valid candidate with a stable softmax of the same Paper. score."""
    if entries and isinstance(temperature, (float, int)) and math.isfinite(temperature) and temperature > 0:
        scores = [entry["static_score"] for entry in entries]
        if all(type(score) in (int, float) and math.isfinite(score) for score in scores):
            peak = max(scores)
            weights = [math.exp(max(-700.0, (score - peak) / temperature)) for score in scores]
            return (rng or random.Random(random_seed)).choices(entries, weights=weights, k=1)[0]
    raise ValueError("Parent selection requires finite complete scores and a positive temperature")


class SharedEvolution:
    """Persist recipes before work; admit complete comparable assessments together.

    The backend owns generation/scoring. It validates every receipt and its source
    artifacts, so cached numbers alone confer no eligibility for selection.
    """

    def __init__(self, *, protocol, output_root, backend):
        self.protocol = protocol
        self.config = protocol.document
        self.backend = backend
        self.root = Path(output_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        limits = self.config["failure_policy"]
        self.controller = FailureController(self.root / "failure-policy.json", limits=FailureLimits(
            **{key: limits[key] for key in ("candidate_repairs", "round_repairs", "candidate_failures")},
            consecutive_candidate_failures=limits.get("consecutive_candidate_failures")))
        self.state_path = self.root / "search.json"
        identity = {"protocol": PROTOCOL, "experiment_protocol_sha256": protocol.protocol_sha256}
        if self.state_path.is_file():
            self.state = read_json(self.state_path)
            if self.state["identity"] != identity:
                raise ValueError("Search resume requires the frozen experiment protocol")
        else:
            self.state = {"identity": identity, "status": "running", "recipes": {}, "results": {},
                          "pool": [], "attempts": {}, "champion": None, "winner": None}
            if self.config["evolution"].get("random_streams_protocol") == "sha256-separated-python-random-v1":
                self.state["rng_streams"] = {}
                for name in ("parent", "rotating", "reflection"):
                    seed = int(value_sha256({"seed": self.config["evolution"]["random_seed"], "stream": name}), 16)
                    self.state["rng_streams"][name] = {"derived_seed": str(seed), "state": json.loads(json.dumps(random.Random(seed).getstate())), "draws": 0}
            self.save()

    def _draw(self, name, operation):
        stream = self.state["rng_streams"][name]
        def tuples(value):
            return tuple(tuples(x) for x in value) if isinstance(value, (list, tuple)) else value
        rng = random.Random()
        rng.setstate(tuples(stream["state"]))
        result = operation(rng)
        stream.update(state=json.loads(json.dumps(rng.getstate())), draws=stream["draws"] + 1)
        return result  # Caller commits the decision and new state in one save.

    def _parent(self, entries, ordinal):
        options = self.config["evolution"]
        if "rng_streams" in self.state:
            return self._draw("parent", lambda rng: select_parent(entries, temperature=options["parent_temperature"], rng=rng))
        return select_parent(entries, temperature=options["parent_temperature"], random_seed=options["random_seed"] + ordinal)

    def save(self):
        atomic_write_json(self.state_path, self.state, immutable=False)

    @property
    def epoch(self):
        return self.controller.value["evaluation_epoch"]

    def _register(self, key, candidate, *, kind, split="static", paper_ids=None, memory_enabled=True):
        recipe = {"key": key, "candidate": candidate, "kind": kind, "split": split,
                  "paper_ids": paper_ids or [row["paper_id"] for row in self.protocol.records(split)],
                  "memory_enabled": memory_enabled}
        existing = self.state["recipes"].get(key)
        if existing is not None:
            return existing
        self.state["recipes"][key] = recipe
        self.save()
        return recipe

    def _gate(self, *, recovery=False):
        self.controller = FailureController(self.controller.path)
        if recovery:
            for name in ("repair_budget", "candidate_failure_budget"):
                budget = self.controller.value[name]
                if name == "repair_budget" and self.controller.value.get("counting_contract") == "terminal-events-v2" and budget["used"] == budget["limit"]:
                    continue
                if budget["used"] >= budget["limit"]:
                    raise EvolutionBudgetExhausted(f"Recovery reached {name}")
            if self.controller.value["status"] == "reevaluating":
                return
            raise EvolutionPaused("Recovery requires the active reevaluation state")
        self.controller.require_running()

    def _validated(self, key, *, results=None, recovery=False):
        self._gate(recovery=recovery)
        epoch = self.epoch
        recipe = self.state["recipes"][key]
        result = (self.state["results"] if results is None else results)[key]
        self.backend.validate(result, recipe=recipe, epoch=epoch)
        self._gate(recovery=recovery)
        if self.epoch != epoch:
            raise EvolutionPaused("Assessment validation requires one comparison epoch")
        score = result["static_score"]
        if type(score) in (int, float) and math.isfinite(score):
            return result
        raise ValueError("Complete assessment requires a finite standard CoCo Paper. score")

    def _entry(self, key, *, results=None, recovery=False):
        result = self._validated(key, results=results, recovery=recovery)
        return {**self.state["recipes"][key]["candidate"], **result, "key": key}

    def _pause(self, exc, phase):
        detail = failure_detail(exc, phase=phase)
        # Include controls and the candidate whose work raised the public fault.
        affected = [key for key, recipe in self.state["recipes"].items()
                    if recipe["split"] == "static" and self.state["results"].get(key, {}).get("status") != "candidate_failed"]
        self.controller.pause(operation_id=f"shared:e{self.epoch}:{detail['fingerprint']}", detail=detail, affected=affected)
        self.state["status"] = self.controller.value["status"]
        self.state["last_failure"] = detail
        self._archive_winner()
        self.save()

    def _archive_winner(self):
        if self.state.get("winner") is not None:
            self.state.setdefault("winner_history", []).append(self.state["winner"])
            self.state["winner"] = None

    def _record_result(self, recipe, result, *, previous_recipe):
        key = recipe["key"]
        if key in self.state["results"]:
            old_recipe = self.state.setdefault("result_recipes", {}).get(key, previous_recipe)
            self.state.setdefault("assessment_history", []).append({
                "recipe": copy.deepcopy(old_recipe), "result": copy.deepcopy(self.state["results"][key])})
        self.state["results"][key] = result
        self.state.setdefault("result_recipes", {})[key] = copy.deepcopy(recipe)
        self.save()

    def _evaluate(self, recipe, *, recovery=False):
        key = recipe["key"]
        previous_recipe = copy.deepcopy(recipe)
        existing = self.state["results"].get(key)
        if existing is not None and existing.get("evaluation_epoch") == self.epoch:
            if existing.get("status") == "candidate_failed":
                return existing
            return self._validated(key, recovery=recovery)
        while True:
            self._gate(recovery=recovery)
            epoch = self.epoch
            try:
                if recipe["candidate"].get("proposal_failure"):
                    raise CandidateProposalFailure(recipe["candidate"]["proposal_failure"]["error"])
                result = self.backend.assess(recipe, epoch=epoch)
                self.backend.validate(result, recipe=recipe, epoch=epoch)
                self._gate(recovery=recovery)
                if self.epoch != epoch:
                    raise EvolutionPaused("Assessment completion requires one comparison epoch")
                self._record_result(recipe, result, previous_recipe=previous_recipe)
                return result
            except (EvolutionPaused, EvolutionBudgetExhausted):
                raise
            except Exception as exc:
                detail = failure_detail(exc, phase="shared_candidate_run")
                candidate = recipe["candidate"]
                if detail["category"] == "candidate" and recipe["kind"] == "candidate":
                    revision = candidate.get("revision", 0)
                    reservation = f"candidate-repair:{candidate['candidate_id']}:r{revision}"
                    if self.controller.reserve_repair(operation_id=reservation, candidate_id=candidate["candidate_id"]):
                        try:
                            repaired = self.backend.propose(parent=candidate, feedback={"contract_failure": detail},
                                                            history=self.state["attempts"],
                                                            directory=self.root / "repairs" / f"e{self.epoch}" / candidate["candidate_id"] / f"r{revision + 1}")
                        except Exception as repair_exc:
                            repair_detail = failure_detail(repair_exc, phase="shared_candidate_proposal")
                            if repair_detail["category"] == "candidate":
                                repaired = {"proposal_failure": repair_detail}
                            else:
                                raise
                        base_candidate = {name: value for name, value in candidate.items() if name != "proposal_failure"}
                        recipe["candidate"] = {**base_candidate, **repaired, "candidate_id": candidate["candidate_id"],
                                               "seed": candidate["seed"], "parent_id": candidate.get("parent_id"),
                                               "revision": revision + 1}
                        self.save()
                        continue
                    self.controller.candidate_failed(operation_id=f"failed:{key}", candidate_id=candidate["candidate_id"], detail=detail)
                    result = {"status": "candidate_failed", "static_score": None, "evaluation_epoch": self.epoch, "failure": detail}
                    self._record_result(recipe, result, previous_recipe=previous_recipe)
                    return result
                raise

    def _admit(self, key):
        entry = self._entry(key)
        epoch = self.epoch
        # The same policy lock serializes publication with a public pause.
        with self.controller.path.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.controller.value = read_json(self.controller.path)
            validate_promotion_policy(self.controller.value)
            if self.epoch != epoch:
                raise EvolutionPaused("Candidate admission requires the validated comparison epoch")
            if key not in self.state["pool"]:
                self.state["pool"].append(key)
            previous = self.controller.value["champions"].get("shared")
            if previous is None or entry["static_score"] > previous["static_score"]:
                self.controller.value["champions"]["shared"] = entry
            self.controller.save()
            self.state["champion"] = self.controller.value["champions"].get("shared")
            self.save()

    def _recover(self, evidence):
        if self.controller.value["status"] in {"running", "complete"}:
            record = read_json(evidence)
            repair_id = value_sha256({key:record[key].strip() for key in ("cause","repair","validation")})
            if any(old.get("repair_id") == repair_id for old in self.controller.value.get("candidate_diagnoses", [])):
                return
        if self.controller.value["status"] == "paused_candidate":
            self.controller.resume_candidate_diagnosis(evidence_path=evidence)
            self.state["status"] = "running"
            self.save()
            return
        if self.controller.value["status"] == "running" and self.state.get("recovery_applied_epoch") == self.epoch:
            record = read_json(evidence)
            repair_id = value_sha256({name: record[name].strip() for name in ("cause", "repair", "validation")})
            if repair_id == self.controller.value.get("repair_evidence", {}).get("repair_id"):
                return
        self.controller.begin_recovery(repair_evidence_path=evidence)
        self._archive_winner()
        self.save()
        keys = list(self.controller.value["pending_candidates"])
        # Keep the prior verified Champion until the whole comparison is ready.
        staged = {}
        for key in keys:
            staged[key] = self._evaluate(self.state["recipes"][key], recovery=True)
        pool = [key for key in self.state["pool"] if staged.get(key, self.state["results"].get(key, {})).get("status") == "complete"]
        entries = [self._entry(key, recovery=True) for key in pool]
        champions = {"shared": max(entries, key=lambda item: item["static_score"])} if entries else {}
        atomic_write_json(self.root / "recovery" / f"e{self.epoch}" / "resolutions.json", staged)
        attempts = copy.deepcopy(self.state["attempts"])
        for attempt in attempts.values():
            candidate_key = attempt.get("candidate_key")
            if candidate_key in staged:
                attempt.update(status=staged[candidate_key]["status"], static_score=staged[candidate_key]["static_score"])
            if attempt.get("status") == "pending":
                attempt.pop("feedback_key", None)
                if "reflection_case_ids" in attempt:
                    attempt.setdefault("reflection_case_history", []).append({
                        "case_ids":attempt.pop("reflection_case_ids"),
                        "selection":attempt.pop("reflection_selection"), "reason":"new comparison epoch"})
        atomic_write_json(self.root / "recovery" / f"e{self.epoch}" / "commit.json", {
            "evaluation_epoch": self.epoch, "pool": pool, "champions": champions,
            "attempts": attempts, "completed_candidates": keys})
        self.controller.finish_recovery(completed_candidates=keys, champions=champions)
        self._apply_recovery_commit()

    def _apply_recovery_commit(self):
        """Replay the prepared population after a policy/search write interruption."""
        path = self.root / "recovery" / f"e{self.epoch}" / "commit.json"
        if self.controller.value["status"] == "running" and path.is_file() and self.state.get("recovery_applied_epoch") != self.epoch:
            commit = read_json(path)
            if commit["evaluation_epoch"] != self.epoch or commit["champions"] != self.controller.value["champions"]:
                raise ValueError("Recovery population requires its committed policy epoch and Champion")
            for key in commit["completed_candidates"]:
                result = self.state["results"][key]
                if result.get("status") == "candidate_failed" and result.get("evaluation_epoch") == self.epoch:
                    continue
                self._validated(key)
            self.state.update(pool=commit["pool"], champion=commit["champions"].get("shared"),
                              attempts=commit["attempts"], status="running", recovery_applied_epoch=self.epoch)
            self.save()

    def _freeze_terminal(self):
        from .failure_policy import validate_frozen_policy
        if self.controller.value.get("counting_contract") != "terminal-events-v2":
            return
        validate_frozen_policy(self.controller.value)
        initial = [f"{prefix}-{seed}" for seed in SEED_NAMES for prefix in ("seed", "no-memory")] + ["coco-native"]
        if any(self.state["results"].get(key, {}).get("status") != "complete" for key in initial):
            raise EvolutionPaused("Terminal freeze requires all initial comparison arms")
        entries = []
        for key in self.state["pool"]:
            result, recipe = self.state["results"][key], self.state["recipes"][key]
            self.backend.validate(result, recipe=recipe, epoch=self.epoch)
            entries.append({**recipe["candidate"], **result, "key": key})
        winner = max(entries, key=lambda row: row["static_score"])
        frozen = self.backend.freeze(winner, self.state)
        atomic_write_json(self.root / f"winner.e{self.epoch}.json", frozen)
        atomic_write_json(self.root / "winner.json", frozen, immutable=False)
        self.state["winner"] = frozen
        self.save()

    def run(self, *, repair_evidence=None):
        with (self.root / "search.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.state = read_json(self.state_path)
            self.controller = FailureController(self.controller.path)
            try:
                if self.state["status"] == "ended" and self.state.get("winner") is not None:
                    from .failure_policy import validate_frozen_policy
                    if self.state["winner"] != read_json(self.root / "winner.json"):
                        raise ValueError("Terminal Champion requires its frozen artifact")
                    validate_frozen_policy(self.controller.value)
                    for key in self.state["pool"]:
                        self.backend.validate(self.state["results"][key], recipe=self.state["recipes"][key], epoch=self.epoch)
                    return self.state
                self._apply_recovery_commit()
                if repair_evidence:
                    self._recover(repair_evidence)
                self.controller.require_running()
                if self.state["status"] == "complete":
                    for key, recipe in self.state["recipes"].items():
                        if recipe["split"] == "static" and self.state["results"].get(key, {}).get("status") != "candidate_failed":
                            self._validated(key)
                    if self.state["winner"] != read_json(self.root / "winner.json"):
                        raise ValueError("Published winner requires its recorded frozen artifact")
                    return self.state
                self.backend.preflight()
                # Materialize all initial comparison identities before generation.
                for seed in SEED_NAMES:
                    candidate = self.backend.seed(seed)
                    self._register(f"seed-{seed}", candidate, kind="seed")
                    self._register(f"no-memory-{seed}", candidate, kind="no_memory", memory_enabled=False)
                self._register("coco-native", self.backend.native_identity(), kind="coco_native", memory_enabled=False)
                for seed in SEED_NAMES:
                    for key in (f"seed-{seed}", f"no-memory-{seed}"):
                        result = self._evaluate(self.state["recipes"][key])
                        if result["status"] == "complete" and key.startswith("seed-"):
                            self._admit(key)
                self._evaluate(self.state["recipes"]["coco-native"])
                options = self.config["evolution"]
                for ordinal in range(1, options["attempts"] + 1):
                    self.controller.require_running()
                    index = str(ordinal)
                    attempt = self.state["attempts"].get(index)
                    if attempt is not None and attempt["status"] in {"complete", "candidate_failed"}:
                        continue
                    entries = [self._entry(key) for key in self.state["pool"]]
                    if attempt is None:
                        parent = self._parent(entries, ordinal)
                        attempt = {"ordinal": ordinal, "parent_key": parent["key"], "status": "pending"}
                        self.state["attempts"][index] = attempt
                        self.save()
                    candidate_key = f"candidate-{ordinal:04d}"
                    recipe = self.state["recipes"].get(candidate_key)
                    if recipe is None:
                        if attempt["parent_key"] not in self.state["pool"]:
                            attempt.setdefault("parent_history", []).append(attempt["parent_key"])
                            attempt["parent_key"] = self._parent(entries, ordinal)["key"]
                            self.save()
                        parent = self._entry(attempt["parent_key"])
                        rotating = [row["paper_id"] for row in self.protocol.records("rotating")]
                        if "rotating_paper_ids" not in attempt:
                            sample = (self._draw("rotating", lambda rng: rng.sample(rotating, options["rotating_sample_size"]))
                                if "rng_streams" in self.state else random.Random(options["random_seed"] + ordinal).sample(rotating, options["rotating_sample_size"]))
                            attempt["rotating_paper_ids"] = sample
                            self.save()
                        sample = attempt["rotating_paper_ids"]
                        feedback_key = f"feedback-{ordinal:04d}-e{self.epoch}"
                        feedback_recipe = self._register(feedback_key, self.state["recipes"][parent["key"]]["candidate"],
                                                         kind="parent_feedback", split="rotating", paper_ids=sample)
                        feedback = self._evaluate(feedback_recipe)
                        attempt["feedback_key"] = feedback_key
                        if options.get("detailed_reflection_cases") is not None:
                            if "reflection_case_ids" not in attempt:
                                rows = self.backend.reflection_scores(feedback)
                                if set(row["paper_id"] for row in rows) != set(sample) or len(rows) != len(sample):
                                    raise ValueError("Reflection case choice requires all freshly evaluated rotating scores")
                                count = options["detailed_reflection_cases"]
                                def choose(rng):
                                    ranked = [(row["score"], rng.random(), row["paper_id"]) for row in sorted(rows, key=lambda x: x["paper_id"])]
                                    return [pid for _, _, pid in sorted(ranked)[:count]]
                                attempt["reflection_case_ids"] = self._draw("reflection", choose)
                                attempt["reflection_selection"] = {"rule": "lowest Paper., independent seeded tie-breaks", "scores": rows}
                            feedback = {**feedback, "reflection_case_ids": attempt["reflection_case_ids"],
                                        "reflection_selection": attempt["reflection_selection"]}
                        self.save()
                        try:
                            candidate = self.backend.propose(parent=parent, feedback=feedback, history=self.state["attempts"],
                                                             directory=self.root / "proposals" / f"a{ordinal}-e{self.epoch}")
                        except Exception as proposal_exc:
                            detail = failure_detail(proposal_exc, phase="shared_candidate_proposal")
                            if detail["category"] == "candidate":
                                candidate = {**self.state["recipes"][parent["key"]]["candidate"], "proposal_failure": detail}
                            else:
                                raise
                        candidate.update(candidate_id=candidate_key, parent_id=parent["candidate_id"], seed=parent["seed"], revision=0,
                                         round=ordinal, lineage=[*parent.get("lineage", []), parent["candidate_id"]])
                        recipe = self._register(candidate_key, candidate, kind="candidate")
                    result = self._evaluate(recipe)
                    if result["status"] == "complete":
                        self._admit(candidate_key)
                        self.controller.candidate_succeeded(candidate_id=candidate_key)
                    attempt["status"] = result["status"]
                    attempt["candidate_key"] = candidate_key
                    attempt["static_score"] = result["static_score"]
                    attempt["change"] = recipe["candidate"].get("change")
                    attempt["seed"] = recipe["candidate"]["seed"]
                    attempt["program_sha256"] = recipe["candidate"].get("program_sha256")
                    parent_code = self.state["recipes"][attempt["parent_key"]]["candidate"].get("program_sha256")
                    if parent_code and attempt["program_sha256"]:
                        attempt["duplicate_parent_code"] = parent_code == attempt["program_sha256"]
                    self.save()
                self.controller.require_running()
                for key, recipe in self.state["recipes"].items():
                    if recipe["split"] == "static" and self.state["results"].get(key, {}).get("status") != "candidate_failed":
                        self._validated(key)
                entries = [self._entry(key) for key in self.state["pool"]]
                winner = max(entries, key=lambda item: item["static_score"])
                frozen = self.backend.freeze(winner, self.state)
                epoch = self.epoch
                with self.controller.path.with_suffix(".lock").open("a+b") as policy_lock:
                    fcntl.flock(policy_lock.fileno(), fcntl.LOCK_EX)
                    self.controller.value = read_json(self.controller.path)
                    validate_promotion_policy(self.controller.value)
                    if self.epoch != epoch:
                        raise EvolutionPaused("Winner publication requires its verified comparison epoch")
                    atomic_write_json(self.root / f"winner.e{self.epoch}.json", frozen)
                    atomic_write_json(self.root / "winner.json", frozen, immutable=False)
                    self.state.update(status="complete", winner=frozen)
                    self.save()
            except EvolutionBudgetExhausted as exc:
                if self.controller.value["status"] != "ended":
                    self.controller.end(str(exc))
                self.state.update(status="ended", end_reason=str(exc))
                self.save()
                self._freeze_terminal()
            except EvolutionPaused:
                self.state["status"] = self.controller.value["status"]
                self._archive_winner()
                self.save()
            except Exception as exc:
                self._pause(exc, "shared_evolution")
            finally:
                try:
                    self.backend.write_report_input(self.state)
                except Exception as exc:
                    atomic_write_json(self.root / "report-index.failure.json", {"error": str(exc)}, immutable=False)
            return self.state
