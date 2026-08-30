import json
import unittest

from agentreview.review_corpus import ROLE_SUBDIMENSIONS
from memory_evolution.core_formal import (
    CorePaperInput,
    CoreRoleEvolutionRunner,
    freeze_suite_from_role_runs,
)
from memory_evolution.core_search import SEED_ORDER
from memory_evolution.role_evaluator import ReferenceFreeRoleEvaluator


def evaluator_for(role):
    dimensions = ROLE_SUBDIMENSIONS[role]

    def complete(messages):
        request = json.loads(messages[1]["content"])
        if request["required_output_fields"][-1] == "decisions":
            return json.dumps(
                {
                    "role": role,
                    "paper_id": request["paper_id"],
                    "decisions": [
                        {
                            "subdimension_id": value,
                            "status": "APPLICABLE",
                            "n_a_reason": None,
                            "paper_anchor_ids": ["SRC-001"],
                        }
                        for value in dimensions
                    ],
                }
            )
        return json.dumps(
            {
                "role": role,
                "paper_id": request["paper_id"],
                "scores": [
                    {
                        "subdimension_id": value,
                        "score": 3,
                        "events": [],
                        "evidence_limitations": [],
                    }
                    for value in dimensions
                ],
            }
        )

    return ReferenceFreeRoleEvaluator(
        complete=complete,
        evaluator_identity="evaluator-v1",
        protocol_identity="core-v1",
        role_rubric=f"frozen {role} rubric",
    )


class CoreFormalTest(unittest.TestCase):
    def runner(self, role, attempts=0):
        paper = CorePaperInput(
            "B-1", "review question", "paper text", "SRC-001"
        )
        return CoreRoleEvolutionRunner(
            role=role,
            role_run_identity=f"role-run-{role}",
            run_seed=0,
            static_papers=(paper,),
            rotating_schedule=tuple((paper,) for _ in range(attempts)),
            full_pool_papers=(paper,),
            evaluator=evaluator_for(role),
            review_candidate=lambda program, candidate_id, paper: (
                f"grounded {role} review from {program.decode()}"
            ),
            mutate=lambda parent, feedback, attempt: f"child-{attempt}".encode(),
            validate_program=lambda program: None,
            behavior_manifest=lambda program: {"program": program.decode()},
            attempts=attempts,
        )

    @staticmethod
    def seeds():
        return dict(zip(SEED_ORDER, (b"vector", b"summary", b"structured")))

    def test_role_runner_uses_shared_pool_and_full_pool_winner(self):
        result = self.runner("QUAL").run(self.seeds())
        self.assertEqual(len(result.search.eligible_pool), 3)
        self.assertEqual(len(result.search.finalists), 3)
        self.assertEqual(result.winner.full_pool_fitness, "3.000000000000")
        self.assertEqual(
            [item.stage for item in result.evaluations],
            ["STATIC_25", "STATIC_25", "STATIC_25"]
            + ["FULL_POOL_FINALIST"] * 3,
        )

    def test_five_role_results_freeze_direct_suite(self):
        results = {
            role: self.runner(role).run(self.seeds())
            for role in ROLE_SUBDIMENSIONS
        }
        suite = freeze_suite_from_role_runs(results)
        self.assertFalse(suite.meta_enabled)
        self.assertEqual(
            [winner.role for winner in suite.winners],
            ["QUAL", "CLAR", "SIGN", "ORIG", "POL"],
        )

    def test_rotating_feedback_is_batch_aggregate_only(self):
        observed = []
        runner = self.runner("QUAL", attempts=1)
        runner.mutate_callback = (
            lambda parent, feedback, attempt: observed.append(dict(feedback))
            or b"child-1"
        )
        result = runner.run(self.seeds())
        self.assertEqual(len(result.search.attempts), 1)
        self.assertEqual(
            set(observed[0]),
            {
                "role",
                "overall_fitness",
                "subdimension_scores",
                "failure_codes",
                "evaluable_paper_count",
            },
        )
        self.assertNotIn("paper_id", observed[0])
        self.assertNotIn("review", observed[0])


if __name__ == "__main__":
    unittest.main()
