import json
import tempfile
import unittest
from pathlib import Path

from agentreview.corpus_bootstrap import (
    canonical_context,
    materialize_corpus_bootstrap,
    selected_paper_ids,
)
from agentreview.corpus_freeze_audit import audit_and_freeze_corpus
from agentreview.review_corpus_builder import CorpusPaper, NoMemoryCorpusBuilder
from tests.test_corpus_bootstrap import CorpusBootstrapTest, FakeTokenizer


def semantic_legal_response(messages):
    shape = messages[1]["content"].split("STRICT OUTPUT SHAPE:\n", 1)[1].split(
        "\n\nCANONICAL PAPER INPUT:", 1
    )[0]
    value = json.loads(shape)
    replacements = {
        "CLAR-FIG": "The text-only input exposes caption text; image pixels are not observable.",
        "POL-PLG": "Frozen overlap evidence is not available, so plagiarism cannot be assessed.",
        "POL-ANO": "Without frozen policy, anonymity compliance cannot be assessed.",
        "POL-IMP": "Without frozen policy, documentation obligations cannot be assessed.",
        "POL-ETH": "No material ethical-risk pathway is identifiable from the supplied paper.",
        "ORIG-MTH": "The paper presents its method as a contribution; global novelty cannot be verified.",
        "SIGN-SOT": "The paper reports a relative benchmark gain; global SOTA cannot be verified.",
    }
    for section in value["subdimension_sections"]:
        section["review_text"] = replacements.get(
            section["subdimension_id"],
            section["subdimension_id"] + " grounded paper-only assessment.",
        )
        section["paper_anchor_ids"] = ["SRC-001"]
    value["overall_assessment"] = "Grounded role-local paper-only assessment."
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CorpusFreezeAuditTest(unittest.TestCase):
    def test_complete_offline_freeze_audit_commits_success_only_after_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, episodes_root, selection = CorpusBootstrapTest().fixtures(root)
            bootstrap = materialize_corpus_bootstrap(
                episodes_root=episodes_root,
                selection_path=selection,
                model_path=model,
                tokenizer=FakeTokenizer(),
                output_token_cap=8192,
                model_revision="revision",
                served_model_name="qwen-test",
                model_context_limit=262144,
            )
            bootstrap_dir = root / "bootstrap"
            bootstrap.write(bootstrap_dir)
            by_id = {}
            for line in (episodes_root / "QUAL" / "episodes.jsonl").read_text().splitlines():
                value = json.loads(line)
                by_id[value["id"].split("::", 1)[0]] = value
            papers = [
                CorpusPaper.from_context(
                    paper_id=paper_id,
                    canonical_paper_context=canonical_context(by_id[paper_id]),
                )
                for paper_id in selected_paper_ids(selection)
            ]
            generation_sha = bootstrap.request_manifest["generation_identity_sha256"]
            builder = NoMemoryCorpusBuilder(
                complete=semantic_legal_response,
                reviewer_identity_sha256=generation_sha,
                generation_identity_sha256=generation_sha,
                max_workers=4,
            )
            result = builder.build(papers, expected_paper_count=125)
            build_dir = root / "corpus_build_v4"
            result.write_audit(build_dir)
            self.assertFalse((build_dir / "frozen_corpus_success_manifest.json").exists())
            success = audit_and_freeze_corpus(
                corpus_build_directory=build_dir,
                bootstrap_directory=bootstrap_dir,
            )
            self.assertEqual(success["status"], "FROZEN_CORPUS_SUCCESS")
            self.assertTrue((build_dir / "frozen_corpus_success_manifest.json").is_file())
            audit = json.loads(
                (build_dir / "corpus_freeze_audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(audit["status"], "PASS")
            self.assertEqual(audit["canonical_record_count"], 625)


if __name__ == "__main__":
    unittest.main()
