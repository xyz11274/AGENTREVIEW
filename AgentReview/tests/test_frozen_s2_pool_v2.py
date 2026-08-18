import copy
import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.toolkit import validate_frozen_s2_pool
from scripts.build_frozen_s2_pool import canonical_sha256
from scripts.build_frozen_s2_pool_v2 import (
    LEGACY_POOL_VERSION,
    POOL_VERSION,
    REUSE_RETRIEVAL_VERSION,
    build_v2_record,
    load_legacy_records,
    query_change_plan,
    query_sha256,
    record_sha256,
    reusable_legacy_record,
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

    @staticmethod
    def _legacy(query="Actual Title"):
        results = []
        return {
            "paper_id": "paper-1",
            "target_identity": {"paper_id": "paper-1"},
            "paper_review_cutoff": "2023-12-31",
            "query": query,
            "retrieval_method": "fixture",
            "retrieval_version": "s2-graph-title-search-v1",
            "retrieval_config": {"top_k": 10},
            "top_k": 10,
            "retrieved_papers": results,
            "result_sha256": canonical_sha256(results),
            "pool_version": LEGACY_POOL_VERSION,
        }

    def test_query_plan_uses_actual_v1_query_and_detects_affected_target(self):
        target = self._target()
        v1_target = self._target("{'value': 'Actual Title'}")
        plan = query_change_plan(
            [target], {"paper-1": v1_target}, {}
        )
        self.assertTrue(plan["paper-1"]["affected"])
        self.assertEqual(plan["paper-1"]["v2_canonical_query"], "Actual Title")

    def test_clean_actual_v1_query_can_be_reused_even_if_v1_manifest_was_dirty(self):
        target = self._target()
        legacy = self._legacy("Actual Title")
        v1_target = self._target("{'value': 'Actual Title'}")
        plan = query_change_plan(
            [target], {"paper-1": v1_target}, {"paper-1": legacy}
        )
        self.assertFalse(plan["paper-1"]["affected"])
        self.assertTrue(reusable_legacy_record(legacy, target, top_k=10))

    def test_polluted_v1_query_is_not_reusable(self):
        self.assertFalse(
            reusable_legacy_record(
                self._legacy("{'value': 'Actual Title'}"),
                self._target(),
                top_k=10,
            )
        )

    def test_legacy_final_and_work_may_differ_only_by_derived_result_count(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = Path(directory) / "v1.jsonl"
            final_record = self._legacy()
            final_record["result_count"] = 0
            pool.write_text(json.dumps(final_record) + "\n", encoding="utf-8")
            work_dir = pool.with_suffix(pool.suffix + ".work")
            work_dir.mkdir()
            work_record = copy.deepcopy(final_record)
            work_record.pop("result_count")
            (work_dir / "record.json").write_text(
                json.dumps(work_record), encoding="utf-8"
            )

            loaded = load_legacy_records(pool)
            self.assertEqual(loaded["paper-1"]["result_count"], 0)
            self.assertNotIn(
                "result_count", json.loads((work_dir / "record.json").read_text())
            )

    def test_v2_zero_result_record_binds_query_and_record_identity(self):
        target = self._target()
        legacy = self._legacy()
        record = build_v2_record(
            target,
            top_k=10,
            results=[],
            retrieval_method="Provenance-safe reuse",
            retrieval_version=REUSE_RETRIEVAL_VERSION,
            retrieval_origin="reused_clean_v1",
            raw_api_response_sha256="a" * 64,
            legacy_record=legacy,
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
            retrieval_origin="retrieved_missing_clean_v1",
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


if __name__ == "__main__":
    unittest.main()
