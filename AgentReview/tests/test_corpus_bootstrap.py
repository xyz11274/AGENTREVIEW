import json
import tempfile
import unittest
from pathlib import Path

from agentreview.corpus_bootstrap import materialize_corpus_bootstrap


class FakeTokenizer:
    def apply_chat_template(
        self, messages, *, tokenize, add_generation_prompt, enable_thinking
    ):
        if enable_thinking is not False:
            raise AssertionError("Corpus bootstrap must disable thinking explicitly")
        rendered = "\n".join(
            f"<{item['role']}>\n{item['content']}" for item in messages
        ) + "\n<assistant>\n"
        if tokenize:
            return {"input_ids": list(rendered.encode("utf-8"))}
        return rendered


class CorpusBootstrapTest(unittest.TestCase):
    def fixtures(self, root: Path):
        model = root / "model"
        model.mkdir()
        for name in (
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "chat_template.jinja",
            "model.safetensors.index.json",
        ):
            (model / name).write_text(name, encoding="utf-8")
        (model / "model-00001-of-00001.safetensors").write_bytes(b"weights")
        episodes_root = root / "episodes"
        (episodes_root / "QUAL").mkdir(parents=True)
        episodes = []
        ids = []
        for index in range(125):
            paper_id = f"A-{index:03d}"
            ids.append(f"{paper_id}::QUAL")
            episodes.append(
                {
                    "id": f"{paper_id}::QUAL",
                    "text": (
                        "prefix\nContext protocol: frozen\n"
                        "=== CANDIDATE-VISIBLE METADATA ===\n"
                        f"paper_id: {paper_id}\nreviewer_role: QUAL\n"
                        "=== END CANDIDATE-VISIBLE METADATA ===\n"
                        "=== BEGIN PAPER (MD) ===\n# METHOD\n"
                        + ("x" * index)
                        + "\n=== END PAPER (MD) ===\n"
                        "=== SOURCE ANCHORS ===\n"
                        "SRC-001 | page=1 | type=text | text=evidence\n"
                        "=== END SOURCE ANCHORS ==="
                        "\n\nReturn the complete QUAL specialist review"
                    ),
                    "metadata": {
                        "work_family": f"family-{index:03d}",
                        "paper_review_cutoff": "2022-12-31",
                        "temporal_zone": "historical_experience",
                    },
                }
            )
        episode_path = episodes_root / "QUAL" / "episodes.jsonl"
        episode_path.write_text(
            "".join(json.dumps(item) + "\n" for item in episodes),
            encoding="utf-8",
        )
        selection = root / "selection.json"
        selection.write_text(
            json.dumps(
                {
                    "episode_ids": ids,
                    "selection_fingerprint": "selection-v1",
                }
            ),
            encoding="utf-8",
        )
        return model, episodes_root, selection

    def test_materializes_exact_625_requests_and_runtime_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, episodes, selection = self.fixtures(root)
            result = materialize_corpus_bootstrap(
                episodes_root=episodes,
                selection_path=selection,
                model_path=model,
                tokenizer=FakeTokenizer(),
                output_token_cap=8192,
                model_revision="revision",
                served_model_name="qwen-test",
                model_context_limit=262144,
                role_system_prompts={
                    role: f"frozen {role} prompt"
                    for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
                },
            )
            self.assertEqual(len(result.requests), 625)
            self.assertEqual(result.a_manifest["paper_count"], 125)
            self.assertEqual(
                result.request_manifest["role_counts"],
                {"QUAL": 125, "CLAR": 125, "SIGN": 125, "ORIG": 125, "POL": 125},
            )
            profile = result.runtime_profile
            self.assertEqual(profile["profile_name"], "CORPUS_BUILD_RUNTIME_PROFILE")
            self.assertEqual(profile["max_num_seqs"], 1)
            self.assertEqual(profile["tensor_parallel_size"], 4)
            self.assertEqual(profile["corpus_build_max_model_len"] % 8192, 0)
            self.assertGreaterEqual(
                profile["corpus_build_max_model_len"],
                profile["required_context_tokens"],
            )
            self.assertEqual(profile["profile_version"], "v4")
            semantic_implementation = profile["generation_semantics"][
                "corpus_builder_semantic_implementation"
            ]
            self.assertEqual(
                semantic_implementation["semantic_audit_version"],
                "core-v1-corpus-semantic-boundary-audit-v2",
            )
            self.assertEqual(
                profile["generation_semantics"]["frozen_review_request_sha256"],
                "609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42",
            )
            self.assertEqual(
                len(
                    semantic_implementation[
                        "corpus_builder_semantic_implementation_sha256"
                    ]
                ),
                64,
            )
            self.assertEqual(
                set(semantic_implementation["files"]),
                {
                    "review_corpus.py",
                    "review_corpus_builder.py",
                    "corpus_role_prompts_v4.py",
                    "corpus_semantic_audit.py",
                },
            )
            self.assertEqual(result.requests[0].response_format["type"], "json_schema")
            self.assertEqual(len(result.structural_contexts), 125)
            self.assertEqual(
                len({item["structural_context_sha256"] for item in result.structural_contexts}),
                125,
            )
            target = root / "output"
            result.write(target)
            self.assertEqual(
                len((target / "corpus_request_materialization.jsonl").read_text().splitlines()),
                625,
            )
            self.assertEqual(
                len((target / "structural_contexts.jsonl").read_text().splitlines()),
                125,
            )

    def test_rejects_non_a_or_old_memory_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, episodes, selection = self.fixtures(root)
            path = episodes / "QUAL" / "episodes.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[0]["text"] += "\nSELF-GENERATED REVIEW EXPERIENCE"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            with self.assertRaisesRegex(ValueError, "forbidden material"):
                materialize_corpus_bootstrap(
                    episodes_root=episodes,
                    selection_path=selection,
                    model_path=model,
                    tokenizer=FakeTokenizer(),
                    output_token_cap=8192,
                    model_revision="revision",
                    served_model_name="qwen-test",
                    model_context_limit=262144,
                    role_system_prompts={
                        role: f"frozen {role} prompt"
                        for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
                    },
                )


if __name__ == "__main__":
    unittest.main()
