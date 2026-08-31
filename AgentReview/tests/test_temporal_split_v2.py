import unittest

from memory_evolution.assets import FOUR_ZONES
from scripts.build_coco_temporal_split import (
    _family_components,
    audit_family_accounting,
    extract_coco_paperhash,
    extract_coco_title,
    select_family_representative,
)


class TemporalSplitV2Test(unittest.TestCase):
    def test_extract_coco_title_accepts_legacy_string(self):
        self.assertEqual(
            extract_coco_title({"title": "  Actual Title  "}, "paper-1"),
            "Actual Title",
        )

    def test_extract_coco_title_accepts_openreview_v2_object(self):
        title = extract_coco_title({"title": {"value": "Actual Title"}}, "paper-1")
        self.assertEqual(title, "Actual Title")
        self.assertNotEqual(title, "{'value': 'Actual Title'}")

    def test_extract_coco_paperhash_accepts_both_source_schemas(self):
        self.assertEqual(
            extract_coco_paperhash({"paperhash": "author|paper"}, "paper-1"),
            "author|paper",
        )
        self.assertEqual(
            extract_coco_paperhash({"paperhash": {"value": "author|paper"}}, "paper-2"),
            "author|paper",
        )

    def test_extract_coco_title_rejects_malformed_values(self):
        for content in (
            {"title": {"value": ""}},
            {"title": {"value": 123}},
            {"title": {}},
            {"title": 123},
            {},
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                extract_coco_title(content, "paper-1")

    @staticmethod
    def _zones(title, family, *, paper_id="paper-1", excluded=None):
        zones = {zone: [] for zone in FOUR_ZONES}
        zones[FOUR_ZONES[0]].append(
            {
                "paper_id": paper_id,
                "title": title,
                "paper_family": family,
                "family_id": family,
                "representative_paper_id": paper_id,
                "excluded_paper_ids": list(excluded or []),
                "venue": "iclr",
                "year": 2022,
                "paper_review_cutoff": "2021-12-31",
                "zone": FOUR_ZONES[0],
            }
        )
        return zones

    def test_family_audit_requires_every_source_record_to_be_accounted(self):
        source = [{"paper_id": "paper-1"}, {"paper_id": "paper-2"}]
        new = self._zones("Actual Title", "family", paper_id="paper-1")
        audit = audit_family_accounting(source, new)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["unaccounted_paper_ids"], ["paper-2"])

        new[FOUR_ZONES[0]][0]["excluded_paper_ids"] = ["paper-2"]
        audit = audit_family_accounting(source, new)
        self.assertTrue(audit["passed"])

    def test_representative_selection_prefers_accepted_submission_globally(self):
        common = {
            "title": "ID and OOD Performance Are Sometimes Inversely Correlated",
            "year": 2023,
            "source_record_sha256": "a" * 64,
            "_pdf_path": "paper.pdf",
        }
        rejected = {**common, "paper_id": "qkdzAuh_gy", "_decision": "Reject"}
        accepted = {
            **common,
            "paper_id": "HZQZli6amV",
            "_decision": "Accept (spotlight)",
        }
        self.assertEqual(
            select_family_representative([rejected, accepted])["paper_id"],
            "HZQZli6amV",
        )

    def test_representative_selection_uses_stable_id_tie_break(self):
        common = {
            "year": 2023,
            "_decision": "Reject",
            "_pdf_path": "paper.pdf",
            "source_record_sha256": "a" * 64,
        }
        selected = select_family_representative(
            [
                {**common, "paper_id": "paper-z"},
                {**common, "paper_id": "paper-a"},
            ]
        )
        self.assertEqual(selected["paper_id"], "paper-a")

    def test_representative_selection_prefers_later_venue_year(self):
        common = {
            "_decision": "Accept",
            "_pdf_path": "paper.pdf",
            "source_record_sha256": "a" * 64,
        }
        selected = select_family_representative(
            [
                {**common, "paper_id": "older", "year": 2022},
                {**common, "paper_id": "newer", "year": 2023},
            ]
        )
        self.assertEqual(selected["paper_id"], "newer")

    def test_representative_selection_prefers_explicit_pdf_version(self):
        common = {
            "year": 2023,
            "_decision": "Accept",
            "source_record_sha256": "a" * 64,
        }
        selected = select_family_representative(
            [
                {**common, "paper_id": "v2", "_pdf_path": "2209.00613v2.pdf"},
                {**common, "paper_id": "v4", "_pdf_path": "2209.00613v4.pdf"},
            ]
        )
        self.assertEqual(selected["paper_id"], "v4")

    def test_family_clustering_uses_canonical_title_or_paperhash(self):
        records = [
            {
                "normalized_title": "same title",
                "paperhash": "first|paper",
            },
            {
                "normalized_title": "same title",
                "paperhash": "second|paper",
            },
            {
                "normalized_title": "different title",
                "paperhash": "second|paper",
            },
        ]
        families = _family_components(records)
        self.assertEqual(len(set(families)), 1)


if __name__ == "__main__":
    unittest.main()
