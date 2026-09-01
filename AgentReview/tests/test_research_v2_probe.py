import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2.probe import (
    FORMAL_TOKENS,
    S2_TOKENS,
    _metric,
    _stagnant_count,
    run_once,
)


class ResearchV2ProbeTest(unittest.TestCase):
    def test_metric_sums_labeled_series(self):
        text = "\n".join(
            (
                'vllm:num_requests_running{model_name="a"} 3',
                'vllm:num_requests_running{model_name="b"} 2',
                "vllm:num_requests_waiting 0",
            )
        )
        self.assertEqual(_metric(text, "vllm:num_requests_running"), 5.0)
        self.assertEqual(_metric(text, "vllm:num_requests_waiting"), 0.0)

    @patch("agentreview.research_v2.probe.time.time", return_value=5000.0)
    def test_stagnation_requires_a_real_probe_interval(self, _time):
        previous = {
            "checked_epoch": 1000.0,
            "stagnation": {"formal": 1},
        }
        self.assertEqual(
            _stagnant_count(
                previous,
                key="formal",
                current_signature="same",
                previous_signature="same",
                active=True,
            ),
            2,
        )
        self.assertEqual(
            _stagnant_count(
                previous,
                key="formal",
                current_signature="new",
                previous_signature="same",
                active=True,
            ),
            0,
        )

    @patch("agentreview.research_v2.probe.collect_probe")
    def test_run_once_persists_auditable_state(self, collect):
        collect.return_value = {
            "protocol": "research-v2-hourly-probe-v1",
            "checked_at": "2026-09-01T00:00:00+00:00",
            "checked_epoch": 1.0,
            "status": "healthy",
            "transition_ready": False,
            "issues": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, result = run_once(root, root / "state")
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "healthy")
            self.assertTrue((root / "state" / "latest.json").is_file())
            self.assertTrue((root / "state" / "probes.jsonl").is_file())
            self.assertTrue((root / "state" / "action.json").is_file())

    def test_expected_commands_freeze_serial_s2_and_formal_identity(self):
        self.assertIn("--min-request-interval-seconds 1.2", S2_TOKENS)
        self.assertIn("--retry-until-success", S2_TOKENS)
        self.assertIn("--action run-all-roles", FORMAL_TOKENS)
        self.assertIn("--run-id formal-evolution-v1", FORMAL_TOKENS)


if __name__ == "__main__":
    unittest.main()
