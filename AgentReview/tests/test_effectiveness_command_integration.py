import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.arguments import parse_args
from agentreview.reviewer_identity import META_REVIEWER_NAME, REVIEWER_SPECS
from effectiveness_screening.memory_cache import freeze_programs
from effectiveness_screening.prepare import write_jsonl
from effectiveness_screening.run_matrix import (
    historical_replay_commands,
    matrix_commands,
)
from run_single_paper_review_cli import main as run_review_main


class EffectivenessCommandIntegrationTest(unittest.TestCase):
    def test_fake_backend_executes_generated_history_then_holdout_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "output"
            s2_pool = root / "frozen_s2_temporal_v2.jsonl"
            write_jsonl(
                s2_pool,
                [
                    {
                        "paper_id": paper_id,
                        "paper_review_cutoff": "2025-01-01",
                        "pool_version": "frozen-s2-temporal-v2",
                        "status": "completed",
                        "retrieved_papers": [],
                    }
                    for paper_id in ("history-paper", "holdout-paper")
                ],
            )
            manifest_path = self._write_context_manifest(output_root)
            embedding = {
                "mode": "frozen",
                "adapter": "fake_embedding:create",
                "model_name": "fake-embedding",
                "revision": "fake-revision",
                "fingerprint": "fake-fingerprint",
            }
            config = {
                "output_root": str(output_root),
                "evolved_program_root": str(root / "evolved"),
                "frozen_s2_pool": str(s2_pool),
                "frozen_s2_pool_version": "frozen-s2-temporal-v2",
                "memory_embedding": embedding,
                "generation": {
                    "model": "fake-review-model",
                    "temperature": 0.0,
                    "review_max_tokens": 256,
                    "review_retry_max_tokens": 512,
                    "reviewer_reasoning_effort": "low",
                    "paper_context_max_chars": 4000,
                    "meta_review_max_tokens": 256,
                },
                "experiment": {
                    "arms": ["seed"],
                    "lineages": ["vector_search"],
                    "replicates": [1],
                    "replicate_run_seeds": {"1": 101},
                },
            }
            freeze_programs(config)
            history_command = historical_replay_commands(config)[0]["command"]
            holdout_command = matrix_commands(config)[0]["command"]
            self.assertEqual(
                history_command[history_command.index("--paper_context_manifest") + 1],
                str(manifest_path),
            )

            backend = _FakeMemoryBackend()

            def players(args):
                bundle = json.loads(
                    Path(args.frozen_context_bundle_path).read_text(encoding="utf-8")
                )
                extractor = _FakeExtractor(bundle)
                reviewers = [
                    _FakeReviewer(spec["name"], backend) for spec in REVIEWER_SPECS
                ]
                return [
                    extractor,
                    *reviewers,
                    _FakeReviewer(META_REVIEWER_NAME, backend),
                ]

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}),
                patch(
                    "run_single_paper_review_cli.build_single_paper_players",
                    side_effect=players,
                ),
                patch(
                    "run_single_paper_review_cli._memory_embedding_factory",
                    return_value=lambda: _FakeEmbedding(),
                ),
            ):
                run_review_main(self._parse_generated_command(history_command))
                run_review_main(self._parse_generated_command(holdout_command))

            history_root = (
                output_root
                / "runs"
                / "replicate_1"
                / "seed_vector_search"
                / "history"
                / "single_paper_review"
            )
            holdout_root = (
                output_root
                / "runs"
                / "replicate_1"
                / "seed_vector_search"
                / "holdout"
                / "single_paper_review"
            )
            self.assertTrue((history_root / "memory_library.jsonl").is_file())
            self.assertTrue((holdout_root / "holdout-paper" / "reviews.json").is_file())
            self.assertFalse((holdout_root / "memory_library.jsonl").exists())
            self.assertIn("Memory KnowledgeItem", backend.agent_names)
            holdout_memory = json.loads(
                (holdout_root / "holdout-paper" / "memory.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(holdout_memory["memory_mode"], "mstar_program")
            self.assertEqual(
                {
                    Path(value["program_path"]).parent.name
                    for value in holdout_memory["reviewer_memory_contexts"].values()
                },
                {"QUAL", "CLAR", "SIGN", "ORIG", "POL", "META"},
            )

    @staticmethod
    def _parse_generated_command(command):
        with patch.object(
            sys, "argv", ["run_single_paper_review_cli.py", *command[2:]]
        ):
            return parse_args()

    @staticmethod
    def _write_context_manifest(output_root: Path) -> Path:
        items = []
        for split, paper_id in (
            ("memory_corpus", "history-paper"),
            ("effectiveness_validation", "holdout-paper"),
        ):
            paper_root = output_root / "paper_context" / split / paper_id
            paper_root.mkdir(parents=True, exist_ok=True)
            markdown = paper_root / "paper.md"
            markdown.write_text(
                f"# {paper_id}\n\nMethod and evidence.\n", encoding="utf-8"
            )
            markdown_sha = hashlib.sha256(markdown.read_bytes()).hexdigest()
            source_anchors = [
                {
                    "source_id": "SRC-001",
                    "page_idx": 1,
                    "section": "Method",
                    "text": "Method and evidence.",
                }
            ]
            bundle = {
                "paper_id": paper_id,
                "paper_markdown_relative_path": "paper.md",
                "paper_markdown_sha256": markdown_sha,
                "contains_human_review_data": False,
                "extractor_content": (
                    "reviewer_role: SPECIALIST\nMethod and evidence SRC-001(page=1)"
                ),
                "last_memory": {
                    "paper_title": paper_id,
                    "retrieved_papers": [],
                    "references_text": "",
                    "source_anchors": source_anchors,
                    "s2_usage": {
                        "available_as_frozen_capability": True,
                        "used": False,
                        "default_injection": False,
                    },
                },
            }
            bundle_path = paper_root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            items.append(
                {
                    "paper_id": paper_id,
                    "split": split,
                    "bundle_path": str(bundle_path),
                    "bundle_sha256": hashlib.sha256(
                        bundle_path.read_bytes()
                    ).hexdigest(),
                    "paper_markdown_sha256": markdown_sha,
                }
            )
        manifest = {
            "contains_human_review_data": False,
            "items": items,
        }
        manifest_path = output_root / "paper_context_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path


class _FakeEmbedding:
    embedding_fingerprint = "fake-fingerprint"

    def embed(self, texts):
        return [[float(index == 0) for index in range(8)] for _ in texts]


class _FakeMemoryBackend:
    def __init__(self):
        self.agent_names = []

    def llm_completion(self, _messages, **kwargs):
        agent_name = kwargs.get("agent_name")
        self.agent_names.append(agent_name)
        if agent_name == "Memory Query":
            return '{"query_text":"prior review guidance","reviewer_dimension":"QUAL"}'
        if agent_name == "Memory KnowledgeItem":
            return (
                '{"topic":"review guidance","content":"ground claims in evidence",'
                '"reviewer_name":"Reviewer QUAL","reviewer_dimension":"QUAL"}'
            )
        return "Use the retrieved evidence."


class _FakeExtractor:
    name = "Paper Extractor"
    role_desc = "extractor"

    def __init__(self, bundle):
        self.bundle = bundle
        self.last_memory = dict(bundle["last_memory"])

    def __call__(self, _observation):
        return self.bundle["extractor_content"]


class _FakeReviewer:
    role_desc = "reviewer"

    def __init__(self, name, backend):
        self.name = name
        self.backend = backend

    def __call__(self, _observation):
        if self.name == META_REVIEWER_NAME:
            return "Final score (1-10): 8\nEvidence: SRC-001(page=1)"
        return "Dimension score (1-10): 8\nEvidence: SRC-001(page=1)"


if __name__ == "__main__":
    unittest.main()
