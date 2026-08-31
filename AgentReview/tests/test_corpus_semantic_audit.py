import dataclasses
import unittest

from agentreview.corpus_semantic_audit import semantic_boundary_violations
from agentreview.review_corpus import CanonicalFullReviewRecord, SubdimensionSection
from tests.test_review_corpus import full_review


def with_text(record: CanonicalFullReviewRecord, subdimension: str, text: str):
    return dataclasses.replace(
        record,
        subdimension_sections=tuple(
            dataclasses.replace(section, review_text=text)
            if section.subdimension_id == subdimension
            else section
            for section in record.subdimension_sections
        ),
    )


class CorpusSemanticAuditTest(unittest.TestCase):
    def codes(self, record):
        return {item.code for item in semantic_boundary_violations(record)}

    def test_clar_text_only_boundary_accepts_caption_review_and_rejects_pixels(self):
        good = with_text(
            full_review("CLAR"),
            "CLAR-FIG",
            "The text-only input exposes a machine-readable caption and table reference; image pixels are not observable.",
        )
        self.assertEqual(self.codes(good), set())
        bad = with_text(
            full_review("CLAR"),
            "CLAR-FIG",
            "The figure is clearly laid out with legible axes and a good color scheme.",
        )
        self.assertIn("UNOBSERVED_VISUAL_PROPERTY_JUDGMENT", self.codes(bad))

    def test_clar_allows_actionable_future_figure_suggestion(self):
        good = with_text(
            full_review("CLAR"),
            "CLAR-FIG",
            (
                "No image pixels are included, so only the vague caption can be "
                "assessed. If a figure is included, its caption should clearly "
                "state what is shown and define the reported score."
            ),
        )
        self.assertEqual(self.codes(good), set())

    def test_clar_does_not_treat_visual_evidence_phrase_as_pixel_judgment(self):
        record = with_text(
            full_review("CLAR"),
            "CLAR-FIG",
            "No image pixels are included; only the machine-readable caption can be assessed.",
        )
        record = dataclasses.replace(
            record,
            overall_assessment=(
                "The submission lacks the structural depth and visual evidence "
                "required for a clear and reproducible presentation."
            ),
        )
        self.assertEqual(self.codes(record), set())

    def test_pol_requires_evidence_limitations_and_rejects_clearance(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Frozen overlap evidence is not available, so plagiarism or dual submission cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity compliance cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation obligations cannot be assessed.",
            "POL-ETH": "No material ethical-risk pathway is identifiable from the supplied paper.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())
        bad = with_text(record, "POL-PLG", "There is no evidence of plagiarism and the content appears original.")
        self.assertIn("PLAGIARISM_CLEARANCE_FROM_ABSENCE", self.codes(bad))

    def test_pol_accepts_equivalent_grounded_limitation_wording(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": (
                "The paper does not provide auditable evidence of overlap. The "
                "absence of such evidence does not prove originality or no dual submission."
            ),
            "POL-ANO": (
                "Without a frozen policy, visible names cannot be declared an "
                "anonymity violation or clearance."
            ),
            "POL-IMP": (
                "The paper does not contain a compliance claim; no specific "
                "policy-required documentation can be evaluated."
            ),
            "POL-ETH": "No material ethical-risk pathway is visible in the paper.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())

    def test_pol_accepts_supplied_evidence_does_not_include_auditable_overlap(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": (
                "The supplied evidence does not include auditable overlap, reuse, "
                "or parallel-submission data. The absence of external plagiarism "
                "or dual-submission evidence means no conclusion regarding "
                "misconduct can be drawn from the text alone."
            ),
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation duties cannot be assessed.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        record = dataclasses.replace(
            record,
            overall_assessment=(
                "The paper text alone is insufficient to determine data/privacy "
                "compliance, anonymity compliance, plagiarism, or policy obligations."
            ),
        )
        self.assertEqual(self.codes(record), set())

    def test_pol_eth_accepts_no_material_pathway_and_rejects_external_standard(self):
        record = full_review("POL")
        safe = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation duties cannot be assessed.",
            "POL-ETH": "No material ethical-risk pathway is supported by the paper text.",
        }
        for key, text in safe.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())
        bad = with_text(
            record,
            "POL-ETH",
            "The paper violates a modern ethical standard not supplied in the input.",
        )
        self.assertIn("GENERIC_EXTERNAL_ETHICS_STANDARD", self.codes(bad))

    def test_pol_imp_accepts_corresponding_frozen_policy_limitation(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": (
                "Without corresponding frozen policy evidence, no requirements "
                "for compliance documentation can be manufactured or assessed."
            ),
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())

    def test_pol_imp_section_level_policy_limitation_scopes_prior_description(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": (
                "The paper does not include a broader-impact statement, required "
                "checklist, or compliance documentation. The text explicitly notes "
                "that no compliance rule is supplied. Without a frozen policy "
                "obligation or a paper-contained compliance claim, the absence of "
                "these documents cannot be evaluated as a policy violation."
            ),
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())

    def test_pol_overall_assessment_obeys_same_evidence_boundaries(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation duties cannot be assessed.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        record = dataclasses.replace(
            record,
            overall_assessment=(
                "No data privacy violations or anonymity breaches are visible. "
                "The use of the public dataset is consistent with standard academic "
                "practices and the norms of the field at the time of publication."
            ),
        )
        codes = self.codes(record)
        self.assertIn("ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY", codes)
        self.assertIn("DATA_COMPLIANCE_CLEARANCE_WITHOUT_EVIDENCE", codes)
        self.assertIn("UNBOUND_EXTERNAL_POLICY_NORM", codes)

    def test_pol_overall_accepts_explicit_insufficient_evidence_boundary(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": "Without frozen policy, anonymity cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation duties cannot be assessed.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        record = dataclasses.replace(
            record,
            overall_assessment=(
                "The paper does not provide sufficient evidence to evaluate ethical "
                "risks, data governance, anonymity compliance, plagiarism, or "
                "policy-required documentation."
            ),
        )
        self.assertEqual(self.codes(record), set())

    def test_pol_accepts_without_a_frozen_venue_year_anonymity_policy(self):
        record = full_review("POL")
        replacements = {
            "POL-PLG": "Overlap evidence is not available; plagiarism cannot be assessed.",
            "POL-ANO": (
                "Without a frozen venue/year anonymity policy, visible author "
                "names cannot be declared a policy violation or a compliance clearance."
            ),
            "POL-IMP": "Without frozen policy, documentation duties cannot be assessed.",
        }
        for key, text in replacements.items():
            record = with_text(record, key, text)
        self.assertEqual(self.codes(record), set())

    def test_pol_ano_section_level_policy_limitation_scopes_prior_description(self):
        record = full_review("POL")
        record = with_text(
            record,
            "POL-ANO",
            (
                "The paper header lists author names and affiliations. While this "
                "is visible, the supplied evidence does not include a frozen venue "
                "anonymity policy for the submission context to determine if this "
                "constitutes a double-blind violation. No policy compliance judgment "
                "can be made without the specific review policy."
            ),
        )
        self.assertEqual(self.codes(record), set())

    def test_external_novelty_and_sota_must_be_attributed(self):
        orig = with_text(
            full_review("ORIG"),
            "ORIG-MTH",
            "This is the first-ever globally novel method.",
        )
        self.assertIn("UNQUALIFIED_GLOBAL_NOVELTY_CLAIM", self.codes(orig))
        qualified = with_text(
            full_review("ORIG"),
            "ORIG-MTH",
            "The paper claims this is the first-ever method, but that global claim cannot be verified from paper-only evidence.",
        )
        self.assertNotIn("UNQUALIFIED_GLOBAL_NOVELTY_CLAIM", self.codes(qualified))
        sign = with_text(
            full_review("SIGN"),
            "SIGN-SOT",
            "The method is state-of-the-art across this field.",
        )
        self.assertIn("UNQUALIFIED_GLOBAL_SOTA_CLAIM", self.codes(sign))


if __name__ == "__main__":
    unittest.main()
