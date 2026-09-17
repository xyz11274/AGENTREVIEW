import json
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.components import (
    FACTORIAL_CELL_SOURCES,
    compose_program_components,
    materialize_factorial_components,
    rebind_memory_snapshot,
    validate_factorial_components,
)
from agentreview.research_v2.memory import (
    build_memory_snapshot,
    retrieve_memory,
    seed_program_path,
)
from agentreview.research_v2.models import Paper
from agentreview.research_v2.program_checks import program_capability_manifest


class FactorialComponentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.baseline_program = seed_program_path("vector_search").resolve()
        source = self.baseline_program.read_text(encoding="utf-8")
        source = source.replace("research_v2_vector", "research_v2_vector_evolved", 1)
        source = source.replace(
            "seed_always_consult_top5", "evolved_selective_policy", 1
        )
        self.evolved_program = self.root / "evolved" / "program.py"
        self.evolved_program.parent.mkdir(parents=True, exist_ok=True)
        self.evolved_program.write_text(source, encoding="utf-8")
        self.review_path = self.root / "source-review.json"
        self.review_path.write_text(
            json.dumps(
                {
                    "review_id": "development-review-1",
                    "role": "QUAL",
                    "review_text": "Check whether the evaluation supports the claims.",
                    "paper": {"title": "Development paper"},
                }
            ),
            encoding="utf-8",
        )
        self.baseline_snapshot = self._snapshot(
            self.baseline_program, "baseline-program-v1"
        )
        self.evolved_snapshot = self._snapshot(
            self.evolved_program, "evolved-program-v1"
        )
        self.shared_identity = {
            "development_manifest_sha256": "development-hash",
            "paper_ids_sha256": "paper-set-hash",
            "model_identity": {"model": "reviewer"},
            "reviewer_identity": {"suite": "research-v2-reviewers"},
            "embedding_identity": {"model": "embedding"},
            "evaluator_identity": {"model": "evaluator"},
            "frozen_s2_sha256": "s2-hash",
            "generation_identity": {"temperature": 0.0},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _snapshot(self, program, version):
        path = self.root / "source-snapshots" / version / "snapshot.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=program,
            program_version=version,
            review_paths=[self.review_path],
            evaluation_paths=[],
            output_path=path,
            seed_kind="vector_search",
            require_bubblewrap=False,
        )
        return path

    def test_materializer_builds_runnable_two_by_two_components(self):
        result = materialize_factorial_components(
            role="QUAL",
            baseline_program_path=self.baseline_program,
            evolved_program_path=self.evolved_program,
            baseline_snapshot_path=self.baseline_snapshot,
            evolved_snapshot_path=self.evolved_snapshot,
            shared_identity=self.shared_identity,
            output_root=self.root / "factorial",
        )
        self.assertEqual(set(result["cells"]), set(FACTORIAL_CELL_SOURCES))
        manifests = {
            cell: value["capability_manifest"]
            for cell, value in result["cells"].items()
        }
        self.assertEqual(
            manifests["baseline_memory__baseline_s2_policy"]["memory_ast_sha256"],
            manifests["baseline_memory__evolved_s2_policy"]["memory_ast_sha256"],
        )
        self.assertEqual(
            manifests["baseline_memory__baseline_s2_policy"]["policy_ast_sha256"],
            manifests["evolved_memory__baseline_s2_policy"]["policy_ast_sha256"],
        )
        self.assertNotEqual(
            manifests["baseline_memory__baseline_s2_policy"]["memory_ast_sha256"],
            manifests["evolved_memory__baseline_s2_policy"]["memory_ast_sha256"],
        )
        self.assertNotEqual(
            manifests["baseline_memory__baseline_s2_policy"]["policy_ast_sha256"],
            manifests["baseline_memory__evolved_s2_policy"]["policy_ast_sha256"],
        )
        self.assertEqual(
            result["cells"]["baseline_memory__baseline_s2_policy"][
                "memory_state_sha256"
            ],
            result["cells"]["baseline_memory__evolved_s2_policy"][
                "memory_state_sha256"
            ],
        )
        self.assertEqual(
            result["cells"]["evolved_memory__baseline_s2_policy"][
                "memory_state_sha256"
            ],
            result["cells"]["evolved_memory__evolved_s2_policy"]["memory_state_sha256"],
        )
        validate_factorial_components(result)
        rebound = json.loads(
            Path(
                result["cells"]["baseline_memory__evolved_s2_policy"]["snapshot_path"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            rebound["items"][0]["derivation_version"],
            "baseline-program-v1",
        )
        self.assertNotEqual(
            rebound["items"][0]["derivation_version"],
            rebound["program_version"],
        )

        paper = Paper(
            paper_id="p1",
            title="Paper",
            abstract="Abstract",
            text="Paper text",
        )
        for cell, value in result["cells"].items():
            with self.subTest(cell=cell):
                retrieved = retrieve_memory(
                    paper=paper,
                    role="QUAL",
                    program_path=value["program_path"],
                    snapshot_path=value["snapshot_path"],
                    prepare_review_plan=True,
                    s2_retrieval_pool={"p1": [{"paperId": "s1"}]},
                    s2_retrieval_pool_sha256="s2-hash",
                    require_bubblewrap=False,
                )
                self.assertEqual(retrieved["review_plan"]["selected_s2_ids"], ["s1"])

    def test_snapshot_rebind_rejects_a_different_memory_component(self):
        with self.assertRaisesRegex(ValueError, "different Memory component"):
            rebind_memory_snapshot(
                source_snapshot_path=self.baseline_snapshot,
                composed_program_path=self.evolved_program,
                program_version="invalid-cross-v1",
                expected_role="QUAL",
                output_path=self.root / "invalid-snapshot.json",
            )

    def test_snapshot_rebind_rejects_tampered_state_and_wrong_role(self):
        source = json.loads(self.baseline_snapshot.read_text(encoding="utf-8"))
        source["snapshot"]["knowledge_base_state"]["leaked"] = "held-out-state"
        tampered = self.root / "tampered-snapshot.json"
        tampered.write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot identity mismatch"):
            rebind_memory_snapshot(
                source_snapshot_path=tampered,
                composed_program_path=self.baseline_program,
                program_version="tampered-v1",
                expected_role="QUAL",
                output_path=self.root / "tampered-output.json",
            )
        with self.assertRaisesRegex(ValueError, "role mismatch"):
            rebind_memory_snapshot(
                source_snapshot_path=self.baseline_snapshot,
                composed_program_path=self.baseline_program,
                program_version="wrong-role-v1",
                expected_role="CLAR",
                output_path=self.root / "wrong-role-output.json",
            )

    def test_snapshot_rebind_rejects_changed_source_program(self):
        self.evolved_program.write_text(
            self.evolved_program.read_text(encoding="utf-8").replace(
                "evolved_selective_policy", "changed_after_snapshot", 1
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Program hash mismatch"):
            rebind_memory_snapshot(
                source_snapshot_path=self.evolved_snapshot,
                composed_program_path=self.evolved_program,
                program_version="changed-source-v1",
                expected_role="QUAL",
                expected_source_program_path=self.evolved_program,
                output_path=self.root / "changed-source-output.json",
            )

    def test_composition_drops_imports_owned_only_by_unselected_memory(self):
        source = self.evolved_program.read_text(encoding="utf-8")
        source = source.replace("from hashlib import sha256", "import hashlib", 1)
        source = source.replace("item_id = sha256(", "item_id = hashlib.sha256(", 1)
        policy_program = self.root / "policy-with-memory-import.py"
        policy_program.write_text(source, encoding="utf-8")
        output = self.root / "selected-imports" / "program.py"
        compose_program_components(
            memory_program_path=self.baseline_program,
            policy_program_path=policy_program,
            output_path=output,
        )
        composed = output.read_text(encoding="utf-8")
        self.assertIn("from hashlib import sha256", composed)
        self.assertNotIn("import hashlib", composed)

    def test_evolved_fixture_changes_both_component_identities(self):
        baseline = program_capability_manifest(path=self.baseline_program)
        evolved = program_capability_manifest(path=self.evolved_program)
        self.assertNotEqual(baseline["memory_ast_sha256"], evolved["memory_ast_sha256"])
        self.assertNotEqual(baseline["policy_ast_sha256"], evolved["policy_ast_sha256"])


if __name__ == "__main__":
    unittest.main()
