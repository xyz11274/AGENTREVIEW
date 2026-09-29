"""Paired effect contracts: alignment, missingness and dependent versions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deployment"))
from paired_benchmark_report import paired_summary


class PairedSummaryTest(unittest.TestCase):
    def test_pairing_uses_ids_and_preserves_direction(self):
        result = paired_summary({"b":3.4,"a":3.0},{"a":2.0,"b":2.4},
            {"a":"f1","b":"f2"},seed=0,resamples=500)
        self.assertAlmostEqual(result["mean_gain"],1.0)
        for bound in result["confidence_interval_95"]:
            self.assertAlmostEqual(bound,1.0)
        self.assertEqual(result["wins"],2)

    def test_versions_are_resampled_as_one_family(self):
        result = paired_summary({"v1":5.0,"v2":1.0},{"v1":3.0,"v2":2.0},
            {"v1":"same","v2":"same"},seed=0,resamples=500)
        self.assertEqual(result["families"],1)
        self.assertEqual(result["confidence_interval_95"],[0.5,0.5])

    def test_missing_and_invalid_scores_never_become_zero(self):
        for control in ({},{"different":3.0},{"p":None},{"p":float('nan')}):
            with self.subTest(control=control), self.assertRaises(ValueError):
                paired_summary({"p":3.0},control,{"p":"f"},seed=0,resamples=500)


if __name__ == "__main__":
    unittest.main()
