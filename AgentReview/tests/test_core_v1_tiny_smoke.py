import json
import unittest

from agentreview.final_suite import (
    ProtectedOfficialAccess,
    RoleWinner,
    freeze_final_suite,
)
from agentreview.review_corpus import FrozenTokenizer, ROLE_SUBDIMENSIONS, materialize_corpus
from agentreview.review_memory import ReviewMemoryBudget, ReviewMemoryIndex, ReviewMemoryStore
from agentreview.reviewer_runtime.memory_program_v2 import (
    MemoryReadMode,
    materialize_reviewer_memory_context,
)
from memory_evolution.core_search import SEED_ORDER, SharedPoolMStar
from memory_evolution.role_evaluator import canonical_fitness, parse_applicability, parse_scoring
from tests.test_review_corpus import full_review
from tests.test_review_memory import embed_texts


class CoreV1TinySmokeTest(unittest.TestCase):
    def test_corpus_memory_fitness_search_suite_and_protected_boundary(self):
        records = [full_review(role) for role in ROLE_SUBDIMENSIONS]
        corpus = materialize_corpus(
            records,
            tokenizer=FrozenTokenizer(list, "smoke-char-tokenizer-v1"),
            max_projection_tokens=10_000,
        )
        self.assertEqual(len(corpus.records), 5)
        self.assertEqual(len(corpus.projections), 23)

        indices = {}
        for role in ROLE_SUBDIMENSIONS:
            indices[role] = ReviewMemoryIndex.build(
                role=role,
                fragments=corpus.fragments,
                embed=embed_texts,
                embedding_identity="smoke-embedding-v1",
                source_manifest_sha256=corpus.manifest["corpus_manifest_sha256"],
            )
        store = ReviewMemoryStore(
            corpus,
            indices,
            embed_query=lambda query: embed_texts([query])[0],
            token_count=len,
        )
        budget = ReviewMemoryBudget(5, 10, 2, 1_000_000, 5, 10_000)
        contexts = {}
        for mode in MemoryReadMode:
            session = store.open_session(
                candidate_id=f"smoke-{mode.value}",
                paper_id="B-paper-1",
                role="QUAL",
                program_identity=mode.value,
                budget=budget,
            )
            session.vector_search("method evidence", top_k=1)
            contexts[mode] = materialize_reviewer_memory_context(
                session=session,
                mode=mode,
                token_count=len,
                max_reviewer_memory_context_tokens=1_000_000,
                summary_text=(
                    "grounded compressed memory"
                    if mode is MemoryReadMode.POST_RETRIEVAL_LLM_SUMMARY
                    else None
                ),
            )
            session.close()
        self.assertIn("MEMORY_SOURCE", contexts[MemoryReadMode.DIRECT_RETRIEVAL].context_text)
        self.assertIn("MEMORY_SUMMARY", contexts[MemoryReadMode.POST_RETRIEVAL_LLM_SUMMARY].context_text)
        self.assertIn("SUBDIMENSION_GROUP", contexts[MemoryReadMode.STRUCTURED_EXPERIENCE_RECALL].context_text)

        winners = {}
        for role, subdimensions in ROLE_SUBDIMENSIONS.items():
            applicable = {subdimensions[0]}
            app = parse_applicability(
                json.dumps(
                    {
                        "role": role,
                        "paper_id": "B-paper-1",
                        "decisions": [
                            {
                                "subdimension_id": value,
                                "status": "APPLICABLE" if value in applicable else "N_A",
                                "n_a_reason": None if value in applicable else "INTRINSICALLY_NOT_APPLICABLE",
                                "paper_anchor_ids": ["SRC-001"],
                            }
                            for value in subdimensions
                        ],
                    }
                ),
                role=role,
                paper_id="B-paper-1",
                evaluator_identity="smoke-evaluator-v1",
                protocol_identity="core-v1",
            )
            score = parse_scoring(
                json.dumps(
                    {
                        "role": role,
                        "paper_id": "B-paper-1",
                        "scores": [
                            {
                                "subdimension_id": value,
                                "score": 3 if value in applicable else None,
                                "events": [],
                                "evidence_limitations": [],
                            }
                            for value in subdimensions
                        ],
                    }
                ),
                applicability=app,
                candidate_id="smoke-candidate",
                candidate_review="paper-grounded specialist review",
                evaluator_identity="smoke-evaluator-v1",
                protocol_identity="core-v1",
            )
            self.assertEqual(canonical_fitness([score]), "3.000000000000")
            fitness = {b"vector": "3.000000000000", b"summary": "2.000000000000", b"structured": "1.000000000000"}
            search = SharedPoolMStar(
                role=role,
                run_seed=0,
                controller_identity="smoke-controller-v1",
                static_evaluate=lambda program, candidate_id: fitness[program],
                rotating_feedback=lambda parent, attempt: {},
                mutate=lambda parent, feedback, attempt: b"unused",
                validate_program=lambda program: None,
                full_pool_evaluate=lambda candidate: fitness[candidate.program_bytes],
                behavior_manifest=lambda program: {"mode": program.decode("utf-8")},
                attempts=0,
            )
            result = search.run(
                dict(zip(SEED_ORDER, (b"vector", b"summary", b"structured")))
            )
            candidate = next(
                item for item in result.eligible_pool if item.candidate_id == result.winner.candidate_id
            )
            winners[role] = RoleWinner(
                role=role,
                role_run_identity=f"smoke-run-{role}",
                candidate_id=candidate.candidate_id,
                program_identity_sha256=candidate.program_identity_sha256,
                full_pool_fitness=result.winner.full_pool_fitness,
            )

        suite = freeze_final_suite(winners)
        access = ProtectedOfficialAccess(lambda: {"C": "untouched", "human": "sealed"})
        with self.assertRaises(PermissionError):
            access.read_protected_inputs(suite)
        access.authorize(suite)
        self.assertEqual(access.read_protected_inputs(suite)["C"], "untouched")
        self.assertTrue(access.sealed)


if __name__ == "__main__":
    unittest.main()
