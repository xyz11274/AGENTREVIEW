import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.io import atomic_write_json
from agentreview.research_v2.meta import (
    MetaSynthesisInfrastructureFailure,
    MetaSynthesisPaperFailure,
    generate_final_review,
)
from agentreview.research_v2.models import Paper


class _Completion:
    def __init__(self, response="Final recommendation: Accept", error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def complete(self, messages, *, max_tokens, component):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class MetaSynthesisResumeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paper_path = self.root / "paper.json"
        atomic_write_json(
            self.paper_path,
            Paper(
                paper_id="p1",
                title="Paper",
                abstract="Abstract",
                text="Complete paper text.",
            ).to_dict(),
        )
        self.review_paths = []
        for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
            path = self.root / f"{role}.json"
            atomic_write_json(
                path,
                {
                    "paper_id": "p1",
                    "role": role,
                    "review_id": f"review-{role}",
                    "review_text": f"{role} review",
                },
            )
            self.review_paths.append(path)

    def tearDown(self):
        self.temp.cleanup()

    def _generate(self, client):
        return generate_final_review(
            paper_path=self.paper_path,
            review_paths=self.review_paths,
            mode="meta",
            run_id="formal-meta",
            output_root=self.root,
            completion_client=client,
            model_identity={"model": "mock"},
        )

    def test_matching_raw_is_replayed_without_another_model_call(self):
        first_client = _Completion()
        first = self._generate(first_client)
        final_path = self.root / "final_reviews" / "formal-meta" / "p1.json"
        final_path.unlink()
        replay_client = _Completion(error=AssertionError("must not call model"))

        replayed = self._generate(replay_client)

        self.assertEqual(first["final_review_id"], replayed["final_review_id"])
        self.assertEqual(first["review_text"], replayed["review_text"])
        self.assertEqual(first_client.calls, 1)
        self.assertEqual(replay_client.calls, 0)

    def test_transport_and_output_failures_have_distinct_types(self):
        with self.assertRaises(MetaSynthesisInfrastructureFailure):
            self._generate(_Completion(error=ConnectionError("offline")))
        with self.assertRaises(MetaSynthesisPaperFailure):
            generate_final_review(
                paper_path=self.paper_path,
                review_paths=self.review_paths,
                mode="meta",
                run_id="invalid-output",
                output_root=self.root,
                completion_client=_Completion(response="Final score: 9"),
                model_identity={"model": "mock"},
            )


if __name__ == "__main__":
    unittest.main()
