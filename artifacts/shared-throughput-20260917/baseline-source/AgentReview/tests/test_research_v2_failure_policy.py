import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from agentreview.research_v2.failure_policy import (
    EvolutionBudgetExhausted,
    EvolutionPaused,
    FailureController,
    FailureLimits,
    validate_promotion_policy,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json


class FailurePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "failure-policy.json"

    def controller(self, **limits):
        return FailureController(self.path, limits=FailureLimits(**limits))

    def evidence(self, **overrides):
        path = self.root / "repair-evidence.json"
        atomic_write_json(path, {
            "cause": "Evaluator responses violated the fixed JSON schema.",
            "repair": "Corrected endpoint configuration.",
            "validation": "Saved contract replay passed.",
            **overrides,
        }, immutable=False)
        return path

    def paused_controller(self):
        controller = self.controller()
        controller.pause(
            operation_id="shared-failure", detail={"category": "shared_evaluation"},
            affected=["QUAL:seed-vector", "CLAR:child-001"],
        )
        return controller

    def test_repair_reservation_is_durable_idempotent_and_keeps_original_limits(self):
        controller = self.controller(candidate_repairs=1, round_repairs=3)
        self.assertTrue(controller.reserve_repair(operation_id="repair-1", candidate_id="child-1"))
        saved = read_json(self.path)
        self.assertEqual(saved["repair_budget"], {"limit": 3, "used": 1})
        self.assertEqual(saved["candidate_repairs"], {"child-1": 1})
        resumed = self.controller(candidate_repairs=5, round_repairs=100)
        self.assertTrue(resumed.reserve_repair(operation_id="repair-1", candidate_id="child-1"))
        self.assertFalse(resumed.reserve_repair(operation_id="repair-2", candidate_id="child-1"))
        self.assertEqual(read_json(self.path), saved)

    def test_candidate_failure_replay_consumes_one_slot(self):
        controller = self.controller(candidate_failures=3)
        arguments = {"operation_id": "failed-child-1", "candidate_id": "child-1", "detail": {"category": "candidate"}}
        controller.candidate_failed(**arguments)
        resumed = self.controller()
        resumed.candidate_failed(**arguments)
        self.assertEqual(read_json(self.path)["candidate_failure_budget"], {"used": 1, "limit": 3})
        self.assertEqual(len(read_json(self.path)["events"]), 1)

    def test_repair_replay_requires_the_original_candidate(self):
        controller = self.controller()
        controller.reserve_repair(operation_id="repair-1", candidate_id="child-1")
        before = read_json(self.path)
        with self.assertRaises(ValueError):
            self.controller().reserve_repair(operation_id="repair-1", candidate_id="child-2")
        self.assertEqual(read_json(self.path), before)

    def test_candidate_failure_exhaustion_preserves_champion_across_restart(self):
        controller = self.controller(candidate_failures=2)
        champion = {"candidate_id": "champion", "static_score": 4.0, "program_sha256": "frozen-source"}
        controller.preserve_champion("QUAL", champion)
        controller.preserve_champion("QUAL", {"candidate_id": "worse", "static_score": 3.0})
        for number in (1, 2):
            controller.candidate_failed(
                operation_id=f"failure-{number}", candidate_id=f"child-{number}", detail={"category": "candidate"},
            )
        resumed = self.controller(candidate_failures=100)
        with self.assertRaises(EvolutionBudgetExhausted):
            resumed.require_running()
        saved = read_json(self.path)
        self.assertEqual(saved["status"], "ended")
        self.assertEqual(saved["candidate_failure_budget"], {"used": 2, "limit": 2})
        self.assertEqual(saved["champions"]["QUAL"], champion)
        with self.assertRaises((EvolutionPaused, EvolutionBudgetExhausted)):
            validate_promotion_policy(saved)

    def test_round_repair_exhaustion_preserves_champion_and_replay(self):
        controller = self.controller(round_repairs=2)
        champion = {"candidate_id": "champion", "static_score": 4.0}
        controller.preserve_champion("QUAL", champion)
        controller.reserve_repair(operation_id="repair-1", candidate_id="child-1")
        controller.reserve_repair(operation_id="repair-2", candidate_id="child-2")
        with self.assertRaises(EvolutionBudgetExhausted):
            controller.require_running()
        with self.assertRaises(EvolutionBudgetExhausted):
            self.controller().reserve_repair(operation_id="repair-3", candidate_id="child-3")
        resumed = self.controller()
        self.assertTrue(resumed.reserve_repair(operation_id="repair-2", candidate_id="child-2"))
        self.assertEqual(read_json(self.path)["repair_budget"], {"used": 2, "limit": 2})
        self.assertEqual(read_json(self.path)["champions"]["QUAL"], champion)

    def test_paused_and_pending_rounds_block_promotion(self):
        controller = self.paused_controller()
        with self.assertRaises(EvolutionPaused):
            controller.require_running()
        with self.assertRaises(EvolutionPaused):
            validate_promotion_policy(read_json(self.path))
        for status in ("running", "complete"):
            with self.subTest(status=status):
                value = {**read_json(self.path), "status": status}
                with self.assertRaises(EvolutionPaused):
                    validate_promotion_policy(value)

    def test_pause_replay_preserves_union_of_affected_candidates(self):
        controller = self.paused_controller()
        controller.pause(
            operation_id="shared-failure", detail={"category": "shared_evaluation"},
            affected=["QUAL:seed-vector", "ORIG:child-002"],
        )
        saved = read_json(self.path)
        self.assertEqual(saved["pending_candidates"], ["CLAR:child-001", "ORIG:child-002", "QUAL:seed-vector"])
        self.assertEqual(saved["repair_budget"]["used"], 0)
        self.assertEqual(saved["candidate_failure_budget"]["used"], 0)
        self.assertEqual(len(saved["events"]), 1)

    def test_recovery_requires_complete_concrete_evidence_before_budget(self):
        controller = self.paused_controller()
        before = deepcopy(read_json(self.path))
        for field in ("cause", "repair", "validation"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    controller.begin_recovery(repair_evidence_path=self.evidence(**{field: "  "}))
                self.assertEqual(read_json(self.path), before)
        with self.assertRaises(FileNotFoundError):
            controller.begin_recovery(repair_evidence_path=self.root / "missing.json")
        self.assertEqual(read_json(self.path), before)

    def test_running_round_requires_a_pause_before_recovery(self):
        controller = self.controller()
        before = read_json(self.path)
        with self.assertRaises(ValueError):
            controller.begin_recovery(repair_evidence_path=self.evidence())
        self.assertEqual(read_json(self.path), before)

    def test_recovery_epoch_and_repair_budget_survive_restart(self):
        controller = self.paused_controller()
        evidence = self.evidence()
        self.assertEqual(controller.begin_recovery(repair_evidence_path=evidence), 1)
        saved = read_json(self.path)
        self.assertEqual(saved["status"], "reevaluating")
        self.assertEqual(saved["repair_budget"]["used"], 1)
        self.assertEqual(
            {name: saved["repair_evidence"][name] for name in ("path", "sha256")},
            {"path": str(evidence.resolve()), "sha256": file_sha256(evidence)},
        )
        self.assertTrue(saved["repair_evidence"]["repair_id"])
        self.assertEqual(self.controller().begin_recovery(repair_evidence_path=evidence), 1)
        self.assertEqual(read_json(self.path), saved)
        with self.assertRaises(EvolutionPaused):
            validate_promotion_policy(saved)

    def test_recovery_rejects_changed_evidence_in_the_same_epoch(self):
        controller = self.paused_controller()
        evidence = self.evidence()
        controller.begin_recovery(repair_evidence_path=evidence)
        before = read_json(self.path)
        self.evidence(repair="A different unvalidated repair.")
        with self.assertRaises(ValueError):
            self.controller().begin_recovery(repair_evidence_path=evidence)
        self.assertEqual(read_json(self.path), before)

    def test_incomplete_or_unrelated_recovery_keeps_gate_closed(self):
        controller = self.paused_controller()
        controller.begin_recovery(repair_evidence_path=self.evidence())
        before = read_json(self.path)
        for completed in ([], ["QUAL:seed-vector"], ["QUAL:seed-vector", "OTHER:child-001"]):
            with self.subTest(completed=completed):
                with self.assertRaises(EvolutionPaused):
                    self.controller().finish_recovery(completed_candidates=completed)
                self.assertEqual(read_json(self.path), before)
        resumed = self.controller()
        resumed.finish_recovery(completed_candidates=["QUAL:seed-vector", "CLAR:child-001"])
        saved = read_json(self.path)
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["pending_candidates"], [])
        self.assertEqual(saved["evaluation_epoch"], 1)
        self.assertEqual(saved["repair_budget"]["used"], 1)
        validate_promotion_policy(saved)

    def test_second_pause_requires_new_repair_evidence_before_allocating_an_epoch(self):
        controller = self.paused_controller()
        evidence = self.evidence()
        controller.begin_recovery(repair_evidence_path=evidence)
        controller.finish_recovery(completed_candidates=controller.value["pending_candidates"])
        controller.pause(operation_id="second-failure", detail={"category": "shared_data"}, affected=["QUAL:seed-vector"])
        with self.assertRaisesRegex(ValueError, "new verified repair"):
            controller.begin_recovery(repair_evidence_path=evidence)
        self.assertEqual(read_json(self.path)["repair_budget"]["used"], 1)
        evidence = self.evidence(cause="A repaired paper asset requires rebuilding all affected memory snapshots.")
        self.assertEqual(controller.begin_recovery(repair_evidence_path=evidence), 2)
        self.assertEqual(read_json(self.path)["repair_budget"]["used"], 2)

    def test_reused_evidence_keeps_budget_when_copied_or_reformatted(self):
        controller = self.paused_controller()
        evidence = self.evidence()
        controller.begin_recovery(repair_evidence_path=evidence)
        controller.finish_recovery(completed_candidates=controller.value["pending_candidates"])
        controller.pause(
            operation_id="second-failure", detail={"category": "shared_evaluation"},
            affected=["QUAL:seed-vector"],
        )
        before = read_json(self.path)
        copied = self.root / "copied-repair.json"
        copied.write_bytes(evidence.read_bytes())
        reformatted = self.root / "reformatted-repair.json"
        values = read_json(evidence)
        reformatted.write_text(
            json.dumps(dict(reversed(list(values.items()))), separators=(",", ":")),
            encoding="utf-8",
        )
        self.assertNotEqual(file_sha256(reformatted), file_sha256(evidence))
        for alias in (copied, reformatted):
            with self.subTest(evidence=alias.name):
                with self.assertRaisesRegex(ValueError, "new verified repair"):
                    self.controller().begin_recovery(repair_evidence_path=alias)
                self.assertEqual(read_json(self.path), before)
                self.assertEqual(read_json(self.path)["repair_budget"]["used"], 1)
                self.assertEqual(read_json(self.path)["evaluation_epoch"], 1)

    def test_recovery_exhaustion_retains_prior_champion(self):
        controller = self.controller(round_repairs=1)
        champion = {"candidate_id": "verified", "static_score": 4.0}
        controller.preserve_champion("QUAL", champion)
        controller.pause(operation_id="shared", detail={"category": "shared_evaluation"}, affected=["QUAL:verified"])
        controller.begin_recovery(repair_evidence_path=self.evidence())
        with self.assertRaises(EvolutionBudgetExhausted):
            controller.finish_recovery(completed_candidates=["QUAL:verified"], champions={"QUAL": {"candidate_id": "new", "static_score": 5.0}})
        self.assertEqual(read_json(self.path)["status"], "ended")
        self.assertEqual(read_json(self.path)["champions"], {"QUAL": champion})

    def test_concurrent_controller_instances_preserve_budget_and_global_pause(self):
        first = self.controller(round_repairs=3)
        stale = self.controller()
        first.reserve_repair(operation_id="repair-1", candidate_id="child-1")
        stale.reserve_repair(operation_id="repair-2", candidate_id="child-2")
        saved = read_json(self.path)
        self.assertEqual(saved["repair_budget"]["used"], 2)
        self.assertEqual(set(saved["events"]), {"repair-1", "repair-2"})
        first.pause(operation_id="shared-failure", detail={"category": "shared_evaluation"}, affected=["QUAL:child-1"])
        with self.assertRaises(EvolutionPaused):
            stale.require_running()
        self.assertEqual(read_json(self.path)["status"], "paused")


if __name__ == "__main__":
    unittest.main()
