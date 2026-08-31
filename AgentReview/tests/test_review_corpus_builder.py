import json
import tempfile
import threading
import unittest

from agentreview.review_corpus import FrozenTokenizer
from agentreview.review_corpus_builder import (
    CorpusPaper,
    NoMemoryCorpusBuilder,
    build_corpus_reviewer_request,
    default_role_system_prompts,
)
from agentreview.single_paper_review_roles import load_review_request_sections


DIGEST = "b" * 64


def response_from_messages(messages):
    user = messages[1]["content"]
    shape = user.split("STRICT OUTPUT SHAPE:\n", 1)[1].split(
        "\n\nCANONICAL PAPER INPUT:", 1
    )[0]
    payload = json.loads(shape)
    for section in payload["subdimension_sections"]:
        subdimension = section["subdimension_id"]
        safe_text = {
            "CLAR-FIG": (
                "The text-only input exposes no image pixels; only the "
                "machine-readable caption can be assessed."
            ),
            "POL-PLG": (
                "Frozen overlap evidence is not available, so plagiarism or "
                "dual submission cannot be assessed."
            ),
            "POL-ANO": "Without frozen policy, anonymity compliance cannot be assessed.",
            "POL-IMP": "Without frozen policy, documentation obligations cannot be assessed.",
        }.get(subdimension, subdimension + " grounded analysis cites SRC-001.")
        section["review_text"] = safe_text
    payload["overall_assessment"] = "grounded overall assessment"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class NoMemoryCorpusBuilderTest(unittest.TestCase):
    def papers(self):
        return [
            CorpusPaper.from_context(
                paper_id=f"A-{index}",
                canonical_paper_context=(
                    "Context protocol: synthetic-v1\n"
                    "=== CANDIDATE-VISIBLE METADATA ===\n"
                    f"paper_id: A-{index}\nreviewer_role: QUAL\n"
                    "=== END CANDIDATE-VISIBLE METADATA ===\n\n"
                    "=== BEGIN PAPER (MD) ===\n# METHOD\n"
                    f"paper {index}\n=== END PAPER (MD) ===\n\n"
                    "=== SOURCE ANCHORS ===\n"
                    "SRC-001 | page=1 | type=text | text=grounded evidence\n"
                    "=== END SOURCE ANCHORS ==="
                ),
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

    def test_paper_grounded_review_is_not_rejected_by_wording_heuristics(self):
        def complete(messages):
            payload = json.loads(response_from_messages(messages))
            for section in payload["subdimension_sections"]:
                if section["subdimension_id"] == "POL-ANO":
                    section["review_text"] = (
                        "The paper header explicitly lists the authors and their "
                        "affiliation. While these details are visible in the text, "
                        "the corpus build does not provide a frozen venue or "
                        "year-specific anonymity policy. Therefore, it is not "
                        "possible to declare a double-blind policy violation or "
                        "compliance clearance based solely on the supplied evidence."
                    )
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        record, attempts = self.builder(complete, workers=1).build_one(
            self.papers()[0], "POL"
        )

        self.assertEqual(record.role, "POL")
        self.assertEqual([item.valid for item in attempts], [True])

    def test_request_binds_dynamic_strict_json_schema(self):
        request = build_corpus_reviewer_request(
            self.papers()[0],
            "CLAR",
            generation_identity_sha256=DIGEST,
            role_system_prompts={role: f"frozen {role}" for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")},
        )
        response_format = request.response_format
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["role"]["const"], "CLAR")
        self.assertEqual(schema["properties"]["paper_id"]["const"], "A-0")
        sections = schema["properties"]["subdimension_sections"]
        self.assertEqual(sections["minItems"], 3)
        self.assertEqual(sections["maxItems"], 3)
        # vLLM 0.27.1's structured-output compiler rejects JSON Schema's
        # uniqueItems keyword. Duplicate rejection remains a strict parser
        # invariant and is covered by test_review_corpus.py.
        self.assertNotIn(
            "uniqueItems",
            sections["items"]["properties"]["paper_anchor_ids"],
        )
        self.assertEqual(len(request.response_format_sha256), 64)

    def test_clar_request_places_text_only_boundary_next_to_paper_input(self):
        request = build_corpus_reviewer_request(
            self.papers()[0],
            "CLAR",
            generation_identity_sha256=DIGEST,
        )
        user = request.messages[1]["content"]
        self.assertIn("CURRENT INPUT MODALITY (MANDATORY)", user)
        self.assertIn("no figure/image pixels", user)
        self.assertIn("A caption's description is not observation of the image", user)
        self.assertIn(
            "Use the observed text object as the grammatical subject",
            user,
        )
        self.assertIn(
            "Never write that figures or images are well-labeled or informative",
            user,
        )
        self.assertLess(
            user.index("CURRENT INPUT MODALITY (MANDATORY)"),
            user.index("CANONICAL PAPER INPUT"),
        )

    def test_request_forbids_raw_latex_backslashes_in_strict_json_text(self):
        request = build_corpus_reviewer_request(
            self.papers()[0],
            "QUAL",
            generation_identity_sha256=DIGEST,
        )
        user = request.messages[1]["content"]
        self.assertIn("must not contain any backslash character or LaTeX command", user)
        self.assertIn("write 'theta'", user)

    def test_corpus_role_prompts_freeze_reference_free_semantic_boundaries(self):
        prompts = default_role_system_prompts()
        frozen_sections = load_review_request_sections()
        clar = " ".join(prompts["CLAR"].split())
        pol = " ".join(prompts["POL"].split())
        orig = " ".join(prompts["ORIG"].split())
        sign = " ".join(prompts["SIGN"].split())
        self.assertIn("text-only Markdown", clar)
        self.assertIn("never assess unseen visual", clar)
        self.assertIn("Absence of evidence never proves no plagiarism", pol)
        self.assertIn("frozen venue/year anonymity policy", pol)
        self.assertIn("material ethical-risk pathway", pol)
        self.assertIn("never summarize missing evidence as", pol)
        self.assertIn("insufficient to determine", pol)
        self.assertIn("paper claims/reports/presents", orig)
        self.assertIn("must not be upgraded to a global SOTA fact", sign)
        for role, prompt in prompts.items():
            self.assertIn(frozen_sections[role], prompt)
            self.assertNotIn("VERIFIED HUMAN REFERENCE OPINIONS", prompt)

    def test_structural_context_identity_is_paper_specific_and_stable(self):
        first = self.papers()[0]
        again = CorpusPaper.from_context(
            paper_id=first.paper_id,
            canonical_paper_context=first.canonical_paper_context,
        )
        second = self.papers()[1]
        self.assertEqual(first.structural_context_sha256, again.structural_context_sha256)
        self.assertEqual(first.structural_context_canonical_json, again.structural_context_canonical_json)
        self.assertNotEqual(first.structural_context_sha256, second.structural_context_sha256)
        self.assertEqual(first.allowed_paper_anchor_ids, ("SRC-001",))
        structural = json.loads(first.structural_context_canonical_json)
        self.assertEqual(structural["paper_id"], "A-0")
        self.assertEqual(structural["source_anchors"][0]["anchor_id"], "SRC-001")
        self.assertTrue(structural["sections"])

    def test_structural_context_records_only_the_frozen_anchor_preamble(self):
        context = self.papers()[0].canonical_paper_context.replace(
            "=== SOURCE ANCHORS ===\n",
            "=== SOURCE ANCHORS ===\n"
            "Structured source anchors from the original paper extraction:\n",
        )
        paper = CorpusPaper.from_context(
            paper_id="A-0", canonical_paper_context=context
        )
        structural = json.loads(paper.structural_context_canonical_json)
        self.assertEqual(
            [item["text"] for item in structural["source_anchor_preamble"]],
            ["Structured source anchors from the original paper extraction:"],
        )
        bad = context.replace(
            "Structured source anchors from the original paper extraction:",
            "unregistered prose inside the anchor registry",
        )
        with self.assertRaisesRegex(ValueError, "Malformed Source Anchor"):
            CorpusPaper.from_context(
                paper_id="A-0", canonical_paper_context=bad
            )


if __name__ == "__main__":
    unittest.main()
