import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.assets import (
    audit_four_zone_isolation,
    load_temporal_split_manifest,
)
from memory_evolution.checkpoint import load_checkpoint, save_checkpoint
from memory_evolution.reflector import EvalFeedback, validate_candidate_feedback
from memory_evolution.evaluator import evaluate_program
from memory_evolution.models import Episode, EvalItem
from memory_evolution.sandbox import SandboxSession
from memory_evolution.toolkit import MemoryToolkit, validate_frozen_s2_pool


class TemporalProtocolTest(unittest.TestCase):
    def _identity(self):
        return {
            "candidate_id": "candidate-1",
            "lineage": "evolved_from_vector_search",
            "replicate_id": "replicate-1",
            "program_sha256": "a" * 64,
            "historical_manifest_sha256": "b" * 64,
            "experiment_config_sha256": "c" * 64,
            "s2_asset_sha256": "d" * 64,
            "s2_asset_version": "frozen-s2-temporal-v1",
            "run_seed": 17,
            "seed_control_available": False,
            "generation_config": {"model": "fixture"},
        }

    def test_checkpoint_requires_prefix_and_rejects_future_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            frozen = ["P1", "P2", "P3"]
            save_checkpoint(
                str(path),
                run_identity=self._identity(),
                frozen_ids=frozen,
                completed_ids=["P1"],
                memory_snapshot={"snapshot_version": "fixture", "state": [1]},
                paper_record={"paper_id": "P1", "completed": True},
            )
            loaded = load_checkpoint(
                str(path), expected_identity=self._identity(), frozen_ids=frozen
            )
            self.assertEqual(loaded["next_paper_index"], 1)
            with self.assertRaisesRegex(ValueError, "strict prefix"):
                save_checkpoint(
                    str(path),
                    run_identity=self._identity(),
                    frozen_ids=frozen,
                    completed_ids=["P1", "P3"],
                    memory_snapshot={"state": []},
                    paper_record={},
                )
            records = path.with_suffix(".json.records")
            (records / "999999_future.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "beyond the legal completed prefix"):
                load_checkpoint(
                    str(path), expected_identity=self._identity(), frozen_ids=frozen
                )

    def test_s2_future_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s2.jsonl"
            results = [{"paperId": "future", "publication_date": "2021-01-01"}]
            entry = {
                "paper_id": "target",
                "paper_review_cutoff": "2020-12-31",
                "retrieval_method": "fixture",
                "retrieval_version": "v1",
                "top_k": 10,
                "retrieval_config": {"top_k": 10},
                "retrieved_papers": results,
                "result_sha256": hashlib.sha256(
                    json.dumps(
                        results, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"target": "2020-12-31"},
                required_ids={"target"},
            )
            self.assertFalse(report["passed"])
            self.assertEqual(len(report["future_results"]), 1)

    def test_mutation_feedback_guard_is_fail_closed(self):
        feedback = EvalFeedback(
            static_score=0.5,
            rotating_score=0.4,
            failed_cases=[{"score": 0.0, "gold": "secret"}],
            success_cases=[],
            traces=[],
            task_description="fixture",
        )
        with self.assertRaisesRegex(ValueError, "forbidden"):
            validate_candidate_feedback(feedback)

    def test_real_frozen_split_passes_temporal_audit(self):
        root = Path(__file__).parents[1]
        _, zones = load_temporal_split_manifest(
            str(root / "configs" / "coco_temporal_split_v1" / "manifest.json")
        )
        report = audit_four_zone_isolation(zones)
        self.assertTrue(report["passed"])
        self.assertEqual(report["zone_counts"]["held_out_benchmark_test"], 900)

    def test_memory_snapshot_round_trip(self):
        root = Path(__file__).parents[1]
        toolkit = MemoryToolkit()
        program = root / "memory_evolution" / "seeds" / "experience_learner.py"
        first = SandboxSession(str(program), toolkit, require_bubblewrap=False)
        interface = first.program_interface()
        first.write(
            interface.KnowledgeItem(
                lesson_learned="check ablations",
                fact_to_remember="fixture",
                reviewer_name="Reviewer QUAL",
                reviewer_dimension="QUAL",
            ),
            "historical review",
        )
        snapshot = first.snapshot()
        first.close()
        second = SandboxSession(str(program), toolkit, require_bubblewrap=False)
        interface = second.program_interface()
        second.restore(snapshot)
        value = second.read(
            interface.Query(query_text="QUAL ablations", reviewer_dimension="QUAL")
        )
        second.close()
        self.assertIn("check ablations", value)

    def test_formal_replay_writes_A_and_keeps_B_read_only(self):
        class Agent:
            @staticmethod
            def generate_query(program, text):
                return program.Query(
                    query_text="QUAL prior experience",
                    reviewer_dimension="QUAL",
                )

            @staticmethod
            def generate_response(program, question, context):
                return "alpha"

            @staticmethod
            def extract_knowledge_item(program, raw_text):
                return program.KnowledgeItem(
                    lesson_learned="inspect ablations",
                    fact_to_remember="alpha",
                    reviewer_name="Reviewer QUAL",
                    reviewer_dimension="QUAL",
                )

        root = Path(__file__).parents[1]
        program = root / "memory_evolution" / "seeds" / "experience_learner.py"
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "history.json"
            result = evaluate_program(
                str(program),
                [
                    Episode(
                        id="P1::QUAL",
                        text="historical paper",
                        metadata={
                            "experience_protocol": "self_generated_after_review",
                            "experience_protocol_version": "self-generated-experience-v1",
                        },
                    )
                ],
                [
                    EvalItem(id="B1::QUAL", question="paper one", answer="alpha"),
                    EvalItem(id="B2::QUAL", question="paper two", answer="alpha"),
                ],
                Agent(),
                MemoryToolkit(),
                lambda prediction, gold: float(prediction == gold),
                require_bubblewrap=False,
                formal=True,
                checkpoint_path=str(checkpoint),
                run_identity=self._identity(),
            )
            self.assertIsNone(result.error)
            self.assertEqual(result.runtime_stats["completed_historical_prefix"], ["P1::QUAL"])
            self.assertTrue(result.runtime_stats["validation_state_reset_per_item"])
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(payload["next_paper_index"], 1)
            legacy = evaluate_program(
                str(program),
                [Episode(id="P1::QUAL", text="legacy", metadata={})],
                [],
                Agent(),
                MemoryToolkit(),
                lambda prediction, gold: 1.0,
                require_bubblewrap=False,
                formal=True,
                checkpoint_path=str(Path(directory) / "legacy.json"),
                run_identity=self._identity(),
            )
            self.assertIn("legacy experience protocol", legacy.error)


if __name__ == "__main__":
    unittest.main()
