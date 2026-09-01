import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.components import materialize_factorial_components
from agentreview.research_v2.io import value_sha256
from agentreview.research_v2.memory import build_memory_snapshot, seed_program_path
from agentreview.research_v2.usage import (
    factorial_attribution,
    summarize_s2_usage,
    validated_factorial_attribution,
)


class ResearchV2UsageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _review_path(
        self,
        name,
        *,
        role="QUAL",
        program_version="program-v1",
        capability_available=True,
        intervention_mode="program_autonomous",
        disposition="not_consulted",
        decision_label="not_needed",
        candidates=(),
        selected=(),
        injected=(),
    ):
        lookup_count = int(disposition != "not_consulted")
        path = self.root / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "review_id": name,
                    "role": role,
                    "program_version": program_version,
                    "retrieval_provenance": {
                        "memory": {"logs": [], "context": "memory context"},
                        "s2": {
                            "capability_available": capability_available,
                            "intervention": {
                                "protocol": "s2-intervention-v1",
                                "mode": intervention_mode,
                                "label": "formal-primary-v1"
                                if intervention_mode == "program_autonomous"
                                else "diagnostic-v1",
                            },
                            "s2_disposition": disposition,
                            "decision_label": decision_label,
                            "lookup_count": lookup_count,
                            "requested_limits": [5] if lookup_count else [],
                            "candidate_ids": list(candidates),
                            "selected_ids": list(selected),
                            "injected_ids": list(injected),
                            "usage_instruction": "Use only for novelty checks.",
                            "context_order": ["memory", "s2", "strategy"],
                            "evidence": [],
                            "pool_version": "s2-pool-v1",
                            "pool_sha256": "a" * 64,
                            "context_sha256": "b" * 64,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_summary_uses_only_autonomous_capability_eligible_reviews(self):
        paths = [
            self._review_path("skip"),
            self._review_path(
                "consulted-unused",
                disposition="consulted_no_use",
                decision_label="novelty_check",
                candidates=("s1", "s2"),
            ),
            self._review_path(
                "use",
                role="ORIG",
                program_version="program-v2",
                disposition="use",
                decision_label="novelty_check",
                candidates=("s1", "s2", "s3"),
                selected=("s1", "s3"),
                injected=("s1", "s3"),
            ),
            self._review_path(
                "empty",
                role="ORIG",
                program_version="program-v2",
                disposition="consulted_no_use",
                decision_label="related_work_check",
            ),
            self._review_path(
                "forced-consult",
                disposition="use",
                intervention_mode="forced_consult",
                candidates=("s1",),
                selected=("s1",),
                injected=("s1",),
            ),
            self._review_path("unavailable", capability_available=False),
        ]

        report = summarize_s2_usage(paths)

        self.assertEqual(report["protocol"], "research-v2-s2-usage-summary-v1")
        self.assertEqual(
            report["intervention_samples"],
            {"forced_consult": 1, "forced_off": 0, "program_autonomous": 5},
        )
        self.assertEqual(report["overall"]["total_reviews"], 6)
        self.assertEqual(report["overall"]["eligible_reviews"], 4)
        self.assertAlmostEqual(report["overall"]["eligible_rate"], 4 / 6)
        self.assertEqual(report["overall"]["lookup_reviews"], 3)
        self.assertEqual(report["overall"]["lookup_rate"], 0.75)
        self.assertEqual(report["overall"]["injection_rate"], 0.25)
        self.assertEqual(report["overall"]["consulted_unused_rate"], 0.5)
        self.assertEqual(report["overall"]["empty_result_rate"], 0.25)
        self.assertEqual(report["overall"]["mean_selected_count"], 0.5)
        self.assertEqual(report["by_role"]["ORIG"]["eligible_reviews"], 2)
        self.assertEqual(report["by_program_version"]["program-v2"]["lookup_rate"], 1.0)
        self.assertEqual(
            report["by_decision_label"]["novelty_check"]["injection_rate"],
            0.5,
        )
        json.dumps(report, allow_nan=False)

    def test_summary_rejects_legacy_fixed_injection_trace(self):
        path = self.root / "legacy.json"
        path.write_text(
            json.dumps(
                {
                    "role": "QUAL",
                    "program_version": "canonical-v1",
                    "retrieval_provenance": {
                        "memory": {"logs": [], "context": ""},
                        "s2": {"enabled": True, "result_ids": ["s1"]},
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "provenance v2 fields missing"):
            summarize_s2_usage([path])

    def test_factorial_attribution_computes_main_and_interaction_effects(self):
        report = factorial_attribution(1.0, 3.0, 2.0, 6.0)

        self.assertEqual(report["effects"]["memory_main_effect"], 3.0)
        self.assertEqual(report["effects"]["s2_policy_main_effect"], 2.0)
        self.assertEqual(report["effects"]["interaction"], 2.0)
        self.assertEqual(report["simple_effects"]["memory_at_evolved_s2_policy"], 4.0)
        json.dumps(report, allow_nan=False)

    def test_factorial_attribution_requires_finite_scores(self):
        with self.assertRaisesRegex(ValueError, "must be finite"):
            factorial_attribution(1.0, 2.0, 3.0, float("nan"))

    def test_validated_factorial_attribution_checks_components_and_shared_identity(
        self,
    ):
        shared = {
            "development_manifest_sha256": "development",
            "paper_ids_sha256": "papers",
            "model_identity": {"model": "reviewer"},
            "reviewer_identity": {"suite": "research-v2-reviewers"},
            "embedding_identity": {"model": "embedding"},
            "evaluator_identity": {"model": "evaluator"},
            "frozen_s2_sha256": "s2",
            "generation_identity": {"temperature": 0.0},
        }
        baseline_program = seed_program_path("vector_search").resolve()
        evolved_source = baseline_program.read_text(encoding="utf-8")
        evolved_source = evolved_source.replace(
            "research_v2_vector", "research_v2_vector_evolved", 1
        ).replace("seed_always_consult_top5", "evolved_policy", 1)
        evolved_program = self.root / "evolved" / "program.py"
        evolved_program.parent.mkdir(parents=True, exist_ok=True)
        evolved_program.write_text(evolved_source, encoding="utf-8")

        def snapshot(program, version):
            path = self.root / "source" / version / "snapshot.json"
            build_memory_snapshot(
                role="QUAL",
                program_path=program,
                program_version=version,
                review_paths=[],
                evaluation_paths=[],
                output_path=path,
                seed_kind="vector_search",
                require_bubblewrap=False,
            )
            return path

        materialized = materialize_factorial_components(
            role="QUAL",
            baseline_program_path=baseline_program,
            evolved_program_path=evolved_program,
            baseline_snapshot_path=snapshot(baseline_program, "baseline-v1"),
            evolved_snapshot_path=snapshot(evolved_program, "evolved-v1"),
            shared_identity=shared,
            output_root=self.root / "factorial",
        )
        scores = {
            "baseline_memory__baseline_s2_policy": 1.0,
            "evolved_memory__baseline_s2_policy": 3.0,
            "baseline_memory__evolved_s2_policy": 2.0,
            "evolved_memory__evolved_s2_policy": 6.0,
        }

        def cell_result(cell, spec):
            run_id = f"evaluation-{cell}"
            artifact = {
                "protocol": "research-v2-factorial-cell-evaluation-v1",
                "cell": cell,
                "role": "QUAL",
                "score": scores[cell],
                "factorial_components_id": materialized["factorial_components_id"],
                "program_sha256": spec["program_sha256"],
                "snapshot_id": spec["snapshot_id"],
                "memory_state_sha256": spec["memory_state_sha256"],
                "paper_ids_sha256": shared["paper_ids_sha256"],
                "reviewer_identity": shared["reviewer_identity"],
                "evaluator_identity": shared["evaluator_identity"],
                "generation_identity": shared["generation_identity"],
                "intervention_mode": "program_autonomous",
                "run_id": run_id,
            }
            artifact["evaluation_id"] = value_sha256(artifact)
            artifact_path = self.root / "evaluations" / f"{cell}.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(artifact, sort_keys=True),
                encoding="utf-8",
            )
            return {
                "cell": cell,
                "role": "QUAL",
                "score": scores[cell],
                "factorial_components_id": materialized["factorial_components_id"],
                "program_sha256": spec["program_sha256"],
                "snapshot_id": spec["snapshot_id"],
                "snapshot_sha256": spec["snapshot_sha256"],
                "memory_state_sha256": spec["memory_state_sha256"],
                "memory_ast_sha256": spec["capability_manifest"]["memory_ast_sha256"],
                "policy_ast_sha256": spec["capability_manifest"]["policy_ast_sha256"],
                "shared_identity": shared,
                "evaluation_identity": {
                    "run_id": run_id,
                    "artifact_path": str(artifact_path),
                    "artifact_sha256": hashlib.sha256(
                        artifact_path.read_bytes()
                    ).hexdigest(),
                    "evaluation_id": artifact["evaluation_id"],
                    "paper_ids_sha256": shared["paper_ids_sha256"],
                    "reviewer_identity": shared["reviewer_identity"],
                    "evaluator_identity": shared["evaluator_identity"],
                    "generation_identity": shared["generation_identity"],
                    "intervention_mode": "program_autonomous",
                },
            }

        cells = {
            cell: cell_result(cell, spec)
            for cell, spec in materialized["cells"].items()
        }
        report = validated_factorial_attribution(
            cells,
            factorial_components=materialized,
        )
        self.assertEqual(
            report["protocol"],
            "research-v2-validated-factorial-attribution-v1",
        )
        self.assertEqual(report["effects"]["memory_main_effect"], 3.0)

        mismatched_state_cell = "baseline_memory__evolved_s2_policy"
        original_state = cells[mismatched_state_cell]["memory_state_sha256"]
        cells[mismatched_state_cell]["memory_state_sha256"] = cells[
            "evolved_memory__evolved_s2_policy"
        ]["memory_state_sha256"]
        with self.assertRaisesRegex(ValueError, "Memory state identity mismatch"):
            validated_factorial_attribution(
                cells,
                factorial_components=materialized,
            )
        cells[mismatched_state_cell]["memory_state_sha256"] = original_state

        cells[mismatched_state_cell]["evaluation_identity"]["intervention_mode"] = (
            "forced_off"
        )
        with self.assertRaisesRegex(ValueError, "autonomous S2 policy"):
            validated_factorial_attribution(
                cells,
                factorial_components=materialized,
            )
        cells[mismatched_state_cell]["evaluation_identity"]["intervention_mode"] = (
            "program_autonomous"
        )

        cells[mismatched_state_cell]["score"] = 99.0
        with self.assertRaisesRegex(ValueError, "score differs"):
            validated_factorial_attribution(
                cells,
                factorial_components=materialized,
            )
        cells[mismatched_state_cell]["score"] = scores[mismatched_state_cell]

        cells["evolved_memory__evolved_s2_policy"]["shared_identity"] = {
            **shared,
            "paper_ids_sha256": "different-papers",
        }
        with self.assertRaisesRegex(ValueError, "materialized shared identity"):
            validated_factorial_attribution(
                cells,
                factorial_components=materialized,
            )

    def test_formal_usage_excludes_other_autonomous_labels(self):
        formal = self._review_path("formal")
        smoke = self._review_path("smoke")
        value = json.loads(smoke.read_text(encoding="utf-8"))
        value["retrieval_provenance"]["s2"]["intervention"]["label"] = (
            "cli-autonomous-review-v1"
        )
        smoke.write_text(json.dumps(value), encoding="utf-8")
        report = summarize_s2_usage([formal, smoke])
        self.assertEqual(report["overall"]["eligible_reviews"], 1)
        self.assertEqual(
            report["intervention_label_samples"],
            {"cli-autonomous-review-v1": 1, "formal-primary-v1": 1},
        )


if __name__ == "__main__":
    unittest.main()
