import json
import tempfile
import unittest
from pathlib import Path

from agentreview.viewer_data import (
    EXPECTED_REVIEWERS,
    load_sample,
    raw_artifact_text,
    scan_output_dir,
)


class ViewerDataTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name) / "outputs"
        self.review_root = self.output_dir / "single_paper_review"
        self.review_root.mkdir(parents=True)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_sample_dir(self, sample_id="paper-1"):
        sample_dir = self.review_root / sample_id
        sample_dir.mkdir()
        return sample_dir

    def write_json(self, sample_dir, name, value):
        (sample_dir / name).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    def complete_reviews(self):
        return {name: f"Output from {name}" for name in EXPECTED_REVIEWERS}

    def write_complete_sample(self, sample_dir):
        self.write_json(sample_dir, "reviews.json", self.complete_reviews())
        self.write_json(
            sample_dir,
            "memory.json",
            {
                "paper_title": "A Test Paper",
                "paper_md_path": "/dataset/paper-1/paper.md",
                "review_model_name": "test-model",
                "semantic_scholar_query": "A Test Paper",
                "memory_mode": "existing",
                "retrieved_papers": [
                    {
                        "title": "Related Paper",
                        "year": 2024,
                        "citationCount": 7,
                    }
                ],
                "reviewer_memory_contexts": {
                    "Reviewer QUAL": {
                        "dimension": "QUAL",
                        "num_available_records": 3,
                        "num_selected_records": 1,
                        "entries": [
                            {
                                "sample_id": "prior-1",
                                "paper_title": "Prior Paper",
                                "score": 0.8,
                                "review_excerpt": "Prior quality feedback",
                            }
                        ],
                        "formatted_context": "Memory context",
                    }
                },
            },
        )
        self.write_json(
            sample_dir,
            "review_history.json",
            {
                "experiment_setting": {"paper_id": None},
                "messages": [
                    {
                        "agent_name": "Paper Extractor",
                        "content": "Paper content",
                        "turn": 0,
                        "timestamp": "123",
                        "visible_to": "all",
                        "msg_type": "text",
                    }
                ],
                "timing": {"duration_text": "00:04", "duration_seconds": 4.0},
            },
        )
        self.write_json(
            sample_dir,
            "timing.json",
            {
                "sample_id": sample_dir.name,
                "started_at_local": "2026-01-01 10:00:00",
                "ended_at_local": "2026-01-01 10:00:05",
                "duration_seconds": 5.0,
                "duration_text": "00:05",
            },
        )

    def test_complete_result(self):
        sample_dir = self.make_sample_dir()
        self.write_complete_sample(sample_dir)

        view = load_sample(str(self.output_dir), sample_dir.name)

        self.assertEqual(view["status"], "complete")
        self.assertEqual(view["paper"]["title"], "A Test Paper")
        self.assertEqual(view["memory"]["mode"], "existing")
        self.assertEqual(len(view["memory"]["retrieved_papers"]), 1)
        self.assertIn("Reviewer QUAL", view["memory"]["reviewer_contexts"])
        self.assertEqual(view["timing"]["duration_text"], "00:05")
        self.assertEqual(view["timing_source"], "timing.json")
        self.assertEqual(len(view["history"]["messages"]), 1)
        self.assertIn("Final Meta-Reviewer", view["final_meta_review"])

        catalog = scan_output_dir(str(self.output_dir))
        self.assertIsNone(catalog["error"])
        self.assertEqual(catalog["samples"][0]["status"], "complete")

    def test_missing_files_use_history_timing_fallback(self):
        sample_dir = self.make_sample_dir()
        self.write_json(sample_dir, "reviews.json", self.complete_reviews())
        self.write_json(sample_dir, "memory.json", {"paper_title": "Partial"})
        self.write_json(
            sample_dir,
            "review_history.json",
            {"messages": [], "timing": {"duration_text": "00:09"}},
        )

        view = load_sample(str(self.output_dir), sample_dir.name)

        self.assertEqual(view["status"], "partial")
        self.assertFalse(view["files"]["timing.json"]["exists"])
        self.assertEqual(view["timing"]["duration_text"], "00:09")
        self.assertEqual(view["timing_source"], "review_history.json")
        self.assertTrue(any("timing.json" in item for item in view["warnings"]))

    def test_empty_memory_is_compatible(self):
        sample_dir = self.make_sample_dir()
        self.write_json(sample_dir, "reviews.json", self.complete_reviews())
        self.write_json(sample_dir, "memory.json", {})
        self.write_json(sample_dir, "review_history.json", {"messages": []})
        self.write_json(sample_dir, "timing.json", {"duration_text": "00:01"})

        view = load_sample(str(self.output_dir), sample_dir.name)

        self.assertEqual(view["status"], "complete")
        self.assertEqual(view["paper"]["title"], sample_dir.name)
        self.assertEqual(view["memory"]["mode"], "unknown")
        self.assertEqual(view["memory"]["retrieved_papers"], [])
        self.assertEqual(view["memory"]["reviewer_contexts"], {})

    def test_corrupt_json_is_reported_without_crashing(self):
        sample_dir = self.make_sample_dir()
        self.write_json(sample_dir, "reviews.json", self.complete_reviews())
        (sample_dir / "memory.json").write_text("{broken", encoding="utf-8")
        self.write_json(sample_dir, "review_history.json", {"messages": []})
        self.write_json(sample_dir, "timing.json", {"duration_text": "00:01"})

        view = load_sample(str(self.output_dir), sample_dir.name)

        self.assertEqual(view["status"], "partial")
        self.assertTrue(view["files"]["memory.json"]["exists"])
        self.assertFalse(view["files"]["memory.json"]["valid"])
        self.assertIn("Unable to parse memory.json", raw_artifact_text(view, "memory.json"))
        self.assertTrue(any("Invalid artifact memory.json" in x for x in view["warnings"]))

    def test_checkpoint_result_is_exposed(self):
        sample_dir = self.make_sample_dir()
        self.write_json(
            sample_dir,
            "reviews.json",
            {"Reviewer QUAL": "Completed final output"},
        )
        self.write_json(
            sample_dir,
            "specialist_checkpoint.json",
            {
                "model_name": "test-model",
                "updated_at_local": "2026-01-01 10:00:00",
                "reviews": {
                    "Reviewer QUAL": "Checkpoint QUAL",
                    "Reviewer CLAR": "Checkpoint CLAR",
                },
            },
        )

        view = load_sample(str(self.output_dir), sample_dir.name)

        self.assertEqual(view["status"], "partial")
        self.assertTrue(view["checkpoint"]["exists"])
        self.assertEqual(
            sorted(view["checkpoint"]["reviews"]),
            ["Reviewer CLAR", "Reviewer QUAL"],
        )
        self.assertIn(
            "Reviewer CLAR",
            raw_artifact_text(view, "specialist_checkpoint.json"),
        )


if __name__ == "__main__":
    unittest.main()
