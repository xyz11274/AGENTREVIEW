import unittest

from agentreview.paper_context import (
    candidate_visible_metadata,
    format_canonical_paper_context,
)
from agentreview.reviewer_identity import load_reviewer_suite
from agentreview.single_paper_review_roles import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    get_meta_reviewer_description,
    load_review_request_sections,
)
from memory_evolution.assets import audit_four_zone_isolation
from memory_evolution.evaluator import candidate_safe_case, candidate_safe_trace


class ProtocolBoundaryTest(unittest.TestCase):
    def test_frozen_identity_covers_all_specialists_and_meta(self):
        sections = load_review_request_sections()
        self.assertEqual(set(sections), {"QUAL", "CLAR", "SIGN", "ORIG", "POL", "META"})
        self.assertEqual(get_meta_reviewer_description(), sections["META"])
        self.assertEqual(
            load_reviewer_suite().task("META").system_prompt,
            sections["META"],
        )
        self.assertEqual(len(EXPECTED_REVIEW_REQUEST_SHA256), 64)

    def test_canonical_context_rejects_internal_provenance(self):
        with self.assertRaisesRegex(ValueError, "Internal provenance"):
            format_canonical_paper_context(
                "paper",
                {
                    **candidate_visible_metadata(
                        paper_id="p1", title="Title", reviewer_role="QUAL"
                    ),
                    "paper_md_path": "/secret/path.md",
                },
                [],
            )

    def test_feedback_strips_gold_and_free_text(self):
        case = candidate_safe_case(
            {
                "id": "p1::QUAL",
                "question": "candidate question",
                "gold": "human answer",
                "answer": "candidate answer",
                "context": "memory",
                "score": 0.25,
                "success": True,
            }
        )
        self.assertEqual(set(case), {"score", "success"})
        trace = candidate_safe_trace(
            {
                "phase": "read",
                "id": "p1::QUAL",
                "gold": "human answer",
                "answer": "candidate answer",
                "error": "ValueError: rationale containing gold",
                "success": False,
            }
        )
        self.assertNotIn("gold", trace)
        self.assertNotIn("answer", trace)
        self.assertEqual(trace["error_category"], "ValueError")

    def test_four_zone_audit_detects_title_family_overlap(self):
        report = audit_four_zone_isolation(
            {
                "historical_experience": [
                    {
                        "paper_id": "a",
                        "title": "Same Paper",
                        "work_family": "f1",
                        "paper_review_cutoff": "2022-12-31",
                    }
                ],
                "evolution_validation": [
                    {
                        "paper_id": "b",
                        "title": "Same-paper",
                        "work_family": "f2",
                        "paper_review_cutoff": "2022-12-31",
                    }
                ],
                "effectiveness_holdout": [],
                "held_out_benchmark_test": [],
            }
        )
        self.assertFalse(report["passed"])
        self.assertTrue(
            any(value["field"] == "normalized_title" for value in report["overlaps"])
        )


if __name__ == "__main__":
    unittest.main()
