import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.agentreview_data import prepare_openreview_data
from memory_evolution.tasks import SPECIALIST_TASKS


class AgentReviewDataTest(unittest.TestCase):
    def _record(self, paper_id, title, paperhash):
        return {
            "id": paper_id,
            "MD_path": f"train/MD/{paper_id}",
            "conference": "ICLR 2024",
            "decision": "Accept",
            "content": {"title": title, "paperhash": paperhash},
            "opinions_md_by_category_dict": {
                "QUAL-MET": [[], [], f"quality {paper_id}"],
                "CLAR-WRT": [[], [], f"clarity {paper_id}"],
                "SIGN-DOM": [[], [], f"significance {paper_id}"],
                "ORIG-MTH": [[], [], f"originality {paper_id}"],
                "POL-ETH": [[], [], f"policy {paper_id}"],
            },
            "metareview": [[["AC", {"comment": f"meta {paper_id}"}]]],
        }

    def test_prepare_isolates_work_families_and_test(self):
        with tempfile.TemporaryDirectory(prefix="mstar_data_test_") as directory:
            root = Path(directory)
            records = [
                self._record("p0", "Version Zero", "same-work"),
                self._record("p0-title-link", "Version Zero", "different-hash"),
                self._record("p1", "Paper One", "work-one"),
                self._record("p2", "Paper Two", "work-two"),
                self._record("p3", "Paper Three", "work-three"),
            ]
            for record in records:
                paper_dir = root / record["MD_path"]
                paper_dir.mkdir(parents=True)
                (paper_dir / "md_content.md").write_text(
                    f"# {record['content']['title']}\nMethod and experiments.",
                    encoding="utf-8",
                )
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(
                    self._record("test-version", "Different title", "same-work")
                )
                + "\n",
                encoding="utf-8",
            )

            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                validation_families=1,
                meta_validation_families=1,
                max_train_families=1,
                seed=0,
            )

            self.assertTrue(manifest["test_overlap_removed"])
            self.assertEqual(manifest["family_overlap"], 0)
            for task_id in SPECIALIST_TASKS:
                task = manifest["tasks"][task_id]
                self.assertEqual(task["episode_count"], 1)
                self.assertEqual(task["validation_count"], 1)
                combined = (
                    Path(task["episodes"]).read_text(encoding="utf-8")
                    + Path(task["validation"]).read_text(encoding="utf-8")
                )
                self.assertNotIn('"paper_id": "p0"', combined)
                self.assertNotIn('"paper_id": "p0-title-link"', combined)

    def test_frozen_s2_cache_is_embedded_and_hashed(self):
        with tempfile.TemporaryDirectory(prefix="mstar_s2_test_") as directory:
            root = Path(directory)
            records = [
                self._record("p1", "Paper One", "work-one"),
                self._record("p2", "Paper Two", "work-two"),
            ]
            for record in records:
                paper_dir = root / record["MD_path"]
                paper_dir.mkdir(parents=True)
                (paper_dir / "md_content.md").write_text(
                    f"# {record['content']['title']}\nMethod.",
                    encoding="utf-8",
                )
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(self._record("test", "Held Out", "held-out")) + "\n",
                encoding="utf-8",
            )
            cache = root / "s2.json"
            cache.write_text(
                json.dumps(
                    {
                        "p1": [
                            {
                                "title": "Frozen Neighbor",
                                "year": 2025,
                                "abstract": "Cached abstract.",
                            }
                        ],
                        "p2": [],
                    }
                ),
                encoding="utf-8",
            )
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                validation_families=1,
                meta_validation_families=0,
                max_train_families=1,
                s2_cache_path=str(cache),
            )
            self.assertTrue(manifest["formal_ready"])
            self.assertEqual(len(manifest["semantic_scholar_cache_sha256"]), 64)
            payload = "".join(
                Path(manifest["tasks"][task]["queries"]).read_text(encoding="utf-8")
                for task in SPECIALIST_TASKS
            )
            self.assertIn("FROZEN RELATED-WORK CACHE", payload)


if __name__ == "__main__":
    unittest.main()
