import json
import tempfile
import unittest
from pathlib import Path

from evolution_viewer.data_loader import (
    ancestor_root,
    load_run,
    load_trace_diagnostics,
    resolve_run_path,
    scan_runs,
)


PROGRAM = '''
INSTRUCTION_KNOWLEDGE_ITEM = "item"
INSTRUCTION_QUERY = "query"
INSTRUCTION_RESPONSE = "response"
ALWAYS_ON_KNOWLEDGE = "always"

class KnowledgeItem:
    pass

class Query:
    pass

class KnowledgeBase:
    def write(self, item, raw_text):
        return None
    def read(self, query):
        return ""
'''


class DataLoaderTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.runs_root = Path(self.temporary_directory.name) / "runs"
        self.run_dir = self.runs_root / "suite_a" / "QUAL" / "seed_0"
        (self.run_dir / "pool").mkdir(parents=True)
        (self.run_dir / "traces").mkdir()
        (self.run_dir / "config.json").write_text(
            json.dumps({"task_id": "QUAL", "seed": 0, "iterations": 2}),
            encoding="utf-8",
        )
        (self.run_dir / "pool" / "program_000_seed.py").write_text(
            PROGRAM, encoding="utf-8"
        )
        (self.run_dir / "pool" / "program_001_child.py").write_text(
            PROGRAM.replace('"response"', '"improved response"'), encoding="utf-8"
        )
        lineage = [
            {
                "program_id": "program_000_seed",
                "parent_id": None,
                "iteration": 0,
                "change_log": {"summary": "Seed"},
                "quality": {"passed": True},
            },
            {
                "program_id": "program_001_child",
                "parent_id": "program_000_seed",
                "iteration": 1,
                "parent_static_score": 0.4,
                "static_score": 0.6,
                "rotating_score": 0.5,
                "change_log": {"summary": "Change prompt", "details": ["Reason"]},
                "quality": {"passed": True},
            },
            {
                "program_id": None,
                "parent_id": "program_001_child",
                "iteration": 2,
                "admitted": False,
                "error": "Reflector failed",
            },
        ]
        with (self.run_dir / "lineage.jsonl").open("w", encoding="utf-8") as handle:
            for value in lineage:
                handle.write(json.dumps(value) + "\n")
            handle.write("{partial")
        scores = [
            {
                "program_id": "program_000_seed",
                "iteration": 0,
                "static_score": 0.4,
                "quality_passed": True,
            },
            {
                "program_id": "program_001_child",
                "parent_id": "program_000_seed",
                "iteration": 1,
                "static_score": 0.6,
                "rotating_score": 0.5,
                "quality_passed": True,
            },
            {
                "program_id": None,
                "parent_id": "program_001_child",
                "iteration": 2,
                "static_score": None,
                "quality_passed": False,
                "compile_errors": ["Reflector failed"],
            },
        ]
        with (self.run_dir / "scores.jsonl").open("w", encoding="utf-8") as handle:
            for value in scores:
                handle.write(json.dumps(value) + "\n")
        (self.run_dir / "best_eval_report.json").write_text(
            json.dumps({"program_id": "program_001_child"}), encoding="utf-8"
        )
        trace = [
            {
                "phase": "write",
                "episode_id": "paper-1::QUAL",
                "success": False,
                "error": "JSON decode failed",
                "raw_text": "x" * 1000,
            },
            {
                "phase": "read",
                "id": "paper-2::QUAL",
                "success": True,
                "score": 0.5,
            },
        ]
        with (self.run_dir / "traces" / "iter_001_rotating.jsonl").open(
            "w", encoding="utf-8"
        ) as handle:
            for value in trace:
                handle.write(json.dumps(value) + "\n")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_scan_and_load_preserve_failed_iterations(self):
        catalog = scan_runs(str(self.runs_root))

        self.assertEqual(len(catalog["entries"]), 1)
        self.assertEqual(catalog["entries"][0]["suite"], "suite_a")
        self.assertEqual(catalog["entries"][0]["task"], "QUAL")
        self.assertEqual(catalog["entries"][0]["run"], "seed_0")

        run = load_run(str(self.runs_root), "suite_a/QUAL/seed_0")

        self.assertEqual(len(run["transformations"]), 3)
        self.assertEqual(run["transformations"][-1]["status"], "failed")
        self.assertEqual(run["best_program_id"], "program_001_child")
        self.assertTrue(any("lineage.jsonl:4" in value for value in run["warnings"]))
        self.assertEqual(
            ancestor_root(run["programs"], "program_001_child"),
            "program_000_seed",
        )

    def test_trace_is_summarized_and_long_fields_are_truncated(self):
        run = load_run(str(self.runs_root), "suite_a/QUAL/seed_0")
        child = run["programs"]["program_001_child"]

        trace = load_trace_diagnostics(
            str(self.runs_root),
            "suite_a/QUAL/seed_0",
            child,
            preview_chars=40,
        )

        self.assertEqual(trace["counts"], {"write": 1, "read": 1})
        self.assertEqual(len(trace["failures"]), 1)
        self.assertIn("已截断", trace["raw_excerpt"])
        self.assertNotIn("x" * 100, trace["raw_excerpt"])

    def test_run_path_cannot_escape_root(self):
        with self.assertRaises(ValueError):
            resolve_run_path(str(self.runs_root), "../../")


if __name__ == "__main__":
    unittest.main()

