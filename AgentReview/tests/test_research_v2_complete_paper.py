import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentreview.paper_context import (
    PaperContextBudgetError,
    build_canonical_paper_context,
    format_canonical_paper_context,
    text_source_anchors,
)
from agentreview.research_v2.data import _paper_asset_path
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.io import atomic_write_json, write_derived_jsonl
from agentreview.research_v2.memory import build_memory_snapshot, seed_program_path
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review_preparation import frozen_paper_context
from agentreview.research_v2.s2 import FrozenS2Store


class CompletePaperTest(unittest.TestCase):
    def test_complete_long_paper_and_appendix_reach_review_context(self):
        text = "BEGIN\n" + "Methods and observations.\n" * 6000 + "APPENDIX FINAL EVIDENCE\n"
        paper = Paper("long", "Long paper", "abstract", text, {"venue_year": "ICLR_2024"})
        context = frozen_paper_context(paper, "QUAL")
        extracted = context.split("=== BEGIN PAPER (MD) ===\n", 1)[1].split("\n=== END PAPER (MD) ===", 1)[0]
        self.assertEqual(extracted, text)
        self.assertIn("venue: ICLR\nyear: 2024", context)
        self.assertIn("chars=[0,2000)", context)
        self.assertNotIn("page=0", context)

    def test_explicit_budget_fails_with_complete_required_size(self):
        text = "Evidence " * 10000
        with self.assertRaisesRegex(PaperContextBudgetError, "Complete paper context requires"):
            format_canonical_paper_context(text, {"paper_id": "p", "title": "Title"}, text_source_anchors(text), max_chars=60000)
        complete = format_canonical_paper_context(text, {"paper_id": "p", "title": "Title"}, text_source_anchors(text), max_chars=100000)
        self.assertIn(text, complete)

    def test_frozen_budget_applies_to_meta_and_specialists(self):
        paper = Paper("p", "Title", "Abstract", "full body", {"paper_context_max_chars": 5})
        for role in ("META", "QUAL"):
            with self.subTest(role=role), self.assertRaises(PaperContextBudgetError):
                frozen_paper_context(paper, role)

    def test_text_anchor_intervals_cover_complete_text_without_invented_pages(self):
        text = "alpha\nbeta " * 1300
        anchors = text_source_anchors(text)
        self.assertEqual(anchors[0]["char_start"], 0)
        self.assertEqual(anchors[-1]["char_end"], len(text))
        for left, right in zip(anchors, anchors[1:]):
            self.assertEqual(left["char_end"], right["char_start"])
        for anchor in anchors:
            excerpt = " ".join(text[anchor["char_start"]:anchor["char_end"]].split())[:120]
            self.assertEqual(anchor["text"], excerpt)
            self.assertNotIn("page_idx", anchor)

    def test_complete_asset_selection_and_strict_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "paper"
            folder.mkdir()
            short = folder / "md_content_no_appendix.md"
            short.write_text("short", encoding="utf-8")
            self.assertIsNone(_paper_asset_path(root, {"MD_path": "paper"}))
            full = folder / "md_content.md"
            full.write_text("body\nappendix", encoding="utf-8")
            self.assertEqual(_paper_asset_path(root, {"MD_path": "paper"}), full)
            self.assertIn("body\nappendix", build_canonical_paper_context(str(full), paper_id="p").text)
            full.write_bytes(b"body\xffappendix")
            with self.assertRaises(UnicodeDecodeError):
                build_canonical_paper_context(str(full), paper_id="p")



if __name__ == "__main__":
    unittest.main()
