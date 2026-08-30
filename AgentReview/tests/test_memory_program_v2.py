import unittest
from pathlib import Path

from agentreview.review_corpus import FrozenTokenizer, materialize_corpus
from agentreview.review_memory import ReviewMemoryBudget, ReviewMemoryIndex, ReviewMemoryStore
from agentreview.reviewer_runtime.memory_program_v2 import (
    CoreV1ReviewerSession,
    MemoryReadMode,
    materialize_reviewer_memory_context,
)
from agentreview.reviewer_runtime.models import ReviewRequest
from agentreview.reviewer_runtime.sandbox import SandboxSession
from memory_evolution.quality import check_program_path
from tests.test_review_corpus import full_review
from tests.test_review_memory import embed_texts


ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "memory_evolution" / "seeds"


class FakeToolkit:
    llm_model = "fake-qwen"

    def __init__(self):
        self.calls = []

    def llm_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if kwargs.get("agent_name") == "Post-Retrieval Memory Summarizer":
            return "canonical summary"
        return "canonical reviewer output"


class MemoryProgramV2Test(unittest.TestCase):
    def setUp(self):
        corpus = materialize_corpus(
            [full_review("QUAL")],
            tokenizer=FrozenTokenizer(list, "char-tokenizer-v1"),
            max_projection_tokens=10_000,
        )
        manifest_sha = corpus.manifest["corpus_manifest_sha256"]
        index = ReviewMemoryIndex.build(
            role="QUAL",
            fragments=corpus.fragments,
            embed=embed_texts,
            embedding_identity="fake-embedding-v1",
            source_manifest_sha256=manifest_sha,
        )
        self.store = ReviewMemoryStore(
            corpus,
            {"QUAL": index},
            embed_query=lambda query: embed_texts([query])[0],
            token_count=len,
        )
        self.budget = ReviewMemoryBudget(10, 20, 5, 1_000_000, 10, 10_000)

    def test_v2_seeds_compile_and_return_capability_free_plans(self):
        for name in ("vector_search_v2.py", "llm_summarizer_v2.py", "experience_learner_v2.py"):
            path = SEEDS / name
            result = check_program_path(
                str(path),
                import_timeout=5,
                write_timeout=5,
                read_timeout=5,
                sandbox_memory_mb=256,
            )
            self.assertTrue(result.passed, result.errors)
            with SandboxSession(
                str(path), FakeToolkit(), require_bubblewrap=False, timeout_seconds=5
            ) as sandbox:
                plan = sandbox.core_retrieval_plan("paper body", "QUAL")
                self.assertIsInstance(plan["actions"], list)

    def _review(self, seed, *, max_summarizer_query_tokens_total=1_000_000):
        toolkit = FakeToolkit()
        with CoreV1ReviewerSession(
            task_id="QUAL",
            memory_program=str(SEEDS / seed),
            toolkit=toolkit,
            memory_store=self.store,
            memory_budget=self.budget,
            candidate_id="candidate-1",
            max_reviewer_memory_context_tokens=1_000_000,
            max_summarizer_input_tokens=1_000_000,
            max_summarizer_query_tokens_total=max_summarizer_query_tokens_total,
            token_count=len,
            require_bubblewrap=False,
            timeout_seconds=5,
        ) as session:
            result = session.review(
                ReviewRequest(
                    paper_id="target-paper",
                    task_id="QUAL",
                    question="A paper about method evidence.",
                    run_identity="smoke",
                )
            )
        return toolkit, result

    def test_direct_retrieval_has_no_query_or_retrieval_llm(self):
        toolkit, result = self._review("vector_search_v2.py")
        self.assertEqual(len(toolkit.calls), 1)
        self.assertEqual(toolkit.calls[0][1]["agent_name"], "Memory Response")
        self.assertEqual(result.review_text, "canonical reviewer output")
        self.assertIn("PROJECTION_FRAGMENT", result.memory_context)
        self.assertIn('"source_paper_id":"paper-1"', result.memory_context)
        self.assertIn('"role":"QUAL"', result.memory_context)
        self.assertNotIn("fullrev_", result.memory_context)
        self.assertNotIn("candidate-1", result.memory_context)

    def test_summary_mode_has_exactly_one_explicit_post_retrieval_llm(self):
        toolkit, result = self._review("llm_summarizer_v2.py")
        self.assertEqual(len(toolkit.calls), 2)
        self.assertEqual(
            [call[1]["agent_name"] for call in toolkit.calls],
            ["Post-Retrieval Memory Summarizer", "Memory Response"],
        )
        self.assertIn("MEMORY_SOURCE_PROVENANCE", result.memory_context)
        self.assertIn("MEMORY_SUMMARY", result.memory_context)
        self.assertIn("canonical summary", result.memory_context)
        self.assertNotIn("fullrev_", result.memory_context)
        self.assertNotIn("candidate-1", result.memory_context)
        summary_input = toolkit.calls[0][0][1]["content"]
        self.assertIn("RETRIEVAL_QUERY", summary_input)
        self.assertIn("MEMORY_SOURCE", summary_input)

    def test_summary_query_total_budget_has_no_truncation_or_fallback(self):
        with self.assertRaisesRegex(ValueError, "SUMMARY_INPUT_BUDGET_EXHAUSTED"):
            self._review(
                "llm_summarizer_v2.py", max_summarizer_query_tokens_total=1
            )

    def test_structured_full_record_projection_preserves_each_section_once(self):
        record = self.store.materialization.records[0]
        session = self.store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget,
        )
        session.get_full_review_record(record.full_review_record_id)
        context = materialize_reviewer_memory_context(
            session=session,
            mode=MemoryReadMode.STRUCTURED_EXPERIENCE_RECALL,
            token_count=len,
            max_reviewer_memory_context_tokens=1_000_000,
        )
        for section in record.subdimension_sections:
            self.assertEqual(context.context_text.count(section.review_text), 1)
        self.assertEqual(context.context_text.count(record.overall_assessment), 1)


if __name__ == "__main__":
    unittest.main()
