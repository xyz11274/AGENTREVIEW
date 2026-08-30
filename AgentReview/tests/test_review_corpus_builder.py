import json
import tempfile
import threading
import unittest

from agentreview.review_corpus import FrozenTokenizer
from agentreview.review_corpus_builder import CorpusPaper, NoMemoryCorpusBuilder


DIGEST = "b" * 64


def response_from_messages(messages):
    user = messages[1]["content"]
    shape = user.split("STRICT OUTPUT SHAPE:\n", 1)[1].split(
        "\n\nCANONICAL PAPER INPUT:", 1
    )[0]
    payload = json.loads(shape)
    for section in payload["subdimension_sections"]:
        section["review_text"] = (
            section["subdimension_id"] + " grounded analysis cites SRC-001."
        )
    payload["overall_assessment"] = "grounded overall assessment"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class NoMemoryCorpusBuilderTest(unittest.TestCase):
    def papers(self):
        return [
            CorpusPaper.from_context(
                paper_id=f"A-{index}",
                canonical_paper_context=f"paper {index}\nSRC-001 evidence",
                structural_context="SRC-001",
            )
            for index in range(2)
        ]

    def builder(self, complete, workers=4):
        return NoMemoryCorpusBuilder(
            complete=complete,
            reviewer_identity_sha256=DIGEST,
            generation_identity_sha256=DIGEST,
            role_system_prompts={
                role: f"frozen {role} system" for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
            },
            max_workers=workers,
        )

    def test_two_papers_build_ten_reviews_and_46_primary_projections(self):
        result = self.builder(response_from_messages).build(
            self.papers(), expected_paper_count=2
        )
        self.assertEqual(len(result.records), 10)
        self.assertEqual(len(result.raw_attempts), 10)
        self.assertTrue(result.manifest["no_memory"])
        self.assertFalse(result.manifest["external_evidence_enabled"])
        materialized = result.materialize(
            tokenizer=FrozenTokenizer(list, "char-tokenizer-v1"),
            max_projection_tokens=10_000,
        )
        self.assertEqual(len(materialized.projections), 46)
        with tempfile.TemporaryDirectory() as directory:
            result.write_audit(directory)
            materialized.write(directory)
            restored = type(materialized).load(directory)
        self.assertEqual(restored.manifest, materialized.manifest)

    def test_retry_is_identical_and_first_legal_response_is_canonical(self):
        lock = threading.Lock()
        calls = {}
        observed = {}

        def complete(messages):
            user = messages[1]["content"]
            key = user.split('"paper_id":"', 1)[1].split('"', 1)[0], user.split(
                '"role":"', 1
            )[1].split('"', 1)[0]
            frozen = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
            with lock:
                calls[key] = calls.get(key, 0) + 1
                observed.setdefault(key, []).append(frozen)
                attempt = calls[key]
            if key == ("A-0", "QUAL") and attempt == 1:
                return "not-json"
            return response_from_messages(messages)

        result = self.builder(complete).build(self.papers(), expected_paper_count=2)
        self.assertEqual(calls[("A-0", "QUAL")], 2)
        self.assertEqual(observed[("A-0", "QUAL")][0], observed[("A-0", "QUAL")][1])
        qual_attempts = [
            item
            for item in result.raw_attempts
            if item.paper_id == "A-0" and item.role == "QUAL"
        ]
        self.assertEqual([item.valid for item in qual_attempts], [False, True])

    def test_two_invalid_outputs_fail_fast_without_partial_corpus(self):
        def complete(messages):
            return "invalid"

        with self.assertRaisesRegex(RuntimeError, "fail-fast"):
            self.builder(complete, workers=1).build(
                self.papers(), expected_paper_count=2
            )


if __name__ == "__main__":
    unittest.main()
