"""Exercise the paused pilot's host -> memory completion -> review boundary."""
from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.shared_experiment import APISharedBackend
from agentreview.research_v2.llm import ToolkitCompletionAdapter
from agentreview.reviewer_runtime.toolkit import MemoryToolkit


def memory_runtime():
    backend = APISharedBackend.__new__(APISharedBackend)
    backend.model = ModelConfig()
    backend.generation = {
        "memory_query_mode": "instruction", "memory_read_llm_calls": 2,
        "memory_build_llm_calls": 2, "memory_max_tokens": 4096,
        "input_max_tokens": 1032192,
    }
    return backend.memory_runtime(), backend.model


def test_memory_completion_can_finish_beyond_old_4096_token_ceiling():
    runtime, model = memory_runtime()
    calls = []

    class RecordedEndpoint:
        def complete(self, messages, *, max_tokens, component, response_format=None):
            calls.append(max_tokens)
            assert max_tokens >= 6000, "The 4096-token pilot failure remains reproducible"
            return "complete summary with its final correction"

    adapter = ToolkitCompletionAdapter(RecordedEndpoint(), runtime["max_tokens"], "memory_summarizer")
    assert adapter.llm_completion([{"role": "user", "content": "summarize retrieved experiences"}]).endswith("correction")
    assert calls == [model.max_output_tokens]


def test_complete_memory_above_4096_reaches_review_context():
    runtime, model = memory_runtime()
    toolkit = MemoryToolkit(memory_token_counter=lambda text: len(text.split()),
                            memory_context_tokens=runtime["context_max_tokens"])
    context = "evidence " * 5000 + "FINAL_CORRECTION"
    toolkit.validate_memory_context(context)
    assert toolkit.select_memory_entries([context]) == [context]
    assert runtime["context_max_tokens"] < model.max_model_len
    assert runtime["context_max_tokens"] > model.max_output_tokens


def test_long_input_uses_remaining_context_and_records_actual_output_allowance(tmp_path):
    from agentreview.research_v2.experiment_requests import PersistentCompletionClient
    from agentreview.research_v2.io import read_json

    class Endpoint:
        def count_chat_tokens(self, messages):
            return {"input_tokens": 95, "max_model_len": 100}

        def complete(self, messages, *, max_tokens, **kwargs):
            assert messages[0]["content"].endswith("COMPLETE_INPUT_TAIL")
            assert max_tokens == 5
            return "done"

    client = PersistentCompletionClient(Endpoint(), tmp_path, identity={}, max_attempts=1)
    assert client.complete([{"role": "user", "content": "COMPLETE_INPUT_TAIL"}],
                           max_tokens=20, component="review") == "done"
    result = read_json(next(tmp_path.glob('*/response.json')))
    assert result["effective_max_tokens"] == 5


def test_model_context_exhaustion_stays_explicit():
    import pytest
    from agentreview.research_v2.token_accounting import available_output_tokens
    with pytest.raises(ValueError, match="never truncated"):
        available_output_tokens({"input_tokens": 100, "max_model_len": 100}, 20)
