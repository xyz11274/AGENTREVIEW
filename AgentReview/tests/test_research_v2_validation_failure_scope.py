"""Candidate validation preserves shared faults and resumable repair identity."""

from dataclasses import dataclass
from threading import Lock
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from agentreview.research_v2 import evolution
from agentreview.research_v2.failure_policy import FailureController, failure_detail
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json
from agentreview.research_v2.memory import seed_program_path
from agentreview.reviewer_runtime.sandbox import SandboxError, SandboxSession


def _patch(guidance):
    return f'''*** Commit Message
Title: Strengthen evidence guidance
- Bind judgments to the paper evidence
*** Begin Patch
*** Update File: program.py
@@
-ALWAYS_ON_KNOWLEDGE = "Ground every judgment in the Frozen Paper."
+ALWAYS_ON_KNOWLEDGE = "{guidance}"
*** End Patch'''


INITIAL_PATCH = _patch("Ground every major judgment in the Frozen Paper.")
REPAIR_PATCH = _patch("Ground every judgment in explicit Frozen Paper evidence.")


class ScriptedCompletion:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, *, max_tokens, component):
        self.calls.append({"messages": messages, "component": component})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@dataclass
class SmokeRecord:
    text: str


class HealthySandbox:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def program_interface(self):
        return SimpleNamespace(KnowledgeItem=SmokeRecord, Query=SmokeRecord)

    def write(self, item, experience):
        pass

    def read(self, query):
        return "Evidence from the paper."

    def plan_review(self, query, result):
        return {"s2_disposition": "not_consulted", "selected_s2_ids": []}


def _connection_error():
    return APIConnectionError(request=httpx.Request("POST", "http://test.invalid/v1"))


def _bubblewrap_error():
    return SandboxError("bubblewrap was required but this host does not permit its namespaces")


def _candidate_error():
    return SandboxError("plan_review() must return a dict")


@pytest.fixture
def proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution, "SandboxSession", HealthySandbox)
    program = seed_program_path("vector_search")
    parent = evolution.PoolEntry(
        candidate_id="seed-vector", role="QUAL", program_path=str(program),
        program_sha256=file_sha256(program), parent_id=None, static_score=3.0,
        seed_kind="vector_search",
    )
    review_path = tmp_path / "review.json"
    evaluation_path = tmp_path / "evaluation.json"
    atomic_write_json(review_path, {
        "review_id": "review-1", "paper_id": "paper-1", "role": "QUAL",
        "review_text": "The experiment needs a controlled ablation.",
        "retrieval_provenance": {"memory": {}, "s2": {}},
    })
    atomic_write_json(evaluation_path, {
        "review_id": "review-1", "paper_id": "paper-1", "role": "QUAL",
        "review_quality": 3.0, "major_missing_points": ["controlled ablation"],
        "factual_evidence_issues": [], "improvement_suggestions": [],
    })
    controller = FailureController(tmp_path / "failure-policy.json")

    def reserve(candidate_id):
        assert controller.reserve_repair(
            operation_id=f"candidate-repair:{candidate_id}", candidate_id=candidate_id,
        )

    arguments = dict(
        role="QUAL", pool=evolution.EvolutionPool("QUAL", [parent]),
        evaluation_paths=[evaluation_path], review_paths=[review_path], attempt=1,
        run_id="scope-test", output_root=tmp_path, require_bubblewrap=True,
        before_repair=reserve,
    )
    return SimpleNamespace(
        arguments=arguments, root=tmp_path, controller=controller,
        directory=tmp_path / "evolution" / "QUAL" / "scope-test",
    )


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

    monkeypatch.setattr(evolution, "SandboxSession", unavailable)
    with pytest.raises(type(error)) as raised:
        evolution.validate_program(seed_program_path("vector_search"), require_bubblewrap=True)
    assert raised.value is error
    assert failure_detail(error, phase="candidate_validation")["category"] == category


def test_sandbox_candidate_contract_receives_one_repair(proposal, monkeypatch):
    sessions = [_candidate_error(), HealthySandbox()]

    def sandbox(*args, **kwargs):
        result = sessions.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(evolution, "SandboxSession", sandbox)
    client = ScriptedCompletion(INITIAL_PATCH, REPAIR_PATCH)
    outcome = evolution.propose_candidate(**proposal.arguments, completion_client=client)
    assert outcome["status"] == "proposed"
    assert outcome["repair_used"] is True
    assert outcome["errors"] == []
    assert [call["component"] for call in client.calls] == ["reflector", "reflector_repair"]
    policy = read_json(proposal.controller.path)
    assert policy["repair_budget"]["used"] == 1
    assert policy["candidate_repairs"] == {outcome["candidate_id"]: 1}
    assert policy["candidate_failure_budget"]["used"] == 0


@pytest.mark.parametrize("stage", ["completion", "program_write", "validation"])
def test_public_proposal_fault_preserves_candidate_repair_budget(proposal, monkeypatch, stage):
    error = {
        "completion": _connection_error(), "program_write": OSError("No space left on device"),
        "validation": _bubblewrap_error(),
    }[stage]

    def unavailable(*args, **kwargs):
        raise error

    client = ScriptedCompletion(error if stage == "completion" else INITIAL_PATCH)
    if stage == "program_write":
        monkeypatch.setattr(evolution, "_write_program", unavailable)
    elif stage == "validation":
        monkeypatch.setattr(evolution, "SandboxSession", unavailable)
    with pytest.raises(type(error)) as raised:
        evolution.propose_candidate(**proposal.arguments, completion_client=client)
    assert raised.value is error
    policy = read_json(proposal.controller.path)
    assert policy["repair_budget"]["used"] == 0
    assert policy["candidate_repairs"] == {}
    assert policy["candidate_failure_budget"]["used"] == 0
    assert list(proposal.directory.glob("*.repair-request.json")) == []
    assert (proposal.directory / "attempt-001.json").is_file() is False
    assert [call["component"] for call in client.calls] == ["reflector"]


@pytest.mark.parametrize("error,category", [
    (FileNotFoundError("Frozen retrieval pool is missing"), "shared_data"),
    (PermissionError("Frozen retrieval pool access requires restoration"), "shared_runtime"),
    (OSError("No space left on device"), "shared_runtime"),
    (OSError("Read-only file system"), "shared_runtime"),
])
def test_sandbox_wrapped_shared_filesystem_fault_retains_scope(proposal, monkeypatch, error, category):
    """Exercise the real tool error serializer and SandboxError wrapper over fake transport."""
    responses = []

    def unavailable(*args, **kwargs):
        raise error

    transport = SandboxSession.__new__(SandboxSession)
    transport._lock = Lock()
    transport._command_id = 0
    transport.toolkit = SimpleNamespace(
        require_capability=lambda name: None, llm_completion=unavailable,
    )
    transport._send = responses.append

    def read_line():
        if len(responses) == 1:
            return {
                "type": "tool_call", "id": 1, "name": "llm_completion",
                "args": ["Summarize the frozen evidence"], "kwargs": {},
            }
        response = responses[-1]
        return {
            "type": "result", "id": 1, "ok": False,
            "error": f"RuntimeError: {response['error']}",
        }

    transport._read_line = read_line

    class WrappedFailureSandbox(HealthySandbox):
        def read(self, query):
            return transport.read(query)

    monkeypatch.setattr(evolution, "SandboxSession", WrappedFailureSandbox)
    client = ScriptedCompletion(INITIAL_PATCH)
    with pytest.raises(SandboxError) as raised:
        evolution.propose_candidate(**proposal.arguments, completion_client=client)
    assert f"{type(error).__name__}: {error}" in str(raised.value)
    assert responses[-1]["type"] == "tool_result"
    assert responses[-1]["ok"] is False
    assert failure_detail(raised.value, phase="candidate_validation")["category"] == category
    policy = read_json(proposal.controller.path)
    assert policy["repair_budget"]["used"] == 0
    assert policy["candidate_repairs"] == {}
    assert policy["candidate_failure_budget"]["used"] == 0
    assert (proposal.directory / "attempt-001.json").is_file() is False
    assert [call["component"] for call in client.calls] == ["reflector"]


@pytest.mark.parametrize("stage", ["completion", "program_write", "validation"])
def test_repair_resumes_same_request_and_budget_after_shared_fault(proposal, monkeypatch, stage):
    shared_error = {
        "completion": _connection_error(),
        "program_write": OSError("No space left on device"),
        "validation": _bubblewrap_error(),
    }[stage]
    sessions = [_candidate_error()]
    if stage == "validation":
        sessions.append(shared_error)

    def sandbox(*args, **kwargs):
        result = sessions.pop(0)
        raise result

    original_write = evolution._write_program
    write_count = 0

    def write_program(path, code):
        nonlocal write_count
        write_count += 1
        if stage == "program_write" and write_count == 2:
            raise shared_error
        return original_write(path, code)

    monkeypatch.setattr(evolution, "SandboxSession", sandbox)
    monkeypatch.setattr(evolution, "_write_program", write_program)
    first = ScriptedCompletion(INITIAL_PATCH, shared_error if stage == "completion" else REPAIR_PATCH)
    with pytest.raises(type(shared_error)):
        evolution.propose_candidate(**proposal.arguments, completion_client=first)
    request_path = proposal.directory / "attempt-001.repair-request.json"
    request_hash = file_sha256(request_path)
    assert read_json(proposal.controller.path)["repair_budget"]["used"] == 1
    assert (proposal.directory / "attempt-001.json").is_file() is False

    monkeypatch.setattr(evolution, "SandboxSession", HealthySandbox)
    monkeypatch.setattr(evolution, "_write_program", original_write)
    resumed = ScriptedCompletion(REPAIR_PATCH) if stage == "completion" else ScriptedCompletion()
    outcome = evolution.propose_candidate(**proposal.arguments, completion_client=resumed)
    assert outcome["status"] == "proposed"
    assert outcome["repair_used"] is True
    assert file_sha256(request_path) == request_hash
    assert [call["component"] for call in resumed.calls] == (["reflector_repair"] if stage == "completion" else [])
    if stage == "completion":
        assert resumed.calls[0]["messages"] == first.calls[1]["messages"]
    policy = read_json(proposal.controller.path)
    assert policy["repair_budget"]["used"] == 1
    assert policy["candidate_repairs"] == {outcome["candidate_id"]: 1}
    assert policy["candidate_failure_budget"]["used"] == 0
    replay = ScriptedCompletion()
    assert evolution.propose_candidate(**proposal.arguments, completion_client=replay) == outcome
    assert replay.calls == []
    assert read_json(proposal.controller.path)["repair_budget"]["used"] == 1


def test_new_epoch_preserves_interrupted_proposal_and_uses_fresh_feedback(proposal, monkeypatch):
    def unavailable(*args, **kwargs):
        raise _bubblewrap_error()

    monkeypatch.setattr(evolution, "SandboxSession", unavailable)
    with pytest.raises(SandboxError):
        evolution.propose_candidate(
            **proposal.arguments, completion_client=ScriptedCompletion(INITIAL_PATCH),
        )
    old_plan = read_json(proposal.directory / "attempt-001.plan.json")
    old_raw = proposal.directory / "attempt-001.reflector.json"
    old_program = proposal.root / "programs" / "QUAL" / old_plan["candidate_id"] / "program.py"
    old_hashes = file_sha256(old_raw), file_sha256(old_program)
    evaluation_path = proposal.arguments["evaluation_paths"][0]
    feedback = read_json(evaluation_path)
    feedback["major_missing_points"] = ["fresh epoch evidence"]
    atomic_write_json(evaluation_path, feedback, immutable=False)
    monkeypatch.setattr(evolution, "SandboxSession", HealthySandbox)
    client = ScriptedCompletion(REPAIR_PATCH)
    outcome = evolution.propose_candidate(
        **proposal.arguments, completion_client=client, evaluation_epoch=1,
        selected_parent_id=old_plan["parent_id"],
    )
    assert outcome["status"] == "proposed"
    assert outcome["candidate_id"] != old_plan["candidate_id"]
    assert "fresh epoch evidence" in client.calls[0]["messages"][0]["content"]
    assert (file_sha256(old_raw), file_sha256(old_program)) == old_hashes
    assert (proposal.directory / "attempt-001.e001.reflector.json").is_file()
    assert read_json(proposal.controller.path)["repair_budget"]["used"] == 0
