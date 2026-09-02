import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import cli as cli_module
from agentreview.research_v2.config import ResearchConfig


class CocoAuditCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = ResearchConfig(
            artifact_root=str(self.root / "artifacts"),
        )
        self.repository_root = Path(cli_module.__file__).resolve().parents[3]

    def tearDown(self):
        self.temporary.cleanup()

    def test_parser_exposes_pre_score_seal_and_final_audit_contracts(self):
        seal = cli_module._base_parser().parse_args(
            [
                "seal-coco-pre-score",
                "--frozen-system",
                "frozen.json",
                "--winner-run-id",
                "winner-v1",
                "--output",
                "seal.json",
            ]
        )
        self.assertEqual(seal.stage, "seal-coco-pre-score")
        self.assertEqual(seal.frozen_system, "frozen.json")
        self.assertEqual(seal.winner_run_id, "winner-v1")
        self.assertEqual(seal.output, "seal.json")

        audit = cli_module._base_parser().parse_args(
            [
                "audit-coco-final",
                "--pre-score-seal",
                "seal.json",
                "--output",
                "audit.json",
            ]
        )
        self.assertEqual(audit.stage, "audit-coco-final")
        self.assertEqual(audit.pre_score_seal, "seal.json")
        self.assertEqual(audit.output, "audit.json")

        validate = cli_module._base_parser().parse_args(
            [
                "validate-coco-final",
                "--audit",
                "audit.json",
                "--live-service",
            ]
        )
        self.assertEqual(validate.stage, "validate-coco-final")
        self.assertEqual(validate.audit, "audit.json")
        self.assertTrue(validate.live_service)

        evaluate = cli_module._base_parser().parse_args(
            [
                "coco-eval",
                "--pre-score-seal",
                "seal.json",
                "--final-mode",
                "meta",
                "--",
                "/qwen/bin/python",
                "/coco/scripts/run_official_candidate.py",
            ]
        )
        self.assertEqual(evaluate.pre_score_seal, "seal.json")
        self.assertEqual(evaluate.final_mode, "meta")
        self.assertEqual(
            evaluate.command,
            ["--", "/qwen/bin/python", "/coco/scripts/run_official_candidate.py"],
        )
        self.assertFalse(hasattr(evaluate, "frozen_system"))
        self.assertFalse(hasattr(evaluate, "winner_run_id"))
        self.assertFalse(hasattr(evaluate, "output"))

    def test_seal_cli_wires_config_artifact_root_and_repository_root(self):
        frozen_system = self.root / "frozen-system.json"
        seal_path = self.root / "pre-score-seal.json"
        rendered = io.StringIO()
        with (
            patch.object(
                cli_module.ResearchConfig,
                "load",
                return_value=self.config,
            ),
            patch.object(
                cli_module,
                "seal_coco_pre_score",
                return_value={"protocol": "research-v2-coco-pre-score-seal-v2"},
            ) as seal,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "--config",
                    "formal.json",
                    "seal-coco-pre-score",
                    "--frozen-system",
                    str(frozen_system),
                    "--winner-run-id",
                    "formal-coco1300-winner-v1",
                    "--output",
                    str(seal_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        seal.assert_called_once_with(
            frozen_system_path=str(frozen_system),
            artifact_root=self.config.artifact_path(),
            winner_run_id="formal-coco1300-winner-v1",
            repository_root=self.repository_root,
            output_path=str(seal_path),
        )

    def test_final_audit_cli_wires_seal_and_repository_root(self):
        seal_path = self.root / "pre-score-seal.json"
        audit_path = self.root / "final-audit.json"
        rendered = io.StringIO()
        with (
            patch.object(
                cli_module.ResearchConfig,
                "load",
                return_value=self.config,
            ),
            patch.object(
                cli_module,
                "audit_coco_final",
                return_value={"protocol": "research-v2-coco-final-audit-v1"},
            ) as audit,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "audit-coco-final",
                    "--pre-score-seal",
                    str(seal_path),
                    "--output",
                    str(audit_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        audit.assert_called_once_with(
            pre_score_seal_path=str(seal_path),
            repository_root=self.repository_root,
            output_path=str(audit_path),
        )

    def test_coco_eval_cli_derives_all_sources_and_output_from_seal(self):
        seal_path = self.root / "pre-score-seal.json"
        scoring_python = "/qwen/bin/python"
        runner = "/coco/scripts/run_official_candidate.py"
        rendered = io.StringIO()
        with (
            patch.object(
                cli_module.ResearchConfig,
                "load",
                return_value=self.config,
            ),
            patch.object(
                cli_module,
                "run_coco_evaluation",
                return_value={"status": "complete"},
            ) as evaluate,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "coco-eval",
                    "--pre-score-seal",
                    str(seal_path),
                    "--final-mode",
                    "concat",
                    "--",
                    scoring_python,
                    runner,
                ]
            )

        self.assertEqual(exit_code, 0)
        evaluate.assert_called_once_with(
            pre_score_seal_path=str(seal_path),
            final_mode="concat",
            command=["--", scoring_python, runner],
        )

    def test_validate_final_cli_is_offline_by_default(self):
        audit_path = self.root / "final-audit.json"
        rendered = io.StringIO()
        with (
            patch.object(
                cli_module.ResearchConfig,
                "load",
                return_value=self.config,
            ),
            patch.object(
                cli_module,
                "validate_coco_final_audit",
                return_value={"protocol": "research-v2-coco-final-audit-v1"},
            ) as validate,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "validate-coco-final",
                    "--audit",
                    str(audit_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        validate.assert_called_once_with(
            str(audit_path),
            require_live_service=False,
        )


if __name__ == "__main__":
    unittest.main()
