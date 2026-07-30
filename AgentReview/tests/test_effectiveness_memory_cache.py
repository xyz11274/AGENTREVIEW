import unittest

from effectiveness_screening.memory_cache import _episodes_by_task


class EffectivenessMemoryCacheTest(unittest.TestCase):
    def test_history_is_split_by_reviewer_task(self):
        record = {
            "sample_id": "paper-1",
            "paper_title": "A Paper",
            "retrieved_papers": [],
            "reviews": {
                "Reviewer QUAL": "quality review",
                "Final Meta-Reviewer": "meta review",
            },
        }
        grouped = _episodes_by_task([record])
        self.assertEqual(len(grouped["QUAL"]), 1)
        self.assertEqual(len(grouped["META"]), 1)
        self.assertEqual(len(grouped["CLAR"]), 0)


if __name__ == "__main__":
    unittest.main()
