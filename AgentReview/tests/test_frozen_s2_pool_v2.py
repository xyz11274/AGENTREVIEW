import copy
import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.toolkit import validate_frozen_s2_pool
from scripts.build_frozen_s2_pool_v2 import (
    POOL_VERSION,
    build_v2_record,
    query_sha256,
    record_sha256,
    valid_v2_work_record,
)


class FrozenS2PoolV2Test(unittest.TestCase):
    @staticmethod
    def _target(title="Actual Title"):
        return {
            "paper_id": "paper-1",
            "title": title,
            "venue": "iclr",
            "year": 2024,
            "zone": "held_out_benchmark_test",
            "paper_review_cutoff": "2023-12-31",
        }

    def test_v2_zero_result_record_binds_query_and_record_identity(self):
        target = self._target()
        record = build_v2_record(
            target,
            top_k=10,
            results=[],
            retrieval_method="Semantic Scholar Graph API canonical title search",
            retrieval_version="fixture-v2",
            retrieval_origin="live_s2_query",
            raw_api_response_sha256="a" * 64,
        )
        self.assertEqual(record["pool_version"], POOL_VERSION)
        self.assertEqual(record["canonical_query_title"], "Actual Title")
        self.assertEqual(record["query_sha256"], query_sha256("Actual Title"))
        self.assertEqual(record["retrieved_papers"], [])
        self.assertEqual(record["result_count"], 0)
        self.assertTrue(valid_v2_work_record(record, target, top_k=10))

        tampered = copy.deepcopy(record)
        tampered["canonical_query_title"] = "Changed Title"
        self.assertFalse(valid_v2_work_record(tampered, target, top_k=10))

        tampered = copy.deepcopy(record)
        tampered["target_identity"]["title"] = "Changed Title"
        tampered["record_sha256"] = record_sha256(tampered)
        self.assertFalse(valid_v2_work_record(tampered, target, top_k=10))

    def test_v2_pool_audit_requires_canonical_query_identity(self):
        target = self._target()
        record = build_v2_record(
            target,
            top_k=10,
            results=[],
            retrieval_method="fixture",
            retrieval_version="fixture-v2",
            retrieval_origin="live_s2_query",
            raw_api_response_sha256="a" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"paper-1": "2023-12-31"},
                required_ids={"paper-1"},
                expected_pool_version=POOL_VERSION,
                expected_queries={"paper-1": "Actual Title"},
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["canonical_mismatches"], [])

            record["query"] = "Different Title"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"paper-1": "2023-12-31"},
                required_ids={"paper-1"},
                expected_pool_version=POOL_VERSION,
                expected_queries={"paper-1": "Actual Title"},
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["canonical_mismatches"][0]["paper_id"], "paper-1")

    def test_canonical_audit_ignores_valid_pool_records_outside_required_scope(self):
        required = self._target("Required A/B Title")
        extra = {
            **self._target("Extra C/D Title"),
            "paper_id": "paper-extra",
            "zone": "held_out_benchmark_test",
        }
        records = [
            build_v2_record(
                target,
                top_k=10,
                results=[],
                retrieval_method="fixture",
                retrieval_version="fixture-v2",
                retrieval_origin="live_s2_query",
                raw_api_response_sha256="a" * 64,
            )
            for target in (required, extra)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.jsonl"
            path.write_text(
                "".join(json.dumps(value) + "\n" for value in records),
                encoding="utf-8",
            )
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"paper-1": "2023-12-31"},
                required_ids={"paper-1"},
                expected_pool_version=POOL_VERSION,
                expected_queries={"paper-1": "Required A/B Title"},
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["required_count"], 1)
            self.assertEqual(report["cached_count"], 2)
            self.assertEqual(report["canonical_mismatches"], [])

    def test_canonical_audit_still_rejects_wrong_required_title(self):
        record = build_v2_record(
            self._target("Wrong Stored Title"),
            top_k=10,
            results=[],
            retrieval_method="fixture",
            retrieval_version="fixture-v2",
            retrieval_origin="live_s2_query",
            raw_api_response_sha256="a" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = validate_frozen_s2_pool(
                str(path),
                expected_cutoffs={"paper-1": "2023-12-31"},
                required_ids={"paper-1"},
                expected_pool_version=POOL_VERSION,
                expected_queries={"paper-1": "Expected Manifest Title"},
            )
            self.assertFalse(report["passed"])
            self.assertEqual(
                report["canonical_mismatches"],
                [
                    {
                        "paper_id": "paper-1",
                        "fields": [
                            "canonical_query_title",
                            "target_identity.title",
                        ],
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
