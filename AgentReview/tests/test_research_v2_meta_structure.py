import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentreview.reviewer_identity import SPECIALIST_TASKS
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.meta import (
    MetaSynthesisPaperFailure,
    generate_final_review,
    parse_meta_synthesis,
)
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review import frozen_paper_context


NOVELTY_POINTS = [
    "The paper introduces a novel model architecture that decouples document representation complexity from corpus size.",
    "The theoretical analysis shows that the corruption mechanism acts as a data-dependent regularization.",
    "The combination of simple average pooling with unbiased corruption yields both efficiency and quality.",
    "The analysis of learned embeddings directly validates the theoretical claims presented in the paper.",
]


def structured_response(points=None):
    return json.dumps({
        "sections": [
            {
                "role": role,
                "opinions": [
                    {"statement": point, "source_roles": [role],
                     "evidence_anchors": ["SRC-001"], "suggestions": []}
                    for point in (points or NOVELTY_POINTS)
                ] if role == "ORIG" else [],
            }
            for role in SPECIALIST_TASKS
        ],
        "recommendation": "Weak Accept",
    })


class _Completion:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, messages, *, max_tokens, component):
        self.calls += 1
        return self.response


class MetaStructureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paper_path = self.root / "paper.json"
        atomic_write_json(self.paper_path, Paper(
            paper_id="p1", title="Paper", abstract="Abstract",
            text="The paper presents its architecture, analysis and experimental results.",
        ).to_dict())
        self.review_paths = []
        for role in SPECIALIST_TASKS:
            path = self.root / f"{role}.json"
            atomic_write_json(path, {"paper_id": "p1", "role": role,
                                     "review_id": f"review-{role}",
                                     "review_text": f"Complete {role} review."})
            self.review_paths.append(path)

    def generate(self, response, run_id="structured-meta"):
        return generate_final_review(
            paper_path=self.paper_path, review_paths=self.review_paths,
            mode="meta", run_id=run_id, output_root=self.root,
            completion_client=_Completion(response), model_identity={"model": "mock"},
        )

    def test_distinct_generated_points_reach_stock_coco_as_distinct_parts(self):
        final = self.generate(structured_response())
        text = final["review_text"]
        for statement in NOVELTY_POINTS:
            matching_lines = [line for line in text.splitlines() if statement in line]
            self.assertEqual(len(matching_lines), 1)
            self.assertEqual(sum(point in matching_lines[0] for point in NOVELTY_POINTS), 1)

        coco = Path(__file__).resolve().parents[3] / "CoCoReviewBench"
        with patch.object(sys, "path", [str(coco), *sys.path]):
            spec = importlib.util.spec_from_file_location("stock_coco_split_for_meta_test", coco / "src/evaluation/_2_split.py")
            split = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(split)
            parts, _, _ = split.build_prompt(text)
        for statement in NOVELTY_POINTS:
            matching_parts = [part for part in parts if statement in part]
            self.assertEqual(len(matching_parts), 1)
            self.assertEqual(sum(point in matching_parts[0] for point in NOVELTY_POINTS), 1)

    def test_every_generated_item_and_its_sources_are_preserved(self):
        response = json.loads(structured_response())
        first = response["sections"][3]["opinions"][0]
        first["source_roles"] = ["ORIG", "QUAL"]
        first["suggestions"] = [
            "Compare the architecture against a capacity-matched baseline.",
            "Report the effect of the corruption mechanism separately.",
        ]
        raw = json.dumps(response)
        final = self.generate(raw)

        opinions = final["structured_meta"]["sections"][3]["opinions"]
        self.assertEqual([item["statement"] for item in opinions], NOVELTY_POINTS)
        self.assertEqual(opinions[0]["suggestions"], first["suggestions"])
        self.assertEqual(opinions[0]["source_review_ids"], ["review-ORIG", "review-QUAL"])
        self.assertEqual(opinions[0]["evidence_anchors"], ["SRC-001"])
        self.assertTrue(final["structured_meta"]["source_anchors"]["SRC-001"].startswith("SRC-001 |"))
        for suggestion in first["suggestions"]:
            self.assertEqual(final["review_text"].count(suggestion), 1)
        saved_raw = read_json(self.root / "final_reviews/structured-meta/p1.meta.raw.1.json")
        self.assertEqual(saved_raw["raw_model_output"], raw)
        self.assertEqual(final["meta_identity"]["identity_version"], "meta-synthesis-identity-v2")

    def test_invalid_structure_is_retained_as_raw_with_specific_failure(self):
        mutations = {
            "unknown_role": lambda value: value["sections"][3]["opinions"][0].update(source_roles=["META"]),
            "unknown_anchor": lambda value: value["sections"][3]["opinions"][0].update(evidence_anchors=["SRC-999"]),
            "invented_page": lambda value: value["sections"][3]["opinions"][0].update(evidence_anchors=["SRC-001(page=1)"]),
            "multiline_statement": lambda value: value["sections"][3]["opinions"][0].update(statement="First point.\nSecond point."),
            "multiline_suggestion": lambda value: value["sections"][3]["opinions"][0].update(suggestions=["First request.\nSecond request."]),
            "extra_field": lambda value: value.update(confidence=5),
            "duplicate_role": lambda value: value["sections"][0].update(role="ORIG"),
            "invalid_recommendation": lambda value: value.update(recommendation="Strong Accept"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                response = json.loads(structured_response())
                mutate(response)
                raw = json.dumps(response)
                with self.assertRaises(MetaSynthesisPaperFailure):
                    self.generate(raw, run_id=name)
                run = self.root / "final_reviews" / name
                self.assertFalse((run / "p1.json").exists())
                for attempt in (1, 2):
                    self.assertEqual(read_json(run / f"p1.meta.raw.{attempt}.json")["raw_model_output"], raw)
                errors = read_json(run / "p1.failure.json")["validation_errors"]
                self.assertEqual([error["attempt"] for error in errors], [1, 2])
                self.assertTrue(all(error["error_code"] == "meta_output_contract" for error in errors))

    def test_duplicate_json_key_retains_the_raw_response(self):
        raw = structured_response().replace('"recommendation": "Weak Accept"', '"recommendation": "Weak Accept", "recommendation": "Reject"')
        with self.assertRaises(MetaSynthesisPaperFailure):
            self.generate(raw)
        failure = read_json(self.root / "final_reviews/structured-meta/p1.failure.json")
        self.assertEqual(failure["validation_errors"][0]["field"], "recommendation")

    def test_final_resume_checks_structured_sources_as_well_as_text(self):
        self.generate(structured_response())
        path = self.root / "final_reviews/structured-meta/p1.json"
        final = read_json(path)
        final["structured_meta"]["sections"][3]["opinions"][0]["source_review_ids"] = ["forged-review"]
        path.write_text(json.dumps(final))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.generate(structured_response())

    def test_structure_validation_preserves_multi_sentence_explanation(self):
        statement = "The theorem assumes bounded noise. This assumption excludes the heavy-tailed setting considered by the experiment."
        paper = Paper.from_dict(read_json(self.paper_path))
        parsed = parse_meta_synthesis(
            structured_response([statement]),
            paper_context=frozen_paper_context(paper, "META"),
            source_review_ids_by_role={role: f"review-{role}" for role in SPECIALIST_TASKS},
        )
        self.assertEqual(parsed["sections"][3]["opinions"][0]["statement"], statement)


if __name__ == "__main__":
    unittest.main()
