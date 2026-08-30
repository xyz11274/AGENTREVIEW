import unittest
import tempfile
from pathlib import Path

from agentreview.review_corpus import FrozenTokenizer, ROLE_SUBDIMENSIONS, materialize_corpus
from agentreview.review_memory import (
    ReviewMemoryBudget,
    ReviewMemoryIndex,
    ReviewMemoryStore,
    canonical_normalized_float32,
)
from tests.test_review_corpus import full_review


def embed_texts(texts):
    output = []
    for text in texts:
        upper = text.upper()
        if "METHOD" in upper or "MET" in upper:
            output.append([2.0, 0.0, 0.5])
        elif "EXPERIMENT" in upper or "EXP" in upper:
            output.append([0.0, 2.0, 0.5])
        else:
            output.append([0.0, 0.0, 2.0])
    return output


class ReviewMemoryTest(unittest.TestCase):
    def setUp(self):
        self.corpus = materialize_corpus(
            [full_review("QUAL"), full_review("CLAR")],
            tokenizer=FrozenTokenizer(list, "char-tokenizer-v1"),
            max_projection_tokens=10_000,
        )
        manifest_sha = self.corpus.manifest["corpus_manifest_sha256"]
        self.indices = {
            role: ReviewMemoryIndex.build(
                role=role,
                fragments=self.corpus.fragments,
                embed=embed_texts,
                embedding_identity="fake-embedding-v1",
                source_manifest_sha256=manifest_sha,
            )
            for role in ("QUAL", "CLAR")
        }
        self.store = ReviewMemoryStore(
            self.corpus,
            self.indices,
            embed_query=lambda query: embed_texts([query])[0],
            token_count=len,
        )

    def budget(self, **updates):
        values = {
            "max_api_calls": 10,
            "max_delivered_fragments": 10,
            "max_full_record_hydrations": 2,
            "max_delivered_tokens": 100_000,
            "max_top_k": 5,
            "max_memory_query_tokens_per_call": 1_000,
        }
        values.update(updates)
        return ReviewMemoryBudget(**values)

    def test_float32_vector_bytes_are_stable(self):
        left, left_bytes = canonical_normalized_float32([1.0, 2.0, 3.0])
        right, right_bytes = canonical_normalized_float32([1.0, 2.0, 3.0])
        self.assertEqual(left, right)
        self.assertEqual(left_bytes, right_bytes)

    def test_index_round_trip_preserves_vector_bytes_and_identity(self):
        index = self.indices["QUAL"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qual.index.json"
            index.write(path)
            restored = ReviewMemoryIndex.load(path)
        self.assertEqual(restored.index_identity, index.index_identity)
        self.assertEqual(
            [record.vector_bytes for record in restored.records],
            [record.vector_bytes for record in index.records],
        )

    def test_exact_flat_search_is_role_scoped_and_audited(self):
        session = self.store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget(),
        )
        result = session.vector_search(
            "method evidence", subdimension_filter="QUAL-MET", top_k=2
        )
        self.assertEqual(result.disposition, "SUCCESS")
        self.assertEqual(len(result.delivered), 1)
        self.assertEqual(result.delivered[0].subdimension_id, "QUAL-MET")
        self.assertEqual(result.delivered[0].source_content_kind, "PROJECTION_FRAGMENT")
        self.assertTrue(result.delivered[0].similarity_hex.startswith("0x"))
        self.assertEqual(len(session.ledger), 1)
        self.assertEqual(len(session.ledger_sha256), 64)

    def test_budget_exhaustion_is_atomic_and_charges_only_call(self):
        session = self.store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget(max_delivered_fragments=0),
        )
        result = session.vector_search("method", top_k=1)
        self.assertEqual(result.disposition, "REVIEW_MEMORY_BUDGET_EXHAUSTED")
        self.assertEqual(result.delivered, ())
        self.assertEqual(result.budget_state.api_calls, 1)
        self.assertEqual(result.budget_state.delivered_fragments, 0)
        self.assertEqual(session.ledger, ())

    def test_overlong_query_is_candidate_invalid_before_embedding(self):
        calls = []
        store = ReviewMemoryStore(
            self.corpus,
            self.indices,
            embed_query=lambda query: calls.append(query) or embed_texts([query])[0],
            token_count=len,
            query_token_count=len,
        )
        session = store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget(max_memory_query_tokens_per_call=3),
        )
        with self.assertRaisesRegex(ValueError, "QUERY_TOKEN_BUDGET"):
            session.vector_search("four", top_k=1)
        self.assertEqual(calls, [])
        self.assertEqual(session.state.api_calls, 1)

    def test_full_review_hydration_is_one_occurrence(self):
        record_id = next(
            record.full_review_record_id
            for record in self.corpus.records
            if record.role == "QUAL"
        )
        session = self.store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget(),
        )
        result = session.get_full_review_record(record_id)
        self.assertEqual(result.disposition, "SUCCESS")
        self.assertEqual(len(result.delivered), 1)
        self.assertEqual(result.delivered[0].source_content_kind, "FULL_REVIEW_RECORD")
        self.assertEqual(result.budget_state.full_record_hydrations, 1)

    def test_cross_role_ids_and_closed_sessions_are_rejected(self):
        clar_record_id = next(
            record.full_review_record_id
            for record in self.corpus.records
            if record.role == "CLAR"
        )
        session = self.store.open_session(
            candidate_id="candidate-1",
            paper_id="target-paper",
            role="QUAL",
            program_identity="program-1",
            budget=self.budget(),
        )
        with self.assertRaisesRegex(ValueError, "FULL_RECORD_ID"):
            session.get_full_review_record(clar_record_id)
        session.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            session.vector_search("method")


if __name__ == "__main__":
    unittest.main()
