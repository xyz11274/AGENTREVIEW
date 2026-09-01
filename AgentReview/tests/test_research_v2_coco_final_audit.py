import contextlib
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import coco_final_audit as audit_module
from agentreview.research_v2.coco_final_audit import (
    audit_coco_final,
    seal_coco_pre_score,
    validate_coco_final_audit,
    validate_coco_pre_score_seal,
)
from agentreview.research_v2.coco_review_batch import SCORING_SOURCE_PROTOCOL
from agentreview.research_v2.coco_service_runtime import (
    PROTOCOL as SERVICE_RUNTIME_PROTOCOL,
)
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
            "src/evaluation/table_1_contract.py",
            "src/utils.py",
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
        evaluation_model_file = (
            self.coco_root / "model/bertscore_roberta_large/model.safetensors"
        )
        _write(evaluation_model_file, "roberta-large")
        evaluation_model_binding = _binding(evaluation_model_file)
        evaluation_model_binding["size_bytes"] = evaluation_model_file.stat().st_size
        models["evaluation"] = {
            "model_type": "roberta-large",
            "root": str(evaluation_model_file.parent.resolve()),
            "num_layers": 17,
            "rescale_with_baseline": False,
            "files": [evaluation_model_binding],
        }
        bert_score_model = {
            "model_type": "roberta-large",
            "root": models["evaluation"]["root"],
            "num_layers": 17,
            "rescale_with_baseline": False,
            "file_count": 1,
            "tree_id": value_sha256(
                {
                    "root": models["evaluation"]["root"],
                    "files": models["evaluation"]["files"],
                }
            ),
        }

        scientific = {
            "split": {
                "base_url": "http://split/v1",
                "model": "split-model",
                "max_in_flight_client_requests": 2,
                "max_completion_tokens": 8192,
                "context_window_tokens": 40960,
                "prompt_preflight": {
                    "algorithm": "qwen-chat-template-v1",
                    "max_model_len": 40960,
                    "max_completion_tokens": 8192,
                    "enable_thinking": True,
                },
                "effort": "medium",
            },
            "classify": {
                "base_url": "http://classify/v1",
                "model": "classify-model",
                "max_in_flight_client_requests": 3,
                "max_completion_tokens": 4096,
                "context_window_tokens": 32768,
                "prompt_preflight": {
                    "algorithm": "qwen-classify-chat-template-v1",
                    "max_model_len": 32768,
                    "max_completion_tokens": 4096,
                    "enable_thinking": True,
                },
                "effort": "medium",
            },
            "evaluation": {
                "enabled": True,
                "require_complete": True,
                "benchmark_files": [benchmark_file.resolve().as_posix()],
                "complete_human_classify_file": str(complete_file.resolve()),
                "reviewer_selection_file": str(selection_file.resolve()),
                "base_url": "https://evaluation/v1",
                "model": "judge-model",
                "max_in_flight_client_requests": 4,
                "effort": "medium",
                "temperature": None,
                "max_completion_tokens": 32768,
                "context_window_tokens": 400000,
                "prompt_preflight": {
                    "algorithm": "utf8-byte-upper-bound-v1",
                    "max_model_len": 400000,
                    "max_completion_tokens": 32768,
                    "chat_overhead_tokens": 1024,
                },
                "cuda_visible_devices": "",
                "bert_score_model": bert_score_model,
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
        services = {
            "software": {"vllm": "test-vllm", "transformers": "test-transformers"},
        }
        for stage, values in {
            "split": ("0", 13099, "split-model", 40960, 8),
            "classify": ("1", 13098, "classify-model", 32768, 16),
        }.items():
            gpu, port, model_name, max_model_len, max_num_seqs = values
            services[stage] = {
                "cuda_visible_devices": gpu,
                "python_executable": str(Path(sys.executable).resolve()),
                "vllm_attention_backend": "FLASH_ATTN",
                "host": "127.0.0.1",
                "port": port,
                "served_model_name": model_name,
                "model_root": models[stage]["root"],
                "max_model_len": max_model_len,
                "max_num_seqs": max_num_seqs,
                "tensor_parallel_size": 1,
                "gpu_memory_utilization": 0.9,
                "dtype": "bfloat16",
                "language_model_only": True,
                "reasoning_parser": "qwen3",
                "enable_prefix_caching": True,
                "enforce_eager": True,
            }
        services["evaluation"] = {
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": "3.12.13",
            "sys_prefix": str(Path(sys.prefix).resolve()),
            "user_site_enabled": False,
            "isolated_mode": False,
            "no_user_site": True,
            "dont_write_bytecode": True,
            "ignore_environment": False,
            "packages": {
                "bert-score": "0.3.13",
                "nltk": "3.9.3",
                "numpy": "2.2.6",
                "openai": "2.6.0",
                "requests": "2.32.5",
                "rouge-score": "0.1.2",
                "torch": "2.8.0",
                "tqdm": "4.67.1",
                "transformers": "4.57.1",
            },
            "prefix_tree": {
                "root": str(Path(sys.prefix).resolve()),
                "file_count": 10,
                "total_bytes": 100,
                "symlink_count": 1,
                "pyc_file_count": 2,
                "tree_sha256": "7" * 64,
            },
            "transport": {
                "HTTP_PROXY": "http://127.0.0.1:7898",
                "HTTPS_PROXY": "http://127.0.0.1:7898",
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
            "services": services,
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
        runtime_services = {}
        for stage in ("split", "classify"):
            service = services[stage]
            runtime_services[stage] = {
                "listener": {
                    "host": service["host"],
                    "port": service["port"],
                    "exactly_one_process": True,
                },
                "process": {
                    "python_executable": service["python_executable"],
                    "resolved_python_executable": service["python_executable"],
                    "working_directory": str(self.coco_root),
                    "entrypoint": "vllm.entrypoints.openai.api_server",
                    "arguments": {
                        name: service[name]
                        for name in (
                            "model_root",
                            "served_model_name",
                            "host",
                            "port",
                            "tensor_parallel_size",
                            "max_model_len",
                            "max_num_seqs",
                            "gpu_memory_utilization",
                            "dtype",
                            "reasoning_parser",
                            "language_model_only",
                            "enable_prefix_caching",
                            "enforce_eager",
                        )
                    },
                    "environment": {
                        "CUDA_VISIBLE_DEVICES": service["cuda_visible_devices"],
                        "VLLM_ATTENTION_BACKEND": service[
                            "vllm_attention_backend"
                        ],
                        "PYTHONHOME_unset": True,
                        "PYTHONPATH_unset": True,
                        "CUDA_DEVICE_ORDER_unset": True,
                        "CUDA_HOME": "/usr/local/cuda",
                        "CUDAToolkit_ROOT": "/usr/local/cuda",
                        "unregistered_runtime_controls_absent": True,
                    },
                },
                "engine": {
                    "present": True,
                    "resolved_python_executable": service[
                        "python_executable"
                    ],
                    "working_directory": str(self.coco_root),
                    "gpu_index": service["cuda_visible_devices"],
                    "exactly_one_compute_device": True,
                },
                "endpoint": {
                    "health": {"status": 200},
                    "models": {
                        "status": 200,
                        "object": "list",
                        "data": [
                            {
                                "id": service["served_model_name"],
                                "object": "model",
                                "owned_by": "vllm",
                                "root": service["model_root"],
                                "parent": None,
                                "max_model_len": service["max_model_len"],
                            }
                        ],
                    },
                },
            }
        runtime_body = {
            "protocol": SERVICE_RUNTIME_PROTOCOL,
            "scoring_spec_id": self.spec["spec_id"],
            "software": dict(services["software"]),
            "evaluation_runtime": dict(services["evaluation"]),
            "services": runtime_services,
        }
        self.runtime_proof = {
            **runtime_body,
            "proof_id": value_sha256(runtime_body),
        }

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
                audit_module,
                "validate_coco_service_runtime",
                side_effect=lambda *_args, **_kwargs: copy.deepcopy(
                    self.runtime_proof
                ),
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
            settings = audit_module._expected_runner_settings(
                spec=self.spec,
                mode=mode,
            )
            settings["split"]["prompt_preflight"] = {
                "algorithm": "qwen-chat-template-v1",
                "max_model_len": 40960,
                "max_completion_tokens": 8192,
                "enable_thinking": True,
                "candidate_count": 2,
                "max_prompt_tokens": 100,
                "max_total_tokens": 8292,
                "prompt_token_counts_sha256": "6" * 64,
            }
            settings["classify"]["prompt_preflight"] = {
                "algorithm": "qwen-classify-chat-template-v1",
                "max_model_len": 32768,
                "max_completion_tokens": 4096,
                "enable_thinking": True,
                "candidate_count": 2,
                "opinion_count": 3,
                "max_prompt_tokens": 100,
                "max_total_tokens": 4196,
                "prompt_token_counts_sha256": "7" * 64,
            }
            runner = {
                "protocol": audit_module.coco_module.COCO_RUNNER_PROTOCOL,
                "status": "complete",
                "evaluation_enabled": evaluation_enabled,
                "candidate_input": {
                    "path": seal["arms"][mode]["candidate"]["path"],
                    "sha256": seal["arms"][mode]["candidate"]["sha256"],
                },
                "settings": settings,
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
                "pre_score_seal": {
                    "path": str(self.seal_path),
                    "sha256": file_sha256(self.seal_path),
                    "seal_id": seal["seal_id"],
                },
                "scoring_spec": seal["scoring_spec"],
                "service_runtime": seal["service_runtime"],
                "candidate_input": seal["arms"][mode]["candidate"]["path"],
                "candidate_input_sha256": seal["arms"][mode]["candidate"]["sha256"],
                "external_coco_identity": seal["external_coco"],
                "command": [
                    self.spec["services"]["evaluation"]["python_executable"],
                    "-s",
                    "-B",
                    str(self.runner_path),
                ],
                "environment": {
                    name: value
                    for name, value in audit_module.coco_module._formal_coco_environment_identity(
                        spec=self.spec,
                        final_mode=mode,
                        candidate_input=Path(
                            seal["arms"][mode]["candidate"]["path"]
                        ),
                        output=output,
                        scoring_spec_path=self.spec_path,
                    ).items()
                },
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

    def test_formal_outputs_reject_symlink_escape_without_outside_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            meta_output = Path(fixture.spec["arms"]["meta"]["output_root"])
            meta_output.mkdir(parents=True)
            candidate = meta_output / audit_module.CANDIDATE_INPUT_NAME
            outside_candidate = fixture.root / "outside-candidate.jsonl"
            candidate.symlink_to(outside_candidate)
            with fixture.patched(), self.assertRaisesRegex(
                ValueError,
                "symbolic link",
            ):
                fixture.seal()
            self.assertTrue(candidate.is_symlink())
            self.assertFalse(outside_candidate.exists())
            self.assertFalse(fixture.seal_path.exists())

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            outside_seal = fixture.root / "outside-seal.json"
            fixture.seal_path.symlink_to(outside_seal)
            with fixture.patched(), self.assertRaisesRegex(
                ValueError,
                "symbolic link",
            ):
                fixture.seal()
            self.assertTrue(fixture.seal_path.is_symlink())
            self.assertFalse(outside_seal.exists())

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                outside_audit = fixture.root / "outside-audit.json"
                fixture.audit_path.symlink_to(outside_audit)
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )
                self.assertTrue(fixture.audit_path.is_symlink())
                self.assertFalse(outside_audit.exists())

    def test_validation_never_recreates_a_missing_sealed_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                candidate = Path(seal["arms"]["meta"]["candidate"]["path"])
                candidate.unlink()
                with self.assertRaisesRegex(
                    FileNotFoundError,
                    "candidate is missing",
                ):
                    validate_coco_pre_score_seal(
                        fixture.seal_path,
                        repository_root=fixture.repository_root,
                    )
                self.assertFalse(candidate.exists())
                with self.assertRaisesRegex(
                    FileNotFoundError,
                    "candidate is missing",
                ):
                    fixture.seal()
                self.assertFalse(candidate.exists())

    def test_existing_tampered_seal_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                fixture.seal()
                tampered = read_json(fixture.seal_path)
                tampered["firewall"][
                    "external_evaluation_is_downstream_of_frozen_reviews"
                ] = False
                _write(fixture.seal_path, tampered)
                before = fixture.seal_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "self-hash differs"):
                    fixture.seal()
                self.assertEqual(fixture.seal_path.read_bytes(), before)

    def test_rehashed_boolean_declarations_reject_integer_substitutes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                tampered_seal = copy.deepcopy(seal)
                tampered_seal["firewall"][
                    "external_evaluation_is_downstream_of_frozen_reviews"
                ] = 1
                tampered_seal["seal_id"] = value_sha256(
                    {
                        name: value
                        for name, value in tampered_seal.items()
                        if name != "seal_id"
                    }
                )
                _write(fixture.seal_path, tampered_seal)
                with self.assertRaisesRegex(
                    ValueError,
                    "firewall declarations values must all be true booleans",
                ):
                    validate_coco_pre_score_seal(
                        fixture.seal_path,
                        repository_root=fixture.repository_root,
                    )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                tampered_audit = copy.deepcopy(audit)
                tampered_audit["declarations"][
                    "output_roots_are_disjoint"
                ] = 1
                tampered_audit["audit_id"] = value_sha256(
                    {
                        name: value
                        for name, value in tampered_audit.items()
                        if name != "audit_id"
                    }
                )
                _write(fixture.audit_path, tampered_audit)
                with self.assertRaisesRegex(
                    ValueError,
                    "final audit declarations values must all be true booleans",
                ):
                    validate_coco_final_audit(fixture.audit_path)

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                tampered_audit = copy.deepcopy(audit)
                tampered_audit["arms"]["meta"]["settings"]["evaluation"][
                    "enabled"
                ] = 1
                tampered_audit["audit_id"] = value_sha256(
                    {
                        name: value
                        for name, value in tampered_audit.items()
                        if name != "audit_id"
                    }
                )
                _write(fixture.audit_path, tampered_audit)
                with self.assertRaisesRegex(ValueError, "arm semantics differ"):
                    validate_coco_final_audit(fixture.audit_path)

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
                self.assertEqual(validate_coco_final_audit(fixture.audit_path), first)
                self.assertEqual(fixture.audit_path.read_bytes(), first_bytes)
                self.assertEqual(first["audit_id"], value_sha256({
                    name: value for name, value in first.items() if name != "audit_id"
                }))
                for mode in ("meta", "concat"):
                    split_preflight = first["arms"][mode]["settings"]["split"][
                        "prompt_preflight"
                    ]
                    self.assertEqual(split_preflight["candidate_count"], 2)
                    self.assertEqual(split_preflight["max_prompt_tokens"], 100)
                    self.assertEqual(split_preflight["max_total_tokens"], 8292)
                    classify_preflight = first["arms"][mode]["settings"][
                        "classify"
                    ]["prompt_preflight"]
                    self.assertEqual(classify_preflight["candidate_count"], 2)
                    self.assertEqual(classify_preflight["opinion_count"], 3)
                    self.assertEqual(classify_preflight["max_total_tokens"], 4196)
                    evaluation_settings = first["arms"][mode]["settings"][
                        "evaluation"
                    ]
                    self.assertEqual(
                        evaluation_settings["bert_score_model"],
                        fixture.spec["scientific_settings"]["evaluation"][
                            "bert_score_model"
                        ],
                    )
                    self.assertEqual(
                        evaluation_settings["python_runtime"],
                        fixture.spec["services"]["evaluation"],
                    )
                    self.assertIs(evaluation_settings["require_complete"], True)
                    self.assertIsNone(evaluation_settings["temperature"])
                    self.assertEqual(
                        evaluation_settings["max_completion_tokens"],
                        32768,
                    )
                    self.assertEqual(
                        evaluation_settings["prompt_preflight"],
                        fixture.spec["scientific_settings"]["evaluation"][
                            "prompt_preflight"
                        ],
                    )
                    self.assertEqual(
                        evaluation_settings["code_files"]["table_1_contract"],
                        next(
                            binding
                            for binding in fixture.spec["critical_files"]
                            if binding["path"].endswith(
                                "src/evaluation/table_1_contract.py"
                            )
                        ),
                    )
                    self.assertEqual(
                        evaluation_settings["code_files"]["llm_client"],
                        next(
                            binding
                            for binding in fixture.spec["critical_files"]
                            if binding["path"].endswith("src/utils.py")
                        ),
                    )
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

    def test_offline_final_validator_rejects_rehashed_binding_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                tampered = copy.deepcopy(audit)
                tampered["arms"]["meta"]["run"]["sha256"] = "0" * 64
                tampered["audit_id"] = value_sha256(
                    {
                        name: value
                        for name, value in tampered.items()
                        if name != "audit_id"
                    }
                )
                _write(fixture.audit_path, tampered)
                with self.assertRaisesRegex(ValueError, "arm semantics differ"):
                    validate_coco_final_audit(fixture.audit_path)

    def test_offline_final_validator_rejects_rehashed_failed_run_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit = audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                run_path = Path(audit["arms"]["meta"]["run"]["path"])
                run = read_json(run_path)
                run.update(
                    {
                        "status": "failed",
                        "returncode": 99,
                        "command": ["/tmp/unregistered-runner"],
                        "environment": {"UNREGISTERED": "1"},
                    }
                )
                _write(run_path, run)

                tampered = copy.deepcopy(audit)
                tampered["arms"]["meta"]["run"]["sha256"] = file_sha256(
                    run_path
                )
                tampered["audit_id"] = value_sha256(
                    {
                        name: value
                        for name, value in tampered.items()
                        if name != "audit_id"
                    }
                )
                _write(fixture.audit_path, tampered)
                with self.assertRaisesRegex(
                    (ValueError, RuntimeError),
                    "command identity differs|must be exactly|run report identity differs",
                ):
                    validate_coco_final_audit(fixture.audit_path)

    def test_final_validator_live_mode_rechecks_service_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                changed = copy.deepcopy(seal["service_runtime"])
                changed["services"]["split"]["engine"]["gpu_index"] = "7"
                changed_body = {
                    name: value
                    for name, value in changed.items()
                    if name != "proof_id"
                }
                changed["proof_id"] = value_sha256(changed_body)
                with (
                    patch.object(
                        audit_module,
                        "_validated_service_runtime",
                        return_value=changed,
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        "Live CoCo service runtime differs",
                    ),
                ):
                    validate_coco_final_audit(
                        fixture.audit_path,
                        require_live_service=True,
                    )

    def test_existing_tampered_final_audit_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                audit_coco_final(
                    pre_score_seal_path=fixture.seal_path,
                    repository_root=fixture.repository_root,
                    output_path=fixture.audit_path,
                )
                _write(fixture.audit_path, {"tampered": True})
                before = fixture.audit_path.read_bytes()
                with self.assertRaisesRegex(FileExistsError, "Immutable artifact"):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )
                self.assertEqual(fixture.audit_path.read_bytes(), before)

    def test_final_audit_rejects_service_runtime_drift_after_both_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                runtime = copy.deepcopy(fixture.runtime_proof)
                runtime["services"]["split"]["listener"]["port"] += 1
                runtime_body = {
                    name: value
                    for name, value in runtime.items()
                    if name != "proof_id"
                }
                runtime["proof_id"] = value_sha256(runtime_body)
                with (
                    patch.object(
                        audit_module,
                        "_validated_service_runtime",
                        side_effect=[
                            copy.deepcopy(seal["service_runtime"]),
                            runtime,
                        ],
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        "service runtime differs from the pre-score proof",
                    ),
                ):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )

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

        mutations = {
            "require complete": lambda settings: settings["evaluation"].__setitem__(
                "require_complete", False
            ),
            "temperature": lambda settings: settings["evaluation"].__setitem__(
                "temperature", 0
            ),
            "evaluation completion budget": lambda settings: settings[
                "evaluation"
            ].__setitem__("max_completion_tokens", 65536),
            "evaluation preflight": lambda settings: settings["evaluation"][
                "prompt_preflight"
            ].__setitem__("chat_overhead_tokens", 0),
            "transport": lambda settings: settings["evaluation"][
                "python_runtime"
            ]["transport"].__setitem__(
                "HTTPS_PROXY", "http://127.0.0.1:7899"
            ),
            "llm client code": lambda settings: settings["evaluation"][
                "code_files"
            ]["llm_client"].__setitem__("sha256", "0" * 64),
            "split completion budget": lambda settings: settings["split"].__setitem__(
                "max_completion_tokens", 32768
            ),
            "classify completion budget": lambda settings: settings[
                "classify"
            ].__setitem__("max_completion_tokens", 12288),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = _Fixture(directory)
                with fixture.patched():
                    seal = fixture.seal()
                    fixture.complete_outputs(seal)
                    output = Path(seal["arms"]["meta"]["output_root"])
                    runner_path = (
                        output / audit_module.coco_module.COCO_RUNNER_ARTIFACT
                    )
                    runner = read_json(runner_path)
                    mutate(runner["settings"])
                    _write(runner_path, runner)
                    report_path = output / "run.json"
                    report = read_json(report_path)
                    report["artifacts"]["runner"] = _binding(runner_path)
                    _write(report_path, report)
                    with self.assertRaisesRegex(ValueError, "settings differ"):
                        audit_coco_final(
                            pre_score_seal_path=fixture.seal_path,
                            repository_root=fixture.repository_root,
                            output_path=fixture.audit_path,
                        )

    def test_final_audit_rejects_legacy_report_without_sealed_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                meta_run = Path(seal["arms"]["meta"]["output_root"]) / "run.json"
                legacy = read_json(meta_run)
                for field in (
                    "pre_score_seal",
                    "scoring_spec",
                    "service_runtime",
                    "command",
                    "environment",
                ):
                    legacy.pop(field)
                _write(meta_run, legacy)
                with self.assertRaisesRegex(
                    ValueError,
                    "run report fields differ",
                ):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )

    def test_final_audit_rejects_report_with_captured_process_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            with fixture.patched():
                seal = fixture.seal()
                fixture.complete_outputs(seal)
                meta_run = Path(seal["arms"]["meta"]["output_root"]) / "run.json"
                report = read_json(meta_run)
                report["stdout"] = "test-key"
                report["stderr"] = "test-key"
                _write(meta_run, report)
                with self.assertRaisesRegex(ValueError, "run report fields differ"):
                    audit_coco_final(
                        pre_score_seal_path=fixture.seal_path,
                        repository_root=fixture.repository_root,
                        output_path=fixture.audit_path,
                    )
                self.assertFalse(fixture.audit_path.exists())


if __name__ == "__main__":
    unittest.main()
