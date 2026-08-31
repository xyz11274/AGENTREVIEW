import tempfile
import unittest
from pathlib import Path

from memory_evolution.core_closure import audit_core_v1_closure
from memory_evolution.core_smoke import run_production_integration_smoke


class CoreClosureAuditTest(unittest.TestCase):
    def test_g1_passes_independently_while_g2_and_g3_remain_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smoke = root / "smoke"
            run_production_integration_smoke(smoke)
            report = audit_core_v1_closure(
                smoke_report_path=smoke / "integration_smoke_report.json",
                formal_artifact_root=root / "not-yet-materialized",
                output_path=root / "core_v1_closure.json",
            )
        self.assertEqual(report["G1_PRODUCTION_WIRING"]["status"], "PASS")
        self.assertEqual(report["G2_PREFORMAL_ARTIFACTS"]["status"], "PENDING")
        self.assertEqual(report["G3_FORMAL_CLOSURE"]["status"], "PENDING")
        self.assertTrue(all(report["G1_PRODUCTION_WIRING"]["checks"].values()))


if __name__ == "__main__":
    unittest.main()

