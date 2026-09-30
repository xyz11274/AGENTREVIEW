from pathlib import Path
from types import SimpleNamespace

import pytest

from agentreview.research_v2.coco_integrity import audit_split, audit_classify, audit_judge, audited_client_class
from agentreview.research_v2.development_coco_worker import _load_standard
from agentreview.research_v2.io import read_jsonl, write_immutable_jsonl

COCO = Path(__file__).resolve().parents[3] / "CoCoReviewBench_official_2580687"


def task(prompt, response):
    return {"messages": [{"content": prompt}], "completion": {
        "choices": [{"message": {"content": response}, "finish_reason": "stop"}]}}


def test_split_preserves_upstream_missing_part_recovery():
    module = _load_standard(COCO, "_2_split")
    source = {"id": "paper", "text": "First sufficiently long reviewer opinion.\n\nSecond sufficiently long reviewer opinion."}
    texts, prompt, _ = module.build_prompt(source["text"])
    assert len(texts) > 1
    result = {**source, "sentence_texts": texts, "opinions": [list(range(len(texts)))]}
    check = audit_split(module, [source], [result], {"attempt": task(prompt, "Part 1: Label 1")})[0]
    assert check["raw_coverage"] == [1 / len(texts)]
    assert check["output_coverage"] == 1
    assert check["upstream_recovery_used"] is True
    raw = "\n".join(f"Part {i + 1}: Label {i + 1}" for i in range(len(texts)))
    assert audit_split(module, [source], [result], {"attempt": task(prompt, raw)})[0]["raw_coverage"] == [1.0]


def test_split_still_rejects_lost_final_artifact_parts():
    module = _load_standard(COCO, "_2_split")
    source = {"id": "paper", "text": "First sufficiently long reviewer opinion.\n\nSecond sufficiently long reviewer opinion."}
    texts, prompt, _ = module.build_prompt(source["text"])
    assert len(texts) > 1
    result = {**source, "sentence_texts": texts, "opinions": [[0]]}
    raw = "\n".join(f"Part {i + 1}: Label {i + 1}" for i in range(len(texts)))
    with pytest.raises(ValueError, match="artifact lost parts"):
        audit_split(module, [source], [result], {"attempt": task(prompt, raw)})


@pytest.mark.parametrize("invalid", ["N/A", "</think>QUAL-MET QUAL-MET"])
def test_classification_preserves_original_na_fallback(invalid):
    module = _load_standard(COCO, "_3_classify")
    source = {"id": "paper", "sentence_texts": ["A greeting."], "opinions": [[0]]}
    result = {**source, "category": [["N/A"]]}
    _, main = module.classify_reviews_prompt()
    prompt = main.strip() + "\n\n\nReviewer Comment:\nA greeting."
    assert audit_classify(module, [source], [result], {"attempt": task(prompt, invalid)})[0]["na_opinion_count"] == 1
    assert audit_classify(module, [source], [result], {"attempt": task(prompt, "</think>N/A")})[0]["classified_count"] == 1


def test_judge_diagnostics_do_not_override_original_missing_metric_rules():
    evaluation = SimpleNamespace(table_judge_metric_map={"Correctness": "alignment", "Verifiability": "verifiability"})
    human = {"p": {"category_correct": {"QUAL-MET": ["reference"]}}}
    ai = {"p": {"QUAL-MET": ["review"]}}
    paper = {"p": {"judge_scores": {"success": True, "alignment": 4, "verifiability": "X",
                                     "claim_extraction": "normal", "actionability": "X"}}}
    category = {"p": {"category_scores": {}}}
    check = audit_judge(evaluation, ["p"], human, ai, category, paper)[0]
    assert check["required_categories"] == ["QUAL-MET"]
    assert check["completed_categories"] == []

    category["p"]["category_scores"]["QUAL-MET"] = paper["p"]
    assert audit_judge(evaluation, ["p"], human, ai, category, paper)[0]["paper_judge_complete"]
    paper["p"]["judge_scores"].pop("verifiability")
    assert audit_judge(evaluation, ["p"], human, ai, category, paper)[0]["paper_judge_complete"]


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


@pytest.mark.parametrize("finish,content", [("length", "Part 1: Label 1"), ("stop", "")])
def test_transport_preserves_original_response_handling(tmp_path, monkeypatch, finish, content):
    import asyncio
    import importlib
    from openai.types.chat import ChatCompletion

    _load_standard(COCO, "_2_split")
    base = importlib.import_module("src.utils").LLMClient
    response = ChatCompletion(id="fixture", created=0, model="fixture", object="chat.completion",
        choices=[{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}])
    async def complete(self, payload):
        return response
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(base, "_create_completion_async", complete)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    adapted_type = audited_client_class(base)
    monkeypatch.setattr(adapted_type, "_token_budget", lambda *_: {
        "input_tokens": 10, "output_budget": 20, "max_model_len": 100})
    clients = [kind(api_key="fixture", base_url="http://127.0.0.1/v1", model_name="fixture",
                    cache_dir=tmp_path / str(i)) for i, kind in enumerate((base, adapted_type))]
    try:
        messages = [{"role": "user", "content": "review"}]
        payload = {"model": "fixture", "messages": messages, "max_tokens": 20}
        direct, adapted = clients
        expected = asyncio.run(direct.get_response("key", messages, payload))
        # Repeated calls must retain the original retry policy, not a global
        # three-response cap imposed by the recording wrapper.
        for i in range(4):
            assert asyncio.run(adapted.get_response(f"key-{i}", messages, payload)) == expected
        assert adapted.client.max_retries == direct.client.max_retries
        assert adapted.get_result.__func__ is base.get_result
        assert adapted.audit_tasks["key-3"]["completion"]["choices"][0]["finish_reason"] == finish
    finally:
        for client in clients:
            client.shutdown()
