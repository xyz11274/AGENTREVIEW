import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.core_cli import main as core_cli_main
from memory_evolution.core_smoke import run_production_integration_smoke


ROLE_ORDER = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
REFLECTOR_FIELDS = {
    "role",
    "overall_fitness",
    "subdimension_scores",
    "failure_codes",
    "evaluable_paper_count",
}


class CoreApplicationIntegrationTest(unittest.TestCase):
    def test_production_path_runs_five_roles_child_suite_and_official_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            report = run_production_integration_smoke(Path(directory) / "direct")

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(tuple(report["role_order"]), ROLE_ORDER)
        self.assertEqual(tuple(report["suite_role_order"]), ROLE_ORDER)
        self.assertEqual(report["protected_loader_calls_before_authorization"], 0)
        self.assertEqual(report["protected_loader_calls_after_official"], 1)
        self.assertEqual(report["official_result"]["status"], "REPORT_ONLY")
        for role in ROLE_ORDER:
            audit = report["roles"][role]
            self.assertEqual(audit["runner_class"], "CoreRoleEvolutionRunner")
            self.assertGreater(audit["reviewer_session_count"], 0)
            self.assertGreater(audit["evaluator_applicability_count"], 0)
            self.assertGreater(audit["evaluator_scoring_count"], 0)
            self.assertEqual(audit["attempt_count"], 1)
            self.assertEqual(audit["attempts"][0]["disposition"], "CANDIDATE_ADMITTED")
            self.assertNotEqual(
                audit["attempts"][0]["child_candidate_id"],
                audit["attempts"][0]["parent_candidate_id"],
            )
            self.assertEqual(set(audit["reflector_feedback_fields"]), REFLECTOR_FIELDS)
            self.assertNotIn("paper_id", audit["reflector_feedback_fields"])

    def test_core_cli_integration_smoke_returns_pass_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cli"
            code = core_cli_main(
                ["integration-smoke", "--output-dir", str(output)]
            )
            report = json.loads(
                (output / "integration_smoke_report.json").read_text(encoding="utf-8")
            )
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
