import threading
import time
import unittest
from pathlib import Path

from memory_evolution.evaluation_orchestrator import run_ordered_evaluation_pipeline
from memory_evolution.evaluator import evaluate_program
from memory_evolution.models import EvalItem
from memory_evolution.toolkit import MemoryToolkit


class EvaluationPipelineTest(unittest.TestCase):
    def test_review_production_overlaps_scoring_and_commits_in_order(self):
        first_score_started = threading.Event()
        second_review_produced = threading.Event()

        def produce(item):
            if item == "B":
                self.assertTrue(first_score_started.wait(timeout=1.0))
                second_review_produced.set()
            return f"review-{item}"

        def score(review):
            if review == "review-A":
                first_score_started.set()
                self.assertTrue(second_review_produced.wait(timeout=1.0))
                time.sleep(0.01)
            return f"score-{review[-1]}"

        run = run_ordered_evaluation_pipeline(
            ["A", "B"],
            produce=produce,
            score=score,
            max_inflight=2,
        )

        self.assertEqual(run.results, ["score-A", "score-B"])
        self.assertEqual(run.stats["pipeline_version"], "ordered-frozen-review-v1")
        self.assertEqual(run.stats["score_workers"], 1)
        self.assertEqual(run.stats["max_inflight_configured"], 2)
        self.assertEqual(run.stats["max_inflight_observed"], 2)
        self.assertEqual(run.stats["reviews_produced_while_scoring"], 1)

    def test_depth_one_preserves_serial_execution(self):
        events = []

        run = run_ordered_evaluation_pipeline(
            ["A", "B"],
            produce=lambda item: events.append(f"produce-{item}") or item,
            score=lambda item: events.append(f"score-{item}") or item,
            max_inflight=1,
        )

        self.assertEqual(events, ["produce-A", "score-A", "produce-B", "score-B"])
        self.assertEqual(run.results, ["A", "B"])
        self.assertFalse(run.stats["enabled"])
        self.assertEqual(run.stats["reviews_produced_while_scoring"], 0)

    def test_invalid_depth_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            run_ordered_evaluation_pipeline(
                [], produce=lambda item: item, score=lambda item: item, max_inflight=0
            )

    def test_evaluator_wires_review_production_to_ordered_score_worker(self):
        first_score_started = threading.Event()
        second_review_produced = threading.Event()

        class Agent:
            @staticmethod
            def generate_query(program, text):
                return program.Query(
                    query_text="QUAL evidence",
                    reviewer_dimension="QUAL",
                )

            @staticmethod
            def generate_response(program, question, context):
                if question == "paper B":
                    if not first_score_started.wait(timeout=1.0):
                        raise TimeoutError("first score did not overlap paper B")
                    second_review_produced.set()
                return f"review-{question[-1]}"

        def metric(prediction, gold, item):
            if prediction == "review-A":
                first_score_started.set()
                if not second_review_produced.wait(timeout=1.0):
                    raise TimeoutError("paper B was not produced during scoring")
            return 1.0

        root = Path(__file__).parents[1]
        result = evaluate_program(
            str(root / "memory_evolution" / "seeds" / "vector_search.py"),
            [],
            [
                EvalItem(id="A::QUAL", question="paper A", answer="gold A"),
                EvalItem(id="B::QUAL", question="paper B", answer="gold B"),
            ],
            Agent(),
            MemoryToolkit(),
            metric,
            require_bubblewrap=False,
            evaluation_pipeline_depth=2,
        )

        self.assertIsNone(result.error)
        self.assertEqual(result.mean_score, 1.0)
        self.assertEqual(
            [trace["id"] for trace in result.traces if trace["phase"] == "read"],
            ["A::QUAL", "B::QUAL"],
        )
        self.assertEqual(
            result.runtime_stats["evaluation_pipeline"][
                "reviews_produced_while_scoring"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
