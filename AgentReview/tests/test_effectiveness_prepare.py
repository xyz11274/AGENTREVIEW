import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from effectiveness_screening.activation import activation_report
from effectiveness_screening.preflight import check as preflight_check
from effectiveness_screening.prepare import (
    DESIGN_VERSION,
    audit_selected_s2_pool,
    build_bundles,
    decision_stratum,
    has_author_reply,
    load_config,
    reviewer_overall_scores,
    text_value,
    write_jsonl,
)
from memory_evolution.assets import FOUR_ZONES


class EffectivenessPrepareTest(unittest.TestCase):
    def test_design_version_is_frozen(self):
        self.assertEqual(DESIGN_VERSION, "effectiveness-screening-v2-temporal")

    def test_v2_config_records_inactive_activation_requirements(self):
        config_path = (
            Path(__file__).resolve().parent.parent
            / "configs"
            / "effectiveness_screening_v2.json"
        )
        config = load_config(config_path)
        self.assertEqual(config["status"], "inactive")
        self.assertEqual(config["arm_schema_version"], "effectiveness-arm-v1")
        self.assertTrue(
            config["temporal_split_manifest"].endswith(
                "configs/coco_temporal_split_v2/manifest.json"
            )
        )
        self.assertTrue(
            config["frozen_s2_pool"].endswith("configs/frozen_s2_temporal_v2.jsonl")
        )
        self.assertEqual(config["frozen_s2_pool_version"], "frozen-s2-temporal-v2")
        self.assertEqual(
            set(config["activation_requirements"]),
            {"evolution_data_root", "evolved_program_root"},
        )
        self.assertEqual(
            config["activation_requirements"]["evolution_data_root"]["current_state"],
            "pending_generation",
        )

        report = activation_report(config, "generation")
        self.assertFalse(report["active"])
        self.assertEqual(report["stage"], "generation")
        self.assertEqual(
            {item["requirement"] for item in report["pending_requirements"]},
            {"evolution_data_root", "evolved_program_root"},
        )

    def test_decision_stratum(self):
        self.assertEqual(
            decision_stratum(
                {"conference": "ICLR 2025", "decision": "Accept (Poster)"}
            ),
            "ICLR-accept",
        )
        self.assertEqual(
            decision_stratum({"conference": "NeurIPS 2024", "decision": "Reject"}),
            "NeurIPS-reject",
        )
        self.assertIsNone(
            decision_stratum({"conference": "ICLR 2025", "decision": "Withdrawal"})
        )

    def test_author_and_score_parsing(self):
        record = {
            "reviews": [
                [
                    ["Reviewer 1", {"scores": {"Overall": 2, "Confidence": 4}}],
                    ["Author", {"value": {"comment": [1]}}],
                ],
                [["Reviewer 2", {"scores": {"Overall": 7}}]],
            ]
        }
        self.assertTrue(has_author_reply(record))
        self.assertEqual(reviewer_overall_scores(record), [2.0, 7.0])
        self.assertEqual(text_value({"value": "A title"}), "A title")

    @patch("effectiveness_screening.prepare.validate_frozen_s2_pool")
    def test_selected_s2_audit_pins_v2_version_queries_and_cutoffs(self, validate):
        validate.return_value = {"passed": True}
        selected = [
            {
                "paper_id": "paper-1",
                "title": "Canonical title",
                "paper_review_cutoff": "2025-01-01",
            }
        ]
        config = {
            "frozen_s2_pool": "/tmp/frozen-v2.jsonl",
            "frozen_s2_pool_version": "frozen-s2-temporal-v2",
        }

        self.assertEqual(audit_selected_s2_pool(config, selected), {"passed": True})
        validate.assert_called_once_with(
            "/tmp/frozen-v2.jsonl",
            expected_cutoffs={"paper-1": "2025-01-01"},
            required_ids={"paper-1"},
            expected_pool_version="frozen-s2-temporal-v2",
            expected_queries={"paper-1": "Canonical title"},
        )

    def test_preflight_reports_structured_activation_and_asset_reasons(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            s2_pool = root / "frozen-v2.jsonl"
            s2_pool.write_text(
                '{"pool_version":"frozen-s2-temporal-v2"}\n', encoding="utf-8"
            )
            s2_manifest = root / "frozen-v2.manifest.json"
            s2_manifest.write_text(
                json.dumps(
                    {
                        "pool_version": "frozen-s2-temporal-v2",
                        "asset_sha256": hashlib.sha256(
                            s2_pool.read_bytes()
                        ).hexdigest(),
                        "query_integrity_audit": {"passed": True},
                        "temporal_audit": {"passed": True},
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "status": "inactive",
                "activation_requirements": {
                    "evolution_data_root": {
                        "active": False,
                        "current_state": "pending_generation",
                        "required_state": "complete",
                        "required_for": ["generation", "evaluation"],
                    }
                },
                "output_root": str(root / "output"),
                "evolved_program_root": str(root / "evolved"),
                "frozen_s2_pool": str(s2_pool),
                "frozen_s2_pool_manifest": str(s2_manifest),
                "frozen_s2_pool_version": "frozen-s2-temporal-v2",
                "external_roots": {"cocoreviewbench": str(root / "coco")},
                "temporal_split_manifest": str(root / "temporal.json"),
                "experiment": {
                    "arms": ["seed"],
                    "lineages": ["vector_search"],
                    "replicates": [1],
                },
            }
            zones = {zone: [] for zone in FOUR_ZONES}
            with (
                patch(
                    "effectiveness_screening.preflight.load_temporal_split_manifest",
                    return_value=({}, zones),
                ),
                patch(
                    "effectiveness_screening.preflight.audit_four_zone_isolation",
                    return_value={"passed": True},
                ),
                patch(
                    "effectiveness_screening.preflight._model_complete",
                    return_value=(False, 0, []),
                ),
                patch(
                    "effectiveness_screening.preflight._endpoint_ready",
                    return_value=False,
                ),
            ):
                report = preflight_check(config)

            self.assertTrue(report["checks"]["frozen_s2_v2_manifest"])
            self.assertFalse(report["ready_for_history_generation"])
            pending = report["pending_activation"]["generation"]
            self.assertEqual(
                {item["requirement"] for item in pending["requirements"]},
                {"evolution_data_root"},
            )
            self.assertIn(
                "paper_context_manifest",
                {item["asset"] for item in pending["assets"]},
            )

    @patch("effectiveness_screening.prepare.validate_frozen_s2_pool")
    def test_bundle_generation_consumes_configured_global_v2_pool(self, validate):
        validate.return_value = {"passed": True, "protocol": "v2"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "output"
            selected = []
            pool = []
            for split, paper_id, zone in (
                ("effectiveness_validation", "paper-c", FOUR_ZONES[2]),
                ("memory_corpus", "paper-a", FOUR_ZONES[0]),
            ):
                markdown = root / paper_id / "paper.md"
                markdown.parent.mkdir(parents=True, exist_ok=True)
                markdown.write_text(
                    f"# {paper_id}\n\nMethod evidence.\n", encoding="utf-8"
                )
                record = {
                    "paper_id": paper_id,
                    "title": paper_id,
                    "work_family": f"family-{paper_id}",
                    "markdown_source": str(markdown),
                    "markdown_sha256": hashlib.sha256(
                        markdown.read_bytes()
                    ).hexdigest(),
                    "conference": "ICLR 2025",
                    "year": 2025,
                    "paper_review_cutoff": "2025-01-01",
                    "temporal_zone": zone,
                }
                selected.append((split, record))
                pool.append(
                    {
                        "paper_id": paper_id,
                        "status": "completed",
                        "pool_version": "frozen-s2-temporal-v2",
                        "retrieved_papers": [],
                    }
                )
            output_root.mkdir(parents=True)
            for split, record in selected:
                write_jsonl(output_root / f"{split}.jsonl", [record])
            pool_path = root / "frozen_s2_temporal_v2.jsonl"
            write_jsonl(pool_path, pool)
            pool_manifest = root / "frozen_s2_temporal_v2.manifest.json"
            pool_manifest.write_text("{}", encoding="utf-8")
            config = {
                "output_root": str(output_root),
                "frozen_s2_pool": str(pool_path),
                "frozen_s2_pool_manifest": str(pool_manifest),
                "frozen_s2_pool_version": "frozen-s2-temporal-v2",
                "generation": {"paper_context_max_chars": 4000},
            }

            manifest = build_bundles(config)

            self.assertEqual(manifest["paper_count"], 2)
            self.assertEqual(
                manifest["frozen_s2_pool_version"], "frozen-s2-temporal-v2"
            )
            self.assertEqual(manifest["frozen_s2_pool"], str(pool_path.resolve()))
            self.assertEqual(
                {item["split"] for item in manifest["items"]},
                {"effectiveness_validation", "memory_corpus"},
            )


if __name__ == "__main__":
    unittest.main()
