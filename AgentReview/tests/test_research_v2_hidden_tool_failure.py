"""A candidate cannot turn a failed host capability into valid memory."""

import pytest

from agentreview.research_v2.experiment_requests import ExperimentRequestFailure
from agentreview.research_v2.failure_policy import failure_detail
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.program_checks import static_check
from agentreview.reviewer_runtime.sandbox import SandboxError, SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit


def program(tmp_path, statement):
    source = seed_program_path("vector_search").read_text()
    source = source[:source.index("    def read(self, query: Query) -> str:")] + f'''    def read(self, query: Query) -> str:
        alias = self.toolkit
        try:
            {statement}
        except Exception:
            return "silently substituted memory"
        return "success"
'''
    target = tmp_path / "program.py"
    target.write_text(source)
    return target


def test_static_rejects_unsupported_toolkit_interface(tmp_path):
    target = program(tmp_path, 'self.toolkit.llm("summarize", max_tokens=500)')
    assert any("Unknown toolkit interface: llm" in error for error in static_check(target.read_text()))


@pytest.mark.parametrize("statement,expected_category", [
    ('alias.llm("summarize")', "candidate"),
    ('alias.llm_completion([{"role": "user", "content": "summarize"}])', "shared_runtime"),
    ('alias.embed(["memory"])', "shared_runtime"),
])
def test_caught_capability_failure_still_aborts_sandbox(tmp_path, statement, expected_category):
    class FailedModel:
        def llm_completion(self, *args, **kwargs):
            raise ExperimentRequestFailure("finite retries exhausted: truncated completion")

    toolkit = MemoryToolkit(llm_client=FailedModel())
    def failed_embed(*args, **kwargs):
        raise ExperimentRequestFailure("embedding service failed")
    toolkit.embed = failed_embed
    target = program(tmp_path, statement)
    with SandboxSession(str(target), toolkit, require_bubblewrap=True) as sandbox:
        query = sandbox.program_interface().Query(query_text="test", reviewer_dimension="QUAL")
        with pytest.raises(SandboxError) as raised:
            sandbox.read(query)
        assert failure_detail(raised.value, phase="shared_candidate_run")["category"] == expected_category
        # Catching the host exception must not let the same session publish state.
        with pytest.raises(SandboxError):
            sandbox.snapshot()
