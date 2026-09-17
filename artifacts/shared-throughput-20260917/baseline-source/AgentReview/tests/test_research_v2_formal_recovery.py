import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import formal_evolution, formal_recovery
from agentreview.research_v2.evaluation import EvaluationFailure, INTERNAL_EVALUATOR_PROMPT_VERSION
from agentreview.research_v2.evolution import (
    EvolutionPool,
    PoolEntry,
    build_evaluation_receipt,
    validate_pool_evaluation_receipts,
)
from agentreview.research_v2.failure_policy import (
    EvolutionPaused,
    controller_for_run,
    validate_promotion_policy,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json, value_sha256
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.models import InternalEvaluation, Paper, SpecialistReview
from agentreview.research_v2.program_checks import program_capability_manifest
from agentreview.research_v2.review_preparation import ReviewPlanError


class NoModelClient:
    def complete(self, *args, **kwargs):
        raise AssertionError("Recovery tests use saved review and evaluation artifacts")

    def embed(self, *args, **kwargs):
        raise AssertionError("Recovery tests use deterministic snapshot fixtures")


class FormalRecoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.run_id = "recovery-test"
        self.model_identity = {"model": "test-model"}
        self.program = seed_program_path("vector_search").resolve()
        self.capabilities = program_capability_manifest(path=self.program)
        self.records = []
        for paper_id in ("p1", "p2"):
            paper = Paper(paper_id, "Test paper", "Abstract", "Complete paper text")
            path = self.write(f"inputs/{paper_id}.json", paper.to_dict())
            self.records.append({"paper_id": paper_id, "path": str(path)})
        self.static_split = self.write("inputs/static.json", {"records": self.records})
        self.evidence = self.write("repair-evidence.json", {
            "cause": "Evaluator endpoint returned invalid scoring responses.",
            "repair": "Corrected the endpoint configuration.",
            "validation": "Saved scoring-contract replay passed.",
        })
        self.arguments = {
            "output_root": self.output,
            "run_id": self.run_id,
            "repair_evidence_path": self.evidence,
            "canonical_evaluation_summary_path": self.write("inputs/canonical-evaluations.json", {}),
            "canonical_split_path": self.write("inputs/canonical.json", {"records": self.records}),
            "static_split_path": self.static_split,
            "rotating_split_path": self.write("inputs/rotating.json", {"records": self.records}),
            "frozen_s2_path": self.write("inputs/s2.json", {}),
            "completion_client": NoModelClient(),
            "model_identity": self.model_identity,
            "embedding_client": NoModelClient(),
            "embedding_identity": {"model": "test-embedding"},
            "max_workers": 1,
            "require_bubblewrap": False,
        }
        self.old_pools = {}
        self.old_champions = {}
        self.evaluated = []
        self.new_scores = {}

    def write(self, name, value):
        return atomic_write_json(self.output / name, value)

    def role_root(self, role):
        return self.output / "evolution" / role / self.run_id

    def policy(self):
        return controller_for_run(self.output, self.run_id).value

    def snapshot(self, *, role, version, output_path):
        value = {
            "snapshot_protocol": "research-v2-memory-snapshot-v1",
            "role": role, "program_version": version,
            "program_path": str(self.program), "program_sha256": file_sha256(self.program),
            "seed_kind": "vector_search", "source_review_ids": [], "source_evaluation_ids": [],
            "items": [], "snapshot": {}, "capability_manifest": self.capabilities,
        }
        value["snapshot_id"] = value_sha256(value)
        return atomic_write_json(output_path, value)

    def reviews(self, *, role, snapshot_path, version, run_id):
        snapshot_id = read_json(snapshot_path)["snapshot_id"]
        paths = []
        for record in self.records:
            paper_id = record["paper_id"]
            review = SpecialistReview(
                review_id=f"{run_id}-{paper_id}", paper_id=paper_id, role=role,
                program_version=version, memory_snapshot_id=snapshot_id,
                review_text="Complete grounded review.", raw_model_output="Complete grounded review.",
                reviewer_identity={"role": role}, model_identity=self.model_identity,
                generation_config={}, retrieval_provenance={}, run_id=run_id,
                created_at="2026-09-15T00:00:00Z",
            )
            paths.append(self.write(f"reviews/{run_id}/{paper_id}.json", review.to_dict()))
        return paths

    def evaluations(self, *, review_paths, run_id, score):
        paths = []
        for path in review_paths:
            review = read_json(path)
            evaluation = InternalEvaluation(
                evaluation_id=f"{run_id}-{review['paper_id']}", review_id=review["review_id"],
                paper_id=review["paper_id"], role=review["role"], review_quality=score,
                major_missing_points=[], factual_evidence_issues=[], improvement_suggestions=[],
                raw_model_output="Valid evaluation.", evaluator_identity=self.model_identity,
                prompt_version=INTERNAL_EVALUATOR_PROMPT_VERSION, run_id=run_id,
                created_at="2026-09-15T00:00:00Z",
            )
            paths.append(self.write(f"evaluations/{run_id}/{review['paper_id']}.json", evaluation.to_dict()))
        return paths

    def add_role(self, role, *, candidates=("first", "second"), complete=False):
        entries = []
        for index, name in enumerate(candidates):
            version = f"{role}-{name}-original"
            snapshot = self.snapshot(
                role=role, version=version, output_path=self.output / "original" / version / "snapshot.json",
            )
            reviews = self.reviews(role=role, snapshot_path=snapshot, version=version, run_id=version)
            score = 4.5 - index * 0.5
            evaluations = self.evaluations(review_paths=reviews, run_id=version + "-eval", score=score)
            receipt = build_evaluation_receipt(
                role=role, snapshot_path=snapshot, review_paths=reviews,
                evaluation_paths=evaluations, epoch=0,
                metadata=formal_evolution._evaluation_metadata(self.static_split),
            )
            entries.append(PoolEntry(
                candidate_id=name, role=role, program_path=str(self.program),
                program_sha256=file_sha256(self.program), parent_id=None,
                static_score=score, seed_kind="vector_search",
                memory_snapshot_path=str(snapshot), memory_snapshot_sha256=file_sha256(snapshot),
                memory_snapshot_id=read_json(snapshot)["snapshot_id"],
                capability_manifest=self.capabilities, evaluation_receipt=receipt,
            ))
            self.new_scores[f"{role}:{name}"] = 2.0 + index
        root = self.role_root(role)
        pool = EvolutionPool(role, entries)
        pool_path = atomic_write_json(root / "pool.json", pool.to_dict())
        self.old_pools[role] = pool_path.read_bytes()
        atomic_write_json(root / "run-spec.json", {"role": role, "epoch": 0})
        state = formal_evolution._state(root, role=role, run_id=self.run_id)
        if complete:
            state["attempts"] = {f"{index:03d}": {"status": "failed"} for index in range(1, 21)}
            formal_evolution._record_winner_if_complete(
                root=root, pool_source=pool_path, pool=pool, role=role,
                run_id=self.run_id, attempt=20, state=state,
            )
        formal_evolution._write_state(root, state)
        champion = asdict(pool.best(require_snapshot=True))
        controller_for_run(self.output, self.run_id).preserve_champion(role, champion)
        self.old_champions[role] = champion

    def pause(self):
        self.affected = formal_recovery.population_ids(formal_recovery.affected_population(self.output, self.run_id))
        controller_for_run(self.output, self.run_id).pause(
            operation_id="initial-shared-failure", detail={"category": "shared_evaluation"},
            affected=self.affected,
        )

    @contextmanager
    def recovery_artifacts(self, *, fail_at=None, before_evaluation=None):
        def preflight(**kwargs):
            root = self.role_root(kwargs["role"])
            epoch = kwargs.get("evaluation_epoch")
            if epoch is not None:
                atomic_write_json(root / f"run-spec.e{epoch:03d}.json", {"role": kwargs["role"], "epoch": epoch})
            return {"sha256": "validated-canonical-summary"}, [], [], self.records

        def build_snapshot(**kwargs):
            return self.snapshot(
                role=kwargs["role"], version=kwargs["program_version"], output_path=kwargs["output_path"],
            )

        def generate_reviews(**kwargs):
            self.assertEqual(kwargs["records"], self.records)
            return self.reviews(
                role=kwargs["role"], snapshot_path=kwargs["snapshot_path"],
                version=kwargs["program_version"], run_id=kwargs["run_id"],
            )

        def score_reviews(**kwargs):
            review = read_json(kwargs["review_paths"][0])
            version = review["program_version"]
            role = review["role"]
            name = version.removeprefix(f"{self.run_id}-{role.lower()}-").split("-recovery-e")[0]
            candidate = f"{role}:{name}"
            self.evaluated.append(candidate)
            if before_evaluation is not None:
                before_evaluation(candidate)
            if len(self.evaluated) == fail_at:
                raise EvaluationFailure({"failure_reason": "deterministic_repeat_limit", "attempt_count": 2})
            return self.evaluations(
                review_paths=kwargs["review_paths"], run_id=kwargs["run_id"], score=self.new_scores[candidate],
            )

        with ExitStack() as stack:
            stack.enter_context(patch.object(formal_evolution, "_preflight_formal_role_run", side_effect=preflight))
            stack.enter_context(patch.object(formal_recovery, "build_memory_snapshot", side_effect=build_snapshot))
            stack.enter_context(patch.object(formal_evolution, "_run_review_batch", side_effect=generate_reviews))
            stack.enter_context(patch.object(formal_evolution, "_run_evaluation_batch", side_effect=score_reviews))
            yield

    def assert_current_pools(self, epoch):
        for role in self.old_pools:
            pool = EvolutionPool.from_dict(read_json(self.role_root(role) / "pool.json"))
            cohort = validate_pool_evaluation_receipts(pool, expected_epoch=epoch)
            self.assertEqual(cohort["paper_ids"], ["p1", "p2"])
            for entry in pool.entries:
                self.assertEqual(entry.static_score, self.new_scores[f"{role}:{entry.candidate_id}"])
            self.assertEqual(self.policy()["champions"][role], asdict(pool.best(require_snapshot=True)))

    def test_all_candidates_share_one_epoch_before_any_pool_is_replaced(self):
        self.add_role("QUAL")
        self.add_role("CLAR", candidates=("first",))
        self.pause()

        def observe_gate(candidate):
            policy = self.policy()
            self.assertEqual(policy["status"], "reevaluating")
            self.assertEqual(policy["pending_candidates"], self.affected)
            self.assertEqual(policy["champions"], self.old_champions)
            with self.assertRaises(EvolutionPaused):
                validate_promotion_policy(policy)
            for role, original in self.old_pools.items():
                self.assertEqual((self.role_root(role) / "pool.json").read_bytes(), original)

        with self.recovery_artifacts(before_evaluation=observe_gate):
            formal_recovery.recover_formal_round(**self.arguments)
        self.assertCountEqual(self.evaluated, self.affected)
        self.assert_current_pools(epoch=1)
        policy = self.policy()
        self.assertEqual(policy["status"], "running")
        self.assertEqual(policy["pending_candidates"], [])
        self.assertEqual(policy["repair_budget"]["used"], 1)
        self.assertEqual(policy["champion_history"], [self.old_champions])
        validate_promotion_policy(policy)

    def test_second_candidate_failure_preserves_every_old_pool_and_pauses_public_runner(self):
        self.add_role("QUAL", candidates=("first",))
        self.add_role("CLAR", candidates=("first",))
        self.pause()
        with self.recovery_artifacts(fail_at=2):
            result = formal_evolution.run_formal_role_evolution(
                role="QUAL", pool_path=self.role_root("QUAL") / "pool.json", **self.arguments,
            )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(len(self.evaluated), 2)
        for role, original in self.old_pools.items():
            self.assertEqual((self.role_root(role) / "pool.json").read_bytes(), original)
            self.assertEqual(read_json(self.role_root(role) / "coordinator.json")["status"], "paused")
        policy = self.policy()
        self.assertEqual(policy["status"], "paused")
        self.assertEqual(policy["evaluation_epoch"], 1)
        self.assertEqual(policy["repair_budget"]["used"], 1)
        self.assertEqual(policy["pending_candidates"], self.affected)
        self.assertEqual(policy["champions"], self.old_champions)
        failures = [event for event in policy["events"].values() if "evaluation_failure" in event]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["category"], "shared_evaluation")
        self.assertEqual(failures[0]["evaluation_failure"]["attempt_count"], 2)
        with self.assertRaises(EvolutionPaused):
            validate_promotion_policy(policy)

    def test_completed_role_publishes_new_epoch_winner_and_archives_previous_winner(self):
        self.add_role("QUAL", complete=True)
        root = self.role_root("QUAL")
        previous = read_json(root / "winner.json")
        self.assertEqual(previous["winner"]["candidate_id"], "first")
        self.pause()
        with self.recovery_artifacts():
            result = formal_evolution.run_formal_role_evolution(
                role="QUAL", pool_path=root / "pool.json", **self.arguments,
            )
        self.assertEqual(result["status"], "complete")
        current = read_json(root / "winner.json")
        self.assertEqual(current["evaluation_epoch"], 1)
        self.assertEqual(current["winner"]["candidate_id"], "second")
        self.assertLess(current["winner"]["static_score"], previous["winner"]["static_score"])
        self.assertEqual(current["pool_sha256"], file_sha256(root / "pool.json"))
        self.assertEqual(current["winner_id"], result["winner_id"])
        self.assertEqual(read_json(root / "winner.e001.json"), current)
        archive = self.output / "runs" / self.run_id / "recovery-e001" / "QUAL" / "before"
        self.assertEqual(read_json(archive / "winner.json"), previous)
        self.assertEqual(read_json(root / "winner.e000.json"), previous)
        self.assert_current_pools(epoch=1)

    def test_interrupted_pool_commit_resumes_same_epoch_and_repair_reservation(self):
        self.add_role("QUAL", candidates=("first",))
        self.add_role("CLAR", candidates=("first",))
        self.pause()
        first_pool = self.role_root("QUAL") / "pool.json"

        def interrupt_after_first_pool(path, value, **kwargs):
            result = atomic_write_json(path, value, **kwargs)
            if Path(path) == first_pool:
                raise KeyboardInterrupt("Simulated process interruption after a durable pool write")
            return result

        with self.recovery_artifacts(), patch.object(
            formal_recovery, "atomic_write_json", side_effect=interrupt_after_first_pool,
        ):
            with self.assertRaises(KeyboardInterrupt):
                formal_recovery.recover_formal_round(**self.arguments)
        policy = self.policy()
        self.assertEqual(policy["status"], "reevaluating")
        self.assertEqual(policy["pending_candidates"], self.affected)
        self.assertEqual(policy["champions"], self.old_champions)
        with self.assertRaises(EvolutionPaused):
            validate_promotion_policy(policy)
        self.assertEqual(read_json(first_pool)["entries"][0]["evaluation_receipt"]["epoch"], 1)
        self.assertEqual((self.role_root("CLAR") / "pool.json").read_bytes(), self.old_pools["CLAR"])
        recovery_root = self.output / "runs" / self.run_id / "recovery-e001"
        frozen_population = (recovery_root / "population.json").read_bytes()
        prepared_pool_hashes = {role: file_sha256(recovery_root / role / "pool.json") for role in self.old_pools}
        with self.recovery_artifacts():
            formal_recovery.recover_formal_round(**self.arguments)
        self.assert_current_pools(epoch=1)
        self.assertEqual(self.policy()["status"], "running")
        self.assertEqual(self.policy()["pending_candidates"], [])
        self.assertEqual(self.policy()["evaluation_epoch"], 1)
        self.assertEqual(self.policy()["repair_budget"]["used"], 1)
        self.assertEqual((recovery_root / "population.json").read_bytes(), frozen_population)
        for role, old in self.old_pools.items():
            self.assertEqual((recovery_root / role / "before" / "pool.json").read_bytes(), old)
            self.assertEqual(file_sha256(recovery_root / role / "pool.json"), prepared_pool_hashes[role])

    def test_failed_pending_runtime_repair_is_disposed_once_across_recovery_restart(self):
        self.add_role("QUAL", candidates=("first",))
        self.add_role("CLAR", candidates=("first",))
        root = self.role_root("QUAL")
        candidate = "pending-child"
        program = self.output / "programs" / "QUAL" / candidate / "program.py"
        program.parent.mkdir(parents=True)
        program.write_bytes(self.program.read_bytes())
        proposal = {
            "protocol": "research-v2-evolution-attempt-v2", "run_id": self.run_id,
            "role": "QUAL", "attempt": 1, "candidate_id": candidate,
            "parent_id": "first", "status": "proposed", "program_path": str(program),
            "program_sha256": file_sha256(program), "capability_manifest": self.capabilities,
        }
        atomic_write_json(root / "attempt-001.json", proposal)
        work = {
            "protocol": formal_evolution.FORMAL_ATTEMPT_WORK_PROTOCOL,
            "role": "QUAL", "run_id": self.run_id, "attempt": 1,
            "pool_before": {"path": str(root / "pool.json"), "sha256": file_sha256(root / "pool.json")},
        }
        work["work_id"] = value_sha256(work)
        atomic_write_json(root / "attempt-001.work.json", work)
        state = formal_evolution._state(root, role="QUAL", run_id=self.run_id)
        state["attempts"]["001"] = {"status": "awaiting_candidate_static", "candidate_id": candidate}
        formal_evolution._write_state(root, state)
        controller = controller_for_run(self.output, self.run_id)
        with patch.object(formal_evolution, "_load_or_call_reflector", side_effect=ConnectionError("Endpoint unavailable")):
            with self.assertRaises(ConnectionError):
                formal_evolution._repair_runtime_candidate(
                    root=root, attempt=1, error=ReviewPlanError("plan_review requires valid evidence"),
                    phase="candidate_static_review", controller=controller,
                    completion_client=NoModelClient(), completion_identity=self.model_identity,
                    require_bubblewrap=False,
                )
        self.assertTrue((root / "attempt-001.runtime-repair-request.json").is_file())
        self.assertEqual(self.policy()["repair_budget"]["used"], 1)
        self.pause()
        self.assertIn("QUAL:pending-child", self.affected)
        committed_pool = root / "pool.json"

        def interrupt_after_disposal(path, value, **kwargs):
            result = atomic_write_json(path, value, **kwargs)
            if Path(path) == committed_pool:
                raise KeyboardInterrupt("Simulated interruption after candidate disposition and first pool write")
            return result

        with self.recovery_artifacts(), patch.object(
            formal_evolution, "_load_or_call_reflector", return_value="def incomplete(",
        ), patch.object(formal_recovery, "atomic_write_json", side_effect=interrupt_after_disposal):
            with self.assertRaises(KeyboardInterrupt):
                formal_recovery.recover_formal_round(**self.arguments)
        failure_path = root / "attempt-001.candidate-failure.json"
        failure = read_json(failure_path)
        self.assertEqual(failure["candidate_id"], candidate)
        self.assertEqual(read_json(root / "coordinator.json")["attempts"]["001"]["status"], "candidate_failed")
        self.assertEqual(self.policy()["candidate_failure_budget"]["used"], 1)
        self.assertEqual(self.policy()["status"], "reevaluating")
        self.assertEqual(self.policy()["champions"], self.old_champions)
        self.assertEqual(self.policy()["pending_candidates"], self.affected)
        self.assertEqual(read_json(committed_pool)["entries"][0]["evaluation_receipt"]["epoch"], 1)
        self.assertEqual((self.role_root("CLAR") / "pool.json").read_bytes(), self.old_pools["CLAR"])
        self.evaluated.clear()
        with self.recovery_artifacts(), patch.object(
            formal_evolution, "_load_or_call_reflector",
            side_effect=AssertionError("Disposed candidate retains its recorded repair result"),
        ):
            formal_recovery.recover_formal_round(**self.arguments)
        self.assertCountEqual(self.evaluated, ["QUAL:first", "CLAR:first"])
        self.assert_current_pools(epoch=1)
        policy = self.policy()
        self.assertEqual(policy["status"], "running")
        self.assertEqual(policy["pending_candidates"], [])
        self.assertEqual(policy["candidate_failure_budget"]["used"], 1)
        self.assertEqual(policy["repair_budget"]["used"], 2)
        self.assertEqual(policy["candidate_repairs"], {"QUAL:pending-child": 1})
        resolutions = read_json(self.output / "runs" / self.run_id / "recovery-e001" / "resolutions.json")
        self.assertEqual(set(resolutions), set(self.affected))
        self.assertEqual(resolutions["QUAL:pending-child"]["status"], "candidate_failed")
        self.assertEqual(resolutions["QUAL:first"]["status"], "complete")
        self.assertEqual(resolutions["CLAR:first"]["status"], "complete")
        self.assertEqual(read_json(failure_path), failure)
        self.assertEqual(read_json(root / "coordinator.json")["attempts"]["001"]["status"], "candidate_failed")
        self.assertEqual(
            [entry["candidate_id"] for entry in read_json(root / "pool.json")["entries"]], ["first"],
        )
        validate_promotion_policy(policy)


if __name__ == "__main__":
    unittest.main()
