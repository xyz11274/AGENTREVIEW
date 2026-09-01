import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentreview.research_v2 import cli as cli_module
from agentreview.research_v2.cli import (
    _diagnostic_s2_intervention,
    _run_evolution_spec,
    _spec_s2_intervention,
)
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.evolution import (
    EvolutionPool,
    PoolEntry,
    _reflector_feedback_view,
    _reflector_prompt,
    feedback_packet,
    initialize_pool,
)
from agentreview.research_v2.freeze import (
    _validate_memory_winner_identity,
    freeze_system,
)
from agentreview.research_v2.io import (
    atomic_write_json,
    file_sha256,
    read_json,
    value_sha256,
)
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
)


class EvolvableWinnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _json(self, relative: str, value: dict) -> Path:
        path = self.root / relative
        atomic_write_json(path, value)
        return path

    def test_pool_is_backward_readable_and_new_entries_bind_capabilities(self):
        program = seed_program_path("vector_search").resolve()
        legacy = EvolutionPool.from_dict(
            {
                "protocol": "research-v2-role-pool-v1",
                "role": "QUAL",
                "entries": [
                    {
                        "candidate_id": "legacy",
                        "role": "QUAL",
                        "program_path": str(program),
                        "program_sha256": file_sha256(program),
                        "parent_id": None,
                        "static_score": 3.0,
                        "seed_kind": "vector_search",
                    }
                ],
            }
        )
        self.assertEqual(legacy.entries[0].capability_manifest, {})
        self.assertEqual(legacy.best().candidate_id, "legacy")

        evaluations = {}
        for seed_name in SEED_NAMES:
            evaluations[seed_name] = [
                self._json(
                    f"evaluation-{seed_name}.json",
                    {"review_quality": 3.5},
                )
            ]
        pool_path = self.root / "pool.json"
        pool = initialize_pool(
            role="QUAL",
            seed_static_evaluations=evaluations,
            pool_path=pool_path,
        )
        self.assertEqual(read_json(pool_path)["protocol"], "research-v2-role-pool-v2")
        for entry in pool.entries:
            self.assertEqual(
                entry.capability_manifest,
                program_capability_manifest(path=entry.program_path),
            )
            self.assertEqual(
                entry.capability_manifest["interface_version"],
                REVIEW_PLAN_PROTOCOL,
            )

    def test_feedback_preserves_memory_and_complete_s2_decision_trace(self):
        program = seed_program_path("vector_search").resolve()
        parent = PoolEntry(
            candidate_id="seed-vector_search",
            role="QUAL",
            program_path=str(program),
            program_sha256=file_sha256(program),
            parent_id=None,
            static_score=3.0,
            seed_kind="vector_search",
            capability_manifest=program_capability_manifest(path=program),
        )
        memory_trace = {
            "query": {"query_text": "paper"},
            "logs": [{"event": "retrieval_hits", "value": [{"id": "m1"}]}],
            "context": "memory context",
            "seed_kind": "vector_search",
        }
        evidence = [
            {
                "paperId": "s2-1",
                "title": "Prior work",
                "abstract": "Frozen evidence.",
            }
        ]
        candidate_evidence = [
            *evidence,
            {"paperId": "s2-2", "title": "Dropped candidate"},
        ]
        s2_trace = {
            "capability_available": True,
            "intervention": {
                "protocol": "s2-intervention-v1",
                "mode": "program_autonomous",
                "label": "formal-primary-v1",
            },
            "s2_disposition": "use",
            "decision_label": "novelty-check",
            "lookup_count": 1,
            "requested_limits": [5],
            "candidate_ids": ["s2-1", "s2-2"],
            "selected_ids": ["s2-1"],
            "injected_ids": ["s2-1"],
            "dropped_ids": ["s2-2"],
            "usage_instruction": "Check novelty and cite S2 IDs.",
            "context_order": ["memory", "s2", "strategy"],
            "review_strategy": "Ground claims in the current paper.",
            "evidence": evidence,
            "candidate_evidence": candidate_evidence,
            "pool_path": "/frozen/s2.jsonl",
            "pool_sha256": "pool-hash",
            "pool_version": "pool-v2",
        }
        review_path = self._json(
            "review.json",
            {
                "review_id": "review-1",
                "paper_id": "paper-1",
                "role": "QUAL",
                "review_text": "Complete review.",
                "retrieval_provenance": {
                    "protocol": "retrieval-provenance-v2",
                    "memory": memory_trace,
                    "s2": s2_trace,
                    "assembly": {"prompt_sha256": "prompt-hash"},
                },
            },
        )
        evaluation_path = self._json(
            "evaluation.json",
            {
                "review_id": "review-1",
                "paper_id": "paper-1",
                "role": "QUAL",
                "review_quality": 2.0,
                "major_missing_points": ["missing"],
                "factual_evidence_issues": [],
                "improvement_suggestions": ["improve"],
            },
        )
        packet = feedback_packet(
            parent=parent,
            evaluation_paths=[evaluation_path],
            review_paths=[review_path],
        )
        case = packet["rotating_failure_cases"][0]
        self.assertEqual(case["memory_trace"], memory_trace)
        self.assertEqual(case["s2_trace"], s2_trace)
        self.assertEqual(case["s2_candidates"]["evidence"], candidate_evidence)
        self.assertEqual(case["s2_selected_ids"], ["s2-1"])
        self.assertEqual(case["s2_selected_evidence"], evidence)
        self.assertEqual(case["s2_injected_ids"], ["s2-1"])
        self.assertEqual(case["review_plan"]["decision_label"], "novelty-check")
        view = _reflector_feedback_view(packet)
        rendered_case = view["rotating_failure_cases"][0]
        self.assertEqual(view["parent"], packet["parent"])
        self.assertEqual(rendered_case["complete_review"], "Complete review.")
        self.assertEqual(rendered_case["retrieval_trace"]["memory"], memory_trace)
        self.assertEqual(rendered_case["retrieval_trace"]["s2"], s2_trace)
        self.assertEqual(
            rendered_case["retrieval_trace"]["assembly"],
            {"prompt_sha256": "prompt-hash"},
        )
        self.assertNotIn("memory_trace", rendered_case)
        self.assertNotIn("s2_trace", rendered_case)
        prompt = _reflector_prompt("PROGRAM = True", packet)
        self.assertIn("research-v2-reflector-feedback-view-v1", prompt)
        self.assertNotIn('"memory_trace":', prompt)
        self.assertNotIn('"s2_trace":', prompt)

    def test_cli_host_switch_is_rejected_and_no_s2_is_named_diagnostic(self):
        with self.assertRaisesRegex(ValueError, "legacy host policy keys"):
            _spec_s2_intervention({"s2_enabled": False})
        with self.assertRaisesRegex(ValueError, "legacy host policy keys"):
            _spec_s2_intervention({"review_strategy": "host strategy"})
        with self.assertRaisesRegex(ValueError, "omit s2_intervention"):
            _spec_s2_intervention(
                {
                    "s2_intervention": {
                        "protocol": "s2-intervention-v1",
                        "mode": "program_autonomous",
                        "label": "formal-primary-v1",
                    }
                }
            )
        diagnostic = {
            "protocol": "s2-intervention-v1",
            "mode": "forced_consult",
            "label": "diagnostic-force-s2-v1",
        }
        self.assertEqual(
            _spec_s2_intervention({"s2_intervention": diagnostic}),
            diagnostic,
        )
        self.assertEqual(
            _diagnostic_s2_intervention(False),
            {
                "protocol": "s2-intervention-v1",
                "mode": "program_autonomous",
                "label": "cli-autonomous-review-v1",
            },
        )
        self.assertEqual(
            _diagnostic_s2_intervention(True),
            {
                "protocol": "s2-intervention-v1",
                "mode": "forced_off",
                "label": "cli-no-s2-diagnostic-v1",
            },
        )

    def test_run_evolution_review_uses_program_autonomous_primary_path(self):
        paper_path = self._json(
            "paper.json",
            {
                "paper_id": "paper-1",
                "source": {"dataset": "development"},
            },
        )
        spec_path = self._json(
            "evolution-spec.json",
            {
                "run_id": "evolution-run",
                "steps": [
                    {
                        "action": "review",
                        "role": "QUAL",
                        "paper": str(paper_path),
                        "program": "/winner/program.py",
                        "snapshot": "/winner/memory.json",
                        "run_id": "winner-evaluation",
                        "program_version": "qual-winner-v1",
                    }
                ],
            },
        )

        class Model:
            def identity(self):
                return {"model": "reviewer"}

        config = SimpleNamespace(
            model=Model(),
            frozen_s2_path="/frozen/development-s2.jsonl",
            frozen_coco_s2_path="/frozen/coco-s2.jsonl",
            require_bubblewrap=False,
            artifact_path=lambda: self.root / "artifacts",
            embedding_identity=lambda: {"model": "embedding"},
        )
        calls = []

        def fake_review(**kwargs):
            calls.append(kwargs)
            return {"review_id": "review-1"}

        with (
            patch.object(cli_module, "OpenAICompletionClient", return_value=object()),
            patch.object(cli_module, "_embedding_client", return_value=object()),
            patch.object(cli_module, "run_specialist_review", side_effect=fake_review),
        ):
            result = _run_evolution_spec(config, str(spec_path))

        self.assertEqual(result["outcomes"], [{"review_id": "review-1"}])
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["frozen_s2_path"], config.frozen_s2_path)
        self.assertEqual(
            call["s2_intervention"],
            {
                "protocol": "s2-intervention-v1",
                "mode": "program_autonomous",
                "label": "evolution-development-v1",
            },
        )
        self.assertNotIn("s2_enabled", call)
        self.assertNotIn("review_strategy", call)

    def test_freeze_binds_each_winner_and_primary_program_authority(self):
        program = seed_program_path("experience_learner").resolve()
        programs = {role: str(program) for role in ROLES}
        snapshots = {}
        pools = {}
        evolution_run_ids = {}
        for role in ROLES:
            snapshot_payload = {
                "snapshot_protocol": "research-v2-memory-snapshot-v1",
                "role": role,
                "program_path": str(program),
                "program_sha256": file_sha256(program),
                "program_version": f"{role.lower()}-winner-v1",
                "seed_kind": "experience_learner",
                "source_review_ids": [],
                "source_evaluation_ids": [],
                "items": [],
                "snapshot": {},
                "capability_manifest": program_capability_manifest(path=program),
                "embedding_identity": {"model": "embedding"},
                "completion_identity": {"model": "reviewer"},
            }
            snapshot_payload["build_identity"] = {
                "protocol": "research-v2-memory-build-input-v1",
                "role": role,
                "program_path": str(program),
                "program_sha256": file_sha256(program),
                "capability_manifest": program_capability_manifest(path=program),
                "seed_kind": "experience_learner",
                "program_version": f"{role.lower()}-winner-v1",
                "embedding_identity": {"model": "embedding"},
                "completion_identity": {"model": "reviewer"},
                "sources": [],
            }
            snapshot_payload["snapshot_id"] = value_sha256(snapshot_payload)
            snapshot_path = self._json(
                f"snapshots/{role}.json", snapshot_payload
            )
            snapshots[role] = str(snapshot_path)
            pool = EvolutionPool(
                role,
                [
                    PoolEntry(
                        candidate_id=f"winner-{role}",
                        role=role,
                        program_path=str(program),
                        program_sha256=file_sha256(program),
                        parent_id=None,
                        static_score=4.0,
                        seed_kind="experience_learner",
                        memory_snapshot_path=str(snapshot_path),
                        memory_snapshot_sha256=file_sha256(snapshot_path),
                        memory_snapshot_id=snapshot_payload["snapshot_id"],
                        capability_manifest=program_capability_manifest(path=program),
                    )
                ],
            )
            evolution_run_id = f"evolution-{role}"
            evolution_run_ids[role] = evolution_run_id
            pool_path = self._json(
                f"evolution/{role}/{evolution_run_id}/pool.json",
                pool.to_dict(),
            )
            pools[role] = str(pool_path)
            winner_payload = {
                "protocol": "research-v2-formal-role-winner-v1",
                "role": role,
                "run_id": evolution_run_id,
                "pool_path": str(pool_path),
                "pool_sha256": file_sha256(pool_path),
                "winner": pool.to_dict()["entries"][0],
            }
            winner_payload["winner_id"] = value_sha256(winner_payload)
            self._json(
                f"evolution/{role}/{evolution_run_id}/winner.json",
                winner_payload,
            )
            self._json(
                f"evolution/{role}/{evolution_run_id}/coordinator.json",
                {
                    "protocol": "research-v2-formal-role-evolution-v1",
                    "role": role,
                    "run_id": evolution_run_id,
                    "status": "complete",
                    "winner_id": winner_payload["winner_id"],
                    "attempts": {
                        f"{attempt:03d}": {"status": "failed"}
                        for attempt in range(1, 21)
                    },
                },
            )
        development_s2 = self.root / "development-s2.jsonl"
        development_s2.write_text("{}\n", encoding="utf-8")
        coco_s2 = self.root / "coco-s2.jsonl"
        coco_s2.write_text("{}\n", encoding="utf-8")
        development_manifest = self._json("development.json", {"split": "development"})
        coco_manifest = self._json(
            "coco.json",
            {
                "protocol": "coco_1300_protocol_compatible_resample_v1",
                "count": 1300,
                "manifest_sha256": "sealed-manifest",
            },
        )
        with self.assertRaisesRegex(ValueError, "embedding identity mismatch"):
            freeze_system(
                output_path=self.root / "invalid-embedding-system.json",
                programs=programs,
                memory_snapshots=snapshots,
                model_identity={"model": "reviewer"},
                embedding_identity={"model": "different"},
                require_bubblewrap=False,
                frozen_s2_pools={
                    "development": str(development_s2),
                    "coco_1300": str(coco_s2),
                },
                primary_meta_enabled=False,
                code_commit="commit",
                development_manifest_path=development_manifest,
                coco_manifest_path=coco_manifest,
                evolution_run_ids=evolution_run_ids,
                evolution_pools=pools,
                evaluator_config={},
                reflector_config={},
                summarizer_config={},
            )
        self.assertFalse((self.root / "invalid-embedding-system.json").exists())
        with self.assertRaisesRegex(ValueError, "completion identity mismatch"):
            freeze_system(
                output_path=self.root / "invalid-completion-system.json",
                programs=programs,
                memory_snapshots=snapshots,
                model_identity={"model": "different"},
                embedding_identity={"model": "embedding"},
                require_bubblewrap=False,
                frozen_s2_pools={
                    "development": str(development_s2),
                    "coco_1300": str(coco_s2),
                },
                primary_meta_enabled=False,
                code_commit="commit",
                development_manifest_path=development_manifest,
                coco_manifest_path=coco_manifest,
                evolution_run_ids=evolution_run_ids,
                evolution_pools=pools,
                evaluator_config={},
                reflector_config={},
                summarizer_config={},
            )
        self.assertFalse((self.root / "invalid-completion-system.json").exists())
        frozen = freeze_system(
            output_path=self.root / "frozen-system.json",
            programs=programs,
            memory_snapshots=snapshots,
            model_identity={"model": "reviewer"},
            embedding_identity={"model": "embedding"},
            require_bubblewrap=False,
            frozen_s2_pools={
                "development": str(development_s2),
                "coco_1300": str(coco_s2),
            },
            primary_meta_enabled=False,
            code_commit="commit",
            development_manifest_path=development_manifest,
            coco_manifest_path=coco_manifest,
            evolution_run_ids=evolution_run_ids,
            evolution_pools=pools,
            evaluator_config={},
            reflector_config={},
            summarizer_config={},
        )
        self.assertEqual(frozen["protocol"], "research-v2-frozen-system-v2")
        runtime = frozen["review_runtime"]
        self.assertEqual(runtime["review_plan"]["protocol"], REVIEW_PLAN_PROTOCOL)
        self.assertEqual(
            runtime["review_plan"]["decision_authority"],
            "role-local-evolved-program",
        )
        self.assertIsNone(runtime["review_plan"]["primary_intervention"])
        self.assertEqual(
            runtime["retrieval_provenance"]["protocol"],
            "retrieval-provenance-v2",
        )
        self.assertIs(runtime["require_bubblewrap"], False)
        for role in ROLES:
            self.assertEqual(
                runtime["programs"][role]["capability_manifest"],
                program_capability_manifest(path=program),
            )

    def test_llm_induced_memory_build_identity_is_exact(self):
        program = seed_program_path("experience_learner").resolve()
        embedding_identity = {"model": "embedding"}
        model_identity = {"model": "reviewer"}
        manifest = program_capability_manifest(path=program)
        snapshot = {
            "role": "QUAL",
            "program_path": str(program),
            "program_sha256": file_sha256(program),
            "program_version": "qual-winner-v1",
            "seed_kind": "experience_learner",
            "capability_manifest": manifest,
            "embedding_identity": embedding_identity,
            "completion_identity": model_identity,
            "build_identity": {
                "protocol": "research-v2-memory-build-input-v1",
                "role": "QUAL",
                "program_path": str(program),
                "program_sha256": file_sha256(program),
                "capability_manifest": manifest,
                "seed_kind": "experience_learner",
                "program_version": "qual-winner-v1",
                "embedding_identity": embedding_identity,
                "completion_identity": model_identity,
                "sources": [],
            },
        }
        _validate_memory_winner_identity(
            role="QUAL",
            program=program,
            snapshot=snapshot,
            expected_seed_kind="experience_learner",
            model_identity=model_identity,
            embedding_identity=embedding_identity,
        )

        cases = {
            "role": "OTHER",
            "program_path": "/different/program.py",
            "program_sha256": "different",
            "capability_manifest": {"different": True},
            "seed_kind": "different",
            "program_version": "different",
            "embedding_identity": {"model": "different"},
            "completion_identity": {"model": "different"},
        }
        for field_name, invalid_value in cases.items():
            with self.subTest(field_name=field_name):
                invalid = deepcopy(snapshot)
                invalid["build_identity"][field_name] = invalid_value
                with self.assertRaisesRegex(
                    ValueError,
                    f"build identity mismatch for {field_name}",
                ):
                    _validate_memory_winner_identity(
                        role="QUAL",
                        program=program,
                        snapshot=invalid,
                        expected_seed_kind="experience_learner",
                        model_identity=model_identity,
                        embedding_identity=embedding_identity,
                    )

        for field_name in ("completion_identity", "build_identity"):
            with self.subTest(missing_field=field_name):
                invalid = deepcopy(snapshot)
                del invalid[field_name]
                with self.assertRaises(ValueError):
                    _validate_memory_winner_identity(
                        role="QUAL",
                        program=program,
                        snapshot=invalid,
                        expected_seed_kind="experience_learner",
                        model_identity=model_identity,
                        embedding_identity=embedding_identity,
                    )


if __name__ == "__main__":
    unittest.main()
