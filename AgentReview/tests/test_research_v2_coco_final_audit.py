import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import coco_final_audit as audit_module
from agentreview.research_v2.coco_final_audit import (
    audit_coco_final,
    seal_coco_pre_score,
    validate_coco_pre_score_seal,
)
from agentreview.research_v2.coco_review_batch import SCORING_SOURCE_PROTOCOL
from agentreview.research_v2.io import file_sha256, read_json, value_sha256


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text(str(value), encoding="utf-8")


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


class _Fixture:
    def __init__(self, directory: str):
        self.root = Path(directory).resolve()
        self.repository_root = self.root / "agentreview"
        self.repository_root.mkdir()
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir()
        self.coco_root = self.root / "coco"
        self.coco_root.mkdir()
        self.winner_run_id = "winner-v1"
        self.seal_path = self.root / "pre-score-seal.json"
        self.audit_path = self.root / "final-audit.json"

        critical_relatives = (
            "scripts/run_official_candidate.py",
            "src/evaluation/_0_human_review.py",
            "src/evaluation/_2_split.py",
            "src/evaluation/_3_classify.py",
            "src/evaluation/_4_evaluation.py",
            "src/benchmark/prompt_registry.py",
            "src/evaluation/prompt/judge.txt",
        )
        self.critical = []
        for relative in critical_relatives:
            path = self.coco_root / relative
            _write(path, relative + "\n")
            self.critical.append(_binding(path))
        self.runner_path = self.coco_root / critical_relatives[0]

        benchmark_file = self.coco_root / "benchmark/benchmark_test_2026.jsonl"
        complete_file = self.coco_root / "benchmark/complete.jsonl"
        selection_file = self.coco_root / "benchmark/selection.json"
        _write(benchmark_file, '{"id":"paper-1"}\n')
        _write(complete_file, '{"id":"paper-1"}\n')
        _write(selection_file, '{}\n')
        benchmark = {
            "files": [_binding(benchmark_file)],
            "complete_human_classify": _binding(complete_file),
            "reviewer_selection": _binding(selection_file),
        }
        models = {}
        for stage in ("split", "classify"):
            model_file = self.coco_root / f"model/{stage}_model/model.bin"
            _write(model_file, stage)
            model_binding = _binding(model_file)
            model_binding["size_bytes"] = model_file.stat().st_size
            models[stage] = {
                "root": str(model_file.parent.resolve()),
                "files": [model_binding],
            }

        scientific = {
            "split": {
                "base_url": "http://split/v1",
                "model": "split-model",
                "max_in_flight_client_requests": 2,
                "effort": "medium",
            },
            "classify": {
                "base_url": "http://classify/v1",
                "model": "classify-model",
                "max_in_flight_client_requests": 3,
                "effort": "medium",
            },
            "evaluation": {
                "enabled": True,
                "benchmark_files": [benchmark_file.resolve().as_posix()],
                "complete_human_classify_file": str(complete_file.resolve()),
                "reviewer_selection_file": str(selection_file.resolve()),
                "base_url": "https://evaluation/v1",
                "model": "judge-model",
                "max_in_flight_client_requests": 4,
                "effort": "medium",
                "cuda_visible_devices": "",
            },
        }
        arms = {}
        for mode in ("meta", "concat"):
            arms[mode] = {
                "mode": mode,
                "output_root": str(
                    (
                        self.artifact_root
                        / "coco_scoring"
                        / f"{self.winner_run_id}-{mode}"
                    ).resolve()
                ),
                "cache_versions": {
                    stage: f"{self.winner_run_id}-{mode}-{stage}-v1"
                    for stage in ("split", "classify", "evaluation")
                },
            }
        spec_body = {
            "protocol": "research-v2-coco-scoring-spec-v1",
            "winner_run_id": self.winner_run_id,
            "artifact_root": str(self.artifact_root.resolve()),
            "repository": {
                "root": str(self.coco_root.resolve()),
                "git_commit": "a" * 40,
                "clean": True,
            },
            "critical_files": self.critical,
            "benchmark": benchmark,
            "models": models,
            "scientific_settings": scientific,
            "services": {},
            "arms": arms,
        }
        self.spec = {**spec_body, "spec_id": value_sha256(spec_body)}
        self.spec_path = self.root / "coco-scoring-spec.json"
        _write(self.spec_path, self.spec)

        self.manifest_path = self.root / "coco-manifest.json"
        manifest_body = {"records": [{"paper_id": "paper-1"}, {"paper_id": "paper-2"}]}
        manifest = {
            "protocol": "coco_1300_protocol_compatible_resample_v1",
            "count": 2,
            "manifest_sha256": value_sha256(manifest_body),
            **manifest_body,
        }
        _write(self.manifest_path, manifest)
        manifest_binding = {
            **_binding(self.manifest_path),
            "protocol": manifest["protocol"],
            "count": manifest["count"],
            "manifest_sha256": manifest["manifest_sha256"],
        }
        self.external_identity = {
            "protocol": "research-v2-external-coco-identity-v1",
            "repository_root": str(self.coco_root.resolve()),
            "git_commit": "a" * 40,
            "runner": _binding(self.runner_path),
        }
        system_body = {
            "protocol": "research-v2-frozen-system-v2",
            "external_evaluation": {
                "coco_1300_manifest": manifest_binding,
                "coco_runner": self.external_identity,
                "coco_scoring_spec": {
                    **_binding(self.spec_path),
                    "spec_id": self.spec["spec_id"],
                    "winner_run_id": self.winner_run_id,
                },
            },
        }
        self.system = {**system_body, "system_id": value_sha256(system_body)}
        self.system_path = self.root / "frozen-system.json"
        _write(self.system_path, self.system)

        self.summary_path = (
            self.artifact_root
            / "runs"
            / self.winner_run_id
            / "coco_review_batch.json"
        )
        _write(self.summary_path, {"status": "complete"})
        self.summary_bytes = self.summary_path.read_bytes()
        paper_ids = ["paper-1", "paper-2"]
        source_arms = {}
        for mode in ("meta", "concat"):
            paths = []
            for paper_id in paper_ids:
                path = self.artifact_root / "final" / mode / f"{paper_id}.json"
                _write(
                    path,
                    {
                        "paper_id": paper_id,
                        "review_text": f"{mode} review for {paper_id}",
                    },
                )
                paths.append(str(path.resolve()))
            source_arms[mode] = {
                "mode": mode,
                "run_id": f"{self.winner_run_id}-{mode}",
                "count": 2,
                "paper_ids_sha256": value_sha256(paper_ids),
                "final_review_ids_sha256": value_sha256([f"f-{mode}-1", f"f-{mode}-2"]),
                "source_review_ids_sha256": value_sha256([["a"], ["b"]]),
                "records_sha256": value_sha256([mode, "records"]),
                "files_sha256": value_sha256(paths),
                "paths": paths,
            }
        source_body = {
            "protocol": SCORING_SOURCE_PROTOCOL,
            "run_id": self.winner_run_id,
            "system": {
                **_binding(self.system_path),
                "system_id": self.system["system_id"],
                "spec_id": "formal-spec-id",
            },
            "coco_manifest": {
                **_binding(self.manifest_path),
                "manifest_sha256": manifest["manifest_sha256"],
                "count": 2,
                "paper_ids_sha256": value_sha256(paper_ids),
            },
            "summary": _binding(self.summary_path),
            "run_spec_sha256": "b" * 64,
            "specialists": {},
            "arms": source_arms,
        }
        self.source = {**source_body, "source_id": value_sha256(source_body)}
        self.spec_return = self.spec

    @contextlib.contextmanager
    def patched(self):
        def validate_batch(**_kwargs):
            if file_sha256(self.summary_path) != self.source["summary"]["sha256"]:
                raise RuntimeError("winner upstream drift")
            return copy.deepcopy(self.source)

        def validate_artifacts(output, **_kwargs):
            runner = read_json(output / audit_module.coco_module.COCO_RUNNER_ARTIFACT)
            names = ["runner", "split", "classify"]
            if runner.get("evaluation_enabled"):
                names.append("evaluation")
            paths = {
                "runner": output / audit_module.coco_module.COCO_RUNNER_ARTIFACT,
                "split": output / audit_module.coco_module.COCO_SPLIT_ARTIFACT,
                "classify": output / audit_module.coco_module.COCO_CLASSIFY_ARTIFACT,
                "evaluation": output / audit_module.coco_module.COCO_EVALUATION_ARTIFACT,
            }
            return {name: _binding(paths[name]) for name in names}

        with (
            patch.object(
                audit_module,
                "validate_frozen_system_transition",
                side_effect=lambda **_kwargs: read_json(self.system_path),
            ),
            patch.object(
                audit_module,
                "validate_coco_scoring_spec",
                side_effect=lambda *_args, **_kwargs: copy.deepcopy(self.spec_return),
            ),
            patch.object(
                audit_module,
                "validate_completed_coco_review_batch",
                side_effect=validate_batch,
            ),
            patch.object(
                audit_module.coco_module,
                "_validate_external_coco_runtime",
                return_value=(copy.deepcopy(self.external_identity), [str(self.runner_path)]),
            ),
            patch.object(
                audit_module.coco_module,
                "_validate_coco_artifacts",
                side_effect=validate_artifacts,
            ),
        ):
            yield

    def seal(self) -> dict:
        return seal_coco_pre_score(
            frozen_system_path=self.system_path,
            artifact_root=self.artifact_root,
            winner_run_id=self.winner_run_id,
            repository_root=self.repository_root,
            output_path=self.seal_path,
        )

    def complete_outputs(self, seal: dict, *, evaluation_enabled: bool = True) -> None:
        for mode in ("meta", "concat"):
            output = Path(seal["arms"][mode]["output_root"])
            _write(output / audit_module.coco_module.COCO_SPLIT_ARTIFACT, "split\n")
            _write(output / audit_module.coco_module.COCO_CLASSIFY_ARTIFACT, "classify\n")
            if evaluation_enabled:
                _write(
                    output / audit_module.coco_module.COCO_EVALUATION_ARTIFACT,
                    {"evaluation": "complete"},
                )
            runner = {
                "protocol": audit_module.coco_module.COCO_RUNNER_PROTOCOL,
                "status": "complete",
                "evaluation_enabled": evaluation_enabled,
                "candidate_input": {
                    "path": seal["arms"][mode]["candidate"]["path"],
                    "sha256": seal["arms"][mode]["candidate"]["sha256"],
                },
                "settings": audit_module._expected_runner_settings(
                    spec=self.spec, mode=mode
                ),
                "scoring_spec": {
                    name: seal["scoring_spec"][name]
                    for name in ("path", "sha256", "spec_id")
                },
            }
            runner_path = output / audit_module.coco_module.COCO_RUNNER_ARTIFACT
            _write(runner_path, runner)
            artifact_paths = {
                "runner": runner_path,
                "split": output / audit_module.coco_module.COCO_SPLIT_ARTIFACT,
                "classify": output / audit_module.coco_module.COCO_CLASSIFY_ARTIFACT,
            }
            if evaluation_enabled:
                artifact_paths["evaluation"] = (
                    output / audit_module.coco_module.COCO_EVALUATION_ARTIFACT
                )
            report = {
                "protocol": "research-v2-external-coco-v1",
                "status": "complete",
                "returncode": 0,
                "artifact_validation_error": "",
                "runtime_validation_error": "",
                "frozen_system_id": seal["frozen_system"]["system_id"],
                "frozen_system_sha256": seal["frozen_system"]["sha256"],
                "coco_1300_manifest_sha256": seal["source"]["coco_manifest"]["sha256"],
                "scoring_source": seal["arms"][mode]["scoring_source"],
                "candidate_input": seal["arms"][mode]["candidate"]["path"],
                "candidate_input_sha256": seal["arms"][mode]["candidate"]["sha256"],
                "external_coco_identity": seal["external_coco"],
                "artifacts": {
                    name: _binding(path) for name, path in artifact_paths.items()
                },
            }
            _write(output / "run.json", report)


class CocoFinalAuditTest(unittest.TestCase):
    def test_happy_seal_rebuild_is_idempotent_and_detects_upstream_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                first = fixture.seal()
                first_bytes = fixture.seal_path.read_bytes()
                self.assertEqual(fixture.seal(), first)
                self.assertEqual(
                    validate_coco_pre_score_seal(
                        fixture.seal_path,
                        repository_root=fixture.repository_root,
                    ),
                    first,
                )
                self.assertEqual(fixture.seal_path.read_bytes(), first_bytes)
                self.assertEqual(first["seal_id"], value_sha256({
                    name: value for name, value in first.items() if name != "seal_id"
                }))
                self.assertNotIn("timestamp", json.dumps(first).lower())

                _write(fixture.summary_path, {"status": "drifted"})
                with self.assertRaisesRegex(ValueError, "summary SHA256 differs"):
                    validate_coco_pre_score_seal(
                        fixture.seal_path,
                        repository_root=fixture.repository_root,
                    )

    def test_seal_rejects_shared_output_root_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.spec_return = copy.deepcopy(fixture.spec)
            fixture.spec_return["arms"]["concat"]["output_root"] = (
                fixture.spec_return["arms"]["meta"]["output_root"]
            )
            with fixture.patched(), self.assertRaisesRegex(ValueError, "output roots"):
                fixture.seal()

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.spec_return = copy.deepcopy(fixture.spec)
            fixture.spec_return["arms"]["concat"]["cache_versions"]["split"] = (
                fixture.spec_return["arms"]["meta"]["cache_versions"]["split"]
            )
            with fixture.patched(), self.assertRaisesRegex(ValueError, "six disjoint"):
                fixture.seal()

    def test_happy_final_audit_is_self_hashed_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                first = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                first_bytes = fixture.audit_path.read_bytes()
                second = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                self.assertEqual(first, second)
                self.assertEqual(fixture.audit_path.read_bytes(), first_bytes)
                self.assertEqual(first["audit_id"], value_sha256({
                    name: value for name, value in first.items() if name != "audit_id"
                }))
                self.assertNotIn("timestamp", json.dumps(first).lower())

                occupied = fixture.root / "occupied-audit.json"
                _write(occupied, {"occupied": True})
                with self.assertRaises(FileExistsError):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=occupied,
                    )
                self.assertEqual(read_json(occupied), {"occupied": True})

    def test_final_audit_rejects_disabled_evaluation_and_shared_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal, evaluation_enabled=False)
                with self.assertRaisesRegex(ValueError, "evaluation to be enabled"):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                meta_runner_path = (
                    Path(seal["arms"]["meta"]["output_root"])
                    / audit_module.coco_module.COCO_RUNNER_ARTIFACT
                )
                runner = read_json(meta_runner_path)
                runner["settings"]["split"]["cache_version"] = seal["arms"][
                    "concat"
                ]["cache_versions"]["split"]
                _write(meta_runner_path, runner)
                meta_report_path = Path(seal["arms"]["meta"]["output_root"]) / "run.json"
                report = read_json(meta_report_path)
                report["artifacts"]["runner"] = _binding(meta_runner_path)
                _write(meta_report_path, report)
                with self.assertRaisesRegex(ValueError, "settings differ"):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )


if __name__ == "__main__":
    unittest.main()
