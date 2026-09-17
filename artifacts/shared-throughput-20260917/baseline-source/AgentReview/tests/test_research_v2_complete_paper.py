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
from agentreview.research_v2.coco_review_batch import ResearchV2ReviewGenerationAdapter, SpecialistInvocation, FinalInvocation
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.evaluation import run_internal_evaluation
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

    def test_existing_review_adapter_meta_and_evaluator_receive_complete_body(self):
        text = "# Complete body\n" + "A detailed observation.\n" * 5000 + "\nAPPENDIX FINAL EVIDENCE."
        identity = {"model": "fixture-reviewer"}
        class Completion:
            config = SimpleNamespace(identity=lambda: identity)
            def __init__(self):
                self.prompts = []
            def complete(self, messages, *, max_tokens, component):
                self.prompts.append((component, "\n".join(message["content"] for message in messages)))
                if component.startswith("specialist_review:"):
                    return "The appendix provides the detailed observations."
                if component == "meta_synthesis":
                    return json.dumps({"sections": [
                        {"role": role, "opinions": [{"statement": "The appendix supports the findings.", "source_roles": [role], "evidence_anchors": [], "suggestions": []}]}
                        for role in ROLES
                    ], "recommendation": "Accept"})
                return json.dumps({"review_quality": 4, "major_missing_points": [], "factual_evidence_issues": [], "improvement_suggestions": []})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper_path = root / "paper.json"
            atomic_write_json(paper_path, Paper("p", "Complete paper", "Abstract", text).to_dict())
            s2 = root / "s2.jsonl"
            write_derived_jsonl(s2, [{"paper_id": "p", "pool_version": "fixture", "retrieved_papers": []}])
            store = FrozenS2Store(s2)
            program = seed_program_path("vector_search")
            embedding_identity = {"provider": "deterministic-fallback-v1"}
            client = Completion()
            adapter = ResearchV2ReviewGenerationAdapter(completion_client=client, embedding_identity=embedding_identity, require_bubblewrap=False)
            review_paths = []
            for role in ROLES:
                snapshot = root / f"{role}.snapshot.json"
                build_memory_snapshot(role=role, program_path=program, program_version="fixture", review_paths=[], evaluation_paths=[], output_path=snapshot, seed_kind="vector_search", embedding_identity=embedding_identity, require_bubblewrap=False)
                invocation = SpecialistInvocation(paper_id="p", paper_path=paper_path, role=role, program_path=program, snapshot_path=snapshot, run_id="fixture", output_root=root, model_identity=identity, embedding_identity=embedding_identity, require_bubblewrap=False, program_version="fixture", frozen_s2_path=s2, frozen_s2_store=store, s2_intervention={"protocol": "s2-intervention-v1", "mode": "forced_off", "label": "fixture"})
                adapter.generate_specialist(invocation)
                review_paths.append(invocation.output_path)
            adapter.generate_final(FinalInvocation(paper_id="p", paper_path=paper_path, review_paths=tuple(review_paths), mode="meta", run_id="fixture", output_root=root, model_identity=identity, embedding_identity=embedding_identity, require_bubblewrap=False))
            run_internal_evaluation(paper_path=paper_path, review_path=review_paths[0], run_id="evaluation", output_root=root, completion_client=client, evaluator_identity=identity)
            self.assertEqual(len(client.prompts), 7)
            for component, prompt in client.prompts:
                with self.subTest(component=component):
                    self.assertIn(text, prompt)


if __name__ == "__main__":
    unittest.main()
