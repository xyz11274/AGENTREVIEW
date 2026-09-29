"""Shared candidate validation must use the declared memory operation budgets."""

import pytest

from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.shared_experiment import (
    CandidateContractError, LocalSharedBackend, binding,
)


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("seed_kind", ["vector_search", "evolved"])
def test_shared_candidate_validation_obeys_memory_call_budget(tmp_path, operation, seed_kind):
    source = seed_program_path("vector_search").read_text()
    start = source.index("    def read(self, query: Query) -> str:")
    if operation == "read":
        source = source[:start] + '''    def read(self, query: Query) -> str:
        first = self.toolkit.llm_completion([{"role": "user", "content": "first"}])
        return self.toolkit.llm_completion([{"role": "user", "content": first}])
'''
    else:
        start = source.index("    def write(self, item: KnowledgeItem, raw_text: str) -> None:")
        source = source[:start] + '''    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        first = self.toolkit.llm_completion([{"role": "user", "content": "first"}])
        self.toolkit.llm_completion([{"role": "user", "content": first}])

    def read(self, query: Query) -> str:
        return "bounded context"
'''
        source = source.replace("from hashlib import sha256\n", "")
    program = tmp_path / "program.py"
    program.write_text(source)
    backend = object.__new__(LocalSharedBackend)
    backend.require_bubblewrap = True
    backend.conditions = {"review_policy_program": binding(seed_program_path("vector_search"))}
    backend.generation = {
        "memory_query_mode": "instruction", "memory_read_llm_calls": 2,
        "memory_build_llm_calls": 2 + int(seed_kind == "evolved"), "memory_max_tokens": 200,
    }
    # This fixture isolates operation call budgets; real tokenizer identity and
    # whole-entry budgets are exercised in test_memory_feedback_budget.py.
    backend.memory_runtime = lambda: {
        "query_mode": backend.generation["memory_query_mode"],
        "read_llm_calls": backend.generation["memory_read_llm_calls"],
        "build_llm_calls": backend.generation["memory_build_llm_calls"],
        "max_tokens": backend.generation["memory_max_tokens"],
    }
    candidate = {"program": binding(program), "seed_kind": seed_kind}
    backend._candidate_check(candidate)
    field = "memory_read_llm_calls" if operation == "read" else "memory_build_llm_calls"
    backend.generation[field] -= 1
    with pytest.raises(CandidateContractError, match="LLM call limit exceeded"):
        backend._candidate_check(candidate)
