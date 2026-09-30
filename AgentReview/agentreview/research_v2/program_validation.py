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

def _sample_dataclass(dataclass_type):
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
        "topic": "Protocol smoke paper", "content": "traceable Review Experience",
        "reviewer_name": "QUAL", "reviewer_dimension": "QUAL",
        "query_text": "Protocol smoke paper: methodology and evidence",
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

    toolkit = MemoryToolkit(
        llm_client=SmokeLLM(),
        **budget_options(runtime),
        max_llm_calls_per_operation=max(write_calls, read_calls),
        s2_retrieval_pool={
            "protocol-smoke-paper": [
                {"paperId": "protocol-smoke-s2", "title": "Frozen smoke evidence"}
            ]
        },
    )
    try:
        with SandboxSession(
            str(source),
            toolkit,
            timeout_seconds=10,
            require_bubblewrap=require_bubblewrap,
        ) as sandbox:
            program = sandbox.program_interface()
            item = _sample_dataclass(program.KnowledgeItem)
            query = _sample_dataclass(program.Query)
            with toolkit.operation("protocol_smoke_write", max_llm_calls=write_calls):
                sandbox.write(item, "traceable Review Experience")
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
    except (SandboxError, MemoryBudgetError) as exc:
        detail = failure_detail(exc, phase="candidate_validation")
        if detail["category"] == "candidate":
            errors.append(f"protocol smoke failed: {type(exc).__name__}: {exc}")
        else:
            raise
    return errors
