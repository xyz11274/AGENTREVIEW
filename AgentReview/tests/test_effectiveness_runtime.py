import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from effectiveness_screening.run_matrix import (
    historical_library_command,
    matrix_commands,
)
from run_single_paper_review_cli import discover_markdown_papers


class EffectivenessRuntimeTest(unittest.TestCase):
    def test_manifest_discovery_verifies_bundle_and_markdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paper_dir = root / "paper-1"
            paper_dir.mkdir()
            markdown = paper_dir / "paper.md"
            markdown.write_text("# Frozen paper\n", encoding="utf-8")
            markdown_sha = hashlib.sha256(markdown.read_bytes()).hexdigest()
            bundle = {
                "paper_id": "paper-1",
                "paper_markdown_relative_path": "paper.md",
                "paper_markdown_sha256": markdown_sha,
                "contains_human_review_data": False,
            }
            bundle_path = paper_dir / "bundle.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            manifest = {
                "contains_human_review_data": False,
                "items": [
                    {
                        "paper_id": "paper-1",
                        "split": "effectiveness_validation",
                        "bundle_path": str(bundle_path),
                        "bundle_sha256": hashlib.sha256(
                            bundle_path.read_bytes()
                        ).hexdigest(),
                        "paper_markdown_sha256": markdown_sha,
                    }
                ],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            args = Namespace(
                paper_md_path=None,
                paper_md_dir=None,
                paper_md_glob="*/md_content_no_appendix.md",
                paper_context_manifest=str(manifest_path),
                paper_context_split="effectiveness_validation",
                frozen_context_bundle_path=None,
                batch_limit=None,
            )
            papers = discover_markdown_papers(args)
            self.assertEqual(papers[0]["sample_id"], "paper-1")
            self.assertEqual(papers[0]["frozen_context_bundle_path"], str(bundle_path))

            markdown.write_text("# Tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                discover_markdown_papers(args)

    def test_matrix_enforces_fresh_replay_and_no_writeback(self):
        config = {
            "output_root": "/tmp/effectiveness",
            "frozen_s2_pool": "/tmp/frozen_s2_temporal_v2.jsonl",
            "frozen_s2_pool_version": "frozen-s2-temporal-v2",
            "memory_embedding": {
                "mode": "frozen",
                "adapter": "memory_evolution.embedding_adapter:create_qwen3_embedding",
                "model_name": "Qwen/Qwen3-Embedding-8B",
                "revision": "revision-1",
                "fingerprint": "fingerprint-1",
            },
            "generation": {
                "model": "gpt-5-mini",
                "temperature": 0.3,
                "review_max_tokens": 8192,
                "review_retry_max_tokens": 16384,
                "reviewer_reasoning_effort": "low",
                "reviewer_paper_max_chars": 35000,
                "meta_review_max_tokens": 4096,
                "meta_paper_max_chars": 30000,
            },
            "experiment": {
                "arms": ["no_memory", "seed", "evolved"],
                "lineages": [
                    "vector_search",
                    "llm_summarizer",
                    "experience_learner",
                ],
                "replicates": [1, 2],
                "replicate_run_seeds": {"1": 101, "2": 202},
            },
        }
        commands = matrix_commands(config)
        self.assertEqual(len(commands), 14)
        expected_arm_ids = {
            "no_memory",
            "seed_vector_search",
            "seed_llm_summarizer",
            "seed_experience_learner",
            "evolved_from_vector_search",
            "evolved_from_llm_summarizer",
            "evolved_from_experience_learner",
        }
        self.assertEqual({cell["arm_id"] for cell in commands}, expected_arm_ids)
        for cell in commands:
            command = cell["command"]
            self.assertIn("--disable_memory_writeback", command)
            self.assertIn(cell["arm_kind"], {"no_memory", "seed", "evolved"})
            if cell["arm_id"] == "no_memory":
                self.assertNotIn("--memory_knowledge_cache_dir", command)
            else:
                self.assertNotIn("--memory_knowledge_cache_dir", command)
                self.assertIn("--memory_source_output_dir", command)
                self.assertIn(f"/programs/{cell['arm_id']}", " ".join(command))
                self.assertIn("--memory_embedding_adapter", command)
                self.assertIn("--memory_embedding_revision", command)
                self.assertIn("revision-1", command)
                self.assertIn("frozen", command)
            self.assertIn("/tmp/frozen_s2_temporal_v2.jsonl", command)
            self.assertEqual(cell["execution_seam"], "external_process_runner")
        history = historical_library_command(config)
        self.assertNotIn("--disable_memory_writeback", history)
        self.assertIn("none", history)


if __name__ == "__main__":
    unittest.main()
