import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_evolution.cli import (
    _experiment_kind,
    _is_nonformal,
    _parse_experiment_seeds,
    _promote_lineage_champions,
    build_parser,
    main,
)


class CliModeTest(unittest.TestCase):
    def test_lineage_promotion_contract_is_shared_by_specialists_and_meta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_root = root / "suite"
            source_run = suite_root / "META" / "seed_3"
            source_run.mkdir(parents=True)
            source_program = source_run / "best_program.py"
            source_program.write_text("VALUE = 'program'\n", encoding="utf-8")
            source_sha256 = hashlib.sha256(source_program.read_bytes()).hexdigest()
            (source_run / "lineage_champions.json").write_text(
                json.dumps(
                    {
                        "vector_search": {
                            "program": "best_program.py",
                            "program_sha256": source_sha256,
                            "fitness": 0.75,
                        }
                    }
                ),
                encoding="utf-8",
            )

            promoted = _promote_lineage_champions(
                {"output_dir": str(source_run)}, suite_root, "META", 3
            )

            entry = promoted["vector_search"]
            self.assertEqual(entry["program_sha256"], source_sha256)
            self.assertEqual(entry["experiment_seed"], 3)
            self.assertTrue(
                entry["program"].endswith("lineages/vector_search/META/best_program.py")
            )
            self.assertFalse(Path(entry["program"]).is_absolute())
            self.assertEqual(entry["source_run"], "META/seed_3")

    def test_all_evolution_llm_roles_default_to_local_qwen(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/pilot",
                "--output",
                "runs/pilot",
                "--pilot",
            ]
        )
        self.assertEqual(args.task_model_name, "qwen3.8-27b-fp8")
        self.assertEqual(args.reflector_model_name, "qwen3.8-27b-fp8")
        self.assertEqual(args.judge_model_name, "qwen3.8-27b-fp8")

    def test_formal_cli_defaults_to_the_paper_iteration_budget(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
            ]
        )
        self.assertEqual(args.iterations, 20)
        self.assertFalse(hasattr(args, "max_attempts_per_lineage"))
        self.assertEqual(_experiment_kind(args), "formal")

    def test_evolution_sandbox_memory_limit_is_explicitly_configurable(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
                "--sandbox-memory-mb",
                "8192",
            ]
        )

        self.assertEqual(args.sandbox_memory_mb, 8192)

    def test_selection_and_runtime_embedding_caches_have_distinct_defaults(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
            ]
        )

        self.assertNotEqual(args.embedding_cache, args.runtime_embedding_cache)
        self.assertTrue(
            args.embedding_cache.endswith("qwen3_embedding_8b_cache.sqlite3")
        )
        self.assertTrue(
            args.runtime_embedding_cache.endswith("qwen3_embedding_runtime.sqlite3")
        )

    def test_pilot_preserves_requested_search_size(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/pilot",
                "--output",
                "runs/pilot",
                "--pilot",
                "--iterations",
                "4",
                "--static-size",
                "3",
                "--rotating-size",
                "2",
            ]
        )
        self.assertTrue(args.pilot)
        self.assertFalse(args.smoke)
        self.assertTrue(_is_nonformal(args))
        self.assertEqual(args.iterations, 4)
        self.assertEqual(args.static_size, 3)
        self.assertEqual(args.rotating_size, 2)
        self.assertEqual(_experiment_kind(args), "pilot_non_reportable")

    def test_formal_suite_defaults_to_one_outer_experiment_seed(self):
        for command in ("evolve-specialists", "evolve-meta"):
            args = build_parser().parse_args(
                [
                    command,
                    "--data-root",
                    "data/formal",
                    "--output",
                    "runs/formal",
                ]
            )
            self.assertEqual(args.experiment_seeds, "0")
            self.assertEqual(_parse_experiment_seeds(args.experiment_seeds), [0])

    def test_outer_experiment_seed_count_is_parameterized_but_nonempty(self):
        self.assertEqual(_parse_experiment_seeds("0"), [0])
        self.assertEqual(_parse_experiment_seeds("0,1,2,3,4"), [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(SystemExit, "at least one experiment seed"):
            _parse_experiment_seeds("")
        with self.assertRaisesRegex(SystemExit, "must not contain duplicates"):
            _parse_experiment_seeds("0,0")

    def test_smoke_and_pilot_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "evolve-task",
                    "--task-id",
                    "QUAL",
                    "--data-root",
                    "data/pilot",
                    "--output",
                    "runs/pilot",
                    "--smoke",
                    "--pilot",
                ]
            )

    def test_evaluate_final_test_is_always_forbidden(self):
        argv = [
            "memory_evolution.cli",
            "evaluate",
            "--task-id",
            "QUAL",
            "--program",
            "program.py",
            "--episodes",
            "episodes.jsonl",
            "--eval",
            "arbitrary.jsonl",
            "--output",
            "report.json",
            "--final-test",
        ]
        with (
            patch("sys.argv", argv),
            self.assertRaisesRegex(
                SystemExit, "final-evaluate-suite is the sealed D/Test entry point"
            ),
        ):
            main()


if __name__ == "__main__":
    unittest.main()
