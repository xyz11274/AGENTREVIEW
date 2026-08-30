import unittest

from memory_evolution.core_search import (
    CandidateOriginatedFailure,
    FormalInfrastructureFailure,
    RoleFormalValidityFailure,
    SEED_ORDER,
    SharedPoolMStar,
)


class CoreSearchTest(unittest.TestCase):
    def build(self):
        score_map = {
            b"vector": "3.000000000000",
            b"summary": "0.100000000000",
            b"structured": "0.200000000000",
            b"child-2": "4.000000000000",
            b"child-3": "3.900000000000",
            b"child-4": "3.800000000000",
        }

        def mutate(parent, feedback, attempt):
            if attempt == 1:
                return parent.program_bytes
            return f"child-{attempt}".encode()

        def full(candidate):
            values = {
                b"child-2": "2.000000000000",
                b"child-3": "4.000000000000",
                b"child-4": "3.000000000000",
            }
            return values.get(candidate.program_bytes, "0.000000000000")

        return SharedPoolMStar(
            role="QUAL",
            run_seed=0,
            controller_identity="controller-v1",
            static_evaluate=lambda program, candidate_id: score_map[program],
            rotating_feedback=lambda parent, attempt: {
                "subdimension_scores": [1, 2, 3],
                "failure_codes": [],
            },
            mutate=mutate,
            validate_program=lambda program: None,
            full_pool_evaluate=full,
            behavior_manifest=lambda program: {"mode": "DIRECT_RETRIEVAL"},
            attempts=4,
            tau=0.15,
            finalist_k=3,
        )

    def seeds(self):
        return dict(zip(SEED_ORDER, (b"vector", b"summary", b"structured")))

    def test_shared_pool_attempts_dedup_top3_and_full_pool_winner(self):
        result = self.build().run(self.seeds())
        self.assertEqual(len(result.attempts), 4)
        self.assertEqual(result.attempts[0].disposition, "DUPLICATE_PROGRAM_MUTATION")
        self.assertEqual(len(result.eligible_pool), 6)
        self.assertEqual(
            [item.static_25_fitness for item in result.finalists],
            ["4.000000000000", "3.900000000000", "3.800000000000"],
        )
        self.assertEqual(result.winner.full_pool_fitness, "4.000000000000")
        winner = next(
            item for item in result.eligible_pool if item.candidate_id == result.winner.candidate_id
        )
        self.assertEqual(winner.program_bytes, b"child-3")

    def test_parent_rng_and_candidate_birth_are_reproducible(self):
        first = self.build().run(self.seeds())
        second = self.build().run(self.seeds())
        self.assertEqual(
            [item.candidate_id for item in first.eligible_pool],
            [item.candidate_id for item in second.eligible_pool],
        )
        self.assertEqual(
            [item.parent_candidate_id for item in first.attempts],
            [item.parent_candidate_id for item in second.attempts],
        )
        for left, right in zip(first.attempts, second.attempts):
            self.assertEqual(left.fitness_float_hex, right.fitness_float_hex)
            self.assertEqual(left.normalized_probabilities, right.normalized_probabilities)
            self.assertEqual(left.rng_post_state, right.rng_post_state)

    def test_failed_mutation_consumes_attempt_without_pool_entry(self):
        search = SharedPoolMStar(
            role="POL",
            run_seed=1,
            controller_identity="controller-v1",
            static_evaluate=lambda program, candidate_id: "1.000000000000",
            rotating_feedback=lambda parent, attempt: {},
            mutate=lambda parent, feedback, attempt: (_ for _ in ()).throw(
                CandidateOriginatedFailure("fail")
            ),
            validate_program=lambda program: None,
            full_pool_evaluate=lambda candidate: "1.000000000000",
            behavior_manifest=lambda program: {"mode": "DIRECT_RETRIEVAL"},
            attempts=2,
        )
        result = search.run(self.seeds())
        self.assertEqual(len(result.eligible_pool), 3)
        self.assertEqual(
            [item.disposition for item in result.attempts],
            ["ATTEMPT_CONSUMED_CANDIDATE_FAILURE"] * 2,
        )

    def test_role_and_infrastructure_failures_are_not_consumed(self):
        for error in (
            RoleFormalValidityFailure("applicability mismatch"),
            FormalInfrastructureFailure("backend unavailable"),
            RuntimeError("unclassified failure"),
        ):
            search = SharedPoolMStar(
                role="QUAL",
                run_seed=0,
                controller_identity="controller-v1",
                static_evaluate=lambda program, candidate_id: "1.000000000000",
                rotating_feedback=lambda parent, attempt: (_ for _ in ()).throw(error),
                mutate=lambda parent, feedback, attempt: b"unused",
                validate_program=lambda program: None,
                full_pool_evaluate=lambda candidate: "1.000000000000",
                behavior_manifest=lambda program: {"mode": "DIRECT_RETRIEVAL"},
                attempts=1,
            )
            with self.assertRaises(type(error)):
                search.run(self.seeds())


if __name__ == "__main__":
    unittest.main()
