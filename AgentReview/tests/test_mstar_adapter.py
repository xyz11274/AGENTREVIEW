from pathlib import Path
import pytest

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.io import read_json
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.mstar_adapter import MstarReflection, ReflectionFailure, source_identity


RUNTIME = {"build_llm_calls": 2, "read_llm_calls": 2}


class RecordedClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        assert "response_format" not in kwargs
        return next(self.responses)


def patch(old, new):
    return ("*** Commit Message\nUse grounded memory.\n\n*** Begin Patch\n*** Update File: program.py\n@@\n-"
            + old + "\n+" + new + "\n*** End Patch\n")


def runner(tmp_path, client):
    return MstarReflection(client=client, model=ModelConfig(), runtime=RUNTIME, directory=tmp_path)


def test_native_patch_applies_and_passes_real_sandbox(tmp_path):
    source = seed_program_path("vector_search").read_text()
    line = next(x for x in source.splitlines() if "Ground every judgment" in x)
    modified = line.replace("Ground every judgment", "Verify every recalled judgment")
    client = RecordedClient(patch(line, modified))
    adapter = runner(tmp_path, client)
    code, change = adapter.run(code=source, prompt=adapter.prompt(source, 4, 1), iteration=1, score=4)
    assert code == source.replace(line, modified)
    assert change == "Use grounded memory."
    assert client.requests[0][1]["max_tokens"] == ModelConfig().max_output_tokens
    assert read_json(tmp_path / "mstar-trace.json")["events"][-1]["error"] is None
    assert source_identity()


def test_upstream_full_code_fallback_is_retained(tmp_path):
    source = seed_program_path("vector_search").read_text()
    adapter = runner(tmp_path, RecordedClient("```python\n" + source + "\n```"))
    code, _ = adapter.run(code=source, prompt="reflect", iteration=1, score=4)
    assert code.strip() == source.strip()


def test_unknown_api_is_repaired_using_native_error_feedback(tmp_path):
    source = seed_program_path("vector_search").read_text()
    old = "        self.toolkit = toolkit"
    first = "*** Begin Patch\n*** Update File: program.py\n@@\n " + old + "\n+        self.config = toolkit.memory_runtime\n*** End Patch\n"
    fix = "*** Begin Patch\n*** Update File: program.py\n@@\n " + old + "\n-        self.config = toolkit.memory_runtime\n*** End Patch\n"
    client = RecordedClient(first, fix)
    adapter = runner(tmp_path, client)
    code, _ = adapter.run(code=source, prompt="reflect", iteration=1, score=4)
    assert code == source
    assert len(client.requests) == 2
    prompt = client.requests[1][0][-1]["content"]
    assert "Unknown toolkit interface: memory_runtime" in prompt
    assert "Memory toolkit:" in prompt and "llm_completion(messages, **kwargs)" in prompt
    assert "3000" not in prompt


def test_native_format_failure_gets_three_repairs_and_persists_all_responses(tmp_path):
    source = seed_program_path("vector_search").read_text()
    client = RecordedClient(*["No patch here."] * 4)
    adapter = runner(tmp_path, client)
    with pytest.raises(ReflectionFailure):
        adapter.run(code=source, prompt="reflect", iteration=1, score=4)
    trace = read_json(tmp_path / "mstar-trace.json")
    assert trace["max_fix_attempts"] == 3 and len(trace["calls"]) == 4
    assert all(Path(call["path"]).read_text() == "No patch here." for call in trace["calls"])


def test_plain_json_source_is_not_a_supported_reflection_protocol(tmp_path):
    client = RecordedClient(*['{"program":"pass", "change":"invented protocol"}'] * 4)
    adapter = runner(tmp_path, client)
    with pytest.raises(ReflectionFailure):
        adapter.run(code=seed_program_path("vector_search").read_text(), prompt="reflect", iteration=1, score=4)


def test_repeated_file_updates_apply_in_order_in_one_request(tmp_path):
    source = seed_program_path("vector_search").read_text()
    first = 'INSTRUCTION_KNOWLEDGE_ITEM = "Store traceable Review Experience without paper prose."'
    second = 'ALWAYS_ON_KNOWLEDGE = "Ground every judgment in the Frozen Paper."'
    patch_body = ("*** Begin Patch\n*** Update File: program.py\n@@\n-" + first
        + '\n+' + first.replace('Store', 'Keep')
        + "\n*** Update File: program.py\n@@\n-" + second
        + '\n+' + second.replace('Ground', 'Verify') + '\n*** End Patch\n')
    client = RecordedClient(patch_body)
    adapter = runner(tmp_path, client)
    result, _ = adapter.run(code=source, prompt="reflect", iteration=1, score=4)
    assert result == source.replace(first, first.replace('Store', 'Keep')).replace(second, second.replace('Ground', 'Verify'))
    assert len(client.requests) == 1


def test_smoke_exercises_nonempty_role_and_query_before_acceptance(tmp_path):
    from agentreview.research_v2.program_validation import validate_program
    source = seed_program_path("vector_search").read_text()
    path = tmp_path / "missing_field.py"
    path.write_text(source.replace('"topic": item.topic}', '"topic": item.subcategories}'))
    errors = validate_program(path, require_bubblewrap=True, memory_runtime=RUNTIME, seed_kind="evolved")
    assert any('subcategories' in error for error in errors)
    path = tmp_path / "missing_read_field.py"
    path.write_text(source.replace('embedding = self.toolkit.embed([query.query_text])[0]', 'embedding = query.nonexistent_embedding'))
    errors = validate_program(path, require_bubblewrap=True, memory_runtime=RUNTIME, seed_kind="evolved")
    assert any('nonexistent_embedding' in error for error in errors)
