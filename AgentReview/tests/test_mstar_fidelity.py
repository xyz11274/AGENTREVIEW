import unittest

from memory_evolution.metrics import CoCoReviewFitness
from memory_evolution.reflector import _apply_v4a_patch, _complete


class FakeClassifierToolkit:
    def classify_review(self, review_text, task_id):
        return ["QUAL-MET"]


class FakeJudgeToolkit:
    def llm_completion(self, messages):
        return (
            '{"alignment": 5, "completeness": 5, '
            '"grounding_specificity": 5, "verifiability": 5, "clarity": 5}'
        )


class MstarFidelityTest(unittest.TestCase):
    def test_reflector_requests_non_thinking_structured_output(self):
        class RecordingReflector:
            def __init__(self):
                self.kwargs = None

            def llm_completion(self, messages, **kwargs):
                self.kwargs = kwargs
                return "reflector response"

        reflector = RecordingReflector()
        response = _complete(
            reflector, [{"role": "user", "content": "make a V4A patch"}]
        )

        self.assertEqual(response, "reflector response")
        self.assertEqual(reflector.kwargs["agent_name"], "Memory Reflector")
        self.assertEqual(
            reflector.kwargs["chat_template_kwargs"],
            {"enable_thinking": False},
        )

    def test_contextual_v4a_patch_is_applied(self):
        source = "x = 1\ny = 2\n"
        response = (
            "*** Commit Message\n"
            "Title: Improve y\n"
            "- y was too small\n"
            "- increment y\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ y\n"
            " x = 1\n"
            "-y = 2\n"
            "+y = 3\n"
            "*** End Patch"
        )
        result, change_log, _ = _apply_v4a_patch(source, response)
        self.assertEqual(result, "x = 1\ny = 3\n")
        self.assertEqual(change_log.summary, "Improve y")
        self.assertEqual(len(change_log.details), 2)

    def test_specialist_coverage_is_bounded_intersection_recall(self):
        metric = CoCoReviewFitness(
            FakeClassifierToolkit(),
            FakeJudgeToolkit(),
            task_id="QUAL",
            formal=True,
        )
        score = metric(
            "candidate",
            "reference",
            {
                "question": "paper",
                "metadata": {"categories": ["QUAL-MET", "QUAL-EXP"]},
            },
        )
        self.assertAlmostEqual(score, 2.0 / 3.0)
        self.assertEqual(metric.last_details["intersection_coverage"], 0.5)


if __name__ == "__main__":
    unittest.main()
