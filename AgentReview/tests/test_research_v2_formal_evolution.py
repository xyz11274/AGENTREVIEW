import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import formal_evolution as formal_module
from agentreview.research_v2.evolution import initialize_pool
from agentreview.research_v2.formal_evolution import (
    _internal_evaluation_failure_score,
    _load_internal_evaluation_failure_score,
    materialize_role_attempt,
    record_candidate_failure,
    run_formal_evolution_roles,
    run_formal_role_evolution,
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

    def _formal_inputs(self, pool_path: Path):
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
        return summary, static, rotating, pool_path

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

    def test_candidate_failure_is_terminal_idempotent_and_allows_next_attempt(self):
        snapshots, reviews, evaluations = self._seed_inputs()
        pool_path = self.root / "pool.json"
        initialize_pool(
            role="QUAL",
            seed_static_evaluations=evaluations,
            seed_static_reviews=reviews,
            seed_memory_snapshots=snapshots,
            pool_path=pool_path,
        )
        summary, static, rotating, _ = self._formal_inputs(pool_path)
        work = materialize_role_attempt(
            role="QUAL",
            run_id="formal-qual-v1",
            attempt=1,
            pool_path=pool_path,
            canonical_evaluation_summary_path=summary,
            static_split_path=static,
            rotating_split_path=rotating,
            output_root=self.root,
        )
        root = self.root / "evolution" / "QUAL" / "formal-qual-v1"
        candidate_program = seed_program_path("vector_search").resolve()
        self._json(
            "evolution/QUAL/formal-qual-v1/attempt-001.json",
            {
                "status": "proposed",
                "candidate_id": "candidate-1",
                "parent_id": work["parent"]["candidate_id"],
                "program_path": str(candidate_program),
            },
        )
        first = record_candidate_failure(
            work_path=root / "attempt-001.work.json",
            pool_path=pool_path,
            output_root=self.root,
            phase="candidate_memory_build",
            error=TypeError("bad KnowledgeItem"),
        )
        second = record_candidate_failure(
            work_path=root / "attempt-001.work.json",
            pool_path=pool_path,
            output_root=self.root,
            phase="candidate_memory_build",
            error=TypeError("a replay must not replace the first failure"),
        )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "candidate_failed")
        next_work = materialize_role_attempt(
            role="QUAL",
            run_id="formal-qual-v1",
            attempt=2,
            pool_path=pool_path,
            canonical_evaluation_summary_path=summary,
            static_split_path=static,
            rotating_split_path=rotating,
            output_root=self.root,
        )
        self.assertEqual(next_work["attempt"], 2)

    def test_internal_evaluator_paper_failure_score_is_resumable_but_transport_is_not(self):
        review_path = self._json(
            "reviews/eval-run/paper/QUAL.json",
            {
                "review_id": "review-1",
                "paper_id": "paper",
                "role": "QUAL",
            },
        )
        parse_failure = self._json(
            "evaluations/eval-run/paper/QUAL.failure.json",
            {
                "paper_id": "paper",
                "role": "QUAL",
                "review_id": "review-1",
                "status": "failed",
                "errors": [
                    "JSONDecodeError: invalid JSON",
                    "ValueError: Internal Evaluator fields mismatch: bad",
                ],
                "review_quality": 1.0,
            },
        )
        score = _internal_evaluation_failure_score(
            output_root=self.root,
            run_id="eval-run",
            review_path=review_path,
            failure_path=parse_failure,
        )
        self.assertEqual(
            _load_internal_evaluation_failure_score(
                path=score,
                run_id="eval-run",
                review={"review_id": "review-1", "paper_id": "paper", "role": "QUAL"},
            ),
            score,
        )
        self.assertEqual(1.0, formal_module.read_json(score)["review_quality"])

        transport_failure = self._json(
            "evaluations/transport/paper/QUAL.failure.json",
            {
                "paper_id": "paper",
                "role": "QUAL",
                "review_id": "review-1",
                "status": "failed",
                "errors": ["APIConnectionError: endpoint unavailable"],
                "review_quality": 1.0,
            },
        )
        with self.assertRaisesRegex(RuntimeError, "must stop"):
            _internal_evaluation_failure_score(
                output_root=self.root,
                run_id="transport",
                review_path=review_path,
                failure_path=transport_failure,
            )

    def test_role_runner_propagates_frozen_identities_through_all_memory_builds(self):
        pool_path = (
            self.root / "evolution" / "QUAL" / "formal-v1" / "pool.json"
        )
        static_records = [
            {"paper_id": f"static-{index}", "path": f"/paper/{index}.json"}
            for index in range(25)
        ]
        work = {
            "role": "QUAL",
            "run_id": "formal-v1",
            "attempt": 1,
            "parent": {
                "program_path": str(seed_program_path("experience_learner")),
                "memory_snapshot_path": "/snapshot/parent.json",
                "program_version": "parent-v1",
            },
            "rotating_batch": {
                "paper_ids": [f"rotating-{index}" for index in range(5)],
                "paper_paths": [f"/rotating/{index}.json" for index in range(5)],
            },
            "artifact_run_ids": {
                "rotating_reviews": "rotating-review",
                "rotating_evaluations": "rotating-eval",
                "candidate_static_reviews": "candidate-static-review",
                "candidate_static_evaluations": "candidate-static-eval",
            },
        }
        build_calls = []

        def fake_build(**kwargs):
            build_calls.append(kwargs)
            return {}

        review_batches = []

        def fake_reviews(**kwargs):
            review_batches.append(kwargs)
            return [Path(f"/reviews/{index}.json") for index in range(len(kwargs["records"]))]

        def fake_evaluations(**kwargs):
            return [Path(f"/evaluations/{index}.json") for index in range(len(kwargs["records"]))]

        advance_calls = []

        def fake_advance(**kwargs):
            advance_calls.append(kwargs)
            if kwargs.get("candidate_memory_snapshot_path") is None:
                return {
                    "status": "awaiting_candidate_static",
                    "candidate_id": "candidate-1",
                    "program_path": "/candidate/program.py",
                    "seed_kind": "experience_learner",
                }
            coordinator = {
                "protocol": formal_module.FORMAL_ROLE_EVOLUTION_PROTOCOL,
                "role": "QUAL",
                "run_id": "formal-v1",
                "status": "complete",
                "attempts": {},
                "winner_id": "winner-1",
            }
            formal_module.atomic_write_json(
                self.root / "evolution/QUAL/formal-v1/coordinator.json",
                coordinator,
                immutable=False,
            )
            return {"status": "committed"}

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    formal_module,
                    "_preflight_formal_role_run",
                    return_value=(
                        {"sha256": "canonical"},
                        [Path("/canonical-review.json")],
                        [Path("/canonical-eval.json")],
                        static_records,
                    ),
                )
            )
            stack.enter_context(patch.object(formal_module, "MAX_MUTATION_ATTEMPTS", 1))
            stack.enter_context(
                patch.object(formal_module, "build_memory_snapshot", side_effect=fake_build)
            )
            stack.enter_context(patch.object(formal_module, "initialize_pool"))
            stack.enter_context(
                patch.object(formal_module, "materialize_role_attempt", return_value=work)
            )
            stack.enter_context(
                patch.object(formal_module, "_run_review_batch", side_effect=fake_reviews)
            )
            stack.enter_context(
                patch.object(
                    formal_module,
                    "_run_evaluation_batch",
                    side_effect=fake_evaluations,
                )
            )
            stack.enter_context(
                patch.object(formal_module, "advance_role_attempt", side_effect=fake_advance)
            )
            result = run_formal_role_evolution(
                role="QUAL",
                run_id="formal-v1",
                pool_path=pool_path,
                canonical_evaluation_summary_path="/canonical-summary.json",
                canonical_split_path="/canonical-split.json",
                static_split_path="/static-split.json",
                rotating_split_path="/rotating-split.json",
                frozen_s2_path="/s2.jsonl",
                output_root=self.root,
                completion_client=object(),
                model_identity={"model": "frozen-model"},
                embedding_client=object(),
                embedding_identity={"provider": "frozen-embedding"},
                max_workers=8,
                require_bubblewrap=True,
            )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(build_calls), 4)
        self.assertTrue(
            all(
                call["embedding_identity"] == {"provider": "frozen-embedding"}
                for call in build_calls
            )
        )
        experience_calls = [
            call for call in build_calls if call["seed_kind"] == "experience_learner"
        ]
        self.assertEqual(len(experience_calls), 2)
        self.assertTrue(
            all(call["completion_identity"] == {"model": "frozen-model"} for call in experience_calls)
        )
        self.assertEqual(len(review_batches), 5)
        self.assertEqual(len(advance_calls), 2)

    def test_all_role_runner_reuses_one_client_pair_and_canonical_pool_paths(self):
        completion_client = object()
        embedding_client = object()
        calls = []

        def fake_role(**kwargs):
            calls.append(kwargs)
            return {"status": "complete", "winner_id": f"winner-{kwargs['role']}"}

        with patch.object(
            formal_module,
            "run_formal_role_evolution",
            side_effect=fake_role,
        ):
            result = run_formal_evolution_roles(
                run_id="formal-v1",
                canonical_evaluation_summary_path="/canonical-summary.json",
                canonical_split_path="/canonical-split.json",
                static_split_path="/static-split.json",
                rotating_split_path="/rotating-split.json",
                frozen_s2_path="/s2.jsonl",
                output_root=self.root,
                completion_client=completion_client,
                model_identity={"model": "frozen-model"},
                embedding_client=embedding_client,
                embedding_identity={"provider": "frozen-embedding"},
                max_workers=8,
            )
        self.assertEqual(result["status"], "complete")
        self.assertEqual([call["role"] for call in calls], list(formal_module.ROLES))
        self.assertTrue(
            all(call["completion_client"] is completion_client for call in calls)
        )
        self.assertTrue(
            all(call["embedding_client"] is embedding_client for call in calls)
        )
        for call in calls:
            self.assertEqual(
                call["pool_path"],
                self.root
                / "evolution"
                / call["role"]
                / "formal-v1"
                / "pool.json",
            )

    def test_formal_review_batch_scopes_legacy_request_compatibility_to_bubblewrap(self):
        snapshot = self._json("formal-snapshot.json", {"snapshot_id": "snapshot-1"})
        calls = []

        def fake_review(**kwargs):
            calls.append(kwargs)

        arguments = {
            "records": [{"paper_id": "paper-1", "path": "/paper-1.json"}],
            "role": "QUAL",
            "program_path": "/program.py",
            "snapshot_path": snapshot,
            "program_version": "program-v1",
            "output_root": self.root,
            "completion_client": object(),
            "model_identity": {"model": "frozen-model"},
            "frozen_s2_path": "/s2.jsonl",
            "embedding_client": object(),
            "embedding_identity": {"provider": "frozen-embedding"},
            "max_workers": 1,
        }
        with patch.object(
            formal_module,
            "run_specialist_review",
            side_effect=fake_review,
        ):
            formal_module._run_review_batch(
                **arguments,
                run_id="formal-bubblewrap-true",
                require_bubblewrap=True,
            )
            formal_module._run_review_batch(
                **arguments,
                run_id="formal-bubblewrap-false",
                require_bubblewrap=False,
            )

        self.assertEqual(
            [
                call["_allow_legacy_missing_require_bubblewrap"]
                for call in calls
            ],
            [True, False],
        )


if __name__ == "__main__":
    unittest.main()
