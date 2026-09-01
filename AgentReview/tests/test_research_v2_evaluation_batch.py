import json
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.evaluation import _parse_json_object
from agentreview.research_v2.evaluation_batch import (
    EvaluationBatchOptions,
    run_evaluation_batch,
)
from agentreview.research_v2.io import atomic_write_json
from agentreview.research_v2.models import Paper, SpecialistReview


class FakeCompletionClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens, component):
        self.calls += 1
        return json.dumps(
            {
                "review_quality": 4,
                "major_missing_points": [],
                "factual_evidence_issues": [],
                "improvement_suggestions": ["Add one concrete comparison."],
            }
        )


class EvaluationBatchTest(unittest.TestCase):
    def test_evaluator_parser_preserves_latex_backslashes_in_json_strings(self):
        raw = r'''```json
{
  "review_quality": 4,
  "major_missing_points": ["Define $\mathcal{M}$ and $\Sigma(X)$ clearly."],
  "factual_evidence_issues": [],
  "improvement_suggestions": ["Keep a valid newline\ninside this string."]
}
```'''

        parsed = _parse_json_object(raw)

        self.assertEqual(parsed["review_quality"], 4.0)
        self.assertEqual(
            parsed["major_missing_points"],
            [r"Define $\mathcal{M}$ and $\Sigma(X)$ clearly."],
        )
        self.assertEqual(
            parsed["improvement_suggestions"],
            ["Keep a valid newline\ninside this string."],
        )

    def test_batch_is_bounded_and_resumes_without_new_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper_path = root / "data" / "papers" / "paper-1.json"
            atomic_write_json(
                paper_path,
                Paper(
                    paper_id="paper-1",
                    title="Example",
                    abstract="Abstract",
                    text="Complete paper text.",
                    source={"dataset": "test"},
                ).to_dict(),
            )
            atomic_write_json(
                root / "data" / "splits" / "canonical_review_corpus.json",
                {
                    "count": 1,
                    "records": [{"paper_id": "paper-1", "path": str(paper_path)}],
                },
            )
            for role in ROLES:
                atomic_write_json(
                    root / "reviews" / "review-run" / "paper-1" / f"{role}.json",
                    SpecialistReview(
                        review_id=f"review-{role}",
                        paper_id="paper-1",
                        role=role,
                        program_version="no-memory-v1",
                        memory_snapshot_id="snapshot",
                        review_text=f"Complete {role} review.",
                        raw_model_output=f"Complete {role} review.",
                        reviewer_identity={},
                        model_identity={},
                        generation_config={},
                        retrieval_provenance={"memory": {}, "s2": {}},
                        run_id="review-run",
                        created_at="2026-09-01T00:00:00+00:00",
                    ).to_dict(),
                )

            client = FakeCompletionClient()
            options = EvaluationBatchOptions(
                review_run_id="review-run",
                run_id="evaluation-run",
                max_workers=5,
            )
            first = run_evaluation_batch(
                output_root=root,
                options=options,
                completion_client=client,
                evaluator_identity={"model": "fake"},
            )
            second = run_evaluation_batch(
                output_root=root,
                options=options,
                completion_client=client,
                evaluator_identity={"model": "fake"},
            )

        self.assertEqual(first["completed"], 5)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(second["resumed"], 5)
        self.assertEqual(client.calls, 5)


if __name__ == "__main__":
    unittest.main()
