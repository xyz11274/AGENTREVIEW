import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.coco_scoring_spec import (
    CRITICAL_FILE_RELATIVE_PATHS,
    materialize_coco_scoring_spec,
    validate_coco_scoring_spec,
)
from agentreview.research_v2.io import value_sha256


EVALUATION_PROXY_URL = "http://127.0.0.1:7898"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _fixture(directory: str, *, include_selection: bool = True) -> dict[str, Path]:
    base = Path(directory).resolve()
    coco = base / "coco"
    coco.mkdir()
    _write(coco / ".gitignore", "model/\n")
    for index, relative in enumerate(CRITICAL_FILE_RELATIVE_PATHS):
        _write(coco / relative, f"critical-{index}\n")
    _write(coco / "benchmark" / "benchmark_ICLR_2024.jsonl", '{"id":"a"}\n')
    _write(coco / "benchmark" / "benchmark_NeurIPS_2023.jsonl", '{"id":"b"}\n')
    _write(
        coco / "benchmark" / "benchmark_all_human_classify.jsonl",
        '{"id":"a","category":[]}\n',
    )
    if include_selection:
        _write(
            coco / "benchmark" / "benchmark_reviewer_selection.json",
            '{"a":"Reviewer 1"}\n',
        )
    _write(coco / "model" / "split_model" / "config.json", '{"split":true}\n')
    _write(coco / "model" / "split_model" / "weights" / "part.bin", b"split")
    _write(
        coco / "model" / "classify_model" / "config.json",
        '{"classify":true}\n',
    )
    _write(
        coco / "model" / "classify_model" / "weights" / "part.bin",
        b"classify",
    )
    _write(
        coco / "model" / "bertscore_roberta_large" / "config.json",
        '{"model_type":"roberta"}\n',
    )
    _write(
        coco / "model" / "bertscore_roberta_large" / "model.safetensors",
        b"roberta-large",
    )

    evaluation_prefix = base / "evaluation-environment"
    evaluation_python = evaluation_prefix / "bin" / "python3"
    package_versions = {
        "bert-score": "0.3.13",
        "nltk": "3.9.3",
        "numpy": "2.2.6",
        "openai": "2.6.0",
        "requests": "2.32.5",
        "rouge-score": "0.1.2",
        "torch": "2.8.0",
        "tqdm": "4.67.1",
        "transformers": "4.57.1",
    }
    probe = {
        "python_executable": str(evaluation_python),
        "python_version": "3.12.11",
        "sys_prefix": str(evaluation_prefix),
        "user_site_enabled": False,
        "isolated_mode": False,
        "no_user_site": True,
        "dont_write_bytecode": True,
        "ignore_environment": False,
        "packages": package_versions,
    }
    _write(
        evaluation_python,
        f"#!/bin/sh\nprintf '%s\\n' '{json.dumps(probe, sort_keys=True)}'\n",
    )
    evaluation_python.chmod(0o755)
    _write(evaluation_prefix / "lib" / "runtime.txt", "evaluation runtime\n")
    _write(evaluation_prefix / "lib" / "existing.pyc", b"sealed bytecode")
    (evaluation_prefix / "bin" / "python").symlink_to("python3")

    _git(coco, "init")
    _git(coco, "config", "user.email", "coco-scoring@example.invalid")
    _git(coco, "config", "user.name", "CoCo Scoring Test")
    _git(coco, "add", ".")
    _git(coco, "commit", "-m", "fixture")

    artifacts = base / "artifacts"
    artifacts.mkdir()
    return {
        "base": base,
        "coco": coco,
        "artifacts": artifacts,
        "output": base / "coco-scoring-spec.json",
        "evaluation_python": evaluation_prefix / "bin" / "python",
    }


def _rewrite_self_hashed(path: Path, value: dict) -> None:
    body = {name: item for name, item in value.items() if name != "spec_id"}
    value["spec_id"] = value_sha256(body)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class CocoScoringSpecTest(unittest.TestCase):
    def test_happy_path_is_self_hashed_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            first = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )
            first_bytes = paths["output"].read_bytes()
            second = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )
            validated = validate_coco_scoring_spec(
                paths["output"],
                coco_root=paths["coco"],
            )

            self.assertEqual(first, second)
            self.assertEqual(validated, first)
            self.assertEqual(paths["output"].read_bytes(), first_bytes)
            body = {name: item for name, item in first.items() if name != "spec_id"}
            self.assertEqual(first["spec_id"], value_sha256(body))
            self.assertEqual(first["repository"]["root"], str(paths["coco"]))
            self.assertTrue(first["repository"]["clean"])
            self.assertEqual(len(first["critical_files"]), 9)
            self.assertEqual(len(first["benchmark"]["files"]), 2)
            self.assertEqual(len(first["models"]["split"]["files"]), 2)
            self.assertEqual(len(first["models"]["classify"]["files"]), 2)
            self.assertEqual(
                first["models"]["evaluation"]["model_type"], "roberta-large"
            )
            self.assertEqual(first["models"]["evaluation"]["num_layers"], 17)
            self.assertIs(first["models"]["evaluation"]["rescale_with_baseline"], False)
            self.assertIs(
                first["scientific_settings"]["evaluation"]["require_complete"],
                True,
            )
            split_settings = first["scientific_settings"]["split"]
            self.assertEqual(split_settings["max_completion_tokens"], 8192)
            self.assertEqual(split_settings["context_window_tokens"], 40960)
            self.assertEqual(
                split_settings["prompt_preflight"],
                {
                    "algorithm": "qwen-chat-template-v1",
                    "max_model_len": 40960,
                    "max_completion_tokens": 8192,
                    "enable_thinking": True,
                },
            )
            classify_settings = first["scientific_settings"]["classify"]
            self.assertEqual(classify_settings["max_completion_tokens"], 4096)
            self.assertEqual(classify_settings["context_window_tokens"], 32768)
            self.assertEqual(
                classify_settings["prompt_preflight"],
                {
                    "algorithm": "qwen-classify-chat-template-v1",
                    "max_model_len": 32768,
                    "max_completion_tokens": 4096,
                    "enable_thinking": True,
                },
            )
            self.assertIsNone(first["scientific_settings"]["evaluation"]["temperature"])
            evaluation_settings = first["scientific_settings"]["evaluation"]
            self.assertEqual(evaluation_settings["max_completion_tokens"], 32768)
            self.assertEqual(evaluation_settings["context_window_tokens"], 400000)
            self.assertEqual(
                evaluation_settings["prompt_preflight"],
                {
                    "algorithm": "utf8-byte-upper-bound-v1",
                    "max_model_len": 400000,
                    "max_completion_tokens": 32768,
                    "chat_overhead_tokens": 1024,
                },
            )

            evaluation_runtime = first["services"]["evaluation"]
            self.assertEqual(
                evaluation_runtime["python_executable"],
                str(paths["evaluation_python"].resolve()),
            )
            self.assertIs(evaluation_runtime["user_site_enabled"], False)
            self.assertIs(evaluation_runtime["isolated_mode"], False)
            self.assertIs(evaluation_runtime["no_user_site"], True)
            self.assertIs(evaluation_runtime["dont_write_bytecode"], True)
            self.assertIs(evaluation_runtime["ignore_environment"], False)
            self.assertEqual(
                evaluation_runtime["transport"],
                {
                    "HTTP_PROXY": EVALUATION_PROXY_URL,
                    "HTTPS_PROXY": EVALUATION_PROXY_URL,
                },
            )
            self.assertEqual(evaluation_runtime["prefix_tree"]["pyc_file_count"], 1)
            self.assertGreater(evaluation_runtime["prefix_tree"]["file_count"], 0)
            self.assertRegex(
                evaluation_runtime["prefix_tree"]["tree_sha256"],
                r"^[0-9a-f]{64}$",
            )

            services = first["services"]
            self.assertEqual(
                services["software"], {"vllm": "0.20.1", "transformers": "5.8.1"}
            )
            self.assertEqual(services["split"]["cuda_visible_devices"], "0")
            self.assertEqual(services["split"]["port"], 13099)
            self.assertEqual(services["split"]["max_model_len"], 40960)
            self.assertEqual(services["split"]["max_num_seqs"], 8)
            self.assertEqual(services["classify"]["cuda_visible_devices"], "1")
            self.assertEqual(services["classify"]["port"], 13098)
            self.assertEqual(services["classify"]["max_model_len"], 32768)
            self.assertEqual(services["classify"]["max_num_seqs"], 16)

            expected_scoring_root = paths["artifacts"] / "coco_scoring"
            self.assertEqual(
                first["arms"]["meta"]["output_root"],
                str(expected_scoring_root / "winner-v1-meta"),
            )
            self.assertEqual(
                first["arms"]["concat"]["output_root"],
                str(expected_scoring_root / "winner-v1-concat"),
            )
            caches = [
                cache
                for arm in first["arms"].values()
                for cache in arm["cache_versions"].values()
            ]
            self.assertEqual(len(set(caches)), 6)
            rendered = json.dumps(first).lower()
            self.assertNotIn("api_key", rendered)
            self.assertNotIn("timestamp", rendered)
            self.assertNotIn("created_at", rendered)

    def test_spec_output_and_validation_reject_symlink_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            outside = paths["base"] / "outside-spec.json"
            paths["output"].symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )
            self.assertTrue(paths["output"].is_symlink())
            self.assertFalse(outside.exists())

        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )
            alias = paths["base"] / "scoring-spec-alias.json"
            alias.symlink_to(paths["output"])
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                validate_coco_scoring_spec(alias)

    def test_validation_rejects_rehashed_json_type_substitutions(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            pristine = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )
            mutations = {
                "evaluation enabled integer": (
                    lambda value: value["scientific_settings"][
                        "evaluation"
                    ].__setitem__("enabled", 1),
                    "enabled must be true",
                ),
                "evaluation require-complete integer": (
                    lambda value: value["scientific_settings"][
                        "evaluation"
                    ].__setitem__("require_complete", 1),
                    "require_complete must be true",
                ),
                "evaluation temperature numeric": (
                    lambda value: value["scientific_settings"][
                        "evaluation"
                    ].__setitem__("temperature", 0),
                    "temperature must be null",
                ),
                "evaluation completion budget boolean": (
                    lambda value: value["scientific_settings"][
                        "evaluation"
                    ].__setitem__("max_completion_tokens", True),
                    "token budgets differ",
                ),
                "evaluation preflight overhead boolean": (
                    lambda value: value["scientific_settings"]["evaluation"][
                        "prompt_preflight"
                    ].__setitem__("chat_overhead_tokens", True),
                    "prompt preflight identity differs",
                ),
                "tensor parallel boolean": (
                    lambda value: value["services"]["split"].__setitem__(
                        "tensor_parallel_size", True
                    ),
                    "tensor_parallel_size must be a positive integer",
                ),
                "worker limit boolean": (
                    lambda value: value["scientific_settings"]["split"].__setitem__(
                        "max_in_flight_client_requests", True
                    ),
                    "max_in_flight_client_requests must be a positive integer",
                ),
                "split completion budget boolean": (
                    lambda value: value["scientific_settings"]["split"].__setitem__(
                        "max_completion_tokens", True
                    ),
                    "token budgets differ",
                ),
                "split preflight boolean": (
                    lambda value: value["scientific_settings"]["split"][
                        "prompt_preflight"
                    ].__setitem__("enable_thinking", 1),
                    "prompt preflight identity differs",
                ),
                "classify completion budget boolean": (
                    lambda value: value["scientific_settings"][
                        "classify"
                    ].__setitem__("max_completion_tokens", True),
                    "token budgets differ",
                ),
                "classify preflight boolean": (
                    lambda value: value["scientific_settings"]["classify"][
                        "prompt_preflight"
                    ].__setitem__("enable_thinking", 1),
                    "prompt preflight identity differs",
                ),
                "gpu utilization boolean": (
                    lambda value: value["services"]["split"].__setitem__(
                        "gpu_memory_utilization", True
                    ),
                    "gpu_memory_utilization must be a finite number",
                ),
                "evaluation model false integer": (
                    lambda value: value["models"]["evaluation"].__setitem__(
                        "rescale_with_baseline", 0
                    ),
                    "models.evaluation scientific identity differs",
                ),
                "scientific evaluation model false integer": (
                    lambda value: value["scientific_settings"]["evaluation"][
                        "bert_score_model"
                    ].__setitem__("rescale_with_baseline", 0),
                    "bert_score_model is invalid",
                ),
                "scientific evaluation model count boolean": (
                    lambda value: value["scientific_settings"]["evaluation"][
                        "bert_score_model"
                    ].__setitem__("file_count", True),
                    "bert_score_model is invalid",
                ),
                "evaluation runtime false integer": (
                    lambda value: value["services"]["evaluation"].__setitem__(
                        "user_site_enabled", 0
                    ),
                    "evaluation Python isolation identity differs",
                ),
                "evaluation no-user-site integer": (
                    lambda value: value["services"]["evaluation"].__setitem__(
                        "no_user_site", 1
                    ),
                    "evaluation Python isolation identity differs",
                ),
                "evaluation no-bytecode integer": (
                    lambda value: value["services"]["evaluation"].__setitem__(
                        "dont_write_bytecode", 1
                    ),
                    "evaluation Python isolation identity differs",
                ),
                "evaluation prefix count boolean": (
                    lambda value: value["services"]["evaluation"][
                        "prefix_tree"
                    ].__setitem__("file_count", True),
                    "file_count must be a non-negative integer",
                ),
                "evaluation transport extra field": (
                    lambda value: value["services"]["evaluation"][
                        "transport"
                    ].__setitem__("ALL_PROXY", EVALUATION_PROXY_URL),
                    "transport fields differ",
                ),
                "evaluation transport mismatch": (
                    lambda value: value["services"]["evaluation"][
                        "transport"
                    ].__setitem__("HTTPS_PROXY", "http://127.0.0.1:7899"),
                    "evaluation transport differs",
                ),
            }
            for label, (mutate, message) in mutations.items():
                with self.subTest(label=label):
                    tampered = json.loads(json.dumps(pristine))
                    mutate(tampered)
                    _rewrite_self_hashed(paths["output"], tampered)
                    with self.assertRaisesRegex(ValueError, message):
                        validate_coco_scoring_spec(paths["output"])

    def test_validation_rejects_evaluation_prefix_regular_symlink_and_pyc_drift(self):
        mutations = (
            ("regular bytes", "lib/runtime.txt", b"changed runtime"),
            ("existing bytecode", "lib/existing.pyc", b"changed bytecode"),
        )
        for label, relative, replacement in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                paths = _fixture(directory)
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )
                (paths["evaluation_python"].parents[1] / relative).write_bytes(
                    replacement
                )
                with self.assertRaisesRegex(ValueError, "paths, hashes"):
                    validate_coco_scoring_spec(paths["output"])

        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            different_python = paths["evaluation_python"].parent / "different-python"
            different_python.write_text("different runtime\n", encoding="utf-8")
            materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )
            alias = paths["evaluation_python"].parent / "python"
            alias.unlink()
            alias.symlink_to("different-python")
            with self.assertRaisesRegex(ValueError, "paths, hashes"):
                validate_coco_scoring_spec(paths["output"])

    def test_materialization_rejects_evaluation_prefix_symlink_escape_and_dangling(
        self,
    ):
        for label, target, message in (
            ("escape", "outside-runtime.txt", "escapes its sys.prefix"),
            ("dangling", "missing-runtime.txt", "dangling"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                paths = _fixture(directory)
                prefix = paths["evaluation_python"].parents[1]
                if label == "escape":
                    outside = paths["base"] / target
                    outside.write_text("outside\n", encoding="utf-8")
                    link_target = outside
                else:
                    link_target = prefix / target
                (prefix / "lib" / f"{label}.link").symlink_to(link_target)

                with self.assertRaisesRegex(ValueError, message):
                    materialize_coco_scoring_spec(
                        coco_root=paths["coco"],
                        artifact_root=paths["artifacts"],
                        winner_run_id="winner-v1",
                        output_path=paths["output"],
                        evaluation_python=paths["evaluation_python"],
                        evaluation_proxy_url=EVALUATION_PROXY_URL,
                    )

    def test_materialization_normalizes_and_rejects_evaluation_proxy_url(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            spec = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL + "/",
            )
            self.assertEqual(
                spec["services"]["evaluation"]["transport"],
                {
                    "HTTP_PROXY": EVALUATION_PROXY_URL,
                    "HTTPS_PROXY": EVALUATION_PROXY_URL,
                },
            )

        for proxy_url in (
            "https://127.0.0.1:7898",
            "http://localhost:7898",
            "http://user@127.0.0.1:7898",
            "http://127.0.0.1",
            "http://127.0.0.1:7899",
            "http://127.0.0.1:7898?query=1",
        ):
            with (
                self.subTest(proxy_url=proxy_url),
                tempfile.TemporaryDirectory() as directory,
            ):
                paths = _fixture(directory)
                with self.assertRaisesRegex(ValueError, "proxy URL"):
                    materialize_coco_scoring_spec(
                        coco_root=paths["coco"],
                        artifact_root=paths["artifacts"],
                        winner_run_id="winner-v1",
                        output_path=paths["output"],
                        evaluation_python=paths["evaluation_python"],
                        evaluation_proxy_url=proxy_url,
                    )

    def test_arm_outputs_reject_parent_and_final_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            outside = paths["base"] / "outside-scoring"
            outside.mkdir()
            scoring_root = paths["artifacts"] / "coco_scoring"
            scoring_root.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(paths["output"].exists())

        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            outside = paths["base"] / "outside-meta"
            outside.mkdir()
            scoring_root = paths["artifacts"] / "coco_scoring"
            scoring_root.mkdir()
            meta_output = scoring_root / "winner-v1-meta"
            meta_output.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(paths["output"].exists())

    def test_validation_rejects_dirty_repository_and_ignored_model_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )

            dirty = paths["coco"] / "untracked.txt"
            dirty.write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "clean Git worktree"):
                validate_coco_scoring_spec(paths["output"])
            dirty.unlink()

            model_file = (
                paths["coco"] / "model" / "split_model" / "weights" / "part.bin"
            )
            model_file.write_bytes(b"drift")
            self.assertEqual(_git(paths["coco"], "status", "--porcelain"), "")
            with self.assertRaisesRegex(ValueError, "paths, hashes"):
                validate_coco_scoring_spec(paths["output"])

    def test_materialization_requires_reviewer_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory, include_selection=False)
            with self.assertRaisesRegex(FileNotFoundError, "reviewer_selection"):
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )

    def test_validation_rejects_arm_output_and_cache_namespace_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            pristine = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
                evaluation_python=paths["evaluation_python"],
                evaluation_proxy_url=EVALUATION_PROXY_URL,
            )

            output_conflict = json.loads(json.dumps(pristine))
            output_conflict["arms"]["concat"]["output_root"] = output_conflict["arms"][
                "meta"
            ]["output_root"]
            _rewrite_self_hashed(paths["output"], output_conflict)
            with self.assertRaisesRegex(ValueError, "distinct output roots"):
                validate_coco_scoring_spec(paths["output"])

            cache_conflict = json.loads(json.dumps(pristine))
            cache_conflict["arms"]["concat"]["cache_versions"]["split"] = (
                cache_conflict["arms"]["meta"]["cache_versions"]["split"]
            )
            _rewrite_self_hashed(paths["output"], cache_conflict)
            with self.assertRaisesRegex(ValueError, "six distinct cache versions"):
                validate_coco_scoring_spec(paths["output"])

    def test_model_inventory_rejects_symlink_that_escapes_model_root(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            outside = paths["base"] / "outside.bin"
            outside.write_bytes(b"outside")
            link = paths["coco"] / "model" / "split_model" / "outside.bin"
            link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink escapes"):
                materialize_coco_scoring_spec(
                    coco_root=paths["coco"],
                    artifact_root=paths["artifacts"],
                    winner_run_id="winner-v1",
                    output_path=paths["output"],
                    evaluation_python=paths["evaluation_python"],
                    evaluation_proxy_url=EVALUATION_PROXY_URL,
                )


if __name__ == "__main__":
    unittest.main()
