import json
import unittest

from agentreview.review_corpus import ROLE_SUBDIMENSIONS
from memory_evolution.role_evaluator import (
    ApplicabilityConsistencyGate,
    ReferenceFreeRoleEvaluator,
    canonical_fitness,
    parse_applicability,
    parse_scoring,
)


def applicability_payload(role, paper_id, applicable):
    return {
        "role": role,
        "paper_id": paper_id,
        "decisions": [
            {
                "subdimension_id": subdimension,
                "status": "APPLICABLE" if subdimension in applicable else "N_A",
                "n_a_reason": None if subdimension in applicable else "INTRINSICALLY_NOT_APPLICABLE",
                "paper_anchor_ids": ["SRC-001"],
            }
            for subdimension in ROLE_SUBDIMENSIONS[role]
        ],
    }


def scoring_payload(role, paper_id, applicable, score):
    return {
        "role": role,
        "paper_id": paper_id,
        "scores": [
            {
                "subdimension_id": subdimension,
                "score": score if subdimension in applicable else None,
                "events": [],
                "evidence_limitations": [],
            }
            for subdimension in ROLE_SUBDIMENSIONS[role]
        ],
    }


class RoleEvaluatorTest(unittest.TestCase):
    def applicability(self, paper_id, applicable):
        return parse_applicability(
            json.dumps(applicability_payload("QUAL", paper_id, applicable)),
            role="QUAL",
            paper_id=paper_id,
            evaluator_identity="eval-v1",
            protocol_identity="protocol-v1",
        )

    def score(self, applicability, applicable, score):
        return parse_scoring(
            json.dumps(scoring_payload("QUAL", applicability.paper_id, applicable, score)),
            applicability=applicability,
            candidate_id="candidate-1",
            candidate_review="grounded review",
            evaluator_identity="eval-v1",
            protocol_identity="protocol-v1",
        )

    def test_fitness_is_two_layer_macro_average_not_flattened(self):
        all_dims = set(ROLE_SUBDIMENSIONS["QUAL"])
        one_dim = {ROLE_SUBDIMENSIONS["QUAL"][0]}
        first_app = self.applicability("paper-1", all_dims)
        second_app = self.applicability("paper-2", one_dim)
        first = self.score(first_app, all_dims, 4)
        second = self.score(second_app, one_dim, 0)
        self.assertEqual(canonical_fitness([first, second]), "2.000000000000")

    def test_strict_integer_score_rejects_float_and_unknown_fields(self):
        one_dim = {ROLE_SUBDIMENSIONS["QUAL"][0]}
        app = self.applicability("paper-1", one_dim)
        payload = scoring_payload("QUAL", "paper-1", one_dim, 4)
        payload["scores"][0]["score"] = 4.0
        with self.assertRaisesRegex(TypeError, "JSON integer"):
            parse_scoring(
                json.dumps(payload),
                applicability=app,
                candidate_id="candidate-1",
                candidate_review="review",
                evaluator_identity="eval-v1",
                protocol_identity="protocol-v1",
            )
        payload = applicability_payload("QUAL", "paper-1", one_dim)
        payload["human_review"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "extra"):
            parse_applicability(
                json.dumps(payload),
                role="QUAL",
                paper_id="paper-1",
                evaluator_identity="eval-v1",
                protocol_identity="protocol-v1",
            )

    def test_applicability_inconsistency_fails_the_gate(self):
        gate = ApplicabilityConsistencyGate()
        gate.observe(self.applicability("paper-1", {"QUAL-MET"}))
        with self.assertRaisesRegex(RuntimeError, "APPLICABILITY_INCONSISTENCY"):
            gate.observe(self.applicability("paper-1", {"QUAL-EXP"}))

    def test_facade_never_injects_human_openreview_or_coco_assets(self):
        captured = []
        phase = {"value": "app"}

        def complete(messages):
            captured.append(messages)
            if phase["value"] == "app":
                return json.dumps(
                    applicability_payload("CLAR", "paper-1", {"CLAR-WRT"})
                )
            return json.dumps(scoring_payload("CLAR", "paper-1", {"CLAR-WRT"}, 3))

        evaluator = ReferenceFreeRoleEvaluator(
            complete=complete,
            evaluator_identity="eval-v1",
            protocol_identity="protocol-v1",
            role_rubric="frozen CLAR rubric",
        )
        app = evaluator.applicability(
            role="CLAR",
            paper_id="paper-1",
            paper="paper text",
            structural_context="SRC-001",
            frozen_external_evidence="",
        )
        phase["value"] = "score"
        result = evaluator.score(
            applicability=app,
            candidate_id="candidate-1",
            candidate_review="review",
            paper="paper text",
            structural_context="SRC-001",
            frozen_external_evidence="",
        )
        self.assertEqual(result.paper_fraction.numerator, 3)
        for messages in captured:
            visible = json.loads(messages[1]["content"])
            self.assertNotIn("human_review", visible)
            self.assertNotIn("openreview_reference", visible)
            self.assertNotIn("coco_score", visible)


if __name__ == "__main__":
    unittest.main()
