from pathlib import Path
from types import SimpleNamespace

import pytest

from agentreview.research_v2.coco_integrity import audit_split, audit_classify, audit_judge
from agentreview.research_v2.development_coco_worker import _load_standard
from agentreview.research_v2.io import read_jsonl, write_immutable_jsonl

COCO = Path(__file__).resolve().parents[3] / "CoCoReviewBench"


def task(prompt, response):
    return {"messages": [{"content": prompt}], "completion": {
        "choices": [{"message": {"content": response}, "finish_reason": "stop"}]}}


def test_split_rejects_upstream_missing_part_fallback():
    module = _load_standard(COCO, "_2_split")
    source = {"id": "paper", "text": "First sufficiently long reviewer opinion.\n\nSecond sufficiently long reviewer opinion."}
    texts, prompt, _ = module.build_prompt(source["text"])
    assert len(texts) > 1
    result = {**source, "sentence_texts": texts, "opinions": [list(range(len(texts)))]}
    with pytest.raises(ValueError, match="coverage"):
        audit_split(module, [source], [result], {"attempt": task(prompt, "Part 1: Label 1")})
    raw = "\n".join(f"Part {i + 1}: Label {i + 1}" for i in range(len(texts)))
    assert audit_split(module, [source], [result], {"attempt": task(prompt, raw)})[0]["raw_coverage"] == [1.0]


@pytest.mark.parametrize("invalid", ["N/A", "</think>QUAL-MET QUAL-MET"])
def test_classification_rejects_fallback_without_original_format_valid_response(invalid):
    module = _load_standard(COCO, "_3_classify")
    source = {"id": "paper", "sentence_texts": ["A greeting."], "opinions": [[0]]}
    result = {**source, "category": [["N/A"]]}
    _, main = module.classify_reviews_prompt()
    prompt = main.strip() + "\n\n\nReviewer Comment:\nA greeting."
    with pytest.raises(ValueError, match="unresolved"):
        audit_classify(module, [source], [result], {"attempt": task(prompt, invalid)})
    assert audit_classify(module, [source], [result], {"attempt": task(prompt, "</think>N/A")})[0]["classified_count"] == 1


def test_paper_score_cannot_hide_missing_required_category_or_metric():
    evaluation = SimpleNamespace(table_judge_metric_map={"Correctness": "alignment", "Verifiability": "verifiability"})
    human = {"p": {"category_correct": {"QUAL-MET": ["reference"]}}}
    ai = {"p": {"QUAL-MET": ["review"]}}
    paper = {"p": {"judge_scores": {"success": True, "alignment": 4, "verifiability": "X",
                                     "claim_extraction": "normal", "actionability": "X"}}}
    category = {"p": {"category_scores": {}}}
    with pytest.raises(ValueError, match="unresolved required categories"):
        audit_judge(evaluation, ["p"], human, ai, category, paper)

    category["p"]["category_scores"]["QUAL-MET"] = paper["p"]
    assert audit_judge(evaluation, ["p"], human, ai, category, paper)[0]["paper_judge_complete"]
    paper["p"]["judge_scores"].pop("verifiability")
    with pytest.raises(ValueError, match="missing/invalid"):
        audit_judge(evaluation, ["p"], human, ai, category, paper)


def test_classifier_receipt_preserves_original_accepted_na_normalization(tmp_path, monkeypatch):
    module = _load_standard(COCO, "_3_classify")
    source = {"id": "paper", "sentence_texts": ['"Decision": "Reject"'], "opinions": [[0]]}
    input_path = write_immutable_jsonl(tmp_path / "input.jsonl", [source])
    tasks = {}
    class OriginalClient:
        def submit_task(self, prompt, **kwargs):
            key = str(len(tasks))
            tasks[key] = task(prompt, "</think>None")
            return key
        def get_result(self, key):
            return {"content": tasks[key]["completion"]["choices"][0]["message"]["content"]}
        def shutdown(self):
            pass
    monkeypatch.setattr(module, "client", OriginalClient())
    output = tmp_path / "output.jsonl"
    module.classify_opinions(SimpleNamespace(input_jsonl=str(input_path), output_jsonl=str(output), effort="medium"))
    results = read_jsonl(output)
    assert results[0]["category"] == [["N/A"]]
    # The original implementation accepted three format-valid responses and
    # intentionally normalized no recognized category to N/A.
    assert audit_classify(module, [source], results, tasks)[0]["classified_count"] == 1
