"""Static and sandbox validation of candidate memory programs."""
from __future__ import annotations
from .execution_telemetry import timed
from pathlib import Path
from typing import Any
from agentreview.reviewer_runtime.sandbox import SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit
from .io import file_sha256
from .program_checks import REVIEW_PLAN_PROTOCOL, program_capability_manifest, static_check

def _program_capabilities(program_path: str | Path) -> dict[str, Any]:
    """Return the verified candidate-visible capability identity for one Program."""
    source = Path(program_path).expanduser().resolve()
    errors = static_check(source.read_text(encoding="utf-8"))
    if errors:
        raise ValueError(f"Program capability static check failed: {errors}")
    manifest = dict(program_capability_manifest(path=source))
    required = {
        "interface_version",
        "program_sha256",
        "policy_ast_sha256",
        "memory_ast_sha256",
    }
    missing = sorted(required - set(manifest))
    unexpected = sorted(set(manifest) - required)
    if missing or unexpected:
        raise ValueError(
            "Program capability manifest fields differ from the formal protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if manifest["interface_version"] != REVIEW_PLAN_PROTOCOL:
        raise ValueError(
            "Program capability interface differs from the formal Review Plan protocol"
        )
    if manifest["program_sha256"] != file_sha256(source):
        raise ValueError("Program capability manifest source hash mismatch")
    return manifest

def _sample_dataclass(dataclass_type, *, role="QUAL", sample=0):
    from dataclasses import MISSING, fields

    values = {}
    for item in fields(dataclass_type):
        if item.default is not MISSING or item.default_factory is not MISSING:
            continue
        name = getattr(item.type, "__name__", str(item.type)).lower()
        if "list" in name:
            values[item.name] = []
        elif "bool" in name:
            values[item.name] = False
        elif "int" in name:
            values[item.name] = 1
        elif "float" in name:
            values[item.name] = 1.0
        else:
            values[item.name] = "sample"
    # Empty defaults otherwise skip the role-filtered write and query paths,
    # accepting programs which fail on the first real review experience.
    review_sample = {
        "topic": f"Protocol smoke paper {sample}",
        "content": f"traceable Review Experience {sample}",
        "reviewer_name": role, "reviewer_dimension": role,
        "query_text": f"Protocol smoke paper {sample}: methodology and evidence",
    }
    values.update({field.name: review_sample[field.name] for field in fields(dataclass_type)
                   if field.name in review_sample})
    return dataclass_type(**values)

@timed('program_validation')
def validate_program(
    path: str | Path,
    *,
    require_bubblewrap: bool,
    memory_runtime: dict[str, Any] | None = None,
    seed_kind: str | None = None,
) -> list[str]:
    from agentreview.reviewer_runtime.sandbox import SandboxError
    from agentreview.reviewer_runtime.memory_budget import budget_options, MemoryBudgetError
    from .failure_policy import failure_detail

    source = Path(path).expanduser().resolve()
    errors = static_check(source.read_text(encoding="utf-8"))
    if errors:
        return errors
    _program_capabilities(source)
    runtime = memory_runtime or {}
    build_calls = runtime.get("build_llm_calls", 1)
    read_calls = runtime.get("read_llm_calls", 1)
    if any(type(value) is not int or value < 0 for value in (build_calls, read_calls)):
        raise ValueError("Candidate validation requires nonnegative memory call budgets")
    # The build budget also pays for host KnowledgeItem induction for learned
    # and evolved programs; only the remainder is available inside KB.write.
    induction_calls = int(seed_kind is not None and seed_kind not in {
        "no_memory", "vector_search", "llm_summarizer",
    })
    write_calls = max(0, build_calls - induction_calls)

    class SmokeLLM:
        def llm_completion(self, messages, **kwargs):
            return "smoke summary"

    toolkit_options = dict(
        llm_client=SmokeLLM(),
        **budget_options(runtime),
        max_llm_calls_per_operation=max(write_calls, read_calls),
        s2_retrieval_pool={
            "protocol-smoke-paper": [
                {"paperId": "protocol-smoke-s2", "title": "Frozen smoke evidence"}
            ]
        },
    )
    def lifecycle(role):
        toolkit = MemoryToolkit(**toolkit_options)
        nonlocal phase
        phase = "load"
        with SandboxSession(
            str(source),
            toolkit,
            timeout_seconds=10,
            require_bubblewrap=require_bubblewrap,
        ) as sandbox:
            program = sandbox.program_interface()
            # Exercise deduplication and state changes on a second distinct write.
            # The validator requires replay equivalence, not a particular retrieval strategy.
            for index in (0, 0, 1):
                item = _sample_dataclass(program.KnowledgeItem, role=role, sample=index)
                phase = f"write {index}"
                with toolkit.operation("protocol_smoke_write", max_llm_calls=write_calls):
                    sandbox.write(item, f"traceable Review Experience {index}")
                phase = "snapshot"
                snapshot = sandbox.snapshot()
            query = _sample_dataclass(program.Query, role=role)
            phase = "read"
            with toolkit.operation("protocol_smoke_read", max_llm_calls=read_calls):
                result = sandbox.read(query)
            if not isinstance(result, str):
                errors.append("KnowledgeBase.read() did not return str")
            else:
                toolkit.validate_memory_context(result)
            with toolkit.paper_scope("protocol-smoke-paper"):
                with toolkit.operation(
                    "review_plan",
                    max_llm_calls=0,
                    allowed_capabilities={"retrieve_current_s2"},
                ):
                    plan = sandbox.plan_review(query, result)
                trace = toolkit.s2_trace_snapshot()
            calls = list(trace.get("calls") or [])
            selected_ids = list(plan.get("selected_s2_ids") or [])
            candidate_ids = [
                item
                for call in calls
                for item in list(call.get("result_ids") or [])
            ]
            expected_disposition = (
                "not_consulted"
                if not calls
                else "use"
                if selected_ids
                else "consulted_no_use"
            )
            if plan.get("s2_disposition") != expected_disposition:
                errors.append("plan_review disposition disagrees with capability trace")
            if not set(selected_ids).issubset(candidate_ids):
                errors.append("plan_review selected an ID absent from capability results")
            after_read = sandbox.snapshot()
        # Neither the process nor its host toolkit state may survive the restart.
        phase = "restore"
        restored_toolkit = MemoryToolkit(**toolkit_options)
        with SandboxSession(str(source), restored_toolkit, timeout_seconds=10,
                            require_bubblewrap=require_bubblewrap) as restored:
            restored.restore(snapshot)
            if restored.snapshot() != snapshot:
                errors.append("lifecycle restore changed persisted Memory state")
            query = _sample_dataclass(restored.program_interface().Query, role=role)
            phase = "restored read"
            with restored_toolkit.operation("protocol_smoke_read", max_llm_calls=read_calls):
                recovered = restored.read(query)
            restored_toolkit.validate_memory_context(recovered)
            if recovered != result:
                errors.append("lifecycle restored read differs from the original read with deterministic smoke tools")
            with restored_toolkit.paper_scope("protocol-smoke-paper"):
                with restored_toolkit.operation("review_plan", max_llm_calls=0,
                        allowed_capabilities={"retrieve_current_s2"}):
                    restored_plan = restored.plan_review(query, recovered)
            if restored_plan != plan:
                errors.append("lifecycle restored review plan differs from the original plan")
            if restored.snapshot() != after_read:
                errors.append("lifecycle restored read changed Memory state differently from the original read")
    for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
        phase = "load"
        try:
            lifecycle(role)
        except (SandboxError, MemoryBudgetError) as exc:
            detail = failure_detail(exc, phase="candidate_validation")
            if detail["category"] == "candidate":
                errors.append(f"protocol smoke failed for {role} during {phase}: {type(exc).__name__}: {exc}")
            else:
                raise
    return errors
