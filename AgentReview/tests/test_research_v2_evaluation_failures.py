import json
import tempfile
import unittest
from pathlib import Path
from threading import Barrier, Event
from time import monotonic
from unittest.mock import patch

from agentreview.research_v2 import evaluation
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.evaluation_batch import EvaluationBatchOptions, run_evaluation_batch
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.models import Paper


VALID_OUTPUT = json.dumps({
    "review_quality": 4,
    "major_missing_points": [],
    "factual_evidence_issues": [],
    "improvement_suggestions": [],
})


class ScriptedClient:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    def complete(self, messages, *, max_tokens, component):
        self.calls += 1
        value = next(self.outcomes)
        if isinstance(value, BaseException):
            raise value
        return value


class EvaluationFailureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paper_path = self.root / "data" / "papers" / "paper.json"
        atomic_write_json(self.paper_path, Paper(
            paper_id="paper", title="Example", abstract="Abstract",
            text="Full paper.", source={"dataset": "test"},
        ).to_dict())
        atomic_write_json(self.root / "data" / "splits" / "canonical_review_corpus.json", {
            "records": [{"paper_id": "paper", "path": str(self.paper_path)}],
        })
        for role in ROLES:
            atomic_write_json(self.root / "reviews" / "reviews" / "paper" / f"{role}.json", {
                "paper_id": "paper", "role": role, "review_id": f"review-{role}",
                "review_text": "Complete review.", "retrieval_provenance": {},
            })
        self.review_path = self.root / "reviews" / "reviews" / "paper" / "QUAL.json"
        self.evaluation_dir = self.root / "evaluations" / "evaluation" / "paper"

    def run_one(self, client):
        return evaluation.run_internal_evaluation(
            paper_path=self.paper_path, review_path=self.review_path,
            run_id="evaluation", output_root=self.root,
            completion_client=client, evaluator_identity={"model": "fake"},
        )

    def assert_shared_failure(self, client):
        with self.assertRaises(RuntimeError) as caught:
            self.run_one(client)
        error = caught.exception
        self.assertEqual(getattr(error, "failure_category", None), "shared_evaluation")
        return error.failure_record

    def test_transient_retries_are_persisted_and_success_is_real(self):
        client = ScriptedClient([TimeoutError("timeout"), ConnectionError("reset"), VALID_OUTPUT])
        result = self.run_one(client)
        self.assertEqual(result["review_quality"], 4.0)
        self.assertEqual(client.calls, 3)
        self.assertEqual(read_json(self.evaluation_dir / "QUAL.raw.1.json")["status"], "failed")
        self.assertEqual(self.run_one(client), result)
        self.assertEqual(client.calls, 3)

    def test_repeated_parse_error_stops_without_fabricated_score_and_resume_calls(self):
        client = ScriptedClient(["bad output", "bad output", VALID_OUTPUT])
        failure = self.assert_shared_failure(client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(failure["failure_reason"], "repeated_deterministic_error")
        self.assertNotIn("review_quality", failure)
        self.assertFalse((self.evaluation_dir / "QUAL.json").exists())
        self.assertEqual(read_json(self.evaluation_dir / "QUAL.failure.json"), failure)
        self.assertEqual(self.assert_shared_failure(client), failure)
        self.assertEqual(client.calls, 2)

    def test_transient_budget_stays_exhausted_after_resume(self):
        client = ScriptedClient([TimeoutError("timeout")] * 4)
        failure = self.assert_shared_failure(client)
        self.assertEqual(client.calls, 3)
        self.assertEqual(failure["failure_reason"], "attempt_budget_exhausted")
        self.assertEqual(len(failure["attempts"]), 3)
        self.assertEqual(self.assert_shared_failure(client), failure)
        self.assertEqual(client.calls, 3)

    def test_interrupted_request_keeps_its_consumed_attempt(self):
        client = ScriptedClient([KeyboardInterrupt(), TimeoutError("timeout"), TimeoutError("timeout"), VALID_OUTPUT])
        with self.assertRaises(KeyboardInterrupt):
            self.run_one(client)
        failure = self.assert_shared_failure(client)
        self.assertEqual(client.calls, 3)
        self.assertEqual(len(failure["attempts"]), 3)
        self.assertEqual(failure["attempts"][0]["failure"]["error_type"], "InterruptedEvaluationAttempt")

    def test_different_parse_errors_can_recover_within_budget(self):
        client = ScriptedClient(["bad output", "[]", VALID_OUTPUT])
        self.assertEqual(self.run_one(client)["review_quality"], 4.0)
        self.assertEqual(client.calls, 3)

    def test_reserved_retry_policy_survives_changed_process_defaults(self):
        client = ScriptedClient([KeyboardInterrupt(), TimeoutError("timeout"), VALID_OUTPUT])
        with patch.object(evaluation, "EVALUATION_MAX_ATTEMPTS", 2):
            with self.assertRaises(KeyboardInterrupt):
                self.run_one(client)
        with patch.object(evaluation, "EVALUATION_MAX_ATTEMPTS", 5):
            failure = self.assert_shared_failure(client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(failure["retry_policy"]["max_attempts"], 2)

    def test_non_numeric_quality_is_a_parse_failure(self):
        payload = json.loads(VALID_OUTPUT)
        payload["review_quality"] = True
        raw = json.dumps(payload)
        client = ScriptedClient([raw, raw])
        failure = self.assert_shared_failure(client)
        self.assertEqual(failure["attempts"][0]["failure"]["stage"], "parse")

    def test_failure_classification_distinguishes_transport_and_request(self):
        class HTTPFailure(RuntimeError):
            def __init__(self, code):
                self.status_code = code
                super().__init__("endpoint failure")

        self.assertEqual(evaluation.evaluation_failure_detail(HTTPFailure(503))["kind"], "transient")
        self.assertEqual(evaluation.evaluation_failure_detail(HTTPFailure(400))["kind"], "deterministic")
        self.assertEqual(evaluation.evaluation_failure_detail(PermissionError("denied"))["kind"], "data")

    def test_invalid_input_is_shared_data_failure_without_model_call(self):
        self.paper_path.unlink()
        client = ScriptedClient([VALID_OUTPUT])
        failure = self.assert_shared_failure(client)
        self.assertEqual(failure["failure_reason"], "input_data_error")
        self.assertEqual(client.calls, 0)
        self.assertNotIn("review_quality", failure)

    def test_batch_pauses_before_dispatching_more_tasks(self):
        client = ScriptedClient(["bad output", "bad output", VALID_OUTPUT])
        options = EvaluationBatchOptions(review_run_id="reviews", run_id="evaluation", max_workers=1)
        result = run_evaluation_batch(
            output_root=self.root, options=options, completion_client=client,
            evaluator_identity={"model": "fake"},
        )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["failure_category"], "shared_evaluation")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["pending"], 4)
        self.assertEqual(client.calls, 2)
        run_evaluation_batch(
            output_root=self.root, options=options, completion_client=client,
            evaluator_identity={"model": "fake"},
        )
        self.assertEqual(client.calls, 2)

    def test_batch_missing_dataset_persists_paused_status(self):
        (self.root / "data" / "splits" / "canonical_review_corpus.json").unlink()
        client = ScriptedClient([VALID_OUTPUT])
        result = run_evaluation_batch(
            output_root=self.root,
            options=EvaluationBatchOptions(review_run_id="reviews", run_id="evaluation"),
            completion_client=client, evaluator_identity={"model": "fake"},
        )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["failure_category"], "shared_evaluation")
        self.assertEqual(client.calls, 0)
        self.assertEqual(read_json(self.root / "runs" / "evaluation" / "internal_evaluation.json"), result)

    def test_parallel_batch_drains_running_work_without_refilling(self):
        started = Barrier(2)
        peer_failed = Event()
        dispatched = []

        def evaluate(**kwargs):
            role = Path(kwargs["review_path"]).stem
            dispatched.append(role)
            started.wait(timeout=5)
            if role == ROLES[0]:
                peer_failed.set()
                raise evaluation.EvaluationFailure({"failure_reason": "repeated_deterministic_error"})
            self.assertTrue(peer_failed.wait(timeout=5))
            deadline = monotonic() + 5
            while not kwargs["stop_requested"]() and monotonic() < deadline:
                Event().wait(0.001)
            self.assertTrue(kwargs["stop_requested"]())
            return {"evaluation_id": f"evaluation-{role}"}

        with patch("agentreview.research_v2.evaluation_batch.run_internal_evaluation", side_effect=evaluate):
            result = run_evaluation_batch(
                output_root=self.root,
                options=EvaluationBatchOptions(review_run_id="reviews", run_id="evaluation", max_workers=2),
                completion_client=ScriptedClient([]), evaluator_identity={"model": "fake"},
            )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["pending"], 3)
        self.assertEqual(set(dispatched), set(ROLES[:2]))

    def test_running_peer_finishes_its_request_and_stops_its_retries(self):
        started = Barrier(2)
        failure_reported = Event()
        calls = []

        class PeerClient:
            def complete(inner, messages, *, max_tokens, component):
                calls.append(component)
                if component.endswith(ROLES[0]):
                    if calls.count(component) == 1:
                        started.wait(timeout=5)
                    return "bad output"
                started.wait(timeout=5)
                if not failure_reported.wait(timeout=5):
                    raise AssertionError("Peer failure must be reported while this request is running")
                raise TimeoutError("peer request timed out")

        def progress(event):
            if event["status"] == "failed":
                failure_reported.set()

        result = run_evaluation_batch(
            output_root=self.root,
            options=EvaluationBatchOptions(review_run_id="reviews", run_id="evaluation", max_workers=2),
            completion_client=PeerClient(), evaluator_identity={"model": "fake"}, progress=progress,
        )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["pending"], 3)


if __name__ == "__main__":
    unittest.main()
