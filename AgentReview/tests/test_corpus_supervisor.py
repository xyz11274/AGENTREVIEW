import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "supervise_core_v1_corpus_bootstrap.py"
)
SPEC = importlib.util.spec_from_file_location("corpus_supervisor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CorpusSupervisorTests(unittest.TestCase):
    def test_atomic_json_replaces_complete_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            MODULE._atomic_json(path, {"state": "FIRST"})
            MODULE._atomic_json(path, {"state": "SECOND", "count": 625})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"state": "SECOND", "count": 625},
            )
            self.assertEqual(list(path.parent.glob(".*.tmp-*")), [])

    def test_validate_success_requires_manifest_and_625_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build_manifest.json").write_text(
                json.dumps(
                    {
                        "canonical_record_count": 625,
                        "build_manifest_sha256": "frozen-build",
                    }
                ),
                encoding="utf-8",
            )
            (root / "canonical_full_reviews.jsonl").write_bytes(
                b"{}\n" * 625
            )
            self.assertEqual(
                MODULE._validate_success(root),
                {
                    "build_manifest_sha256": "frozen-build",
                    "canonical_record_count": 625,
                },
            )

    def test_validate_success_rejects_incomplete_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build_manifest.json").write_text(
                json.dumps({"canonical_record_count": 625}), encoding="utf-8"
            )
            (root / "canonical_full_reviews.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "line count"):
                MODULE._validate_success(root)


if __name__ == "__main__":
    unittest.main()
