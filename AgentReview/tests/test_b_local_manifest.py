import tempfile
import unittest
from pathlib import Path

from memory_evolution.b_local_manifest import (
    BLocalManifest,
    materialize_b_local_manifest,
)


ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")


def paper(index):
    return {
        "paper_id": f"B-{index:02d}",
        "family_id": f"family-{index:02d}",
        "reviewer_question": f"review B-{index:02d}",
        "paper_text": f"paper text {index}",
        "structural_context": "SRC-001",
        "cutoff": "2024-01-01",
    }


class BLocalManifestTest(unittest.TestCase):
    def test_candidate_blind_membership_is_deterministic_and_round_trips(self):
        papers = [paper(index) for index in range(32)]
        eligibility = {
            role: {
                item["paper_id"]: {
                    "eligible": True,
                    "reason_code": "ROLE_EVALUABLE",
                }
                for item in papers
            }
            for role in ROLES
        }
        thresholds = {
            role: {"min_static_evaluable": 20, "min_full_pool_evaluable": 25}
            for role in ROLES
        }
        first = materialize_b_local_manifest(
            papers=papers,
            eligibility=eligibility,
            thresholds=thresholds,
            selection_seed="b-local-smoke-seed",
            attempts=2,
            static_size=25,
            rotating_size=5,
            source_manifest_sha256="a" * 64,
            rubric_protocol_version="core-v1",
        )
        second = materialize_b_local_manifest(
            papers=list(reversed(papers)),
            eligibility=eligibility,
            thresholds=thresholds,
            selection_seed="b-local-smoke-seed",
            attempts=2,
            static_size=25,
            rotating_size=5,
            source_manifest_sha256="a" * 64,
            rubric_protocol_version="core-v1",
        )
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        for role in ROLES:
            view = first.role_view(role)
            self.assertEqual(len(view.static_paper_ids), 25)
            self.assertEqual(len(view.rotating_schedule), 2)
            self.assertTrue(all(len(batch) == 5 for batch in view.rotating_schedule))
            self.assertEqual(len(view.full_pool_paper_ids), 32)
            self.assertNotIn("candidate", first.canonical_payload)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b_local_manifest.json"
            first.write(path)
            loaded = BLocalManifest.load(path)
        self.assertEqual(loaded.manifest_sha256, first.manifest_sha256)
        self.assertEqual(loaded.canonical_payload, first.canonical_payload)


if __name__ == "__main__":
    unittest.main()
