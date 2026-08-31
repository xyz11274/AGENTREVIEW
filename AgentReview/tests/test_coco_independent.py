import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.coco_eval import build_parser, candidate_rows, run
from agentreview.reviewer_runtime.artifacts import CandidateReviewRecord
from agentreview.reviewer_runtime.tasks import reviewer_identity


def _record(paper_id="p1", task_id="META", run_identity="run-1"):
    return CandidateReviewRecord(
        paper_id=paper_id,
        task_id=task_id,
        review_text=f"review for {paper_id}",
        reviewer_identity=reviewer_identity(task_id),
        memory_program_identity={
            "logical_identity": "program",
            "source_kind": "optimizer_output",
        },
        memory_program_sha256="b" * 64,
        model="mock",
        revision="r1",
        run_identity=run_identity,
    )


class IndependentCoCoAdapterTest(unittest.TestCase):
    def test_candidate_jsonl_enters_adapter_without_lineage_or_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_reviews.jsonl"
            path.write_text(json.dumps(_record().to_dict()) + "\n", encoding="utf-8")
            rows = candidate_rows(str(path), "META")
        self.assertEqual(rows, [{"id": "p1", "text": "review for p1"}])

    def test_adapter_rejects_a_different_reviewer_suite_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_reviews.jsonl"
            value = _record().to_dict()
            value["reviewer_identity"]["suite_identity_sha256"] = "0" * 64
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen task META"):
                candidate_rows(str(path), "META")

    def test_duplicate_paper_after_task_filter_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_reviews.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(_record(run_identity=run_id).to_dict())
                    for run_id in ("run-1", "run-2")
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "one review per paper"):
                candidate_rows(str(path), "META")
            selected = candidate_rows(str(path), "META", "run-2")
        self.assertEqual(selected[0]["id"], "p1")

    def test_official_three_stage_commands_keep_default_evaluation_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "coco"
            output = Path(directory) / "output"
            (root / "benchmark").mkdir(parents=True)
            benchmark = root / "benchmark" / "benchmark_ICLR_2023.jsonl"
            benchmark.write_text("{}\n", encoding="utf-8")
            candidate = Path(directory) / "candidate_reviews.jsonl"
            candidate.write_text(
                json.dumps(_record().to_dict()) + "\n", encoding="utf-8"
            )
            args = build_parser().parse_args(
                [
                    "--input",
                    str(candidate),
                    "--task-id",
                    "META",
                    "--coco-root",
                    str(root),
                    "--output-dir",
                    str(output),
                ]
            )
            commands = []
            with (
                patch(
                    "agentreview.coco_eval._benchmark_paths", return_value=[benchmark]
                ),
                patch(
                    "agentreview.coco_eval.verify_coco_protocol",
                    return_value={"protocol": "official"},
                ),
                patch(
                    "agentreview.coco_eval._run",
                    side_effect=lambda command, **kwargs: commands.append(command),
                ),
            ):
                run(args)
        self.assertEqual(len(commands), 3)
        self.assertIn("_2_split.py", commands[0][1])
        self.assertIn("_3_classify.py", commands[1][1])
        evaluation = commands[2]
        self.assertEqual(evaluation[evaluation.index("--model") + 1], "qwen3.8-27b-fp8")
        benchmark_argument = evaluation[evaluation.index("--benchmark_file") + 1]
        self.assertEqual(
            benchmark_argument,
            "benchmark/benchmark_ICLR_2023.jsonl",
        )
        self.assertFalse(Path(benchmark_argument).is_absolute())
        for flag in (
            "--category_level_eval",
            "--category_incorrect_eval",
            "--calc_old_metrics",
            "--paper_level_eval",
        ):
            self.assertNotIn(flag, evaluation)

    def test_benchmark_outside_coco_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "coco"
            root.mkdir()
            external_benchmark = Path(directory) / "benchmark_ICLR_2023.jsonl"
            external_benchmark.write_text("{}\n", encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "--input",
                    str(Path(directory) / "candidate_reviews.jsonl"),
                    "--task-id",
                    "META",
                    "--coco-root",
                    str(root),
                    "--output-dir",
                    str(Path(directory) / "output"),
                    "--benchmark-file",
                    str(external_benchmark),
                ]
            )

            with self.assertRaisesRegex(ValueError, "inside coco_root"):
                run(args)


if __name__ == "__main__":
    unittest.main()
