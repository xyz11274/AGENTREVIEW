"""Candidate validation must preserve infrastructure failure scope."""
from pathlib import Path
import pytest
from openai import APIConnectionError
import httpx
from agentreview.research_v2 import program_validation
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.failure_policy import failure_detail
from agentreview.reviewer_runtime.sandbox import SandboxError

def _connection_error():
    return APIConnectionError(request=httpx.Request("POST", "http://test.invalid/v1"))


def _bubblewrap_error():
    return SandboxError("bubblewrap was required but this host does not permit its namespaces")


@pytest.mark.parametrize("error_factory,category", [
    (_connection_error, "shared_runtime"),
    (_bubblewrap_error, "shared_runtime"),
    (lambda: FileNotFoundError("Frozen resource is missing"), "shared_data"),
    (lambda: PermissionError("Read access requires restoration"), "shared_runtime"),
    (lambda: OSError("No space left on device"), "shared_runtime"),
])
def test_validation_preserves_shared_exception(error_factory, category, monkeypatch):
    error = error_factory()

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(program_validation, "SandboxSession", unavailable)
    with pytest.raises(type(error)) as raised:
        program_validation.validate_program(seed_program_path("vector_search"), require_bubblewrap=True)
    assert raised.value is error
    assert failure_detail(error, phase="candidate_validation")["category"] == category
