import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import test_research_v2_evaluation_receipts as receipt_fixtures
from agentreview.research_v2 import formal_evolution as formal
from agentreview.research_v2.evolution import EvolutionPool
from agentreview.research_v2.failure_policy import (
    EvolutionPaused, FailureController, FailureLimits, failure_detail,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json
from agentreview.research_v2.review_preparation import ReviewPlanError


class FailClosedEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = receipt_fixtures.EvaluationReceiptTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.output = self.fixture.root
        self.root = self.output / "evolution" / "QUAL" / "run"
        self.controller = FailureController(self.output / "runs" / "run" / "failure-policy.json")
        self.entry, _, _ = self.fixture._candidate("champion")
        self.pool = EvolutionPool("QUAL", [self.entry], str(self.controller.path))
        self.pool_path = self.root / "pool.json"
        atomic_write_json(self.pool_path, self.pool.to_dict())
        self.controller.preserve_champion("QUAL", asdict(self.entry))

    def state(self):
        value = formal._state(self.root, role="QUAL", run_id="run")
        value["attempts"] = {f"{i:03d}": {"status": "failed"} for i in range(1, 21)}
        return value

    def publish(self, state):
        formal._record_winner_if_complete(
            root=self.root, pool_source=self.pool_path, pool=self.pool,
            role="QUAL", run_id="run", attempt=20, state=state,
        )

    def test_round_exhaustion_retains_champion_and_creates_zero_winners(self):
        before = read_json(self.controller.path)["champions"]
        self.controller.end("repair_budget")
        state = self.state()
        self.publish(state)
        self.assertEqual(state["status"], "ended")
        self.assertEqual(read_json(self.controller.path)["champions"], before)
        self.assertEqual(list(self.root.glob("winner*.json")), [])

    def test_last_attempt_number_alone_provides_zero_publication_authority(self):
        state = self.state()
        state["attempts"]["012"] = {"status": "awaiting_candidate_static"}
        with self.assertRaisesRegex(ValueError, "complete terminal"):
            self.publish(state)
        self.assertEqual(list(self.root.glob("winner*.json")), [])
        state["attempts"]["012"] = {"status": "failed"}
        self.publish(state)
        self.assertEqual(read_json(self.root / "winner.json")["winner"]["candidate_id"], "champion")

    def test_loaded_pool_stops_selection_when_another_controller_pauses_round(self):
        self.assertEqual(self.pool.select_parent(seed=1).candidate_id, "champion")
        FailureController(self.controller.path).pause(
            operation_id="shared", detail={"category": "shared_evaluation"},
            affected=["QUAL:champion"],
        )
        for pool in (self.pool, EvolutionPool.from_dict(read_json(self.pool_path))):
            with self.assertRaises(EvolutionPaused):
                pool.select_parent(seed=1)
            with self.assertRaises(EvolutionPaused):
                pool.best(require_snapshot=True)

    def test_review_contract_failure_is_persisted_and_replay_consumes_zero_requests(self):
        snapshot = Path(self.entry.memory_snapshot_path)
        arguments = dict(
            records=[{"paper_id": "p1", "path": "/paper.json"}], role="QUAL",
            program_path=self.entry.program_path, snapshot_path=snapshot,
            program_version="champion", run_id="review-run", output_root=self.output,
            completion_client=object(), model_identity={"model": "test"},
            frozen_s2_path="/frozen.jsonl", embedding_client=object(),
            embedding_identity={"model": "embed"}, max_workers=1, require_bubblewrap=False,
        )
        with patch.object(formal, "run_specialist_review", side_effect=ReviewPlanError("Review Plan contract violation")) as generate:
            with self.assertRaises(ReviewPlanError):
                formal._run_review_batch(**arguments)
            with self.assertRaises(formal.RecordedStageFailure) as failure:
                formal._run_review_batch(**arguments)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(failure_detail(failure.exception, phase="candidate_execution")["category"], "candidate")
        saved = read_json(self.output / "reviews/review-run/p1/QUAL.stage-failure.json")
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("review_quality", saved)
        self.assertEqual(list(self.output.rglob("*.paper_failure.json")), [])

    def test_shared_failure_stops_all_roles_and_resume_waits_for_repair(self):
        arguments = dict(
            run_id="run", canonical_evaluation_summary_path="/summary.json",
            canonical_split_path="/canonical.json", static_split_path="/static.json",
            rotating_split_path="/rotating.json", frozen_s2_path="/s2.json",
            output_root=self.output, completion_client=object(), model_identity={"model": "test"},
            embedding_client=object(), embedding_identity={"model": "embed"},
        )
        with patch.object(formal, "_run_formal_role_evolution", side_effect=ConnectionError("endpoint down")) as run:
            first = formal.run_formal_evolution_roles(**arguments)
            second = formal.run_formal_evolution_roles(**arguments)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first["status"], "paused")
        self.assertEqual(second["status"], "paused")
        self.assertEqual(read_json(self.controller.path)["pending_candidates"], ["QUAL:champion"])
        self.assertEqual(read_json(self.pool_path)["entries"][0]["candidate_id"], "champion")

    def test_bounded_scheduler_stops_submissions_on_first_error(self):
        started = []
        def execute(number):
            started.append(number)
            raise ConnectionError("shared service unavailable")
        with self.assertRaises(ConnectionError):
            formal._bounded_batch(list(range(100)), execute, max_workers=1)
        self.assertEqual(started, [0])

    def test_error_scope_uses_contract_evidence(self):
        self.assertEqual(failure_detail(TimeoutError("service"), phase="candidate_memory_build")["category"], "shared_runtime")
        self.assertEqual(failure_detail(TypeError("host bug"), phase="candidate_memory_build")["category"], "shared_runtime")
        self.assertEqual(failure_detail(FileNotFoundError("paper"), phase="candidate_review")["category"], "shared_data")
        self.assertEqual(failure_detail(ReviewPlanError("schema"), phase="candidate_review")["category"], "candidate")

    def test_runtime_repair_resumes_saved_request_and_consumes_one_candidate_slot(self):
        original = self.output / "programs" / "QUAL" / "runtime-child" / "program.py"
        original.parent.mkdir(parents=True)
        original.write_text(Path(self.entry.program_path).read_text())
        original_hash = file_sha256(original)
        atomic_write_json(self.root / "attempt-001.json", {
            "role": "QUAL", "status": "proposed", "candidate_id": "runtime-child",
            "parent_id": "champion", "program_path": str(original),
            "program_sha256": original_hash, "capability_manifest": self.entry.capability_manifest,
        })
        corrected = original.read_text() + "\n# Complete corrected revision.\n"
        calls = []
        class Completion:
            def complete(self, messages, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    raise ConnectionError("Endpoint paused during candidate repair")
                return corrected
        arguments = dict(
            root=self.root, attempt=1, error=ReviewPlanError("Review Plan contract violation"),
            phase="candidate_static_review", controller=self.controller,
            completion_client=Completion(), completion_identity={"model": "test"},
            require_bubblewrap=False,
        )
        with patch.object(formal, "validate_program", return_value=[]):
            with self.assertRaises(ConnectionError):
                formal._repair_runtime_candidate(**arguments)
            saved_request = read_json(self.root / "attempt-001.runtime-repair-request.json")
            self.assertTrue(formal._repair_runtime_candidate(**arguments))
            self.assertFalse(formal._repair_runtime_candidate(**arguments))
        self.assertEqual(calls, [saved_request["messages"], saved_request["messages"]])
        self.assertEqual(read_json(self.controller.path)["repair_budget"]["used"], 1)
        self.assertEqual(file_sha256(original), original_hash)
        _, outcome = formal._effective_attempt(self.root, 1)
        self.assertEqual(Path(outcome["program_path"]).read_text(), corrected)
        self.assertEqual(outcome["runtime_repair"]["revision"], 1)
        self.assertEqual(list(self.output.rglob("winner*.json")), [])


if __name__ == "__main__":
    unittest.main()
