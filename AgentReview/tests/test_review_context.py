import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentreview.message import Message
from run_single_paper_review_cli import (
    SourceAnchorValidationError,
    clip_context,
    load_specialist_checkpoint,
    normalize_source_anchors,
    run_parallel_reviews,
    run_one_paper,
    specialist_checkpoint_path,
    validate_review_source_anchors,
)


class ReviewContextTest(unittest.TestCase):
    SOURCE_ANCHORS = [
        {"source_id": "SRC-001", "page_idx": 1},
        {"source_id": "SRC-003", "page_idx": 0},
    ]

    def test_clip_context_keeps_head_and_tail(self):
        text = "HEAD-" + ("x" * 200) + "-TAIL"
        clipped = clip_context(text, 100)
        self.assertLessEqual(len(clipped), 100)
        self.assertTrue(clipped.startswith("HEAD-"))
        self.assertTrue(clipped.endswith("-TAIL"))
        self.assertIn("context clipped", clipped)

    def test_short_context_is_unchanged(self):
        self.assertEqual(clip_context("short", 100), "short")

    def test_source_anchor_normalization_expands_ranges_and_adds_pages(self):
        source_anchors = [
            {"source_id": "SRC-044", "page_idx": 3},
            {"source_id": "SRC-045", "page_idx": 3},
            {"source_id": "SRC-046", "page_idx": 3},
            {"source_id": "SRC-054", "page_idx": 4},
            {"source_id": "SRC-055", "page_idx": 4},
            {"source_id": "SRC-056", "page_idx": 4},
            {"source_id": "SRC-060", "page_idx": 4},
        ]
        text = (
            "Evidence: SRC-044–046 (page=3); "
            "sequence model SRC-054; SRC-055; SRC-056 and SRC-060 (page=4)."
        )

        normalized = normalize_source_anchors(text, source_anchors)

        self.assertIn("SRC-044(page=3)", normalized)
        self.assertIn("SRC-045(page=3)", normalized)
        self.assertIn("SRC-046(page=3)", normalized)
        self.assertIn("SRC-054(page=4)", normalized)
        self.assertIn("SRC-055(page=4)", normalized)
        self.assertIn("SRC-056(page=4)", normalized)
        self.assertIn("SRC-060(page=4)", normalized)

    def test_partial_specialist_checkpoint_is_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            paper_path = Path(directory) / "paper.md"
            paper_path.write_text("# Paper", encoding="utf-8")
            args = Namespace(
                output_dir=directory,
                model_name="gpt-5",
                reuse_completed_specialists=True,
            )
            paper_info = {
                "sample_id": "paper-1",
                "paper_md_path": str(paper_path),
            }
            checkpoint = specialist_checkpoint_path(args, paper_info)
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(
                json.dumps(
                    {
                        "paper_md_path": str(paper_path),
                        "model_name": "gpt-5",
                        "reviews": {
                            "Reviewer QUAL": (
                                "Dimension score (1-10): 8\n"
                                "Evidence: SRC-001(page=1)"
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )

            messages = load_specialist_checkpoint(
                args,
                paper_info,
                self.SOURCE_ANCHORS,
            )

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].agent_name, "Reviewer QUAL")

    def test_specialist_paper_context_is_clipped(self):
        observations = []

        class FakeReviewer:
            name = "Reviewer CLAR"

            def __call__(self, observation):
                observations.append(observation)
                return (
                    "Dimension score (1-10): 8\n"
                    "Evidence: SRC-001(page=1)"
                )

        messages = run_parallel_reviews(
            [FakeReviewer()],
            Message(
                agent_name="Paper Extractor",
                content="HEAD-" + ("x" * 1000) + "-TAIL",
                turn=0,
            ),
            {},
            paper_max_chars=200,
            source_anchors=self.SOURCE_ANCHORS,
        )

        self.assertEqual(len(messages), 1)
        paper_context = observations[0][0].content
        self.assertLessEqual(len(paper_context), 200)
        self.assertTrue(paper_context.startswith("HEAD-"))
        self.assertTrue(paper_context.endswith("-TAIL"))

    def test_partial_failure_saves_checkpoint_and_prints_resume(self):
        class GoodReviewer:
            name = "Reviewer QUAL"

            def __call__(self, observation):
                return (
                    "Dimension score (1-10): 8\n"
                    "Evidence: SRC-001(page=1)"
                )

        class FailedReviewer:
            name = "Reviewer CLAR"

            def __call__(self, observation):
                raise RuntimeError("empty response")

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "specialist_checkpoint.json"
            output = io.StringIO()
            with self.assertRaisesRegex(
                RuntimeError, "Reviewer CLAR"
            ), redirect_stdout(output):
                run_parallel_reviews(
                    [GoodReviewer(), FailedReviewer()],
                    Message(
                        agent_name="Paper Extractor",
                        content="paper",
                        turn=0,
                    ),
                    {},
                    max_workers=2,
                    checkpoint_path=checkpoint,
                    checkpoint_metadata={"model_name": "gpt-5"},
                    resume_command="python resume.py",
                    source_anchors=self.SOURCE_ANCHORS,
                )

            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertIn("Reviewer QUAL", payload["reviews"])
            self.assertNotIn("Reviewer CLAR", payload["reviews"])
            self.assertIn("python resume.py", output.getvalue())
            self.assertIn("failed=['Reviewer CLAR']", output.getvalue())

    def test_valid_numeric_anchor_passes(self):
        tokens = validate_review_source_anchors(
            "Evidence: SRC-003(page=0)",
            self.SOURCE_ANCHORS,
            "Reviewer QUAL",
        )
        self.assertEqual(tokens, ["SRC-003(page=0)"])

    def test_named_anchor_fails(self):
        with self.assertRaisesRegex(
            SourceAnchorValidationError,
            "invalid format.*SRC-ABST",
        ):
            validate_review_source_anchors(
                "Evidence: SRC-ABST(page=1)",
                self.SOURCE_ANCHORS,
                "Reviewer SIGN",
            )

    def test_unknown_numeric_anchor_fails(self):
        with self.assertRaisesRegex(
            SourceAnchorValidationError,
            "unknown anchor ID.*SRC-999",
        ):
            validate_review_source_anchors(
                "Evidence: SRC-999(page=0)",
                self.SOURCE_ANCHORS,
                "Reviewer SIGN",
            )

    def test_first_invalid_response_is_repaired_once(self):
        observations = []

        class RepairingReviewer:
            name = "Reviewer SIGN"

            def __init__(self):
                self.responses = iter(
                    [
                        "Dimension score (1-10): 7\nEvidence: SRC-INTRO(page=2)",
                        "Dimension score (1-10): 7\nEvidence: SRC-003(page=0)",
                    ]
                )

            def __call__(self, observation):
                observations.append(observation)
                return next(self.responses)

        messages = run_parallel_reviews(
            [RepairingReviewer()],
            Message(agent_name="Paper Extractor", content="paper", turn=0),
            {},
            source_anchors=self.SOURCE_ANCHORS,
        )

        self.assertEqual(len(observations), 2)
        self.assertIn("Preserve the original review conclusions", observations[1][-1].content)
        self.assertIn("SRC-003(page=0)", messages[0].content)

    def test_repair_still_invalid_marks_reviewer_failed(self):
        class InvalidReviewer:
            name = "Reviewer SIGN"

            def __init__(self):
                self.calls = 0

            def __call__(self, observation):
                self.calls += 1
                return "Dimension score (1-10): 7\nEvidence: SRC-EXP(page=4)"

        reviewer = InvalidReviewer()
        with self.assertRaisesRegex(RuntimeError, "repair attempt remained invalid"):
            run_parallel_reviews(
                [reviewer],
                Message(agent_name="Paper Extractor", content="paper", turn=0),
                {},
                source_anchors=self.SOURCE_ANCHORS,
            )
        self.assertEqual(reviewer.calls, 2)

    def test_invalid_specialist_keeps_checkpoint_and_blocks_all_commits(self):
        reviewer_names = [
            "Reviewer QUAL",
            "Reviewer CLAR",
            "Reviewer SIGN",
            "Reviewer ORIG",
            "Reviewer POL",
        ]

        class FakeExtractor:
            name = "Paper Extractor"
            role_desc = "extractor"

            def __init__(self):
                self.last_memory = {
                    "paper_title": "Test Paper",
                    "semantic_scholar_query": "Test Paper",
                    "retrieved_papers": [{"title": "Prior Paper"}],
                    "references_text": "Prior Paper",
                    "source_anchors": list(ReviewContextTest.SOURCE_ANCHORS),
                }

            def __call__(self, observation):
                return (
                    "=== SOURCE ANCHORS ===\n"
                    "SRC-001(page=1)\nSRC-003(page=0)\n"
                    "=== END SOURCE ANCHORS ==="
                )

        class FakeReviewer:
            role_desc = "reviewer"

            def __init__(self, name):
                self.name = name
                self.calls = 0

            def __call__(self, observation):
                self.calls += 1
                if self.name == "Reviewer SIGN":
                    return (
                        "Dimension score (1-10): 7\n"
                        "Evidence: SRC-ABST(page=1)"
                    )
                return (
                    "Dimension score (1-10): 8\n"
                    "Evidence: SRC-001(page=1)"
                )

        class FakeMetaReviewer:
            name = "Final Meta-Reviewer"
            role_desc = "meta"

            def __init__(self):
                self.calls = 0

            def __call__(self, observation):
                self.calls += 1
                return "Final score (1-10): 8\nEvidence: SRC-001(page=1)"

        with tempfile.TemporaryDirectory() as directory:
            paper_path = Path(directory) / "paper.md"
            paper_path.write_text("# Test Paper", encoding="utf-8")
            paper_info = {
                "sample_id": "paper-1",
                "paper_md_path": str(paper_path),
                "paper_dir": directory,
            }
            args = Namespace(
                output_dir=directory,
                model_name="fake-model",
                keyword_model_name="fake-model",
                memory_mode="none",
                disable_memory_retrieval=False,
                reuse_completed_specialists=False,
                reviewer_workers=1,
                overwrite=True,
                max_retrieved_refs=2,
                memory_top_k=1,
                memory_max_chars=1000,
                reviewer_paper_max_chars=1000,
                meta_paper_max_chars=1000,
            )
            extractor = FakeExtractor()
            reviewers = [FakeReviewer(name) for name in reviewer_names]
            meta = FakeMetaReviewer()

            with patch(
                "run_single_paper_review_cli.build_single_paper_players",
                return_value=[extractor, *reviewers, meta],
            ), self.assertRaisesRegex(RuntimeError, "Reviewer SIGN"):
                run_one_paper(args, paper_info)

            review_dir = (
                Path(directory) / "single_paper_review" / "paper-1"
            )
            checkpoint = json.loads(
                (review_dir / "specialist_checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(checkpoint["reviews"]), 4)
            self.assertIn("Reviewer SIGN", checkpoint["failures"])
            self.assertEqual(meta.calls, 0)
            for final_name in (
                "reviews.json",
                "memory.json",
                "review_history.json",
                "timing.json",
            ):
                self.assertFalse((review_dir / final_name).exists())
            self.assertFalse(
                (Path(directory) / "single_paper_review" / "memory_library.jsonl").exists()
            )
            self.assertFalse(
                (Path(directory) / "single_paper_review" / "memory_index.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
