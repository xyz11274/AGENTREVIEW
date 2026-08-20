import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.assets import (
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    validate_asset_manifest,
)
from memory_evolution.checkpoint import load_checkpoint, save_checkpoint
from memory_evolution.reflector import EvalFeedback, validate_candidate_feedback
from memory_evolution.evaluator import evaluate_program
from memory_evolution.formal_protocol import (
    FORMAL_REQUIRED_VERSIONS,
    FROZEN_COCO_ROOT,
    _remap_frozen_coco_path,
    _validate_frozen_coco_provenance,
    _validate_v2_sidecar,
)
from memory_evolution.models import Episode, EvalItem
from memory_evolution.sandbox import SandboxSession
from memory_evolution.toolkit import (
    MemoryToolkit,
    frozen_s2_pool_version,
    validate_frozen_s2_pool,
)
from scripts.finalize_formal_readiness import build_candidate_manifest


class TemporalProtocolTest(unittest.TestCase):
    def _coco_provenance_fixture(self, root: Path):
        files = {
            "implementation_path": root / "src" / "evaluation" / "_4_evaluation.py",
            "selection_loader_path": root / "src" / "evaluation" / "_0_human_review.py",
            "left_out_selection": root / "benchmark" / "benchmark_left_out_human.jsonl",
            "benchmark": root / "benchmark" / "benchmark_ICLR_2023.jsonl",
        }
        for name, path in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture:{name}\n", encoding="utf-8")

        def frozen(path: Path) -> str:
            return str(FROZEN_COCO_ROOT / path.relative_to(root))

        return {
            "implementation_path": frozen(files["implementation_path"]),
            "implementation_sha256": hashlib.sha256(
                files["implementation_path"].read_bytes()
            ).hexdigest(),
            "selection_loader_path": frozen(files["selection_loader_path"]),
            "selection_loader_sha256": hashlib.sha256(
                files["selection_loader_path"].read_bytes()
            ).hexdigest(),
            "left_out_selection": frozen(files["left_out_selection"]),
            "left_out_selection_sha256": hashlib.sha256(
                files["left_out_selection"].read_bytes()
            ).hexdigest(),
            "benchmark_file_sha256": {
                frozen(files["benchmark"]): hashlib.sha256(
                    files["benchmark"].read_bytes()
                ).hexdigest()
            },
        }

    def test_frozen_coco_provenance_remaps_same_relative_paths_and_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            provenance = self._coco_provenance_fixture(current_root)
            report = _validate_frozen_coco_provenance(
                provenance, coco_root=str(current_root)
            )
            implementation = report["verified_reference_files"][
                "implementation_path"
            ]
            self.assertEqual(
                implementation["relative_path"], "src/evaluation/_4_evaluation.py"
            )
            self.assertEqual(
                Path(implementation["resolved_path"]),
                current_root / "src" / "evaluation" / "_4_evaluation.py",
            )

    def test_frozen_coco_provenance_missing_mapped_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            provenance = self._coco_provenance_fixture(current_root)
            (current_root / "src" / "evaluation" / "_0_human_review.py").unlink()
            with self.assertRaisesRegex(RuntimeError, "missing after root remap"):
                _validate_frozen_coco_provenance(
                    provenance, coco_root=str(current_root)
                )

    def test_frozen_coco_provenance_changed_implementation_sha_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            provenance = self._coco_provenance_fixture(current_root)
            provenance["implementation_sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "implementation_path SHA256"):
                _validate_frozen_coco_provenance(
                    provenance, coco_root=str(current_root)
                )

    def test_frozen_coco_provenance_changed_benchmark_sha_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            provenance = self._coco_provenance_fixture(current_root)
            benchmark = next(iter(provenance["benchmark_file_sha256"]))
            provenance["benchmark_file_sha256"][benchmark] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "benchmark provenance SHA256"):
                _validate_frozen_coco_provenance(
                    provenance, coco_root=str(current_root)
                )

    def test_frozen_coco_path_escape_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            current_root.mkdir()
            escaped = str(FROZEN_COCO_ROOT / "benchmark" / ".." / "outside.jsonl")
            with self.assertRaisesRegex(ValueError, "unsafe relative identity"):
                _remap_frozen_coco_path(escaped, str(current_root))

    def test_unrelated_absolute_path_cannot_use_coco_root_remap(self):
        with tempfile.TemporaryDirectory() as directory:
            current_root = Path(directory) / "current-coco"
            current_root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the frozen CoCo root"):
                _remap_frozen_coco_path(
                    "/unrelated/absolute/path.jsonl", str(current_root)
                )

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
            new_candidate = dict(self._identity())
            new_candidate["candidate_id"] = "candidate-2"
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                load_checkpoint(
                    str(path), expected_identity=new_candidate, frozen_ids=frozen
                )
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
                "target_identity": {"paper_id": "target", "title": "Target"},
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
                "pool_version": "frozen-s2-temporal-v1",
            }
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"target": "2020-12-31"},
                required_ids={"target"},
            )
            self.assertFalse(report["passed"])
            self.assertEqual(len(report["future_results"]), 1)

    def test_strict_history_identity_reads_v2_pool_version_from_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frozen.jsonl"
            path.write_text(
                json.dumps({"pool_version": "frozen-s2-temporal-v2"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                frozen_s2_pool_version(path), "frozen-s2-temporal-v2"
            )

    def test_readiness_manifest_registration_is_explicit_and_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool.jsonl"
            sidecar = root / "pool.jsonl.manifest.json"
            pool.write_text("{}\n", encoding="utf-8")
            sidecar.write_text("{}\n", encoding="utf-8")
            split = root / "manifest.json"
            split.write_text("{}\n", encoding="utf-8")
            family_path = root / "paper_families.jsonl"
            family_path.write_text("", encoding="utf-8")
            split_summary = {
                "zones": {},
                "families": {"path": family_path.name},
            }
            for zone in (
                "historical_experience",
                "evolution_validation",
                "effectiveness_holdout",
                "held_out_benchmark_test",
            ):
                zone_path = root / f"{zone}.jsonl"
                zone_path.write_text("", encoding="utf-8")
                split_summary["zones"][zone] = {"path": zone_path.name}
            candidate = build_candidate_manifest(
                {
                    "manifest_version": "mstar-assets-v2-temporal",
                    "assets": {},
                    "formal_ready": False,
                    "formal_blockers": ["fixture"],
                },
                temporal_split_manifest=split,
                split_summary=split_summary,
                s2_pool=pool,
                s2_manifest=sidecar,
            )
            self.assertTrue(candidate["formal_ready"])
            self.assertEqual(candidate["formal_blockers"], [])
            self.assertEqual(
                candidate["assets"]["frozen_s2_pool"]["version"],
                "frozen-s2-temporal-v2",
            )
            self.assertEqual(
                len(candidate["assets"]["frozen_s2_pool"]["sha256"]), 64
            )

    def test_asset_manifest_rejects_wrong_required_s2_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool.jsonl"
            pool.write_text("{}\n", encoding="utf-8")
            manifest = root / "assets.json"
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": "mstar-assets-v2-temporal",
                        "assets": {
                            "frozen_s2_pool": {
                                "path": str(pool),
                                "sha256": hashlib.sha256(pool.read_bytes()).hexdigest(),
                                "version": "wrong-version",
                                "split": "A+B+C+D",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Asset version mismatch"):
                validate_asset_manifest(
                    str(manifest),
                    required_assets=("frozen_s2_pool",),
                    required_versions={
                        "frozen_s2_pool": "frozen-s2-temporal-v1"
                    },
                )

    def test_current_formal_versions_reject_frozen_v1_manifest(self):
        root = Path(__file__).parents[1]
        manifest_path = root / "configs" / "asset_manifest_v2.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["assets"]["temporal_split_manifest"]["version"],
            "coco-temporal-paper-split-v1",
        )
        with self.assertRaises(ValueError):
            validate_asset_manifest(
                str(manifest_path),
                required_assets=("temporal_split_manifest",),
                required_versions=FORMAL_REQUIRED_VERSIONS,
            )

    def test_formal_required_versions_explicitly_reject_v1_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = {}
            for name in FORMAL_REQUIRED_VERSIONS:
                path = root / f"{name}.json"
                path.write_text("{}\n", encoding="utf-8")
                assets[name] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "version": (
                        "coco-temporal-paper-split-v1"
                        if name == "temporal_split_manifest"
                        else "frozen-s2-temporal-v1"
                    ),
                    "split": "A+B+C+D",
                }
            manifest = root / "assets.json"
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": "mstar-assets-v2-temporal",
                        "assets": assets,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, "Asset version mismatch: temporal_split_manifest"
            ):
                validate_asset_manifest(
                    str(manifest),
                    required_assets=tuple(FORMAL_REQUIRED_VERSIONS),
                    required_versions=FORMAL_REQUIRED_VERSIONS,
                )

    def test_formal_v2_sidecar_requires_passed_query_and_temporal_audits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split = root / "split.json"
            pool = root / "pool.jsonl"
            sidecar_path = root / "pool.jsonl.manifest.json"
            split.write_text("{}\n", encoding="utf-8")
            pool.write_text("{}\n", encoding="utf-8")
            sidecar = {
                "pool_version": "frozen-s2-temporal-v2",
                "asset_sha256": hashlib.sha256(pool.read_bytes()).hexdigest(),
                "temporal_split_manifest_sha256": hashlib.sha256(
                    split.read_bytes()
                ).hexdigest(),
                "temporal_audit": {"passed": True},
                "query_integrity_audit": {
                    "required_count": 3900,
                    "checked_count": 3900,
                    "malformed_titles": [],
                    "canonical_mismatches": [],
                    "polluted_query_titles": [],
                    "passed": True,
                },
            }
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            asset_report = {
                "verified_assets": {
                    "frozen_s2_pool_manifest": {
                        "resolved_path": str(sidecar_path)
                    }
                }
            }
            self.assertEqual(
                _validate_v2_sidecar(
                    asset_report,
                    temporal_split_manifest_path=str(split),
                    s2_pool_path=str(pool),
                    expected_target_count=3900,
                )["pool_version"],
                "frozen-s2-temporal-v2",
            )
            sidecar["query_integrity_audit"]["passed"] = False
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "query integrity"):
                _validate_v2_sidecar(
                    asset_report,
                    temporal_split_manifest_path=str(split),
                    s2_pool_path=str(pool),
                    expected_target_count=3900,
                )

    def test_adjacent_uncommitted_record_is_cleaned_but_future_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            frozen = ["P1", "P2", "P3"]
            save_checkpoint(
                str(path),
                run_identity=self._identity(),
                frozen_ids=frozen,
                completed_ids=["P1"],
                memory_snapshot={"state": [1]},
                paper_record={"paper_id": "P1", "paper_index": 0, "completed": True},
            )
            records = path.with_suffix(".json.records")
            adjacent = records / (
                "000001_" + hashlib.sha256(b"P2").hexdigest()[:16] + ".json"
            )
            adjacent.write_text(
                json.dumps({"paper_id": "P2", "paper_index": 1, "completed": True}),
                encoding="utf-8",
            )
            loaded = load_checkpoint(
                str(path), expected_identity=self._identity(), frozen_ids=frozen
            )
            self.assertEqual(loaded["discarded_uncommitted_records"], [adjacent.name])
            self.assertFalse(adjacent.exists())

            future = records / (
                "000002_" + hashlib.sha256(b"P3").hexdigest()[:16] + ".json"
            )
            future.write_text(
                json.dumps({"paper_id": "P3", "paper_index": 2, "completed": True}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "beyond the legal completed prefix"):
                load_checkpoint(
                    str(path), expected_identity=self._identity(), frozen_ids=frozen
                )

    def test_fresh_formal_run_rejects_residual_memory_without_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            (Path(directory) / "memory_library.jsonl").write_text(
                '{"stale": true}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "residual Memory"):
                load_checkpoint(
                    str(path), expected_identity=self._identity(), frozen_ids=["P1"]
                )

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
