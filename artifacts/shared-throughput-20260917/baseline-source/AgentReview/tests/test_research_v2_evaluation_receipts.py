import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from agentreview.research_v2.evolution import (
    EvolutionPool,
    PoolEntry,
    build_evaluation_receipt,
    candidate_static_mean,
    commit_candidate,
    initialize_pool,
    static_mean,
    validate_pool_evaluation_receipts,
)
from agentreview.research_v2.freeze import _validate_completed_evolution
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json, value_sha256
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.models import InternalEvaluation, SpecialistReview
from agentreview.research_v2.program_checks import program_capability_manifest


class EvaluationReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, name, value):
        path = self.root / name
        atomic_write_json(path, value, immutable=False)
        return path

    def _candidate(self, name="candidate", *, papers=("p1", "p2"), epoch=0, score=4.0, seed="vector_search"):
        program = seed_program_path(seed).resolve()
        capabilities = program_capability_manifest(path=program)
        snapshot = {
            "snapshot_protocol": "research-v2-memory-snapshot-v1",
            "role": "QUAL", "program_version": name,
            "program_path": str(program), "program_sha256": file_sha256(program),
            "seed_kind": seed, "source_review_ids": [], "source_evaluation_ids": [],
            "items": [], "snapshot": {}, "capability_manifest": capabilities,
        }
        snapshot["snapshot_id"] = value_sha256(snapshot)
        snapshot_path = self._write(f"{name}/snapshot.json", snapshot)
        review_paths, evaluation_paths = [], []
        for paper in papers:
            review = SpecialistReview(
                review_id=f"{name}-{paper}", paper_id=paper, role="QUAL",
                program_version=name, memory_snapshot_id=snapshot["snapshot_id"],
                review_text="Complete grounded review.", raw_model_output="Complete grounded review.",
                reviewer_identity={"role": "QUAL"}, model_identity={"model": "test-model"},
                generation_config={}, retrieval_provenance={}, run_id=name,
                created_at="2026-09-15T00:00:00Z",
            ).to_dict()
            review_paths.append(self._write(f"{name}/{paper}-review.json", review))
            evaluation = InternalEvaluation(
                evaluation_id=f"eval-{name}-{paper}", review_id=review["review_id"],
                paper_id=paper, role="QUAL", review_quality=score,
                major_missing_points=[], factual_evidence_issues=[], improvement_suggestions=[],
                raw_model_output="Valid evaluation.", evaluator_identity={"model": "test-judge"},
                prompt_version="test-prompt-v1", run_id=f"eval-{name}",
                created_at="2026-09-15T00:00:00Z",
            ).to_dict()
            evaluation_paths.append(self._write(f"{name}/{paper}-eval.json", evaluation))
        receipt = build_evaluation_receipt(
            role="QUAL", snapshot_path=snapshot_path, review_paths=review_paths,
            evaluation_paths=evaluation_paths, epoch=epoch,
            metadata={"static_split_sha256": "frozen-split"},
        )
        entry = PoolEntry(
            candidate_id=name, role="QUAL", program_path=str(program),
            program_sha256=file_sha256(program), parent_id=None, static_score=score,
            seed_kind=seed, memory_snapshot_path=str(snapshot_path),
            memory_snapshot_sha256=file_sha256(snapshot_path), memory_snapshot_id=snapshot["snapshot_id"],
            capability_manifest=capabilities, evaluation_receipt=receipt,
        )
        return entry, review_paths, evaluation_paths

    def _score(self, entry, reviews, evaluations):
        return candidate_static_mean(
            role="QUAL", snapshot_path=entry.memory_snapshot_path,
            review_paths=reviews, evaluation_paths=evaluations,
        )

    def test_receipt_binds_complete_cohort_and_survives_pool_reload(self):
        entry, reviews, evaluations = self._candidate(epoch=2)
        pool = EvolutionPool("QUAL", [entry])
        restored = EvolutionPool.from_dict(pool.to_dict())
        cohort = validate_pool_evaluation_receipts(restored, expected_epoch=2)
        self.assertEqual(cohort["paper_ids"], ["p1", "p2"])
        self.assertEqual(restored.best(require_snapshot=True), entry)
        self.assertEqual(restored.select_parent(seed=7), entry)
        self.assertEqual(self._score(entry, reviews, evaluations), 4.0)
        with self.assertRaisesRegex(ValueError, "epoch is stale"):
            validate_pool_evaluation_receipts(restored, expected_epoch=3)

    def test_penalty_and_incomplete_success_artifacts_never_become_scores(self):
        entry, reviews, evaluations = self._candidate()
        original = read_json(evaluations[0])
        for update in (
            {"status": "paper_failure_score", "review_quality": 1.0},
            {"protocol": "research-v2-internal-evaluation-paper-failure-score-v1"},
            {"status": "failed"},
        ):
            with self.subTest(update=update):
                self._write(evaluations[0].relative_to(self.root), {**original, **update})
                with self.assertRaisesRegex(ValueError, "successful"):
                    self._score(entry, reviews, evaluations)
        broken = dict(original)
        broken.pop("evaluator_identity")
        self._write(evaluations[0].relative_to(self.root), broken)
        with self.assertRaisesRegex(ValueError, "valid InternalEvaluation"):
            self._score(entry, reviews, evaluations)
        self._write(evaluations[0].relative_to(self.root), original)
        review = read_json(reviews[0])
        self._write(reviews[0].relative_to(self.root), {**review, "review_text": ""})
        with self.assertRaisesRegex(ValueError, "valid SpecialistReview"):
            self._score(entry, reviews, evaluations)

    def test_nonfinite_and_boolean_scores_are_rejected(self):
        entry, reviews, evaluations = self._candidate()
        original = read_json(evaluations[0])
        for score in (float("nan"), float("inf"), True, "4.0"):
            with self.subTest(score=score):
                self._write(evaluations[0].relative_to(self.root), {**original, "review_quality": score})
                with self.assertRaises(ValueError):
                    self._score(entry, reviews, evaluations)
                with self.assertRaises(ValueError):
                    static_mean(evaluations)

    def test_missing_receipt_or_mutated_losing_candidate_blocks_selection(self):
        winner, _, _ = self._candidate("winner", score=5.0)
        loser, _, evaluations = self._candidate("loser", score=3.0)
        pool = EvolutionPool("QUAL", [winner, replace(loser, evaluation_receipt={})])
        with self.assertRaisesRegex(ValueError, "evaluation receipt"):
            pool.best(require_snapshot=True)
        partial = replace(loser, memory_snapshot_id="", evaluation_receipt={})
        with self.assertRaisesRegex(ValueError, "frozen Memory snapshot"):
            EvolutionPool("QUAL", [partial]).select_parent(seed=1)
        pool = EvolutionPool("QUAL", [winner, loser])
        changed = {**read_json(evaluations[0]), "raw_model_output": "Changed after scoring."}
        self._write(evaluations[0].relative_to(self.root), changed)
        with self.assertRaisesRegex(ValueError, "differs from its scoring artifacts"):
            pool.best(require_snapshot=True)
        with self.assertRaises(ValueError):
            pool.select_parent(seed=1)

    def test_mixed_epoch_cohort_and_judge_fail_before_pool_mutation(self):
        first, _, _ = self._candidate("first")
        for name, kwargs in (("epoch", {"epoch": 1}), ("cohort", {"papers": ("p1",)})):
            other, _, _ = self._candidate(name, **kwargs)
            pool = EvolutionPool("QUAL", [first])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "mixed evaluation"):
                pool.add(other)
            self.assertEqual(pool.entries, [first])
        other, reviews, evaluations = self._candidate("judge")
        for path in evaluations:
            self._write(path.relative_to(self.root), {**read_json(path), "prompt_version": "changed-prompt"})
        receipt = build_evaluation_receipt(
            role="QUAL", snapshot_path=other.memory_snapshot_path,
            review_paths=reviews, evaluation_paths=evaluations, metadata=first.evaluation_receipt["metadata"],
        )
        with self.assertRaisesRegex(ValueError, "mixed evaluation"):
            EvolutionPool("QUAL", [first]).add(replace(other, evaluation_receipt=receipt))

    def test_initialize_and_commit_write_matching_verified_receipts(self):
        snapshots, reviews, evaluations = {}, {}, {}
        for seed in SEED_NAMES:
            entry, reviews[seed], evaluations[seed] = self._candidate(seed, epoch=3, seed=seed)
            snapshots[seed] = entry.memory_snapshot_path
        pool_path = self.root / "pool.json"
        metadata = {"static_split_sha256": "frozen-split"}
        pool = initialize_pool(
            role="QUAL", seed_static_evaluations=evaluations, seed_static_reviews=reviews,
            seed_memory_snapshots=snapshots, pool_path=pool_path,
            evaluation_epoch=3, evaluation_metadata=metadata,
        )
        parent = pool.entries[0]
        child, child_reviews, child_evaluations = self._candidate("child", epoch=3, seed=parent.seed_kind)
        attempt_path = self._write("attempt.json", {
            "status": "proposed", "role": "QUAL", "candidate_id": "child", "parent_id": parent.candidate_id,
            "program_path": child.program_path, "program_sha256": child.program_sha256,
            "capability_manifest": child.capability_manifest, "parent_capability_manifest": parent.capability_manifest,
        })
        committed = commit_candidate(
            pool=pool, attempt_path=attempt_path, static_review_paths=child_reviews,
            static_evaluation_paths=child_evaluations, pool_path=pool_path,
            memory_snapshot_path=child.memory_snapshot_path, evaluation_epoch=3, evaluation_metadata=metadata,
        )
        self.assertEqual(committed.evaluation_receipt["epoch"], 3)
        restored = EvolutionPool.from_dict(read_json(pool_path))
        self.assertEqual(len(restored.entries), 4)
        validate_pool_evaluation_receipts(restored, expected_epoch=3, expected_metadata=metadata)

    def _completed_run(self):
        entry, _, _ = self._candidate()
        pool = EvolutionPool("QUAL", [entry])
        pool_path = self._write("evolution/QUAL/run/pool.json", pool.to_dict())
        winner = {
            "protocol": "research-v2-formal-role-winner-v1", "role": "QUAL", "run_id": "run",
            "pool_path": str(pool_path), "pool_sha256": file_sha256(pool_path), "winner": asdict(entry),
        }
        winner["winner_id"] = value_sha256(winner)
        self._write("evolution/QUAL/run/winner.json", winner)
        state = {
            "protocol": "research-v2-formal-role-evolution-v1", "role": "QUAL", "run_id": "run",
            "status": "complete", "winner_id": winner["winner_id"],
            "attempts": {f"{i:03d}": {"status": "committed"} for i in range(1, 21)},
            "failure_policy": {
                "protocol": "research-v2-failure-policy-v1", "status": "running", "evaluation_epoch": 0,
                "pending_candidates": [], "repair_budget": {"limit": 3, "used": 0},
                "candidate_failure_budget": {"limit": 3, "used": 0},
            },
        }
        self._write("evolution/QUAL/run/coordinator.json", state)
        return pool, pool_path, state

    def test_freeze_checks_policy_epoch_and_pending_candidates(self):
        pool, pool_path, state = self._completed_run()
        _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)
        for update in (
            {"status": "paused"}, {"status": "ended"}, {"pending_candidates": ["candidate"]},
            {"repair_budget": {"limit": 3, "used": 3}}, {"evaluation_epoch": 1},
        ):
            with self.subTest(update=update):
                invalid = {**state, "failure_policy": {**state["failure_policy"], **update}}
                self._write("evolution/QUAL/run/coordinator.json", invalid)
                with self.assertRaises((ValueError, RuntimeError)):
                    _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)
        state.pop("failure_policy")
        self._write("evolution/QUAL/run/coordinator.json", state)
        with self.assertRaisesRegex(ValueError, "failure policy"):
            _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)

    def test_live_policy_blocks_freeze_after_earlier_role_completed(self):
        pool, pool_path, state = self._completed_run()
        live_path = self._write("live-policy.json", state["failure_policy"])
        state["failure_policy_path"] = str(live_path)
        self._write("evolution/QUAL/run/coordinator.json", state)
        current = {**state["failure_policy"], "events": {"new-event": {"ok": True}}}
        self._write("live-policy.json", current)
        _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)
        self._write("live-policy.json", {**current, "status": "paused"})
        with self.assertRaisesRegex(RuntimeError, "ready round"):
            _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)
        self._write("live-policy.json", {**current, "evaluation_epoch": 1})
        with self.assertRaisesRegex(ValueError, "epoch is stale"):
            _validate_completed_evolution(role="QUAL", run_id="run", pool_path=pool_path, pool=pool)


if __name__ == "__main__":
    unittest.main()
