import unittest

from memory_evolution.cli import _experiment_kind, _is_nonformal, build_parser


class CliModeTest(unittest.TestCase):
    def test_all_evolution_llm_roles_default_to_gpt_5_mini(self):
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
        self.assertEqual(args.task_model_name, "gpt-5-mini")
        self.assertEqual(args.reflector_model_name, "gpt-5-mini")
        self.assertEqual(args.judge_model_name, "gpt-5-mini")

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


if __name__ == "__main__":
    unittest.main()
