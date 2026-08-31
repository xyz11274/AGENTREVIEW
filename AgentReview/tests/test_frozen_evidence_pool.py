import tempfile
import unittest
from pathlib import Path

from agentreview.frozen_evidence import (
    FrozenEvidencePool,
    materialize_frozen_evidence_pool,
)


class FrozenEvidencePoolTest(unittest.TestCase):
    def test_item_identity_read_only_loader_and_scope_validation(self):
        pool = materialize_frozen_evidence_pool(
            pool_version="frozen-external-evidence-v1",
            paper_sets=(
                {
                    "target_paper_id": "B-01",
                    "target_family_id": "family-01",
                    "cutoff": "2024-01-01",
                    "partition_availability": {
                        "PRIOR_WORK": "available",
                        "POLICY": "unavailable",
                        "ARTIFACT_LICENSE": "not_archived_before_cutoff",
                    },
                },
            ),
            items=(
                {
                    "partition": "PRIOR_WORK",
                    "target_paper_id": "B-01",
                    "canonical_source_identity": "s2:paper:123",
                    "source_url": "https://example.invalid/s2/123",
                    "source_id": "123",
                    "venue": "ExampleConf",
                    "year": "2023",
                    "cutoff": "2024-01-01",
                    "retrieval_snapshot_time": "2026-08-31T00:00:00Z",
                    "mime_type": "application/json",
                    "provenance": "frozen-s2-temporal-v2",
                    "raw_content": b"Prior work evidence.\r\n",
                    "publication_or_version_date": "2023-05-01",
                },
            ),
            projection_identity="deterministic-text-projection-v1",
        )
        manifest = pool.manifest("B-01")
        self.assertEqual(manifest.result_count, 1)
        evidence_id = manifest.partition_evidence_ids["PRIOR_WORK"][0]
        record = pool.read_evidence("B-01", (evidence_id,))[0]
        self.assertEqual(record.canonical_text, "Prior work evidence.\n")
        self.assertEqual(record.evidence_id, evidence_id)
        self.assertTrue(pool.validate_evidence_ids("B-01", (evidence_id,)))
        with self.assertRaisesRegex(ValueError, "not in the target paper"):
            pool.read_evidence("B-01", ("evidence_" + "0" * 64,))
        delivered = pool.render_full_context("B-01")
        self.assertIn(evidence_id, delivered)
        self.assertIn("PRIOR_WORK", delivered)
        self.assertIn("POLICY", delivered)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence_set_manifest.json"
            pool.write(path)
            loaded = FrozenEvidencePool.load(path)
        self.assertEqual(loaded.pool_sha256, pool.pool_sha256)
        self.assertEqual(loaded.render_full_context("B-01"), delivered)


if __name__ == "__main__":
    unittest.main()
