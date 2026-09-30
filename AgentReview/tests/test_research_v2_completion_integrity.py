"""Incomplete model text must never become a successful persisted generation."""

from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionChunk
from contextlib import nullcontext

from agentreview.research_v2.experiment_requests import PersistentCompletionClient, ExperimentRequestFailure
from agentreview.research_v2.io import read_json
from agentreview.research_v2.llm import OpenAICompletionClient
from agentreview.research_v2.config import ModelConfig


def endpoint(reason):
    response = ChatCompletionChunk.model_validate({
        "id": "completion-test", "object": "chat.completion.chunk", "created": 0,
        "model": "test", "choices": [{"index": 0, "finish_reason": reason,
            "delta": {"role": "assistant", "content": "partial but nonempty"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    })
    client = OpenAICompletionClient.__new__(OpenAICompletionClient)
    client.config = ModelConfig(model="test", response_model="test", enable_thinking=False)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: nullcontext(iter([response])))))
    client.count_chat_tokens = lambda messages: {"input_tokens": 5, "max_model_len": 100}
    return client


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls"])
def test_incomplete_text_is_not_returned(reason):
    with pytest.raises(RuntimeError, match="finish_reason"):
        endpoint(reason).complete([{"role": "user", "content": "test"}], max_tokens=10, component="role")


def test_finite_truncation_failures_preserve_raw_and_never_cache_success(tmp_path):
    client = PersistentCompletionClient(endpoint("length"), tmp_path, identity={}, max_attempts=2)
    with pytest.raises(ExperimentRequestFailure):
        client.complete([{"role": "user", "content": "test"}], max_tokens=10, component="role")
    assert not list(tmp_path.rglob("response.json"))
    errors = list(tmp_path.rglob("attempt-*.error.json"))
    assert len(errors) == 2
    for error in errors:
        assert read_json(error)["completion"]["choices"][0]["finish_reason"] == "length"
    assert len(list(tmp_path.rglob("failure.json"))) == 1


def test_complete_response_retains_usage_and_finish_reason(tmp_path):
    client = PersistentCompletionClient(endpoint("stop"), tmp_path, identity={}, max_attempts=2)
    assert client.complete([], max_tokens=10, component="role") == "partial but nonempty"
    result = read_json(next(tmp_path.rglob("response.json")))
    assert result["completion"]["choices"][0]["finish_reason"] == "stop"
    assert result["completion"]["usage"]["completion_tokens"] == 10
