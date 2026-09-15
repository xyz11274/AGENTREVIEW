import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2.development_coco import aggregate_paper_scores
from agentreview.research_v2.experiment_report import (
    INPUT_PROTOCOL,
    PAPER_PROTOCOL,
    TABLE_COLUMNS,
    generate_experiment_report,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256


class ExperimentReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assets = self.root / "inputs"
        self.assets.mkdir()
        self.output = self.root / "report"
        self.manifest = self.assets / "manifest.json"
        self.protocol = {"protocol": "research-v2-standard-coco-v1", "config_sha256": "a" * 64, "standard_source_sha256": "b" * 64}
        self.identity = {"partition": "development", "run_id": "run-1", "evaluation_epoch": 0, "scoring_protocol": self.protocol}

    def binding(self, path):
        return {"path": str(path), "sha256": file_sha256(path)}

    def score(self, paper_id, value=70.0, status="complete", verify=True, **identity):
        complete = status == "complete"
        metrics = {column: value if complete else None for column in TABLE_COLUMNS}
        counts = {column: int(complete) for column in TABLE_COLUMNS}
        applicability = {column: "applicable" if complete else "unknown" for column in TABLE_COLUMNS}
        if complete and verify is False:
            metrics["Verify."] = None
            counts["Verify."] = 0
            applicability["Verify."] = "not_applicable"
        result = {"protocol": PAPER_PROTOCOL, **self.identity, **identity, "candidate_id": "seed-vector", "paper_id": paper_id, "status": status, "metrics": metrics, "metric_counts": counts, "applicability": applicability, "paper_judge_scores": {"average": 99, "verifiability": "X" if verify is False else value}}
        if status == "failed":
            result["failure"] = {"stage": "classify", "category": "shared_evaluation", "message": "classification failed"}
        return result

    def assessment(self, records, *, aggregate=None, suffix="", **identity):
        bindings = []
        for record in records:
            path = self.assets / f"{record['paper_id']}{suffix}.json"
            atomic_write_json(path, record)
            bindings.append({"paper_id": record["paper_id"], **self.binding(path)})
        result = {**self.identity, **identity, "candidate_id": "seed-vector", "papers": bindings, "aggregate": aggregate or aggregate_paper_scores(records)}
        path = self.assets / f"assessment{suffix}.json"
        atomic_write_json(path, result)
        return self.binding(path)

    def scope(self, assessment=None, paper_ids=("p1", "p2", "p3"), **identity):
        return {**self.identity, **identity, "purpose": "fitness", "cohort_id": "cohort-1", "papers": [{"paper_id": paper_id, "title": f"Title {paper_id}"} for paper_id in paper_ids], "variants": [{"variant_id": "seed-vector", "kind": "seed", "seed": "vector_search", "candidate_id": "seed-vector", "assessment": assessment}]}

    def run_report(self, scopes, *, stage="development", output=None):
        atomic_write_json(self.manifest, {"protocol": INPUT_PROTOCOL, "scopes": scopes}, immutable=False)
        result = generate_experiment_report(self.manifest, output or self.output, stage=stage)
        return result, json.loads(Path(result["paths"]["report.json"]).read_text())

    def test_partial_cohort_writes_all_rows_and_preserves_valid_denominators(self):
        records = [self.score("p1", 70, verify=False), self.score("p2", status="failed")]
        original = aggregate_paper_scores(records)
        original["absolute_scores"]["Paper."] = 99
        binding = self.assessment(records, aggregate=original)
        inputs_before = {path: path.read_bytes() for path in self.assets.iterdir()}
        result, report = self.run_report([self.scope(binding)])
        summary = report["variants"][0]
        self.assertEqual((summary["expected_count"], summary["complete_count"], summary["failed_count"], summary["missing_count"]), (3, 1, 1, 1))
        self.assertEqual(summary["aggregate"]["absolute_scores"]["Paper."], 70)
        self.assertEqual(summary["aggregate"]["paper_counts"]["Paper."], 1)
        self.assertEqual(summary["aggregate"]["paper_counts"]["Verify."], 0)
        self.assertEqual(summary["aggregate_role"], "successful_subset_diagnostic")
        self.assertEqual(summary["original_comparison"]["status"], "different")
        with Path(result["paths"]["papers.csv"]).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["paper_id"] for row in rows], ["p1", "p2", "p3"])
        self.assertEqual([row["status"] for row in rows], ["complete", "failed", "missing"])
        self.assertEqual(rows[0]["Verify._applicability"], "not_applicable")
        self.assertEqual(rows[1]["Verify._applicability"], "unknown")
        self.assertEqual(rows[2]["Paper."], "")
        self.assertEqual(report["papers"][0]["paper_judge_scores"]["average"], 99)
        for path, content in inputs_before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_historical_absent_seeds_and_matched_controls_keep_explicit_missing_rows(self):
        scope = self.scope()
        scope["variants"] = []
        for seed in ("vector_search", "llm_summarizer", "experience_learner"):
            scope["variants"].append({"variant_id": seed, "kind": "seed", "seed": seed, "assessment": None})
            scope["variants"].append({"variant_id": f"no-memory-{seed}", "kind": "no_memory", "matched_seed": seed, "assessment": None})
        scope["variants"].append({"variant_id": "native", "kind": "coco_native", "assessment": None})
        _, report = self.run_report([scope])
        self.assertEqual(len(report["papers"]), 21)
        for summary in report["variants"]:
            self.assertEqual(summary["missing_count"], 3)
            self.assertIsNone(summary["aggregate"]["absolute_scores"]["Paper."])
            self.assertEqual(summary["aggregate"]["paper_counts"]["Paper."], 0)

    def test_development_stage_skips_sealed_assets_before_reading_or_hashing(self):
        sealed = self.scope(partition="sealed_test")
        sealed["variants"][0]["assessment"] = {"path": "/sealed/private/assessment.json", "sha256": "0" * 64}
        original_read = Path.read_bytes
        touched = []

        def guarded_read(path):
            touched.append(str(path))
            if str(path).startswith("/sealed/"):
                raise AssertionError("sealed asset was opened")
            return original_read(path)

        with patch.object(Path, "read_bytes", guarded_read):
            _, report = self.run_report([self.scope(), sealed])
        self.assertEqual(report["skipped_scope_count"], 1)
        self.assertEqual(len(report["papers"]), 3)
        self.assertTrue(all(source["path"].startswith(str(self.assets)) for source in report["provenance"]))

    def test_final_stage_groups_partitions_epochs_and_variants(self):
        dev = self.scope(self.assessment([self.score("p1")]), paper_ids=("p1",))
        test_score = self.score("p1", 30, partition="sealed_test", evaluation_epoch=1)
        sealed = self.scope(self.assessment([test_score], suffix="-test", partition="sealed_test", evaluation_epoch=1), paper_ids=("p1",), partition="sealed_test", evaluation_epoch=1)
        sealed["variants"][0].update(kind="champion", variant_id="champion", parent_id="parent-2", round=3, program_sha256="c" * 64)
        _, report = self.run_report([dev, sealed], stage="final")
        self.assertEqual([row["aggregate"]["absolute_scores"]["Paper."] for row in report["variants"]], [70, 30])
        self.assertEqual(report["variants"][1]["parent_id"], "parent-2")
        self.assertTrue(all(row["evaluation_complete"] for row in report["variants"]))

    def test_role_internal_score_remains_diagnostic_and_never_becomes_paper_score(self):
        diagnostic_path = self.assets / "role-eval.json"
        atomic_write_json(diagnostic_path, {"paper_id": "p1", "review_quality": 4.5})
        scope = self.scope()
        scope["variants"][0]["role_diagnostics"] = [{"paper_id": "p1", "role": "QUAL", "scoring_protocol": "historical-internal-v1", "artifact": self.binding(diagnostic_path)}]
        result, report = self.run_report([scope])
        self.assertIsNone(report["variants"][0]["aggregate"]["absolute_scores"]["Paper."])
        with Path(result["paths"]["role_diagnostics.csv"]).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(json.loads(rows[0]["diagnostic"])["review_quality"], 4.5)
        self.assertEqual(rows[0]["score_role"], "diagnostic")

    def test_hash_drift_and_wrong_epoch_fail_before_output(self):
        binding = self.assessment([self.score("p1", evaluation_epoch=1)])
        with self.assertRaisesRegex(ValueError, "evaluation_epoch"):
            self.run_report([self.scope(binding)])
        self.assertFalse(self.output.exists())
        path = Path(binding["path"])
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            self.run_report([self.scope(binding)])
        self.assertFalse(self.output.exists())

    def test_report_replays_identically_and_new_snapshot_requires_separate_directory(self):
        scopes = [self.scope()]
        first, _ = self.run_report(scopes)
        second = generate_experiment_report(self.manifest, self.output)
        self.assertEqual(first, second)
        saved = {path.name: path.read_bytes() for path in self.output.iterdir()}
        scopes[0]["papers"].append({"paper_id": "p4"})
        with self.assertRaises(FileExistsError):
            self.run_report(scopes)
        self.assertEqual(saved, {path.name: path.read_bytes() for path in self.output.iterdir()})
        with self.assertRaisesRegex(ValueError, "dedicated directory"):
            generate_experiment_report(self.manifest, self.assets)

    def test_duplicate_paper_and_cross_cohort_receipt_are_rejected(self):
        scope = self.scope()
        scope["papers"].append({"paper_id": "p1"})
        with self.assertRaisesRegex(ValueError, "repeats a planned"):
            self.run_report([scope])
        binding = self.assessment([self.score("foreign")])
        with self.assertRaisesRegex(ValueError, "planned cohort"):
            self.run_report([self.scope(binding)])

    def test_legacy_internal_score_cannot_be_registered_as_standard_coco(self):
        row = self.score("p1")
        row["protocol"] = "research-v2-internal-evaluation-v1"
        row["review_quality"] = 4.5
        binding = self.assessment([row])
        with self.assertRaisesRegex(ValueError, "standard CoCo paper protocol"):
            self.run_report([self.scope(binding)])

    def test_development_permission_supports_distinct_selection_and_reflection_purposes(self):
        row = self.score("p1", partition="static")
        binding = self.assessment([row], partition="static", purpose="development")
        scope = self.scope(binding, paper_ids=("p1",), partition="static")
        scope["purpose"] = "selection"
        _, report = self.run_report([scope])
        self.assertEqual(report["variants"][0]["purpose"], "selection")
        scope["purpose"] = "final_test"
        with self.assertRaisesRegex(ValueError, "execution purpose"):
            self.run_report([scope], output=self.root / "another-report")

    def test_saved_generation_outcomes_distinguish_unscored_and_failed_papers(self):
        scope = self.scope()
        variant = scope["variants"][0]
        variant.update(status="paused", failure={"category": "shared_runtime"}, paper_outcomes=[
            {"paper_id": "p1", "status": "not_scored", "review": {"path": "saved-review.json", "sha256": "a" * 64}},
            {"paper_id": "p2", "status": "failed", "failure": {"stage": "generation", "message": "token budget"}},
            {"paper_id": "p3", "status": "not_generated"},
        ])
        _, report = self.run_report([scope])
        self.assertEqual([row["status"] for row in report["papers"]], ["pending", "failed", "missing"])
        self.assertEqual([row["generation_status"] for row in report["papers"]], ["complete", "failed", "pending"])
        self.assertEqual(report["papers"][1]["failure"]["stage"], "generation")
        self.assertEqual(report["variants"][0]["recorded_status"], "paused")
        self.assertEqual(report["variants"][0]["aggregate"]["paper_counts"]["Paper."], 0)


if __name__ == "__main__":
    unittest.main()
