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
            )
            first_bytes = paths["output"].read_bytes()
            second = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
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
            self.assertEqual(len(first["critical_files"]), 7)
            self.assertEqual(len(first["benchmark"]["files"]), 2)
            self.assertEqual(len(first["models"]["split"]["files"]), 2)
            self.assertEqual(len(first["models"]["classify"]["files"]), 2)

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

    def test_validation_rejects_dirty_repository_and_ignored_model_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
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
                )

    def test_validation_rejects_arm_output_and_cache_namespace_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _fixture(directory)
            pristine = materialize_coco_scoring_spec(
                coco_root=paths["coco"],
                artifact_root=paths["artifacts"],
                winner_run_id="winner-v1",
                output_path=paths["output"],
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
                )


if __name__ == "__main__":
    unittest.main()
