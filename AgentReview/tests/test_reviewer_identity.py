import tempfile
import unittest
from pathlib import Path

from agentreview.reviewer_identity import (
    ALL_TASKS,
    SPECIALIST_TASKS,
    build_meta_review_question,
    load_meta_synthesizer,
    load_reviewer_suite,
    validate_meta_synthesis_manifest,
    validate_reviewer_identity_manifest,
)
from agentreview.reviewer_runtime.tasks import reviewer_identity
from agentreview.single_paper_review_roles import (
    build_meta_reviewer_config,
    build_specialist_reviewer_configs,
)


class FrozenReviewerIdentityTest(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).parents[1]

    def test_specialist_manifest_has_exactly_five_roles(self):
        payload = validate_reviewer_identity_manifest(
            self.root / "configs" / "frozen_specialist_identity_v3.json"
        )
        self.assertEqual(tuple(task["task_id"] for task in payload["tasks"]), SPECIALIST_TASKS)
        self.assertEqual(ALL_TASKS, SPECIALIST_TASKS)
        self.assertNotIn("META", ALL_TASKS)

    def test_meta_has_independent_prompt_and_manifest(self):
        payload = validate_meta_synthesis_manifest(
            self.root / "configs" / "frozen_meta_synthesis_v1.json"
        )
        prompt = load_meta_synthesizer().system_prompt
        self.assertEqual(payload["source_path"], "META_SYNTHESIS_Request_v1.md")
        self.assertIn("must not use or request related-work Memory", prompt)
        self.assertNotIn("Final score", prompt)
        self.assertNotIn("Confidence (", prompt)

    def test_deployment_configs_keep_meta_outside_specialist_suite(self):
        suite = load_reviewer_suite()
        specialist_configs = build_specialist_reviewer_configs()
        self.assertEqual(len(specialist_configs), 5)
        for task_id, config in zip(SPECIALIST_TASKS, specialist_configs):
            self.assertEqual(config["role_desc"], suite.task(task_id).system_prompt)
            for key, value in reviewer_identity(task_id).items():
                self.assertEqual(config["metadata"][key], value)
        meta = build_meta_reviewer_config()
        self.assertEqual(meta["metadata"]["component"], "meta_synthesis")
        with self.assertRaisesRegex(ValueError, "Unknown Specialist"):
            reviewer_identity("META")

    def test_meta_request_orders_specialists_and_requires_paper(self):
        reversed_reviews = {
            task_id: f"review-{task_id}" for task_id in reversed(SPECIALIST_TASKS)
        }
        question = build_meta_review_question(
            paper_id="paper-1",
            specialist_reviews=reversed_reviews,
            paper_context="=== BEGIN PAPER (MD) ===\npaper\n=== SOURCE ANCHORS ===",
        )
        positions = [
            question.index(f"=== {task_id} SPECIALIST REVIEW ===")
            for task_id in SPECIALIST_TASKS
        ]
        self.assertEqual(positions, sorted(positions))
        with self.assertRaisesRegex(ValueError, "Frozen Paper"):
            build_meta_review_question(
                paper_id="paper-1",
                specialist_reviews=reversed_reviews,
                paper_context="",
            )

    def test_manifest_detects_semantic_drift(self):
        source = self.root / "configs" / "frozen_specialist_identity_v3.json"
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "identity.json"
            changed.write_text(
                source.read_text(encoding="utf-8").replace(
                    '"suite_identity_sha256": "',
                    '"suite_identity_sha256": "0',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "rendered suite"):
                validate_reviewer_identity_manifest(changed)


if __name__ == "__main__":
    unittest.main()
