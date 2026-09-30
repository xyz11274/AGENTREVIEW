import pytest

from agentreview.research_v2.experiment_requests import PersistentCompletionClient, ExperimentRequestFailure


class Client:
    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.calls = 0
    def count_chat_tokens(self, messages):
        return {"input_tokens": 20, "max_model_len": 100}
    def complete(self, *args, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "complete review"


def wrapped(tmp_path, client):
    return PersistentCompletionClient(client, tmp_path, identity={"model": "qwen"}, max_attempts=3)


def invoke(client):
    return client.complete([{"role": "user", "content": "paper"}], max_tokens=10, component="review")


def test_transient_retries_and_raw_resume(tmp_path):
    client = Client([TimeoutError("temporary")])
    assert invoke(wrapped(tmp_path, client)) == "complete review"
    assert client.calls == 2
    assert invoke(wrapped(tmp_path, client)) == "complete review"
    assert client.calls == 2


def test_deterministic_repetition_stops_and_resume_has_zero_calls(tmp_path):
    client = Client([ValueError("same")] * 3)
    with pytest.raises(ExperimentRequestFailure):
        invoke(wrapped(tmp_path, client))
    assert client.calls == 2
    with pytest.raises(ExperimentRequestFailure):
        invoke(wrapped(tmp_path, client))
    assert client.calls == 2


def test_full_input_budget_failure_is_explicit_without_generation(tmp_path):
    client = Client()
    client.count_chat_tokens = lambda messages: {"input_tokens": 100, "max_model_len": 100}
    with pytest.raises(ExperimentRequestFailure):
        invoke(wrapped(tmp_path, client))
    assert client.calls == 0


def test_interruption_after_repeated_error_preserves_stop_on_resume(tmp_path, monkeypatch):
    from agentreview.research_v2 import experiment_requests as module
    original = module.atomic_write_json
    def interrupted(path, value, **kwargs):
        if path.name == "failure.json":
            raise OSError("interrupted final failure write")
        return original(path, value, **kwargs)
    client = Client([ValueError("same")] * 3)
    monkeypatch.setattr(module, "atomic_write_json", interrupted)
    with pytest.raises(OSError):
        invoke(wrapped(tmp_path, client))
    assert client.calls == 2
    monkeypatch.setattr(module, "atomic_write_json", original)
    with pytest.raises(ExperimentRequestFailure):
        invoke(wrapped(tmp_path, client))
    assert client.calls == 2
