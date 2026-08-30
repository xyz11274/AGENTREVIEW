import json
import tempfile
import unittest
from pathlib import Path

from agentreview.review_corpus import (
    ROLE_SUBDIMENSIONS,
    CanonicalFullReviewRecord,
    FrozenTokenizer,
    materialize_corpus,
)


DIGEST = "a" * 64


def full_review(role: str, paper_id: str = "paper-1") -> CanonicalFullReviewRecord:
    payload = {
        "role": role,
        "paper_id": paper_id,
        "subdimension_sections": [
            {
                "subdimension_id": subdimension,
                "review_text": (
                    f"{subdimension} first paragraph cites SRC-001(page=1).\n\n"
                    f"{subdimension} second sentence. Final observation."
                ),
                "paper_anchor_ids": ["SRC-001(page=1)"],
            }
            for subdimension in ROLE_SUBDIMENSIONS[role]
        ],
        "overall_assessment": f"Overall {role}",
    }
    return CanonicalFullReviewRecord.from_model_response(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        expected_role=role,
        expected_paper_id=paper_id,
        paper_identity_sha256=DIGEST,
        structural_context_sha256=DIGEST,
        reviewer_identity_sha256=DIGEST,
        prompt_identity_sha256=DIGEST,
        request_identity_sha256=DIGEST,
    )


class ReviewCorpusTest(unittest.TestCase):
    def test_strict_full_review_schema_and_order(self):
        record = full_review("QUAL")
        restored = CanonicalFullReviewRecord.from_value(record.to_dict())
        self.assertEqual(restored.full_review_record_id, record.full_review_record_id)
        self.assertTrue(record.full_review_record_id.startswith("fullrev_"))

        value = record.canonical_payload
        value["subdimension_sections"] = list(reversed(value["subdimension_sections"]))
        with self.assertRaisesRegex(ValueError, "subdimension order mismatch"):
            CanonicalFullReviewRecord.from_model_response(
                json.dumps(value).encode(),
                expected_role="QUAL",
                expected_paper_id="paper-1",
                paper_identity_sha256=DIGEST,
                structural_context_sha256=DIGEST,
                reviewer_identity_sha256=DIGEST,
                prompt_identity_sha256=DIGEST,
                request_identity_sha256=DIGEST,
            )

    def test_code_fence_is_not_repaired(self):
        payload = json.dumps(full_review("CLAR").canonical_payload)
        with self.assertRaisesRegex(ValueError, "strict JSON object"):
            CanonicalFullReviewRecord.from_model_response(
                f"```json\n{payload}\n```".encode(),
                expected_role="CLAR",
                expected_paper_id="paper-1",
                paper_identity_sha256=DIGEST,
                structural_context_sha256=DIGEST,
                reviewer_identity_sha256=DIGEST,
                prompt_identity_sha256=DIGEST,
                request_identity_sha256=DIGEST,
            )

    def test_one_paper_all_roles_produces_23_primary_projections(self):
        tokenizer = FrozenTokenizer(list, "char-tokenizer-v1")
        corpus = materialize_corpus(
            [full_review(role) for role in ROLE_SUBDIMENSIONS],
            tokenizer=tokenizer,
            max_projection_tokens=10_000,
        )
        self.assertEqual(len(corpus.records), 5)
        self.assertEqual(len(corpus.projections), 23)
        self.assertEqual(len(corpus.fragments), 23)
        self.assertEqual(len({item.projection_id for item in corpus.projections}), 23)
        self.assertEqual(
            len({item.projection_fragment_id for item in corpus.fragments}), 23
        )

    def test_fragmentation_is_contiguous_byte_preserving_and_anchor_conservative(self):
        record = full_review("CLAR")
        corpus = materialize_corpus(
            [record],
            tokenizer=FrozenTokenizer(list, "char-tokenizer-v1"),
            max_projection_tokens=24,
        )
        projection = corpus.projections[0]
        fragments = [
            item for item in corpus.fragments if item.projection_id == projection.projection_id
        ]
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(item.text for item in fragments), projection.review_text)
        self.assertTrue(all(item.token_count <= 24 for item in fragments))
        for item in fragments:
            self.assertEqual(
                item.text,
                projection.review_text[item.character_start : item.character_end],
            )
            self.assertTrue(
                set(item.explicit_fragment_anchor_ids)
                <= set(item.inherited_section_anchor_ids)
            )

    def test_materialization_is_stable_on_disk(self):
        corpus = materialize_corpus(
            [full_review("POL")],
            tokenizer=FrozenTokenizer(list, "char-tokenizer-v1"),
            max_projection_tokens=10_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            corpus.write(directory)
            first = {
                path.name: path.read_bytes()
                for path in Path(directory).iterdir()
                if path.is_file()
            }
            corpus.write(directory)
            second = {
                path.name: path.read_bytes()
                for path in Path(directory).iterdir()
                if path.is_file()
            }
            loaded = type(corpus).load(directory)
        self.assertEqual(first, second)
        self.assertEqual(loaded.manifest, corpus.manifest)


if __name__ == "__main__":
    unittest.main()
