"""Exercise the adapter against the upstream CoCo checkout, without model calls."""
from pathlib import Path
import copy
import json
from types import SimpleNamespace
import pytest

from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2 import development_coco_worker as worker

ROOT = Path(__file__).resolve().parents[3] / "CoCoReviewBench"


def test_upstream_source_identity_does_not_require_added_runner():
    assert not (ROOT / "scripts/run_official_candidate.py").exists()
    identity = coco._source_identity(ROOT)
    assert str(ROOT / "src/evaluation/_4_evaluation.py") in {x["path"] for x in identity}


def test_original_client_constructor_and_judge_defaults(tmp_path):
    worker._load_standard(ROOT, "_4_evaluation")
    config = {"services": {"judge": {"base_url": "http://127.0.0.1:18001/v1", "model": "qwen3.8-27b"}}}
    client = worker._client(config, "judge", {"output_root": str(tmp_path)})
    try:
        assert client.max_workers == 128
        _, _, payload = client._prepare_request("review", temperature=0, reasoning_effort="medium")
        assert payload["max_tokens"] == 32768
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    finally:
        client.shutdown()


def test_metric_aliases_preserve_values_counts_and_raw_result():
    raw = {"absolute_scores": {"BERT.": None, "BERT-F1": 30.0, "Correct.": None, "Correctness": 4.0},
           "paper_counts": {"BERT.": 0, "BERT-F1": 2, "Correct.": 0, "Correctness": 1}}
    before = copy.deepcopy(raw)
    normalized = worker.normalize_table(raw)
    assert raw == before
    assert normalized["absolute_scores"]["BERT."] == 30.0
    assert normalized["paper_counts"]["BERT."] == 2
    assert normalized["absolute_scores"]["Correct."] == 4.0
    assert normalized["paper_counts"]["Correct."] == 1
    assert "BERT-F1" not in normalized["absolute_scores"]


def test_frozen_config_rejects_changed_scientific_request_settings():
    from agentreview.research_v2.io import value_sha256
    body = {"protocol": coco.CONFIG_PROTOCOL, "request_settings": copy.deepcopy(coco.ORIGINAL_REQUEST_SETTINGS)}
    body["request_settings"]["judge"]["max_completion_tokens"] = 8192
    with pytest.raises(ValueError, match="upstream request settings"):
        coco._validate_config({**body, "config_id": value_sha256(body)})


def test_bert_cache_resolution_must_match_frozen_model(tmp_path):
    cache = tmp_path / "hub/models--roberta-large"
    (cache / "refs").mkdir(parents=True)
    (cache / "snapshots/revision-a").mkdir(parents=True)
    (cache / "snapshots/revision-b").mkdir()
    (cache / "refs/main").write_text("revision-a")
    config = {"hf_home": str(tmp_path), "bert_score_model": {"root": str(cache / "snapshots/revision-a")}}
    coco._validate_bert_cache(config)
    (cache / "refs/main").write_text("revision-b")
    with pytest.raises(ValueError, match="different frozen model"):
        coco._validate_bert_cache(config)


@pytest.mark.parametrize("stage", ["split", "classify"])
def test_worker_and_original_direct_call_have_identical_requests_and_outputs(tmp_path, monkeypatch, stage):
    module = worker._load_standard(ROOT, "_2_split" if stage == "split" else "_3_classify")
    sentence = "The proof needs a clearer explanation of its assumptions."
    row = {"id": "probe", "text": sentence}
    if stage == "classify":
        row.update(sentence_texts=[sentence], opinions=[[0]])
    source = tmp_path / "candidate.jsonl"
    source.write_text(json.dumps(row) + "\n")

    class Client:
        def __init__(self):
            self.calls = []
        def submit_task(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return len(self.calls) - 1
        def get_result(self, key):
            # Preserve the original N/A priority when a retry later returns a category.
            content = "Part 1: Label 1" if stage == "split" else ("</think>N/A" if key == 0 else "</think>QUAL-MTH")
            return {"status": "done", "content": content}
        def shutdown(self):
            pass

    direct = Client()
    monkeypatch.setattr(module, "client", direct, raising=False)
    direct_output = tmp_path / "direct.jsonl"
    args = SimpleNamespace(input_jsonl=str(source), output_jsonl=str(direct_output), effort="medium")
    (module.split_all if stage == "split" else module.classify_opinions)(args)
    through_adapter = Client()
    monkeypatch.setattr(worker, "_client", lambda *args: through_adapter)
    monkeypatch.setattr(worker, "_previous_stage", lambda *args: source)
    result = worker._split_or_classify({"output_root": str(tmp_path)},
        {"coco_root": str(ROOT), "services": {stage: {"model": stage, "base_url": "http://127.0.0.1/v1"}}},
        stage, tmp_path / "adapted.json")
    assert Path(result["artifact"]["path"]).read_bytes() == direct_output.read_bytes()
    assert through_adapter.calls == direct.calls
    assert direct.calls[0][1]["max_completion_tokens"] == (32768 if stage == "split" else 12288)
    if stage == "classify":
        assert json.loads(direct_output.read_text())["category"] == [["N/A"]]
