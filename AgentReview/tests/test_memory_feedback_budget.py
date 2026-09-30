"""Regression at the build -> sandbox retrieval -> actual review-input seam."""
import json

import pytest

from test_research_v2_shared_memory_contract import SharedMemoryContractTest, MemoryCompletion
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.memory import build_memory_snapshot, seed_program_path, retrieve_memory


class CorrectedCompletion(MemoryCompletion):
    def complete(self, messages, **kwargs):
        text = "\n".join(m["content"] for m in messages)
        if "Frozen hit set:" in text:
            assert "CORRECTION_SENTINEL" in text
            self.calls.append({"messages": messages, **kwargs})
            return "A" * 1501 + " CORRECTION_SENTINEL: check the stated learning rate before claiming it is missing."
        if "The KnowledgeItem must conform" in text:
            assert "CORRECTION_SENTINEL" in text
            result = json.loads(super().complete(messages, **kwargs))
            result["failure_mode"] = "CORRECTION_SENTINEL: the learning rate is explicitly stated."
            return json.dumps(result)
        return super().complete(messages, **kwargs)


class FeedbackIntoReviewTest(SharedMemoryContractTest):
    # Inherit the isolation and fixed-policy checks at the same real host seam.
    def test_build_correction_survives_write_retrieval_and_review_input(self):
        for seed in ("vector_search", "llm_summarizer", "experience_learner"):
            with self.subTest(seed=seed):
                source = self.review_source("QUAL")
                feedback = self.root / "feedback.json"
                atomic_write_json(feedback, {
                    "review_id": "source-QUAL", "evaluation_id": "build-feedback", "role": "QUAL",
                    "review_quality": 2, "factual_evidence_issues": ["CORRECTION_SENTINEL: learning rate is stated."],
                })
                client = CorrectedCompletion()
                snapshot = self.root / f"{seed}.json"
                build_memory_snapshot(role="QUAL", program_path=seed_program_path(seed),
                    program_version="corrected-build", review_paths=[source], evaluation_paths=[feedback],
                    output_path=snapshot, seed_kind=seed, embedding_identity=self.embedding,
                    completion_client=client, completion_identity={"model": "fixture"},
                    memory_runtime=self.runtime, require_bubblewrap=False)
                prepared = self.prepare(seed, snapshot, client)
                self.assertIn("CORRECTION_SENTINEL", prepared.retrieval_provenance["memory"]["context"])
                self.assertIn("CORRECTION_SENTINEL", prepared.messages[-1]["content"])
                self.assertEqual(read_json(snapshot)["source_evaluation_ids"], ["build-feedback"])
                if seed != "experience_learner":
                    self.assertIn("CORRECTION_SENTINEL", read_json(snapshot)["items"][0]["payload"]["content"])

    def test_feedback_change_invalidates_snapshot_and_item_cache(self):
        source = self.review_source("QUAL")
        feedback = self.root / "changing-feedback.json"
        snapshot = self.root / "changing-snapshot.json"
        args = dict(role="QUAL", program_path=seed_program_path("vector_search"), program_version="same-code",
            review_paths=[source], evaluation_paths=[feedback], output_path=snapshot, seed_kind="vector_search",
            embedding_identity=self.embedding, require_bubblewrap=False, memory_runtime=self.runtime)
        def write_correction(text):
            atomic_write_json(feedback, {"review_id": "source-QUAL", "evaluation_id": "same-id", "role": "QUAL",
                "factual_evidence_issues": [text]}, immutable=False)
        write_correction("FIRST_CORRECTION")
        build_memory_snapshot(**args)
        write_correction("SECOND_CORRECTION")
        with self.assertRaisesRegex(ValueError, "build identity differs"):
            build_memory_snapshot(**args)
        snapshot.unlink()  # A partial resume must also reject the stale item checkpoint.
        with self.assertRaisesRegex(ValueError, "identity"):
            build_memory_snapshot(**args)
        args["output_path"] = self.root / "rebuilt.json"
        build_memory_snapshot(**args)
        prepared = self.prepare("vector_search", args["output_path"], MemoryCompletion())
        self.assertIn("SECOND_CORRECTION", prepared.messages[-1]["content"])
        self.assertNotIn("FIRST_CORRECTION", prepared.messages[-1]["content"])

    def test_legacy_host_contract_rejected_even_with_same_program_bytes(self):
        snapshot = self.snapshot("vector_search", "QUAL")
        old = read_json(snapshot)
        del old["build_identity"]["memory_contract"]
        old["snapshot_id"] = value_sha256({k: v for k, v in old.items() if k != "snapshot_id"})
        atomic_write_json(snapshot, old, immutable=False)
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.prepare("vector_search", snapshot, MemoryCompletion())

    def test_memory_exceeding_context_cannot_reach_review_input(self):
        self.runtime["context_max_tokens"] = 4096
        snapshot = self.snapshot("llm_summarizer", "QUAL", client=MemoryCompletion())
        class HugeSummary(MemoryCompletion):
            def complete(self, messages, **kwargs):
                if "Frozen hit set:" in messages[-1]["content"]:
                    return "indivisible evidence. " * 5000
                return super().complete(messages, **kwargs)
        with self.assertRaisesRegex(ValueError, "token budget"):
            self.prepare("llm_summarizer", snapshot, HugeSummary())

    def test_rotating_or_test_feedback_cannot_become_build_experience(self):
        source = self.review_source("QUAL")
        review = read_json(source)
        for partition in ("rotating", "sealed_test", "test"):
            atomic_write_json(source, {**review, "partition": partition}, immutable=False)
            with self.assertRaisesRegex(ValueError, "cannot ingest"):
                self.snapshot("vector_search", "QUAL", review_paths=[source])


def test_token_budget_selects_whole_entries_and_records_omissions():
    from agentreview.reviewer_runtime.toolkit import MemoryToolkit
    toolkit = MemoryToolkit(memory_token_counter=lambda text: len(text.split()), memory_context_tokens=6)
    entries = ["too long " * 10 + "CORRECTION_TAIL", "complete corrected claim", "next whole entry"]
    selected = toolkit.select_memory_entries(entries)
    assert selected == entries[1:]
    assert [row["selected"] for row in toolkit.memory_budget_trace[0]["entries"]] == [False, True, True]
    assert toolkit.count_tokens("\n\n".join(selected)) == 6


def test_qwen_embedding_never_encodes_an_over_budget_prefix(tmp_path):
    from memory_evolution.embedding_adapter import Qwen3Embedding, QWEN3_MODEL_ID
    embedding = Qwen3Embedding(QWEN3_MODEL_ID, "a" * 40, cache_path=str(tmp_path / "cache.sqlite"), max_length=4)
    class Tokenizer:
        def __call__(self, batch, **kwargs):
            assert kwargs["truncation"] is False
            return {"input_ids": [list(range(len(text.split()))) for text in batch]}
    class Model:
        def encode(self, *args, **kwargs):
            raise AssertionError("Over-budget material reached encoding")
    embedding._tokenizer = Tokenizer()
    embedding._model = Model()
    with pytest.raises(ValueError, match="never truncate"):
        embedding._embed_uncached_batch(["claim and complete correction at end"])
    assert embedding.embedding_spec["input_contract"] == "complete-input-no-truncation-v2"


def test_whole_reflection_case_selection_preserves_correction_and_scores():
    from agentreview.research_v2.reflection_budget import pack_reflection_feedback
    roles = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
    packet = {"parent": {}, "history": {"0001": {"change": "COMPLETE_HISTORY_TAIL"}}, "current_rotating_feedback": [
        {"paper_id": "oversized", "metrics": {"Paper.": 1.0}, "final_review": "x" * 10000},
        {"paper_id": "fits", "metrics": {"Paper.": 2.0}, "final_review": "WHOLE_FINAL_TAIL"}],
        "trajectories": {role: [{"paper_id": pid, "role": role, "review_text": "ROLE_TAIL"} for pid in ("oversized", "fits")] for role in roles}}
    original = json.dumps(packet)
    count = lambda messages: {"input_tokens": len(messages[0]["content"]), "max_model_len": 3000}
    prompt, selected, trace = pack_reflection_feedback(packet, prefix="PARENT_COMPLETE\n", count_chat_tokens=count,
        input_limit=2000, output_budget=1000)
    assert json.dumps(packet) == original
    assert [row["paper_id"] for row in selected["current_rotating_feedback"]] == ["fits"]
    assert "WHOLE_FINAL_TAIL" in prompt and prompt.count("ROLE_TAIL") == 5
    assert selected["history"] == packet["history"] and "COMPLETE_HISTORY_TAIL" in prompt
    assert [row["selected"] for row in trace["cases"]] == [False, True]
    with pytest.raises(ValueError, match="No complete"):
        pack_reflection_feedback(packet, prefix="PARENT_COMPLETE\n", count_chat_tokens=count, input_limit=250, output_budget=1000)
