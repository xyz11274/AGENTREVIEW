import unittest
from unittest.mock import patch

from memory_evolution.cli import (
    _experiment_kind,
    _is_nonformal,
    _parse_experiment_seeds,
    build_parser,
    main,
)


class CliModeTest(unittest.TestCase):
    def test_all_evolution_llm_roles_default_to_kimi_k3(self):
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
        self.assertEqual(args.task_model_name, "kimi-k3")
        self.assertEqual(args.reflector_model_name, "kimi-k3")
        self.assertEqual(args.judge_model_name, "kimi-k3")

    def test_formal_attempt_budget_cli_is_explicitly_configurable(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
                "--max-attempts-per-lineage",
                "9",
            ]
        )
        self.assertEqual(args.max_attempts_per_lineage, 9)

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
        with patch("sys.argv", argv), self.assertRaisesRegex(
            SystemExit, "only formal D/Test entry point is final-evaluate-suite"
        ):
            main()


if __name__ == "__main__":
    unittest.main()
