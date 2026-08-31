import json
import tempfile
import unittest
from pathlib import Path

from agentreview.memory_library import load_memory_records
from agentreview.reviewer_identity import (
    ALL_TASKS,
    META_REQUEST_PROTOCOL_VERSION,
    SPECIALIST_TASKS,
    build_meta_review_question,
    load_reviewer_suite,
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

    def test_semantic_manifest_matches_six_rendered_prompts(self):
        payload = validate_reviewer_identity_manifest(
            self.root / "configs" / "frozen_reviewer_identity_v2.json"
        )
        self.assertEqual(
            tuple(task["task_id"] for task in payload["tasks"]),
            ALL_TASKS,
        )
        self.assertEqual(
            payload["request_protocols"]["meta"]["version"],
            META_REQUEST_PROTOCOL_VERSION,
        )

    def test_deployment_configs_use_the_frozen_suite_prompts(self):
        suite = load_reviewer_suite()
        specialist_configs = build_specialist_reviewer_configs()
        self.assertEqual(len(specialist_configs), len(SPECIALIST_TASKS))
        for task_id, config in zip(SPECIALIST_TASKS, specialist_configs):
            self.assertEqual(config["role_desc"], suite.task(task_id).system_prompt)
            for key, value in reviewer_identity(task_id).items():
                self.assertEqual(config["metadata"][key], value)
        meta = build_meta_reviewer_config()
        self.assertEqual(meta["role_desc"], suite.task("META").system_prompt)
        for key, value in reviewer_identity("META").items():
            self.assertEqual(meta["metadata"][key], value)

    def test_meta_request_requires_context_and_orders_specialists(self):
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
        with self.assertRaisesRegex(ValueError, "canonical paper context"):
            build_meta_review_question(
                paper_id="paper-1",
                specialist_reviews=reversed_reviews,
                paper_context="",
            )

    def test_manifest_detects_semantic_drift(self):
        source = self.root / "configs" / "frozen_reviewer_identity_v2.json"
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

    def test_memory_library_selects_the_current_reviewer_suite(self):
        current = load_reviewer_suite().suite_identity_sha256
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "single_paper_review"
            library.mkdir()
            (library / "memory_library.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "sample_id": sample_id,
                            "reviewer_suite_identity_sha256": identity,
                        }
                    )
                    for sample_id, identity in (
                        ("current", current),
                        ("historical", "0" * 64),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            records = load_memory_records(directory)
        self.assertEqual([record["sample_id"] for record in records], ["current"])


if __name__ == "__main__":
    unittest.main()
