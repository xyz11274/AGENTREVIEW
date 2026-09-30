"""API boundary regressions: authentication, payloads, budgets and replay."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.llm import OpenAICompletionClient, completion_payload
from agentreview.research_v2.token_accounting import count_chat_tokens, text_tokenizer
from agentreview.research_v2.retry import retry_delay
from agentreview.research_v2.request_admission import profile, slot_for
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json
from agentreview.research_v2.experiment_requests import PersistentCompletionClient, ExperimentRequestFailure


def test_missing_credential_fails_before_sdk_construction(monkeypatch):
    import openai
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: pytest.fail("Must not create SDK without a key"))
    with pytest.raises(ValueError, match="ARK_API_KEY"):
        OpenAICompletionClient(ModelConfig())


def test_api_payload_uses_ark_thinking_and_preserves_json_mode():
    config = ModelConfig()
    messages = [{"role": "user", "content": 'Return JSON {"ok":true}'}]
    payload = completion_payload(config, messages, max_tokens=8192, response_format={"type": "json_object"})
    assert payload == {"model": config.model, "messages": messages, "temperature": 0.0,
                       "stream": True, "stream_options": {"include_usage": True},
                       "max_tokens": 8192, "extra_body": {"thinking": {"type": "disabled"}},
                       "response_format": {"type": "json_object"}}
    judge = completion_payload(replace(config, enable_thinking=True), messages, max_tokens=32768)
    assert judge["extra_body"] == {"thinking": {"type": "enabled"}}
    assert judge["reasoning_effort"] == "high"
    with pytest.raises(ValueError, match="output capacity"):
        completion_payload(config, messages, max_tokens=config.max_output_tokens + 1)


def test_model_alias_cannot_silently_route_to_another_version():
    from agentreview.research_v2.llm import validate_completion, IncompleteCompletionOutput
    from openai.types.chat import ChatCompletion
    response = ChatCompletion.model_validate({
        "id": "wrong-model", "object": "chat.completion", "created": 0,
        "model": "another-model", "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": "apparently valid response"}}]})
    with pytest.raises(IncompleteCompletionOutput, match="expected"):
        validate_completion(response, component="review", expected_model=ModelConfig().response_model)


def test_coco_cache_cannot_bypass_response_model_validation(tmp_path):
    from agentreview.research_v2.coco_integrity import audited_client_class

    class Base:
        def __init__(self):
            self.client = SimpleNamespace(max_retries=0)
            self.model_name = ModelConfig().response_model
            self.cache_dir = tmp_path

        def _cache_load(self, key):
            return "review", []

    client = audited_client_class(Base)()
    atomic_write_json(tmp_path / "key.audit.json", {"status": "complete", "completion": {
        "model": "unexpected-model", "choices": [{"finish_reason": "stop", "message": {"content": "review"}}]}})
    with pytest.raises(ValueError, match="receipt"):
        client._cache_load("key")


def test_removed_local_model_configuration_is_not_accepted():
    with pytest.raises(TypeError):
        ModelConfig(vllm_version="legacy")
    with pytest.raises(TypeError):
        ModelConfig(source_model="Qwen/Qwen3.8-27B")


def test_provider_is_part_of_cache_identity_and_credentials_are_not():
    config = ModelConfig()
    assert config.identity() != replace(config, base_url="https://another.example/api/v3").identity()
    assert config.identity() == replace(config, api_key_env="ANOTHER_KEY").identity()
    assert config.identity() != replace(config, enable_thinking=True).identity()
    assert "api_key_env" not in config.identity()


def test_tokenizer_counts_full_paper_tail_without_network(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Offline tokenization"))
    config = ModelConfig()
    text = "methods 中文结果\n" * 5000
    first = count_chat_tokens([{"role": "user", "content": text}], config)
    second = count_chat_tokens([{"role": "user", "content": text + " APPENDIX_NEW_EVIDENCE"}], config)
    assert second["input_tokens"] > first["input_tokens"] > 10000
    assert first["method"] == "deepseek-v4.1-local-estimate"
    assert len(text_tokenizer().encode("x" * 5000, add_special_tokens=False).ids) < 5000


def test_retry_after_is_respected():
    exc = RuntimeError("rate limited")
    exc.response = SimpleNamespace(headers={"retry-after": "90"})
    assert retry_delay(exc, 1) == 90


def test_shared_cloud_admission_has_no_port_or_two_worker_restriction(tmp_path, monkeypatch):
    endpoint = ModelConfig().base_url
    path = atomic_write_json(tmp_path / "execution.json", {
        "protocol": "shared-api-execution-v2", "paper_workers": 5,
        "admission_root": str(tmp_path / "admission"), "limits": {endpoint: 3}})
    monkeypatch.setenv("AGENTREVIEW_EXECUTION_PROFILE", str(path))
    monkeypatch.setenv("AGENTREVIEW_EXECUTION_PROFILE_SHA256", file_sha256(path))
    assert profile()["paper_workers"] == 5
    slots = [slot_for(endpoint) for _ in range(4)]
    try:
        assert all(s.try_acquire() for s in slots[:3])
        assert not slots[3].try_acquire()
        slots[0].close()
        assert slots[3].try_acquire()
    finally:
        for slot in slots:
            slot.close()
    path.write_text("{}")
    with pytest.raises(ValueError, match="frozen binding"):
        slot_for(endpoint)


def test_corrupt_cached_output_cannot_be_replayed(tmp_path):
    class Client:
        def count_chat_tokens(self, messages):
            return {"input_tokens": 2, "max_model_len": 100}

        def complete(self, *args, **kwargs):
            return "complete response"
    client = PersistentCompletionClient(Client(), tmp_path, identity=ModelConfig().identity(),
                                        max_attempts=2)
    client.complete([], max_tokens=10, component="test")
    path = next(tmp_path.rglob("response.json"))
    result = read_json(path)
    result["raw_model_output"] = "tampered"
    atomic_write_json(path, result, immutable=False)
    with pytest.raises(ExperimentRequestFailure, match="integrity"):
        client.complete([], max_tokens=10, component="test")


@pytest.mark.parametrize("stage", ["split", "classify", "evaluation"])
def test_only_judge_worker_receives_cloud_credential(tmp_path, monkeypatch, stage):
    from agentreview.research_v2 import development_coco as coco
    monkeypatch.setenv("ARK_API_KEY", "fixture-secret-must-not-be-persisted")
    request = atomic_write_json(tmp_path / "request.json", {"identity": "test"})
    config = {"max_stage_attempts": 1, "timeout_seconds": 10, "python_executable": "/test/python",
              "services": {"judge": ModelConfig(enable_thinking=True).protocol_config()}}
    observed = {}

    def launch(command, **kwargs):
        observed.update(kwargs["env"])
        atomic_write_json(command[-1], {"status": "complete"})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(coco.subprocess, "run", launch)
    coco._run_stage(stage=stage, request_path=request, output=tmp_path, config=config)
    assert ("ARK_API_KEY" in observed) == (stage == "evaluation")
    for path in tmp_path.rglob("*.json"):
        assert "fixture-secret-must-not-be-persisted" not in path.read_text()
