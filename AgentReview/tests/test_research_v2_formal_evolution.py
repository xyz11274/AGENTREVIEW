import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import formal_evolution as formal_module
from agentreview.research_v2.evaluation import EvaluationFailure
from agentreview.research_v2.evolution import EvolutionPool, initialize_pool, validate_pool_evaluation_receipts
from agentreview.research_v2.formal_evolution import (
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
from agentreview.research_v2.models import InternalEvaluation, SpecialistReview
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
                        SpecialistReview(
                            review_id=review_id, paper_id=paper_id, role="QUAL",
                            program_version=version, memory_snapshot_id=payload["snapshot_id"],
                            review_text="Complete review.", raw_model_output="Complete review.",
                            reviewer_identity={"role": "QUAL"}, model_identity={"model": "test-reviewer"},
                            generation_config={}, retrieval_provenance={}, run_id=f"review-{seed_name}",
                            created_at="2026-09-15T00:00:00Z",
                        ).to_dict(),
                    )
                )
                seed_evaluations.append(
                    self._json(
                        f"evaluations/{seed_name}-{index}.json",
                        InternalEvaluation(
                            evaluation_id=f"eval-{review_id}", review_id=review_id,
                            paper_id=paper_id, role="QUAL", review_quality=3.0 + index,
                            major_missing_points=[], factual_evidence_issues=[], improvement_suggestions=[],
                            raw_model_output="Complete evaluation.", evaluator_identity={"model": "test-judge"},
                            prompt_version="internal-evaluator-v1", run_id=f"eval-{seed_name}",
                            created_at="2026-09-15T00:00:00Z",
                        ).to_dict(),
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

    def test_current_canonical_batch_protocol_requires_every_successful_task(self):
        tasks = [
            {"paper_id": f"p-{index}", "role": role, "status": "completed"}
            for index in range(125) for role in formal_module.ROLES
        ]
        value = {
            "schema_version": "research-v2-internal-evaluation-batch-v2", "status": "complete",
            "task_count": 625, "completed": 625, "resumed": 0, "failed": 0,
            "run_id": "current-eval", "review_run_id": "canonical", "task_results": tasks,
        }
        path = self._json("current-summary.json", value)
        self.assertEqual(validate_canonical_evaluation_summary(path)["run_id"], "current-eval")
        for changed in (tasks[:-1], [tasks[0], *tasks[:-1]]):
            atomic_write_json(path, {**value, "task_results": changed}, immutable=False)
            with self.assertRaisesRegex(ValueError, "complete successful task manifest"):
                validate_canonical_evaluation_summary(path)

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

    def test_formal_evaluation_failure_propagates_without_score_and_preserves_retry_budget(self):
        _, reviews, _ = self._seed_inputs()
        review_path = reviews["vector_search"][0]
        paper_path = self._json("paper.json", {
            "paper_id": "paper-0", "title": "Paper", "abstract": "Abstract", "text": "Full paper text.",
        })

        class FailingClient:
            def __init__(self, response):
                self.response = response
                self.calls = 0

            def complete(self, *args, **kwargs):
                self.calls += 1
                if isinstance(self.response, Exception):
                    raise self.response
                return self.response

        for run_id, response, expected_calls in (
            ("parse-failure", "invalid JSON", 2),
            ("transport-failure", TimeoutError("endpoint unavailable"), 3),
        ):
            with self.subTest(run_id=run_id):
                client = FailingClient(response)
                events = []
                arguments = dict(
                    records=[{"paper_id": "paper-0", "path": str(paper_path)}],
                    review_paths=[review_path], run_id=run_id, output_root=self.root,
                    completion_client=client, evaluator_identity={"model": "test-judge"},
                    max_workers=1, progress=events.append,
                )
                with self.assertRaises(EvaluationFailure):
                    formal_module._run_evaluation_batch(**arguments)
                self.assertEqual(client.calls, expected_calls)
                target = self.root / "evaluations" / run_id / "paper-0"
                failure = formal_module.read_json(target / "QUAL.failure.json")
                self.assertEqual(failure["status"], "paused")
                self.assertNotIn("review_quality", failure)
                self.assertFalse((target / "QUAL.json").exists())
                self.assertFalse(list(target.glob("*.paper_failure.json")))
                self.assertEqual(events[-1]["disposition"], "failed")
                with self.assertRaises(EvaluationFailure):
                    formal_module._run_evaluation_batch(**arguments)
                self.assertEqual(client.calls, expected_calls)

    def test_role_runner_propagates_frozen_identities_through_all_memory_builds(self):
        pool_path = self.root / "evolution" / "QUAL" / "formal-v1" / "pool.json"
        summary, static, rotating, _ = self._formal_inputs(pool_path)
        static_records = formal_module.read_json(static)["records"]
        build_calls, review_batches = [], []

        def fake_build(**kwargs):
            build_calls.append(kwargs)
            program = Path(kwargs["program_path"]).resolve()
            snapshot = {
                "snapshot_protocol": "research-v2-memory-snapshot-v1", "role": kwargs["role"],
                "program_version": kwargs["program_version"], "program_path": str(program),
                "program_sha256": file_sha256(program), "seed_kind": kwargs["seed_kind"],
                "source_review_ids": [], "source_evaluation_ids": [], "items": [], "snapshot": {},
                "capability_manifest": program_capability_manifest(path=program),
                "embedding_identity": kwargs["embedding_identity"],
            }
            snapshot["snapshot_id"] = value_sha256(snapshot)
            atomic_write_json(kwargs["output_path"], snapshot)
            return snapshot

        def fake_reviews(**kwargs):
            review_batches.append(kwargs)
            snapshot = formal_module.read_json(kwargs["snapshot_path"])
            paths = []
            for record in kwargs["records"]:
                paper_id, run_id = record["paper_id"], kwargs["run_id"]
                review = SpecialistReview(
                    review_id=f"{run_id}-{paper_id}", paper_id=paper_id, role=kwargs["role"],
                    program_version=kwargs["program_version"], memory_snapshot_id=snapshot["snapshot_id"],
                    review_text="Complete review.", raw_model_output="Complete review.",
                    reviewer_identity={"role": kwargs["role"]}, model_identity=kwargs["model_identity"],
                    generation_config={}, retrieval_provenance={}, run_id=run_id,
                    created_at="2026-09-15T00:00:00Z",
                ).to_dict()
                path = self.root / "reviews" / run_id / paper_id / f"{kwargs['role']}.json"
                atomic_write_json(path, review)
                paths.append(path)
            return paths

        def fake_evaluations(**kwargs):
            paths = []
            for review_path in kwargs["review_paths"]:
                review = formal_module.read_json(review_path)
                version = review["program_version"]
                score = 5.0 if "experience_learner" in version else 1.0
                evaluation = InternalEvaluation(
                    evaluation_id=f"{kwargs['run_id']}-{review['review_id']}",
                    review_id=review["review_id"], paper_id=review["paper_id"], role=review["role"],
                    review_quality=score, major_missing_points=[], factual_evidence_issues=[],
                    improvement_suggestions=[], raw_model_output="Complete evaluation.",
                    evaluator_identity=kwargs["evaluator_identity"], prompt_version="internal-evaluator-v1",
                    run_id=kwargs["run_id"], created_at="2026-09-15T00:00:00Z",
                ).to_dict()
                path = self.root / "evaluations" / kwargs["run_id"] / review["paper_id"] / f"{review['role']}.json"
                atomic_write_json(path, evaluation)
                paths.append(path)
            return paths

        def fake_propose(**kwargs):
            parent = kwargs["pool"].select_parent(seed=kwargs["attempt"])
            self.assertEqual(parent.seed_kind, "experience_learner")
            outcome = {
                "status": "proposed", "role": kwargs["role"], "candidate_id": "candidate-1",
                "parent_id": parent.candidate_id, "program_path": parent.program_path,
                "program_sha256": parent.program_sha256, "capability_manifest": parent.capability_manifest,
                "parent_capability_manifest": parent.capability_manifest,
            }
            target = self.root / "evolution" / "QUAL" / "formal-v1" / "attempt-001.json"
            atomic_write_json(target, outcome)
            return outcome

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                formal_module, "_preflight_formal_role_run",
                return_value=(
                    {"sha256": "canonical"}, [Path("/canonical-review.json")],
                    [Path("/canonical-eval.json")], static_records,
                ),
            ))
            stack.enter_context(patch.object(formal_module, "MAX_MUTATION_ATTEMPTS", 1))
            stack.enter_context(patch.object(formal_module, "build_memory_snapshot", side_effect=fake_build))
            stack.enter_context(patch.object(formal_module, "_run_review_batch", side_effect=fake_reviews))
            stack.enter_context(patch.object(formal_module, "_run_evaluation_batch", side_effect=fake_evaluations))
            stack.enter_context(patch.object(formal_module, "propose_candidate", side_effect=fake_propose))
            advance = stack.enter_context(patch.object(
                formal_module, "advance_role_attempt", wraps=formal_module.advance_role_attempt,
            ))
            result = run_formal_role_evolution(
                role="QUAL", run_id="formal-v1", pool_path=pool_path,
                canonical_evaluation_summary_path=summary,
                canonical_split_path="/canonical-split.json", static_split_path=static,
                rotating_split_path=rotating, frozen_s2_path="/s2.jsonl", output_root=self.root,
                completion_client=object(), model_identity={"model": "frozen-model"},
                embedding_client=object(), embedding_identity={"provider": "frozen-embedding"},
                max_workers=8, require_bubblewrap=True,
            )
        self.assertEqual(result["status"], "complete", result)
        pool = EvolutionPool.from_dict(formal_module.read_json(pool_path))
        self.assertEqual(len(pool.entries), 4)
        validate_pool_evaluation_receipts(pool, expected_epoch=0)
        self.assertTrue((pool_path.parent / "winner.json").is_file())
        self.assertEqual(len(build_calls), 4)
        self.assertTrue(all(call["embedding_identity"] == {"provider": "frozen-embedding"} for call in build_calls))
        experience_calls = [call for call in build_calls if call["seed_kind"] == "experience_learner"]
        self.assertEqual(len(experience_calls), 2)
        self.assertTrue(all(call["completion_identity"] == {"model": "frozen-model"} for call in experience_calls))
        self.assertTrue(all(call["completion_client"] is not None for call in experience_calls))
        self.assertTrue(all(call["completion_client"] is None for call in build_calls if call["seed_kind"] != "experience_learner"))
        self.assertEqual(len(review_batches), 5)
        self.assertEqual(advance.call_count, 2)

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
