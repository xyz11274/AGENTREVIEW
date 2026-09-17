import copy
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import coco_review_batch as batch_module
from agentreview.research_v2.io import (
    atomic_write_json,
    value_sha256,
    write_immutable_jsonl,
)


class ImmutableJsonlTest(unittest.TestCase):
    def test_identical_content_is_an_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "records.jsonl"
            values = [
                {"paper_id": "paper-1", "text": "First"},
                {"value": "\u4e2d\u6587"},
            ]

            first = write_immutable_jsonl(path, values)
            original_bytes = path.read_bytes()
            second = write_immutable_jsonl(path, (dict(value) for value in values))

            self.assertEqual(first, path.resolve())
            self.assertEqual(second, first)
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_different_content_is_rejected_without_replacing_the_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            write_immutable_jsonl(path, [{"paper_id": "paper-1", "text": "First"}])
            original_bytes = path.read_bytes()

            with self.assertRaisesRegex(FileExistsError, "Immutable JSONL artifact"):
                write_immutable_jsonl(
                    path,
                    [{"paper_id": "paper-1", "text": "Changed"}],
                )

            self.assertEqual(path.read_bytes(), original_bytes)


class CompletedCocoReviewBatchValidationTest(unittest.TestCase):
    run_id = "winner-review-run"
    run_spec = {"frozen": "run-spec"}

    def _completed_progress(self, planned):
        return {
            "planned": planned,
            "completed": 0,
            "resumed": planned,
            "failed": 0,
            "blocked": 0,
            "pending": 0,
        }

    def _complete_summary(self):
        return {
            "protocol": batch_module.BATCH_PROTOCOL,
            "stage": "generate-coco-1300-reviews",
            "run_id": self.run_id,
            "status": "complete",
            "max_workers": 4,
            "run_spec": self.run_spec,
            "run_spec_sha256": value_sha256(self.run_spec),
            "progress": {
                "specialist": self._completed_progress(6_500),
                "meta": self._completed_progress(1_300),
                "concat": self._completed_progress(1_300),
            },
            "failure_count": 0,
            "failures": [],
            "infrastructure_failures": [],
            "started_at": "2026-09-02T00:00:00+00:00",
            "updated_at": "2026-09-02T00:01:00+00:00",
            "completed_at": "2026-09-02T00:01:00+00:00",
        }

    def _write_summary(self, root, summary):
        return atomic_write_json(
            root / "runs" / self.run_id / "coco_review_batch.json",
            summary,
            immutable=False,
        )

    def _validate(self, root):
        return batch_module.validate_completed_coco_review_batch(
            frozen_system_path=root / "frozen-system.json",
            coco_manifest_path=root / "coco-manifest.json",
            output_root=root,
            run_id=self.run_id,
        )

    def _assert_summary_gate_accepts(self, root):
        with (
            patch.object(
                batch_module,
                "_load_frozen_runtime",
                return_value=object(),
            ),
            patch.object(
                batch_module,
                "_run_spec",
                return_value=self.run_spec,
            ),
            patch.object(
                batch_module,
                "_load_existing_artifacts",
                return_value=({}, {}, {}, deque(), deque()),
            ) as load_artifacts,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Completed CoCo Review artifacts do not cover the exact batch",
            ):
                self._validate(root)
        load_artifacts.assert_called_once()

    def _assert_summary_gate_rejects(self, root, message):
        with (
            patch.object(
                batch_module,
                "_load_frozen_runtime",
                return_value=object(),
            ),
            patch.object(
                batch_module,
                "_run_spec",
                return_value=self.run_spec,
            ),
            patch.object(batch_module, "_load_existing_artifacts") as load_artifacts,
        ):
            with self.assertRaisesRegex(ValueError, message):
                self._validate(root)
        load_artifacts.assert_not_called()

    def test_complete_summary_requires_the_exact_planned_and_finished_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact = self._complete_summary()
            self._write_summary(root, exact)

            self._assert_summary_gate_accepts(root)

            cases = {}
            wrong_planned = copy.deepcopy(exact)
            wrong_planned["progress"]["meta"]["planned"] = 1_299
            cases["planned"] = wrong_planned
            wrong_finished = copy.deepcopy(exact)
            wrong_finished["progress"]["specialist"]["resumed"] = 6_499
            cases["finished"] = wrong_finished
            for label, inexact in cases.items():
                with self.subTest(count=label):
                    self._write_summary(root, inexact)
                    self._assert_summary_gate_rejects(root, "progress is not exact")

    def test_noncomplete_or_missing_count_is_rejected_before_artifact_loading(self):
        cases = {}
        noncomplete = self._complete_summary()
        noncomplete["status"] = "running"
        cases["noncomplete"] = (
            noncomplete,
            "not a complete failure-free winner batch",
        )
        missing_count = self._complete_summary()
        del missing_count["progress"]["meta"]["planned"]
        cases["missing planned count"] = (
            missing_count,
            "Meta progress is not exact",
        )

        for label, (summary, message) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write_summary(root, summary)
                self._assert_summary_gate_rejects(root, message)


if __name__ == "__main__":
    unittest.main()
