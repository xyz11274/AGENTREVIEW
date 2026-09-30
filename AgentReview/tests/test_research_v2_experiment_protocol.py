import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.experiment_protocol import (
    ExperimentProtocolError,
    load_experiment_protocol,
    normalized_content_sha256,
    pending_experiment_template,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json, write_derived_jsonl
from agentreview.research_v2.models import Paper


class ExperimentProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "experiment.json"
        self.manifest_paths = {}
        self.paper_paths = {}
        self.config = pending_experiment_template()
        self.config.update({
            "status": "frozen", "experiment_id": "fixture-shared-experiment",
            "model": {**ModelConfig().protocol_config(), "api_key_env": "OPENAI_API_KEY"},
            "generation": {
                "review_max_tokens": 300, "meta_max_tokens": 500,
                "memory_max_tokens": 200, "native_max_tokens": 600,
                "input_max_tokens": 4000, "paper_context_max_chars": None,
                "memory_build_llm_calls": 4, "memory_read_llm_calls": 2,
                "memory_query_mode": "instruction",
                "native_num_fewshot": 0,
            },
            "evolution": {
                "attempts": 0, "rotating_sample_size": 1,
                "parent_temperature": 0.25, "random_seed": 2,
                "max_reflection_tokens": 500, "mutable_scope": "memory",
                "reflection_scope": "scores_and_traces",
            },
            "failure_policy": {"candidate_repairs": 1, "round_repairs": 2, "candidate_failures": 2, "request_attempts": 3},
            "embedding": {"provider": "fixture", "model": "fixture", "revision": "fixed", "cache_path": "cache.sqlite", "device": "cpu"},
        })
        policy = self.root / "policy.py"
        policy.write_text("# frozen task policy\n", encoding="utf-8")
        self.config["non_memory_conditions"] = {
            "final_mode": "meta", "s2_mode": "off", "frozen_s2": {},
            "review_policy_program": self.ref(policy),
        }
        scoring = self.root / "scoring.json"
        atomic_write_json(scoring, {"protocol": "frozen-coco-scoring-fixture"})
        self.config["scoring_config"] = self.ref(scoring)
        for split in ("build", "static", "rotating", "sealed_test"):
            paper = Paper(f"{split}-paper", f"{split} Study", "abstract", f"Complete {split} paper text.", {"source_partition": split})
            paper_path = self.root / split / "paper.json"
            self.paper_paths[split] = paper_path
            atomic_write_json(paper_path, paper.to_dict())
            assets = {"paper": self.ref(paper_path)}
            if split != "build":
                reference = self.root / split / "reference.json"
                atomic_write_json(reference, {"paper_ids": [paper.paper_id], "partition": split})
                assets["coco_reference"] = self.ref(reference)
            row = self.identity(paper.paper_id, paper.title, paper.text)
            row.update({"source_partition": split, "assets": assets})
            manifest_path = self.root / split / "manifest.json"
            atomic_write_json(manifest_path, {"protocol": "shared-memory-split-v1", "split": split, "records": [row]})
            self.manifest_paths[split] = manifest_path
            self.config["splits"][split] = self.ref(manifest_path)
        history = self.root / "historical-exposure.json"
        atomic_write_json(history, {"records": [self.identity("historical-paper", "Historical Study", "Previously exposed body.")]})
        self.config["historical_exposure_manifests"] = [self.ref(history)]
        self.rewrite_config()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def ref(path):
        return {"path": str(path), "sha256": file_sha256(path)}

    @staticmethod
    def identity(paper_id, title, text):
        return {
            "paper_id": paper_id, "title": title,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "normalized_content_sha256": normalized_content_sha256(text),
            "work_family": f"family-{paper_id}", "version_ids": [paper_id],
        }

    def rewrite_config(self):
        audit_path = self.root / "family-audit.json"
        for split, path in self.manifest_paths.items():
            self.config["splits"][split] = self.ref(path)
        atomic_write_json(audit_path, {
            "status": "complete", "method": "Fixture research-family identity audit",
            "split_manifest_sha256": {name: item["sha256"] for name, item in self.config["splits"].items()},
        }, immutable=False)
        self.config["deduplication"]["family_audit"] = self.ref(audit_path)
        atomic_write_json(self.config_path, self.config, immutable=False)

    def mutate_row(self, split, change):
        path = self.manifest_paths[split]
        manifest = read_json(path)
        change(manifest["records"][0])
        atomic_write_json(path, manifest, immutable=False)
        self.rewrite_config()

    def test_development_load_reads_only_sealed_identity_metadata(self):
        # Guard file hashing as well as JSON parsing: both expose sealed assets.
        real_hash = file_sha256
        real_read = read_json
        sealed_paper = str(self.paper_paths["sealed_test"])
        sealed_reference = str(self.root / "sealed_test" / "reference.json")
        def guarded_hash(path):
            self.assertNotIn(str(path), {sealed_paper, sealed_reference})
            return real_hash(path)
        def guarded_read(path):
            self.assertNotIn(str(path), {sealed_paper, sealed_reference})
            return real_read(path)
        with patch("agentreview.research_v2.experiment_protocol.file_sha256", guarded_hash), patch("agentreview.research_v2.experiment_protocol.read_json", guarded_read):
            protocol = load_experiment_protocol(self.config_path)
        self.assertEqual(protocol.paper_paths("static", purpose="selection"), [self.paper_paths["static"]])
        with self.assertRaisesRegex(ExperimentProtocolError, "frozen_system_sha256"):
            protocol.paper_paths("sealed_test", purpose="final_test")
        with self.assertRaisesRegex(ExperimentProtocolError, "requires split"):
            protocol.paper_paths("sealed_test", purpose="reflection")
        receipt = protocol.freeze(self.root / "receipt.json")
        self.assertEqual(read_json(receipt)["protocol_sha256"], protocol.protocol_sha256)

    def test_pending_configuration_and_omitted_budgets_fail_explicitly(self):
        atomic_write_json(self.config_path, pending_experiment_template(), immutable=False)
        with self.assertRaisesRegex(ExperimentProtocolError, "status=frozen"):
            load_experiment_protocol(self.config_path)
        self.config["evolution"].pop("attempts")
        self.rewrite_config()
        with self.assertRaisesRegex(ExperimentProtocolError, "evolution.attempts"):
            load_experiment_protocol(self.config_path)

    def test_native_fewshot_requires_a_partition_audited_source_inventory(self):
        # CoCo's example filenames and file digests carry no research-family
        # mapping. Current execution supports its original zero-example mode.
        for count in (1, 2, 3):
            with self.subTest(count=count):
                self.config["generation"]["native_num_fewshot"] = count
                self.rewrite_config()
                with self.assertRaisesRegex(ExperimentProtocolError, "partition-audited few-shot study identity inventory"):
                    load_experiment_protocol(self.config_path)
        self.config["generation"]["native_num_fewshot"] = 0
        self.rewrite_config()
        self.assertEqual(load_experiment_protocol(self.config_path).document["generation"]["native_num_fewshot"], 0)

    def test_research_family_and_version_links_keep_partitions_disjoint(self):
        original = read_json(self.manifest_paths["static"])
        for field, value in (("work_family", "family-build-paper"), ("version_ids", ["static-paper", "build-paper"])):
            with self.subTest(field=field):
                atomic_write_json(self.manifest_paths["static"], original, immutable=False)
                self.mutate_row("static", lambda row: row.update({field: value}))
                with self.assertRaisesRegex(ExperimentProtocolError, "Data identity overlap"):
                    load_experiment_protocol(self.config_path)

    def test_whitespace_and_case_content_duplicates_are_rejected(self):
        text = "  COMPLETE   build paper TEXT.\n"
        path = self.paper_paths["static"]
        paper = read_json(path)
        paper["text"] = text
        atomic_write_json(path, paper, immutable=False)
        self.mutate_row("static", lambda row: row.update({
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "normalized_content_sha256": normalized_content_sha256(text),
            "assets": {**row["assets"], "paper": self.ref(path)},
        }))
        with self.assertRaisesRegex(ExperimentProtocolError, "normalized_content"):
            load_experiment_protocol(self.config_path)

    def test_historical_study_version_is_excluded_from_sealed_test(self):
        self.mutate_row("sealed_test", lambda row: row.update({"version_ids": ["sealed_test-paper", "historical-paper"]}))
        with self.assertRaisesRegex(ExperimentProtocolError, "historical exposure"):
            load_experiment_protocol(self.config_path)

    def test_paper_partition_and_frozen_body_identity_are_verified(self):
        path = self.paper_paths["static"]
        paper = read_json(path)
        paper["source"]["source_partition"] = "sealed_test"
        atomic_write_json(path, paper, immutable=False)
        self.mutate_row("static", lambda row: row["assets"].update({"paper": self.ref(path)}))
        with self.assertRaisesRegex(ExperimentProtocolError, "paper source_partition"):
            load_experiment_protocol(self.config_path)

    def test_mutable_model_or_oversized_generation_budget_is_rejected(self):
        for field, value, message in (
            ("model", "", "nonempty"),
            ("base_url", "https://key@example.com/v1", "without credentials"),
            ("max_model_len", 4200, "capacity"),
        ):
            with self.subTest(field=field):
                original = self.config["model"][field]
                self.config["model"][field] = value
                self.rewrite_config()
                with self.assertRaisesRegex(ExperimentProtocolError, message):
                    load_experiment_protocol(self.config_path)
                self.config["model"][field] = original

    def test_family_audit_binds_exact_manifest_versions(self):
        audit_path = self.root / "family-audit.json"
        audit = read_json(audit_path)
        audit["split_manifest_sha256"]["static"] = "0" * 64
        atomic_write_json(audit_path, audit, immutable=False)
        self.config["deduplication"]["family_audit"] = self.ref(audit_path)
        atomic_write_json(self.config_path, self.config, immutable=False)
        with self.assertRaisesRegex(ExperimentProtocolError, "exact frozen split"):
            load_experiment_protocol(self.config_path)

    def test_final_release_verifies_declared_body_identity(self):
        protocol = load_experiment_protocol(self.config_path)
        protocol.validate_final_test_assets(frozen_system_sha256="f" * 64)
        self.mutate_row("sealed_test", lambda row: row.update({"content_sha256": "0" * 64}))
        protocol = load_experiment_protocol(self.config_path)
        with self.assertRaisesRegex(ExperimentProtocolError, "identity differs from its frozen paper"):
            protocol.validate_final_test_assets(frozen_system_sha256="f" * 64)

    def add_s2_assets(self, *, cross_partition_evidence=False):
        self.config["non_memory_conditions"]["s2_mode"] = "frozen"
        for split in self.manifest_paths:
            path = self.root / split / "s2.jsonl"
            evidence = [{"paperId": "external-paper", "title": "Independent external work", "abstract": "Related work"}]
            if cross_partition_evidence and split == "static":
                evidence.append({"paperId": "another-provider-id", "title": "sealed_test Study", "abstract": "Test abstract"})
            write_derived_jsonl(path, [{
                "paper_id": f"{split}-paper", "source_partition": split,
                "contains_human_review_data": False, "retrieved_papers": evidence,
            }])
            self.config["non_memory_conditions"]["frozen_s2"][split] = self.ref(path)
        self.rewrite_config()

    def test_s2_targets_and_returned_evidence_preserve_split_boundaries(self):
        self.add_s2_assets()
        protocol = load_experiment_protocol(self.config_path)
        protocol.validate_final_test_assets(frozen_system_sha256="f" * 64)
        self.add_s2_assets(cross_partition_evidence=True)
        with self.assertRaisesRegex(ExperimentProtocolError, "evidence overlaps another experiment partition"):
            load_experiment_protocol(self.config_path)

    def test_sealed_s2_is_validated_only_after_final_release(self):
        self.add_s2_assets()
        path = self.root / "sealed_test" / "s2.jsonl"
        write_derived_jsonl(path, [{
            "paper_id": "static-paper", "source_partition": "sealed_test",
            "contains_human_review_data": False, "retrieved_papers": [],
        }])
        self.config["non_memory_conditions"]["frozen_s2"]["sealed_test"] = self.ref(path)
        self.rewrite_config()
        protocol = load_experiment_protocol(self.config_path)
        with self.assertRaisesRegex(ExperimentProtocolError, "exactly its split paper IDs"):
            protocol.validate_final_test_assets(frozen_system_sha256="f" * 64)


if __name__ == "__main__":
    unittest.main()
