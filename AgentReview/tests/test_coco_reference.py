import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_evolution.coco_reference import (
    load_official_coco_human_references,
)


class CoCoReferenceReuseTest(unittest.TestCase):
    def test_bridge_reuses_left_out_artifact_and_official_loader(self):
        with tempfile.TemporaryDirectory(prefix="mstar_coco_reuse_") as directory:
            root = Path(directory)
            evaluation_root = root / "src" / "evaluation"
            benchmark_root = root / "benchmark"
            evaluation_root.mkdir(parents=True)
            benchmark_root.mkdir()
            human_path = evaluation_root / "_0_human_review.py"
            evaluator_path = evaluation_root / "_4_evaluation.py"
            benchmark_path = benchmark_root / "benchmark_fixture.jsonl"
            left_out_path = benchmark_root / "benchmark_left_out_human.jsonl"
            human_path.write_text("# fixture\n", encoding="utf-8")
            evaluator_path.write_text("# fixture\n", encoding="utf-8")
            benchmark_path.write_text("{}\n", encoding="utf-8")
            left_out_path.write_text(
                json.dumps(
                    {
                        "id": "paper-1",
                        "reviewer": "Reviewer 2",
                        "text": "held-out review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            human_module = SimpleNamespace(
                load_selection_from_left_out_file=lambda _path: {
                    "paper-1": "Reviewer 2"
                },
                collect_reviewer_texts=lambda _records: {
                    "paper-1": {"Reviewer 2": "held-out review"}
                },
            )
            calls = []

            def official_loader(records, files):
                calls.append((records, files))
                return {
                    "paper-1": {
                        "category_correct": {
                            "QUAL-MET": ["official reference"]
                        }
                    }
                }

            evaluation_module = SimpleNamespace(
                left_out_one=lambda _files: {},
                load_human_review_data=official_loader,
            )
            with patch(
                "memory_evolution.coco_reference._load_module",
                side_effect=[human_module, evaluation_module],
            ):
                references, provenance = load_official_coco_human_references(
                    str(root),
                    {"paper-1": {"id": "paper-1"}},
                    [benchmark_path],
                )

            self.assertEqual(
                references["paper-1"]["category_correct"]["QUAL-MET"],
                ["official reference"],
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                provenance["implementation"],
                "CoCoReviewBench.src.evaluation._4_evaluation.load_human_review_data",
            )
            self.assertEqual(provenance["left_out_selection"], str(left_out_path))


if __name__ == "__main__":
    unittest.main()

