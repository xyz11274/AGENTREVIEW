import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import coco as coco_module
from agentreview.research_v2.io import (
    atomic_write_json,
    file_sha256,
    read_json,
    value_sha256,
    write_immutable_jsonl,
)

FORMAL_KEY_ENV = {coco_module.COCO_API_KEY_ENV: "test-key"}


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


class _ExecutionFixture:
    def __init__(self, directory: str):
        self.root = Path(directory).resolve()
        self.artifact_root = self.root / "artifacts"
        self.coco_root = self.root / "coco"
        self.runner = self.coco_root / "scripts" / "run_official_candidate.py"
        self.runner.parent.mkdir(parents=True)
        self.runner.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.runner.chmod(0o700)
        self.identity = {
            "protocol": coco_module.EXTERNAL_COCO_IDENTITY_PROTOCOL,
            "repository_root": str(self.coco_root),
            "git_commit": "1" * 40,
            "runner": _binding(self.runner),
        }

        self.manifest = self.root / "manifest.json"
        atomic_write_json(
            self.manifest,
            {"count": 1, "records": [{"paper_id": "p1"}]},
        )
        self.system_path = self.root / "system.json"
        atomic_write_json(
            self.system_path,
            {
                "system_id": "system-1",
                "external_evaluation": {
                    "coco_runner": self.identity,
                    "coco_1300_manifest": _binding(self.manifest),
                },
            },
        )

        benchmark = self.coco_root / "benchmark" / "benchmark_test_2026.jsonl"
        complete_human = (
            self.coco_root / "benchmark" / "benchmark_all_human_classify.jsonl"
        )
        reviewer_selection = (
            self.coco_root / "benchmark" / "benchmark_reviewer_selection.json"
        )
        for path in (benchmark, complete_human, reviewer_selection):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        self.bert_score_model_root = (
            self.coco_root / "model" / "bertscore_roberta_large"
        )
        self.bert_score_model_root.mkdir(parents=True)

        self.outputs = {
            mode: self.artifact_root / "coco_scoring" / f"winner-v1-{mode}"
            for mode in ("meta", "concat")
        }
        caches = {
            mode: {
                stage: f"winner-v1-{mode}-{stage}-cache-v1"
                for stage in ("split", "classify", "evaluation")
            }
            for mode in ("meta", "concat")
        }
        self.spec = {
            "spec_id": "2" * 64,
            "winner_run_id": "winner-v1",
            "artifact_root": str(self.artifact_root),
            "repository": {"root": str(self.coco_root)},
            "scientific_settings": {
                "split": {
                    "base_url": "http://127.0.0.1:13099/v1",
                    "model": "Qwen3-8B-Split",
                    "max_in_flight_client_requests": 64,
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
                    "base_url": "http://127.0.0.1:13098/v1",
                    "model": "Qwen3-8B-Classify",
                    "max_in_flight_client_requests": 128,
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
                    "benchmark_files": [str(benchmark)],
                    "complete_human_classify_file": str(complete_human),
                    "reviewer_selection_file": str(reviewer_selection),
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-5-mini",
                    "max_in_flight_client_requests": 128,
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
                    "bert_score_model": {
                        "model_type": "roberta-large",
                        "root": str(self.bert_score_model_root),
                        "num_layers": 17,
                        "rescale_with_baseline": False,
                        "file_count": 2,
                        "tree_id": "5" * 64,
                    },
                },
            },
            "services": {
                "split": {"python_executable": "/gpu/split/bin/python"},
                "classify": {"python_executable": "/gpu/classify/bin/python"},
                "evaluation": {
                    "python_executable": sys.executable,
                    "transport": {
                        "HTTP_PROXY": "http://127.0.0.1:7898",
                        "HTTPS_PROXY": "http://127.0.0.1:7898",
                    },
                },
            },
            "arms": {
                mode: {
                    "mode": mode,
                    "output_root": str(self.outputs[mode]),
                    "cache_versions": caches[mode],
                }
                for mode in ("meta", "concat")
            },
        }
        self.spec_path = self.root / "scoring-spec.json"
        atomic_write_json(self.spec_path, self.spec)
        self.spec_binding = {
            "path": str(self.spec_path),
            "sha256": file_sha256(self.spec_path),
            "spec_id": self.spec["spec_id"],
            "winner_run_id": "winner-v1",
        }

        paper_ids_sha256 = value_sha256(["p1"])
        self.arms = {}
        for mode in ("meta", "concat"):
            candidate = self.outputs[mode] / "candidate_official_input.jsonl"
            write_immutable_jsonl(candidate, [{"id": "p1", "text": f"{mode} review"}])
            self.arms[mode] = {
                "mode": mode,
                "output_root": str(self.outputs[mode]),
                "scoring_source": {
                    "source_id": "source-1",
                    "winner_run_id": "winner-v1",
                    "final_mode": mode,
                    "final_run_id": f"winner-v1-{mode}",
                    "count": 1,
                    "paper_ids_sha256": paper_ids_sha256,
                    "final_review_ids_sha256": f"{mode}-final-ids",
                    "source_review_ids_sha256": f"{mode}-source-ids",
                    "records_sha256": f"{mode}-records",
                    "files_sha256": f"{mode}-files",
                },
                "candidate": {
                    **_binding(candidate),
                    "count": 1,
                    "paper_ids_sha256": paper_ids_sha256,
                },
                "cache_versions": caches[mode],
            }
        self.seal = {
            "seal_id": "3" * 64,
            "frozen_system": {
                "path": str(self.system_path),
                "sha256": file_sha256(self.system_path),
                "system_id": "system-1",
            },
            "scoring_spec": self.spec_binding,
            "external_coco": self.identity,
            "service_runtime": {
                "protocol": "research-v2-coco-service-runtime-v1",
                "proof_id": "4" * 64,
            },
            "source": {
                "coco_manifest": {
                    "path": str(self.manifest),
                    "sha256": file_sha256(self.manifest),
                }
            },
            "arms": self.arms,
        }
        self.seal_path = self.root / "pre-score-seal.json"
        atomic_write_json(self.seal_path, self.seal)

    @property
    def command(self) -> list[str]:
        return [sys.executable, "-s", "-B", str(self.runner)]

    def register_clean_git_identity(self) -> None:
        for arguments in (
            ("init",),
            ("config", "user.email", "coco-execution@example.invalid"),
            ("config", "user.name", "CoCo Execution Test"),
            ("add", "."),
            ("commit", "-m", "sealed external runner fixture"),
        ):
            subprocess.run(
                ["git", *arguments],
                cwd=self.coco_root,
                check=True,
                capture_output=True,
                text=True,
            )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.coco_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.identity["git_commit"] = commit
        self.system = read_json(self.system_path)
        self.system["external_evaluation"]["coco_runner"] = copy.deepcopy(
            self.identity
        )
        atomic_write_json(self.system_path, self.system, immutable=False)
        self.seal["frozen_system"]["sha256"] = file_sha256(self.system_path)
        self.seal["external_coco"] = copy.deepcopy(self.identity)
        atomic_write_json(self.seal_path, self.seal, immutable=False)

    def patch_boundaries(self):
        def validate_runtime(*, system, root, command):
            self.assert_runtime(system=system, root=root)
            normalized = list(command)
            if normalized and normalized[0] == "--":
                normalized = normalized[1:]
            return self.identity, normalized

        return (
            patch.object(
                coco_module,
                "_validate_pre_score_seal",
                return_value=self.seal,
            ),
            patch.object(
                coco_module,
                "_validate_external_coco_runtime",
                side_effect=validate_runtime,
            ),
        )

    def assert_runtime(self, *, system, root):
        if system["system_id"] != "system-1" or root != self.coco_root:
            raise AssertionError("execution did not derive its runtime from the seal")

    def write_complete_outputs(
        self,
        *,
        mode: str = "meta",
        evaluation_enabled: bool = True,
        checkpoint_spec: dict[str, str] | None = None,
    ) -> None:
        output = self.outputs[mode]
        output.mkdir(parents=True, exist_ok=True)
        candidate = Path(self.arms[mode]["candidate"]["path"])
        candidate_binding = _binding(candidate)
        split = output / coco_module.COCO_SPLIT_ARTIFACT
        classify = output / coco_module.COCO_CLASSIFY_ARTIFACT
        split.write_text(
            json.dumps(
                {"id": "p1", "sentence_texts": ["review"], "opinions": [[0]]}
            )
            + "\n",
            encoding="utf-8",
        )
        classify.write_text(
            json.dumps(
                {
                    "id": "p1",
                    "sentence_texts": ["review"],
                    "opinions": [[0]],
                    "category": [["QUAL-MET"]],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        settings = {
            "split": {
                "model": "split",
                "prompt_preflight": {
                    "algorithm": "qwen-chat-template-v1",
                    "max_model_len": 40960,
                    "max_completion_tokens": 8192,
                    "enable_thinking": True,
                    "candidate_count": 1,
                    "max_prompt_tokens": 100,
                    "max_total_tokens": 8292,
                    "prompt_token_counts_sha256": "6" * 64,
                },
            },
            "classify": {
                "model": "classify",
                "prompt_preflight": {
                    "algorithm": "qwen-classify-chat-template-v1",
                    "max_model_len": 32768,
                    "max_completion_tokens": 4096,
                    "enable_thinking": True,
                    "candidate_count": 1,
                    "opinion_count": 1,
                    "max_prompt_tokens": 100,
                    "max_total_tokens": 4196,
                    "prompt_token_counts_sha256": "7" * 64,
                },
            },
            "evaluation": {
                "enabled": evaluation_enabled,
                "require_complete": True,
            },
        }
        artifact_paths = {"split": split, "classify": classify}
        if evaluation_enabled:
            evaluation = output / coco_module.COCO_EVALUATION_ARTIFACT
            atomic_write_json(
                evaluation,
                {
                    "input_label": "candidate_official_classify",
                    "overall_metrics": {
                        "full_text": {},
                        "category_correct": {},
                        "paper_level": {},
                    },
                    "category_breakdown": {"category_correct": {}},
                    "table_1_summary": {
                        "input_label": "candidate_official_classify",
                        "absolute_scores": {
                            name: 1.0 for name in coco_module.TABLE_1_COLUMNS
                        },
                        "paper_counts": {
                            name: 1 for name in coco_module.TABLE_1_COLUMNS
                        },
                        "category_coverage": {
                            "item_count": 1,
                            "metric_counts": {"Complete.": 1},
                        },
                    },
                },
            )
            artifact_paths["evaluation"] = evaluation
        artifacts = {name: _binding(path) for name, path in artifact_paths.items()}
        scoring_spec = {
            name: self.spec_binding[name] for name in ("path", "sha256", "spec_id")
        }
        checkpoint_identity = checkpoint_spec or scoring_spec
        stage_inputs = {
            "split": candidate_binding,
            "classify": artifacts["split"],
        }
        if evaluation_enabled:
            stage_inputs["evaluation"] = artifacts["classify"]
        checkpoints = {}
        for stage, output_path in artifact_paths.items():
            body = {
                "protocol": coco_module.COCO_STAGE_PROTOCOL,
                "stage": stage,
                "candidate_input": candidate_binding,
                "input_artifact": stage_inputs[stage],
                "scoring_spec": checkpoint_identity,
                "settings": settings[stage],
                "output_filename": output_path.name,
                "output_sha256": file_sha256(output_path),
            }
            checkpoint = {**body, "checkpoint_id": value_sha256(body)}
            checkpoint_path = (
                output
                / coco_module.COCO_STAGE_ROOT
                / stage
                / coco_module.COCO_STAGE_CHECKPOINT
            )
            atomic_write_json(checkpoint_path, checkpoint)
            checkpoints[stage] = _binding(checkpoint_path)
        atomic_write_json(
            output / coco_module.COCO_RUNNER_ARTIFACT,
            {
                "protocol": coco_module.COCO_RUNNER_PROTOCOL,
                "status": "complete",
                "evaluation_enabled": evaluation_enabled,
                "candidate_input": candidate_binding,
                "scoring_spec": scoring_spec,
                "settings": settings,
                "artifacts": artifacts,
                "stage_checkpoints": checkpoints,
            },
        )


class CocoSealedExecutionTest(unittest.TestCase):
    def test_split_prompt_preflight_summary_is_exact_and_within_context(self):
        summary = {
            "algorithm": "qwen-chat-template-v1",
            "max_model_len": 40960,
            "max_completion_tokens": 8192,
            "enable_thinking": True,
            "candidate_count": 1,
            "max_prompt_tokens": 5487,
            "max_total_tokens": 13679,
            "prompt_token_counts_sha256": "6" * 64,
        }
        self.assertEqual(
            coco_module._validated_split_prompt_preflight(
                summary,
                expected_count=1,
            ),
            summary,
        )

        mutations = {
            "integer boolean": lambda value: value.__setitem__(
                "enable_thinking", 1
            ),
            "boolean candidate count": lambda value: value.__setitem__(
                "candidate_count", True
            ),
            "candidate count differs": lambda value: value.__setitem__(
                "candidate_count", 2
            ),
            "context exceeded": lambda value: value.update(
                max_prompt_tokens=32769,
                max_total_tokens=40961,
            ),
            "digest differs": lambda value: value.__setitem__(
                "prompt_token_counts_sha256", "G" * 64
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                tampered = copy.deepcopy(summary)
                mutate(tampered)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "split prompt preflight identity differs",
                ):
                    coco_module._validated_split_prompt_preflight(
                        tampered,
                        expected_count=1,
                    )

    def test_classify_prompt_preflight_summary_is_exact_and_within_context(self):
        summary = {
            "algorithm": "qwen-classify-chat-template-v1",
            "max_model_len": 32768,
            "max_completion_tokens": 4096,
            "enable_thinking": True,
            "candidate_count": 2,
            "opinion_count": 3,
            "max_prompt_tokens": 6000,
            "max_total_tokens": 10096,
            "prompt_token_counts_sha256": "7" * 64,
        }
        self.assertEqual(
            coco_module._validated_classify_prompt_preflight(
                summary,
                expected_count=2,
                expected_opinion_count=3,
            ),
            summary,
        )

        mutations = {
            "integer boolean": lambda value: value.__setitem__(
                "enable_thinking", 1
            ),
            "boolean opinion count": lambda value: value.__setitem__(
                "opinion_count", True
            ),
            "opinion count differs": lambda value: value.__setitem__(
                "opinion_count", 4
            ),
            "fewer opinions than papers": lambda value: value.__setitem__(
                "opinion_count", 1
            ),
            "context exceeded": lambda value: value.update(
                max_prompt_tokens=28673,
                max_total_tokens=32769,
            ),
            "digest differs": lambda value: value.__setitem__(
                "prompt_token_counts_sha256", "G" * 64
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                tampered = copy.deepcopy(summary)
                mutate(tampered)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "classify prompt preflight identity differs",
                ):
                    coco_module._validated_classify_prompt_preflight(
                        tampered,
                        expected_count=2,
                        expected_opinion_count=3,
                    )

    def test_evaluation_summary_uses_metric_specific_paper_counts(self):
        full_count_columns = {
            "BLEU",
            "ROUGE-L",
            "BERT.",
            "Paper.",
            "Complete.",
        }
        eligible_columns = {
            "Correct.",
            "Thoro.",
            "Ground.",
            "Clarity",
            "Average",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.json"
            counts = {name: 3 for name in coco_module.TABLE_1_COLUMNS}
            for name in eligible_columns:
                counts[name] = 2
            counts["Verify."] = 0
            scores = {name: 1.0 for name in coco_module.TABLE_1_COLUMNS}
            scores["Verify."] = None
            summary = {
                "input_label": "candidate_official_classify",
                "overall_metrics": {
                    "full_text": {},
                    "category_correct": {},
                    "paper_level": {},
                },
                "category_breakdown": {"category_correct": {}},
                "table_1_summary": {
                    "input_label": "candidate_official_classify",
                    "absolute_scores": scores,
                    "paper_counts": counts,
                    "category_coverage": {
                        "item_count": 3,
                        "metric_counts": {"Complete.": 3},
                    },
                },
            }
            atomic_write_json(path, summary)
            coco_module._validate_evaluation_summary(path, expected_count=3)

            mutations = {
                "full metric count": lambda value: value["table_1_summary"][
                    "paper_counts"
                ].__setitem__(next(iter(full_count_columns)), 2),
                "eligible counts differ": lambda value: value["table_1_summary"][
                    "paper_counts"
                ].__setitem__(next(iter(eligible_columns)), 1),
                "verify exceeds eligible": lambda value: value["table_1_summary"][
                    "paper_counts"
                ].__setitem__("Verify.", 3),
                "verify zero has score": lambda value: value["table_1_summary"][
                    "absolute_scores"
                ].__setitem__("Verify.", 1.0),
                "verify count has no score": lambda value: (
                    value["table_1_summary"]["paper_counts"].__setitem__(
                        "Verify.", 1
                    ),
                    value["table_1_summary"]["absolute_scores"].__setitem__(
                        "Verify.", None
                    ),
                ),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    tampered = copy.deepcopy(summary)
                    mutate(tampered)
                    atomic_write_json(path, tampered, immutable=False)
                    with self.assertRaisesRegex(RuntimeError, "exact frozen paper set"):
                        coco_module._validate_evaluation_summary(
                            path,
                            expected_count=3,
                        )

    def test_success_uses_only_sealed_inputs_fixed_env_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.register_clean_git_identity()
            observed_environment = {}
            reported_secret = "runner-leaked-secret"
            real_subprocess_run = subprocess.run

            def complete(command, **kwargs):
                if command and command[0] == "git":
                    return real_subprocess_run(command, **kwargs)
                self.assertEqual(command, fixture.command)
                self.assertNotIn("capture_output", kwargs)
                self.assertNotIn("stdout", kwargs)
                self.assertNotIn("stderr", kwargs)
                observed_environment.update(kwargs["env"])
                fixture.write_complete_outputs()
                return subprocess.CompletedProcess(
                    command,
                    0,
                    reported_secret,
                    reported_secret,
                )

            with (
                patch.dict(
                    os.environ,
                    {
                        "COCO_EVALUATION_API_KEY": reported_secret,
                        "OPENAI_API_KEY": "leak",
                        "HTTP_PROXY": "http://ambient.invalid:1",
                        "HTTPS_PROXY": "http://ambient.invalid:2",
                        "ALL_PROXY": "http://ambient.invalid:3",
                    },
                    clear=True,
                ),
                patch.object(
                    coco_module,
                    "_validate_pre_score_seal",
                    return_value=fixture.seal,
                ),
                patch.object(coco_module, "prepare_coco_input", side_effect=AssertionError),
                patch.object(coco_module.subprocess, "run", side_effect=complete) as run,
            ):
                first = coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            self.assertEqual(
                sum(call.args[0] == fixture.command for call in run.call_args_list),
                1,
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(
                set(first["artifacts"]),
                {"runner", "split", "classify", "evaluation"},
            )
            self.assertEqual(first["pre_score_seal"]["seal_id"], fixture.seal["seal_id"])
            self.assertEqual(first["scoring_spec"], fixture.spec_binding)
            self.assertEqual(
                first["service_runtime"], fixture.seal["service_runtime"]
            )
            self.assertEqual(observed_environment["COCO_SCORING_ARM"], "meta")
            self.assertEqual(observed_environment["COCO_REQUIRE_SCORING_SPEC"], "1")
            self.assertEqual(observed_environment["COCO_REQUIRE_EVALUATION"], "1")
            self.assertEqual(observed_environment["COCO_RUN_EVALUATION"], "1")
            self.assertEqual(observed_environment["COCO_SPLIT_MAX_WORKERS"], "64")
            self.assertEqual(
                observed_environment["COCO_SPLIT_MAX_COMPLETION_TOKENS"],
                "8192",
            )
            self.assertEqual(observed_environment["COCO_CLASSIFY_MAX_WORKERS"], "128")
            self.assertEqual(
                observed_environment["COCO_CLASSIFY_MAX_COMPLETION_TOKENS"],
                "4096",
            )
            self.assertEqual(
                observed_environment["COCO_EVALUATION_MAX_COMPLETION_TOKENS"],
                "32768",
            )
            self.assertEqual(observed_environment["PYTHONNOUSERSITE"], "1")
            self.assertEqual(observed_environment["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertEqual(observed_environment["PYTHONUNBUFFERED"], "1")
            self.assertEqual(observed_environment["HF_HUB_OFFLINE"], "1")
            self.assertEqual(observed_environment["TRANSFORMERS_OFFLINE"], "1")
            self.assertEqual(
                observed_environment["COCO_BERT_SCORE_MODEL_ROOT"],
                str(fixture.bert_score_model_root),
            )
            self.assertEqual(observed_environment["COCO_BERT_SCORE_NUM_LAYERS"], "17")
            self.assertEqual(
                observed_environment["COCO_EVALUATION_API_KEY"], reported_secret
            )
            self.assertNotIn("OPENAI_API_KEY", observed_environment)
            self.assertNotIn("HTTP_PROXY", observed_environment)
            self.assertNotIn("HTTPS_PROXY", observed_environment)
            self.assertNotIn("ALL_PROXY", observed_environment)
            self.assertNotIn("COCO_EVALUATION_API_KEY", first["environment"])
            self.assertNotIn("stdout", first)
            self.assertNotIn("stderr", first)
            self.assertNotIn(
                reported_secret,
                json.dumps(first, sort_keys=True),
            )

            report_bytes = (fixture.outputs["meta"] / "run.json").read_bytes()
            self.assertNotIn(
                reported_secret.encode(),
                report_bytes,
            )
            def reject_process_relaunch(command, **kwargs):
                if command and command[0] == "git":
                    return real_subprocess_run(command, **kwargs)
                raise AssertionError("idempotent rerun launched subprocess")

            with (
                patch.dict(
                    os.environ,
                    {"COCO_EVALUATION_API_KEY": "rotated"},
                    clear=True,
                ),
                patch.object(
                    coco_module,
                    "_validate_pre_score_seal",
                    return_value=fixture.seal,
                ),
                patch.object(
                    coco_module.subprocess,
                    "run",
                    side_effect=reject_process_relaunch,
                ),
            ):
                second = coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            self.assertEqual(second, first)
            self.assertEqual(
                (fixture.outputs["meta"] / "run.json").read_bytes(), report_bytes
            )

    def test_real_command_gate_rejects_flag_and_runner_position_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.register_clean_git_identity()
            real_subprocess_run = subprocess.run

            def reject_launch(command, **kwargs):
                if command and command[0] == "git":
                    return real_subprocess_run(command, **kwargs)
                raise AssertionError("drifted formal command launched")

            commands = (
                [sys.executable, "-B", "-s", str(fixture.runner)],
                [sys.executable, "-s", str(fixture.runner), "-B"],
                [str(fixture.runner), "-s", "-B", sys.executable],
                [*fixture.command, "--extra"],
            )
            for command in commands:
                with (
                    self.subTest(command=command),
                    patch.dict(
                        os.environ,
                        {coco_module.COCO_API_KEY_ENV: "test-key"},
                        clear=True,
                    ),
                    patch.object(
                        coco_module,
                        "_validate_pre_score_seal",
                        return_value=fixture.seal,
                    ),
                    patch.object(
                        coco_module.subprocess,
                        "run",
                        side_effect=reject_launch,
                    ),
                    self.assertRaisesRegex(RuntimeError, "must be exactly"),
                ):
                    coco_module.run_coco_evaluation(
                        pre_score_seal_path=fixture.seal_path,
                        final_mode="meta",
                        command=command,
                    )

    def test_rejects_symlinked_run_and_attempt_paths_without_outside_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            output = fixture.outputs["meta"]
            outside_run = fixture.root / "outside-run.json"
            run_path = output / "run.json"
            run_path.symlink_to(outside_run)
            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                boundary_patches[0],
                boundary_patches[1],
                patch.object(
                    coco_module.subprocess,
                    "run",
                    side_effect=AssertionError("symlinked report launched subprocess"),
                ),
                self.assertRaisesRegex(ValueError, "symbolic link"),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            self.assertTrue(run_path.is_symlink())
            self.assertFalse(outside_run.exists())

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            output = fixture.outputs["meta"]
            outside_attempts = fixture.root / "outside-attempts"
            attempt_root = output / coco_module.COCO_EVALUATION_ATTEMPT_ROOT
            attempt_root.symlink_to(outside_attempts)
            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                boundary_patches[0],
                boundary_patches[1],
                patch.object(
                    coco_module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        fixture.command, 9, "", ""
                    ),
                ),
                self.assertRaisesRegex(ValueError, "symbolic link"),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            self.assertTrue(attempt_root.is_symlink())
            self.assertFalse(outside_attempts.exists())

    def test_rejects_symlinked_external_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)

            def complete(command, **_kwargs):
                fixture.write_complete_outputs()
                split = fixture.outputs["meta"] / coco_module.COCO_SPLIT_ARTIFACT
                outside_split = fixture.root / "outside-split.jsonl"
                split.replace(outside_split)
                split.symlink_to(outside_split)
                return subprocess.CompletedProcess(command, 0, "", "")

            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                boundary_patches[0],
                boundary_patches[1],
                patch.object(coco_module.subprocess, "run", side_effect=complete),
                self.assertRaisesRegex(
                    RuntimeError,
                    "artifact validation failed",
                ),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            self.assertFalse((fixture.outputs["meta"] / "run.json").exists())

    def test_rejects_ambient_override_and_nonsealed_command_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            for environment, command, message in (
                ({}, fixture.command, "requires a non-empty"),
                (
                    {coco_module.COCO_API_KEY_ENV: "   "},
                    fixture.command,
                    "requires a non-empty",
                ),
                (
                    {**FORMAL_KEY_ENV, "COCO_SPLIT_MODEL": "other"},
                    fixture.command,
                    "Ambient",
                ),
                (
                    FORMAL_KEY_ENV,
                    [str(fixture.runner)],
                    "exactly the sealed Python",
                ),
                (
                    FORMAL_KEY_ENV,
                    [*fixture.command, "--split-model", "other"],
                    "exactly the sealed Python",
                ),
            ):
                boundary_patches = fixture.patch_boundaries()
                with (
                    self.subTest(environment=environment, command=command),
                    patch.dict(os.environ, environment, clear=True),
                    boundary_patches[0],
                    boundary_patches[1],
                    patch.object(
                        coco_module.subprocess,
                        "run",
                        side_effect=AssertionError("invalid execution launched"),
                    ),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    coco_module.run_coco_evaluation(
                        pre_score_seal_path=fixture.seal_path,
                        final_mode="meta",
                        command=command,
                    )

    def test_candidate_missing_tamper_and_wrong_paper_set_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            candidate = Path(fixture.arms["meta"]["candidate"]["path"])
            candidate.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "candidate is missing"):
                coco_module._validated_sealed_candidate(
                    arm=fixture.arms["meta"], output=fixture.outputs["meta"]
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            candidate = Path(fixture.arms["meta"]["candidate"]["path"])
            candidate.write_text('{"id":"p2","text":"tampered"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed after sealing"):
                coco_module._validated_sealed_candidate(
                    arm=fixture.arms["meta"], output=fixture.outputs["meta"]
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.arms["meta"]["candidate"]["paper_ids_sha256"] = value_sha256(
                ["p2"]
            )
            with self.assertRaisesRegex(ValueError, "paper set differs"):
                coco_module._validated_sealed_candidate(
                    arm=fixture.arms["meta"], output=fixture.outputs["meta"]
                )

    def test_contract_requires_evaluation_and_exact_runner_stage_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.write_complete_outputs(evaluation_enabled=False)
            with self.assertRaisesRegex(RuntimeError, "requires the evaluation stage"):
                coco_module._validate_coco_artifacts(
                    fixture.outputs["meta"],
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            wrong_spec = {
                "path": str(fixture.spec_path),
                "sha256": file_sha256(fixture.spec_path),
                "spec_id": "f" * 64,
            }
            fixture.write_complete_outputs(checkpoint_spec=wrong_spec)
            with self.assertRaisesRegex(RuntimeError, "stage checkpoint differs"):
                coco_module._validate_coco_artifacts(
                    fixture.outputs["meta"],
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.write_complete_outputs()
            runner_path = fixture.outputs["meta"] / coco_module.COCO_RUNNER_ARTIFACT
            runner = read_json(runner_path)
            runner["scoring_spec"]["spec_id"] = "f" * 64
            atomic_write_json(runner_path, runner, immutable=False)
            with self.assertRaisesRegex(RuntimeError, "scoring spec binding"):
                coco_module._validate_coco_artifacts(
                    fixture.outputs["meta"],
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.write_complete_outputs()
            output = fixture.outputs["meta"]
            checkpoint_path = (
                output
                / coco_module.COCO_STAGE_ROOT
                / "evaluation"
                / coco_module.COCO_STAGE_CHECKPOINT
            )
            checkpoint = read_json(checkpoint_path)
            checkpoint["settings"]["enabled"] = 1
            checkpoint_body = {
                name: value
                for name, value in checkpoint.items()
                if name != "checkpoint_id"
            }
            checkpoint["checkpoint_id"] = value_sha256(checkpoint_body)
            atomic_write_json(checkpoint_path, checkpoint, immutable=False)
            runner_path = output / coco_module.COCO_RUNNER_ARTIFACT
            runner = read_json(runner_path)
            runner["stage_checkpoints"]["evaluation"] = _binding(checkpoint_path)
            atomic_write_json(runner_path, runner, immutable=False)
            with self.assertRaisesRegex(RuntimeError, "stage checkpoint differs"):
                coco_module._validate_coco_artifacts(
                    output,
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.write_complete_outputs()
            runner_path = fixture.outputs["meta"] / coco_module.COCO_RUNNER_ARTIFACT
            runner = read_json(runner_path)
            runner["settings"]["classify"]["prompt_preflight"][
                "opinion_count"
            ] = 2
            atomic_write_json(runner_path, runner, immutable=False)
            with self.assertRaisesRegex(
                RuntimeError,
                "classify prompt preflight identity differs",
            ):
                coco_module._validate_coco_artifacts(
                    fixture.outputs["meta"],
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            fixture.write_complete_outputs()
            runner_path = fixture.outputs["meta"] / coco_module.COCO_RUNNER_ARTIFACT
            runner = read_json(runner_path)
            runner["settings"]["evaluation"]["enabled"] = 1
            atomic_write_json(runner_path, runner, immutable=False)
            with self.assertRaisesRegex(RuntimeError, "requires artifact bindings"):
                coco_module._validate_coco_artifacts(
                    fixture.outputs["meta"],
                    expected_ids={"p1"},
                    candidate_input=Path(
                        fixture.arms["meta"]["candidate"]["path"]
                    ),
                    expected_scoring_spec={
                        name: fixture.spec_binding[name]
                        for name in ("path", "sha256", "spec_id")
                    },
                    require_evaluation=True,
                )

    def test_existing_report_rejects_every_sealed_execution_identity_tamper(self):
        mutations = {
            "pre_score_seal": lambda report: report["pre_score_seal"].__setitem__(
                "seal_id", "f" * 64
            ),
            "scoring_spec": lambda report: report["scoring_spec"].__setitem__(
                "spec_id", "f" * 64
            ),
            "service_runtime": lambda report: report["service_runtime"].__setitem__(
                "proof_id", "f" * 64
            ),
            "environment": lambda report: report["environment"].__setitem__(
                "COCO_SPLIT_MODEL", "other"
            ),
            "command": lambda report: report["command"].append("--extra"),
            "boolean returncode": lambda report: report.__setitem__(
                "returncode", False
            ),
            "boolean scoring count": lambda report: report["scoring_source"].__setitem__(
                "count", True
            ),
            "extra stdout": lambda report: report.__setitem__(
                "stdout", FORMAL_KEY_ENV[coco_module.COCO_API_KEY_ENV]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = _ExecutionFixture(directory)

                def complete(command, **_kwargs):
                    fixture.write_complete_outputs()
                    return subprocess.CompletedProcess(command, 0, "", "")

                boundary_patches = fixture.patch_boundaries()
                with (
                    patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                    boundary_patches[0],
                    boundary_patches[1],
                    patch.object(coco_module.subprocess, "run", side_effect=complete),
                ):
                    coco_module.run_coco_evaluation(
                        pre_score_seal_path=fixture.seal_path,
                        final_mode="meta",
                        command=fixture.command,
                    )
                report_path = fixture.outputs["meta"] / "run.json"
                report = read_json(report_path)
                mutate(report)
                atomic_write_json(report_path, report, immutable=False)
                tampered_bytes = report_path.read_bytes()

                boundary_patches = fixture.patch_boundaries()
                with (
                    patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                    boundary_patches[0],
                    boundary_patches[1],
                    patch.object(
                        coco_module.subprocess,
                        "run",
                        side_effect=AssertionError("tampered report launched subprocess"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "report identity differs"),
                ):
                    coco_module.run_coco_evaluation(
                        pre_score_seal_path=fixture.seal_path,
                        final_mode="meta",
                        command=fixture.command,
                    )
                self.assertEqual(report_path.read_bytes(), tampered_bytes)

    def test_failed_process_or_artifact_validation_never_writes_complete_run(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                boundary_patches[0],
                boundary_patches[1],
                patch.object(
                    coco_module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        fixture.command,
                        7,
                        FORMAL_KEY_ENV[coco_module.COCO_API_KEY_ENV],
                        FORMAL_KEY_ENV[coco_module.COCO_API_KEY_ENV],
                    ),
                ),
                self.assertRaisesRegex(RuntimeError, "failed: 7"),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            output = fixture.outputs["meta"]
            self.assertFalse((output / "run.json").exists())
            attempts = list(
                (output / coco_module.COCO_EVALUATION_ATTEMPT_ROOT).glob("*.json")
            )
            self.assertEqual(len(attempts), 1)
            attempt = read_json(attempts[0])
            self.assertEqual(attempt["status"], "failed")
            self.assertNotIn("stdout", attempt)
            self.assertNotIn("stderr", attempt)
            self.assertNotIn(
                FORMAL_KEY_ENV[coco_module.COCO_API_KEY_ENV],
                attempts[0].read_text(encoding="utf-8"),
            )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                boundary_patches[0],
                boundary_patches[1],
                patch.object(
                    coco_module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        fixture.command, 0, "", ""
                    ),
                ),
                self.assertRaisesRegex(RuntimeError, "contract artifact"),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            output = fixture.outputs["meta"]
            self.assertFalse((output / "run.json").exists())
            attempts = list(
                (output / coco_module.COCO_EVALUATION_ATTEMPT_ROOT).glob("*.json")
            )
            self.assertEqual(len(attempts), 1)
            self.assertIn(
                "contract artifact",
                read_json(attempts[0])["artifact_validation_error"],
            )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _ExecutionFixture(directory)
            drifted_seal = copy.deepcopy(fixture.seal)
            drifted_seal["service_runtime"]["proof_id"] = "f" * 64

            def complete(command, **_kwargs):
                fixture.write_complete_outputs()
                return subprocess.CompletedProcess(command, 0, "", "")

            boundary_patches = fixture.patch_boundaries()
            with (
                patch.dict(os.environ, FORMAL_KEY_ENV, clear=True),
                patch.object(
                    coco_module,
                    "_validate_pre_score_seal",
                    side_effect=[fixture.seal, fixture.seal, drifted_seal],
                ),
                boundary_patches[1],
                patch.object(coco_module.subprocess, "run", side_effect=complete),
                self.assertRaisesRegex(RuntimeError, "upstream changed"),
            ):
                coco_module.run_coco_evaluation(
                    pre_score_seal_path=fixture.seal_path,
                    final_mode="meta",
                    command=fixture.command,
                )
            output = fixture.outputs["meta"]
            self.assertFalse((output / "run.json").exists())
            attempts = list(
                (output / coco_module.COCO_EVALUATION_ATTEMPT_ROOT).glob("*.json")
            )
            self.assertEqual(len(attempts), 1)
            self.assertIn(
                "upstream changed",
                read_json(attempts[0])["runtime_validation_error"],
            )

if __name__ == "__main__":
    unittest.main()
