import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.evolution import initialize_pool
from agentreview.research_v2.formal_evolution import (
    materialize_role_attempt,
    validate_canonical_evaluation_summary,
)
from agentreview.research_v2.io import (
    atomic_write_json,
    file_sha256,
    value_sha256,
)
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.program_checks import program_capability_manifest


class FormalEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _json(self, relative: str, value: dict) -> Path:
        path = self.root / relative
        atomic_write_json(path, value)
        return path

    def _seed_inputs(self):
        snapshots = {}
        reviews = {}
        evaluations = {}
        for seed_name in SEED_NAMES:
            program = seed_program_path(seed_name).resolve()
            version = f"qual-{seed_name}-formal-v1"
            payload = {
                "snapshot_protocol": "research-v2-memory-snapshot-v1",
                "role": "QUAL",
                "program_version": version,
                "program_path": str(program),
                "program_sha256": file_sha256(program),
                "seed_kind": seed_name,
                "source_review_ids": [],
                "source_evaluation_ids": [],
                "items": [],
                "snapshot": {},
                "capability_manifest": program_capability_manifest(path=program),
            }
            payload["snapshot_id"] = value_sha256(payload)
            snapshots[seed_name] = self._json(
                f"snapshots/{seed_name}.json", payload
            )
            seed_reviews = []
            seed_evaluations = []
            for index in range(2):
                review_id = f"{seed_name}-review-{index}"
                paper_id = f"paper-{index}"
                seed_reviews.append(
                    self._json(
                        f"reviews/{seed_name}-{index}.json",
                        {
                            "review_id": review_id,
                            "paper_id": paper_id,
                            "role": "QUAL",
                            "program_version": version,
                            "memory_snapshot_id": payload["snapshot_id"],
                            "review_text": "complete",
                        },
                    )
                )
                seed_evaluations.append(
                    self._json(
                        f"evaluations/{seed_name}-{index}.json",
                        {
                            "review_id": review_id,
                            "paper_id": paper_id,
                            "role": "QUAL",
                            "review_quality": 3.0 + index,
                        },
                    )
                )
            reviews[seed_name] = seed_reviews
            evaluations[seed_name] = seed_evaluations
        return snapshots, reviews, evaluations

    def test_seed_pool_binds_reviews_scores_and_snapshots_idempotently(self):
        snapshots, reviews, evaluations = self._seed_inputs()
        pool_path = self.root / "pool.json"
        first = initialize_pool(
            role="QUAL",
            seed_static_evaluations=evaluations,
            seed_static_reviews=reviews,
            seed_memory_snapshots=snapshots,
            pool_path=pool_path,
        )
        second = initialize_pool(
            role="QUAL",
            seed_static_evaluations=evaluations,
            seed_static_reviews=reviews,
            seed_memory_snapshots=snapshots,
            pool_path=pool_path,
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.to_dict()["protocol"], "research-v2-role-pool-v3")
        self.assertTrue(all(entry.has_memory_snapshot for entry in first.entries))

        wrong = dict(evaluations)
        wrong["vector_search"] = evaluations["llm_summarizer"]
        with self.assertRaisesRegex(ValueError, "do not match the Review set"):
            initialize_pool(
                role="QUAL",
                seed_static_evaluations=wrong,
                seed_static_reviews=reviews,
                seed_memory_snapshots=snapshots,
                pool_path=self.root / "wrong-pool.json",
            )

    def test_attempt_materialization_requires_complete_canonical_eval_and_is_stable(self):
        snapshots, reviews, evaluations = self._seed_inputs()
        pool_path = self.root / "pool.json"
        initialize_pool(
            role="QUAL",
            seed_static_evaluations=evaluations,
            seed_static_reviews=reviews,
            seed_memory_snapshots=snapshots,
            pool_path=pool_path,
        )
        summary = self._json(
            "canonical-summary.json",
            {
                "schema_version": "research-v2-internal-evaluation-batch-v1",
                "status": "complete",
                "task_count": 625,
                "completed": 625,
                "resumed": 0,
                "failed": 0,
                "run_id": "canonical-internal-eval-v1",
                "review_run_id": "canonical-bootstrap-v1",
            },
        )
        static = self._json(
            "static.json",
            {
                "count": 25,
                "split_sha256": "static",
                "records": [
                    {"paper_id": f"s-{index}", "path": f"/paper/s-{index}.json"}
                    for index in range(25)
                ],
            },
        )
        rotating = self._json(
            "rotating.json",
            {
                "count": 125,
                "split_sha256": "rotating",
                "records": [
                    {"paper_id": f"r-{index}", "path": f"/paper/r-{index}.json"}
                    for index in range(125)
                ],
            },
        )
        first = materialize_role_attempt(
            role="QUAL",
            run_id="formal-qual-v1",
            attempt=1,
            pool_path=pool_path,
            canonical_evaluation_summary_path=summary,
            static_split_path=static,
            rotating_split_path=rotating,
            output_root=self.root,
        )
        second = materialize_role_attempt(
            role="QUAL",
            run_id="formal-qual-v1",
            attempt=1,
            pool_path=pool_path,
            canonical_evaluation_summary_path=summary,
            static_split_path=static,
            rotating_split_path=rotating,
            output_root=self.root,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first["rotating_batch"]["paper_ids"],
            [f"r-{index}" for index in range(5)],
        )
        with self.assertRaisesRegex(ValueError, "terminal attempt 1"):
            materialize_role_attempt(
                role="QUAL",
                run_id="formal-qual-v1",
                attempt=2,
                pool_path=pool_path,
                canonical_evaluation_summary_path=summary,
                static_split_path=static,
                rotating_split_path=rotating,
                output_root=self.root,
            )

        incomplete = self._json(
            "incomplete-summary.json",
            {
                "schema_version": "research-v2-internal-evaluation-batch-v1",
                "status": "completed_with_failures",
                "task_count": 625,
                "completed": 624,
                "resumed": 0,
                "failed": 1,
            },
        )
        with self.assertRaisesRegex(ValueError, "625/625"):
            validate_canonical_evaluation_summary(incomplete)


if __name__ == "__main__":
    unittest.main()
