import copy
import json
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.io import value_sha256
from agentreview.research_v2.s2 import (
    S2_RECORD_BUILDER_SHA256,
    _expected_record_identity,
    validate_frozen_s2_pool,
)


class FrozenS2ValidationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pool_path = self.root / "frozen-s2.jsonl"
        self.query_path = self.root / "queries.jsonl"
        self.targets = [
            {
                "paper_id": f"paper-{index}",
                "canonical_query_title": f"Target title {index}",
                "s2_paper_id": f"target-s2-{index}",
                "externalIds": {"DOI": f"10.1000/target-{index}"},
            }
            for index in range(2)
        ]
        self.records = [self._record(target) for target in self.targets]
        self._write(self.query_path, self.targets)
        self._write(self.pool_path, self.records)

    def tearDown(self):
        self.temporary.cleanup()

    def _record(self, target):
        record = _expected_record_identity(
            target=target,
            paper_id=target["paper_id"],
            title=target["canonical_query_title"],
            pool_version="validation-test-v1",
            raw_limit=50,
            top_k=5,
            builder_sha256=S2_RECORD_BUILDER_SHA256,
        )
        record.update(
            {
                "retrieved_papers": [],
                "result_sha256": value_sha256([]),
                "rejected": [],
            }
        )
        record["record_sha256"] = value_sha256(record)
        return record

    def _write(self, path, values):
        path.write_text(
            "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
            encoding="utf-8",
        )

    def _validate(self, **overrides):
        arguments = {
            "pool_path": self.pool_path,
            "targets": self.targets,
            "expected_count": 2,
            "pool_version": "validation-test-v1",
            "raw_limit": 50,
            "top_k": 5,
            "builder_sha256": S2_RECORD_BUILDER_SHA256,
        }
        arguments.update(overrides)
        return validate_frozen_s2_pool(**arguments)

    def _replace_evidence(self, evidence):
        records = copy.deepcopy(self.records)
        records[0]["retrieved_papers"] = evidence
        records[0]["result_sha256"] = value_sha256(evidence)
        records[0]["record_sha256"] = value_sha256(
            {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
        )
        self._write(self.pool_path, records)

    def test_accepts_direct_targets_or_a_query_path(self):
        direct = self._validate()
        from_query = self._validate(targets=None, query_path=self.query_path)
        self.assertEqual(direct, from_query)
        self.assertEqual(
            direct,
            {
                "validated": True,
                "target_count": 2,
                "record_count": 2,
                "pool_sha256": direct["pool_sha256"],
            },
        )
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            self._validate(query_path=self.query_path)
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            self._validate(targets=None)

    def test_rejects_record_order_builder_and_content_hash_mismatches(self):
        self._write(self.pool_path, list(reversed(self.records)))
        with self.assertRaisesRegex(ValueError, "record identity mismatch"):
            self._validate()

        self._write(self.pool_path, self.records)
        with self.assertRaisesRegex(ValueError, "record identity mismatch"):
            self._validate(builder_sha256="0" * 64)

        records = copy.deepcopy(self.records)
        records[0]["record_sha256"] = "0" * 64
        self._write(self.pool_path, records)
        with self.assertRaisesRegex(ValueError, "record hash mismatch"):
            self._validate()

        records = copy.deepcopy(self.records)
        records[0]["result_sha256"] = "0" * 64
        records[0]["record_sha256"] = value_sha256(
            {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
        )
        self._write(self.pool_path, records)
        with self.assertRaisesRegex(ValueError, "result hash mismatch"):
            self._validate()

    def test_rejects_every_self_match_identity(self):
        cases = (
            (
                [{"paperId": "other", "title": "Target title 0"}],
                "exact-title self match",
            ),
            (
                [{"paperId": "other", "title": "Target title 0 v2"}],
                "same-family self match",
            ),
            (
                [{"paperId": "target-s2-0", "title": "Different title"}],
                "target S2 ID",
            ),
            (
                [
                    {
                        "paperId": "other",
                        "title": "Different title",
                        "externalIds": {"doi": "10.1000/TARGET-0"},
                    }
                ],
                "shared-external-ID self match",
            ),
        )
        for evidence, message in cases:
            with self.subTest(message=message):
                self._replace_evidence(evidence)
                with self.assertRaisesRegex(ValueError, message):
                    self._validate()

    def test_rejects_duplicate_evidence_ids_titles_and_excess_top_k(self):
        cases = (
            (
                [
                    {"paperId": "same", "title": "Evidence A"},
                    {"paperId": "same", "title": "Evidence B"},
                ],
                "duplicate evidence ID",
            ),
            (
                [
                    {"paperId": "first", "title": "Evidence A"},
                    {"paperId": "second", "title": "Evidence A"},
                ],
                "duplicate evidence title",
            ),
            (
                [
                    {"paperId": f"evidence-{index}", "title": f"Evidence {index}"}
                    for index in range(6)
                ],
                "more than top-5",
            ),
        )
        for evidence, message in cases:
            with self.subTest(message=message):
                self._replace_evidence(evidence)
                with self.assertRaisesRegex(ValueError, message):
                    self._validate()

    def test_rejects_target_count_and_non_unique_target_ids(self):
        with self.assertRaisesRegex(ValueError, "target count is 2, expected 3"):
            self._validate(expected_count=3)
        duplicate_targets = copy.deepcopy(self.targets)
        duplicate_targets[1]["paper_id"] = duplicate_targets[0]["paper_id"]
        with self.assertRaisesRegex(ValueError, "unique non-empty paper IDs"):
            self._validate(targets=duplicate_targets)


if __name__ == "__main__":
    unittest.main()
