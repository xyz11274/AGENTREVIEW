import json
import tempfile
import unittest

from agentreview.corpus_checkpoint import run_checkpointed_corpus_build
from agentreview.review_corpus_builder import (
    CorpusBuildFailure,
    CorpusPaper,
    NoMemoryCorpusBuilder,
    default_role_system_prompts,
)


DIGEST = "c" * 64
ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")


def legal_response(messages):
    shape = messages[1]["content"].split("STRICT OUTPUT SHAPE:\n", 1)[1].split(
        "\n\nCANONICAL PAPER INPUT:", 1
    )[0]
    value = json.loads(shape)
    for section in value["subdimension_sections"]:
        section["review_text"] = "Grounded role-local assessment."
    value["overall_assessment"] = None
    return json.dumps(value, separators=(",", ":"))


class CorpusCheckpointTest(unittest.TestCase):
    def paper(self):
        return CorpusPaper.from_context(
            paper_id="A-1",
            canonical_paper_context="reviewer_role: QUAL\nSRC-001 evidence",
            structural_context="SRC-001 evidence",
        )

    def builder(self, complete):
        return NoMemoryCorpusBuilder(
            complete=complete,
            reviewer_identity_sha256=DIGEST,
            generation_identity_sha256=DIGEST,
            role_system_prompts={role: f"frozen {role}" for role in ROLES},
            max_workers=1,
        )

    def test_resume_reuses_all_durable_items_without_model_calls(self):
        calls = []

        def complete(messages):
            calls.append(messages)
            return legal_response(messages)

        with tempfile.TemporaryDirectory() as directory:
            first = run_checkpointed_corpus_build(
                builder=self.builder(complete),
                papers=[self.paper()],
                checkpoint_directory=directory,
                expected_paper_count=1,
            )
            self.assertEqual(len(calls), 5)

            def forbidden(_messages):
                raise AssertionError("resume called the model")

            second = run_checkpointed_corpus_build(
                builder=self.builder(forbidden),
                papers=[self.paper()],
                checkpoint_directory=directory,
                expected_paper_count=1,
            )
        self.assertEqual(first.records, second.records)
        self.assertEqual(first.raw_attempts, second.raw_attempts)

    def test_two_illegal_outputs_create_sealed_fail_fast_record(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CorpusBuildFailure):
                run_checkpointed_corpus_build(
                    builder=self.builder(lambda _messages: "not-json"),
                    papers=[self.paper()],
                    checkpoint_directory=directory,
                    expected_paper_count=1,
                )
            with self.assertRaisesRegex(RuntimeError, "sealed fail-fast"):
                run_checkpointed_corpus_build(
                    builder=self.builder(legal_response),
                    papers=[self.paper()],
                    checkpoint_directory=directory,
                    expected_paper_count=1,
                )

    def test_default_corpus_prompt_has_only_json_output_contract(self):
        prompts = default_role_system_prompts()
        self.assertEqual(set(prompts), set(ROLES))
        for prompt in prompts.values():
            self.assertIn("single strict JSON object", prompt)
            self.assertIn("lexicographically ascending", prompt)
            self.assertNotIn("Dimension score (1-10)", prompt)
            self.assertNotIn("Recommendation: Accept", prompt)


if __name__ == "__main__":
    unittest.main()
