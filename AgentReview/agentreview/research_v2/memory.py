"""Offline M★ Memory build and read-only deterministic Review retrieval."""

from __future__ import annotations

import hashlib
from dataclasses import MISSING, asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import load_reviewer_suite
from agentreview.reviewer_runtime.sandbox import SandboxSession
from agentreview.reviewer_runtime.task_agent import TaskAgent
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .constants import ROLES, SUMMARIZER_MAX_TOKENS
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient, ToolkitCompletionAdapter
from .models import Paper

SEED_NAMES = ("vector_search", "llm_summarizer", "experience_learner")
BUILD_KINDS = ("no_memory", *SEED_NAMES)


def seed_program_path(seed_name: str) -> Path:
    if seed_name not in BUILD_KINDS:
        raise ValueError(f"Unknown seed: {seed_name}")
    return Path(__file__).resolve().parent / "seeds" / f"{seed_name}.py"


def _serializable(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Expected dataclass or dict, got {type(value).__name__}")


def _default_value(annotation: Any, field_name: str) -> Any:
    name = getattr(annotation, "__name__", str(annotation)).lower()
    if "list" in name:
        return []
    if "dict" in name:
        return {}
    if "bool" in name:
        return False
    if "int" in name:
        return 0
    if "float" in name:
        return 0.0
    return ""


def deterministic_query(program: Any, paper: Paper, role: str):
    """Construct an evolvable Query schema from frozen fields without an LLM."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    query_text = "\n".join(
        value for value in (paper.title.strip(), paper.abstract.strip()) if value
    )
    aliases = {
        "query": query_text,
        "query_text": query_text,
        "text": query_text,
        "title": paper.title,
        "paper_title": paper.title,
        "abstract": paper.abstract,
        "paper_abstract": paper.abstract,
        "role": role,
        "reviewer_role": role,
        "reviewer_dimension": role,
        "dimension": role,
        "paper_id": paper.paper_id,
    }
    values: dict[str, Any] = {}
    for item in fields(program.Query):
        if item.name in aliases:
            values[item.name] = aliases[item.name]
        elif item.default is not MISSING or item.default_factory is not MISSING:
            # Dataclass construction will use any declared default.
            continue
        else:
            values[item.name] = _default_value(item.type, item.name)
    try:
        return program.Query(**values)
    except TypeError:
        complete = {
            item.name: values.get(item.name, _default_value(item.type, item.name))
            for item in fields(program.Query)
        }
        return program.Query(**complete)


def _deterministic_item(
    program: Any,
    *,
    review: dict[str, Any],
    role: str,
) -> Any:
    review_text = str(review.get("review_text") or "")
    title = str((review.get("paper") or {}).get("title") or review.get("paper_title") or "")
    aliases = {
        "topic": title,
        "content": review_text,
        "raw_text": review_text,
        "reviewer_name": f"Reviewer {role}",
        "reviewer_dimension": role,
        "reviewer_role": role,
        "role": role,
    }
    values = {
        item.name: aliases.get(item.name, _default_value(item.type, item.name))
        for item in fields(program.KnowledgeItem)
    }
    return program.KnowledgeItem(**values)


def _experience_source(
    review: dict[str, Any],
    evaluation: dict[str, Any] | None,
) -> str:
    feedback = evaluation or {}
    return (
        f"SOURCE REVIEW ID: {review.get('review_id', '')}\n"
        f"ROLE: {review.get('role', '')}\n\n"
        "COMPLETE SPECIALIST REVIEW:\n"
        f"{review.get('review_text', '')}\n\n"
        "INTERNAL EVALUATION:\n"
        f"review_quality: {feedback.get('review_quality', '')}\n"
        f"major_missing_points: {feedback.get('major_missing_points', [])}\n"
        f"factual_evidence_issues: {feedback.get('factual_evidence_issues', [])}\n"
        f"improvement_suggestions: {feedback.get('improvement_suggestions', [])}"
    )


def build_memory_snapshot(
    *,
    role: str,
    program_path: str | Path,
    program_version: str,
    review_paths: list[str | Path],
    evaluation_paths: list[str | Path] | None,
    output_path: str | Path,
    seed_kind: str,
    embedding_client: Any = None,
    completion_client: CompletionClient | None = None,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Build one role-local snapshot; this is the only stage allowed to write KB."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    if seed_kind not in BUILD_KINDS and completion_client is None:
        raise ValueError("Evolved KnowledgeItem build requires an offline completion client")
    reviews = [read_json(path) for path in review_paths]
    if any(str(review.get("role")) != role for review in reviews):
        raise ValueError("Memory build received a cross-role Review")
    evaluations = {
        str(value.get("review_id")): value
        for value in (read_json(path) for path in (evaluation_paths or []))
    }
    if any(str(value.get("role") or "") != role for value in evaluations.values()):
        raise ValueError("Memory build received a cross-role Internal Evaluation")
    review_ids = {str(review.get("review_id") or "") for review in reviews}
    unknown_evaluations = set(evaluations) - review_ids
    if unknown_evaluations:
        raise ValueError(
            "Memory build received Internal Evaluation without its source Review: "
            + ", ".join(sorted(unknown_evaluations))
        )
    llm_adapter = (
        ToolkitCompletionAdapter(completion_client, SUMMARIZER_MAX_TOKENS, "memory_build")
        if completion_client is not None
        else None
    )
    toolkit = MemoryToolkit(
        llm_client=llm_adapter,
        embedding_client=embedding_client,
        max_llm_calls_per_operation=1,
    )
    item_records = []
    program_source = Path(program_path).expanduser().resolve()
    with SandboxSession(
        str(program_source),
        toolkit,
        require_bubblewrap=require_bubblewrap,
    ) as sandbox:
        program = sandbox.program_interface()
        task_agent = TaskAgent(
            toolkit,
            system_prompt=load_reviewer_suite().task(role).system_prompt,
            task_id=role,
        )
        for review in reviews:
            evaluation = evaluations.get(str(review.get("review_id")))
            raw_text = _experience_source(review, evaluation)
            if seed_kind in {"vector_search", "llm_summarizer"}:
                item = _deterministic_item(program, review=review, role=role)
            else:
                if completion_client is None:
                    raise ValueError("Experience Learner requires an offline completion client")
                with toolkit.operation("knowledge_item_generation", max_llm_calls=1):
                    item = task_agent.extract_knowledge_item(program, raw_text)
            with toolkit.operation("memory_write", max_llm_calls=1):
                sandbox.write(item, raw_text)
            review_id = str(review.get("review_id") or "")
            evaluation_id = str((evaluation or {}).get("evaluation_id") or "")
            envelope = {
                "item_id": hashlib.sha256(
                    f"{program_version}\n{role}\n{review_id}\n{evaluation_id}".encode("utf-8")
                ).hexdigest(),
                "role": role,
                "source_review_ids": [review_id],
                "source_evaluation_ids": [evaluation_id] if evaluation_id else [],
                "derivation_version": program_version,
                "payload": _serializable(item),
            }
            item_records.append(envelope)
        snapshot = sandbox.snapshot()
    payload = {
        "snapshot_protocol": "research-v2-memory-snapshot-v1",
        "role": role,
        "program_version": program_version,
        "program_path": str(program_source),
        "program_sha256": file_sha256(program_source),
        "seed_kind": seed_kind,
        "source_review_ids": [str(review.get("review_id") or "") for review in reviews],
        "source_evaluation_ids": [
            str(value.get("evaluation_id") or "") for value in evaluations.values()
        ],
        "items": item_records,
        "snapshot": snapshot,
    }
    payload["snapshot_id"] = value_sha256(payload)
    atomic_write_json(output_path, payload)
    return payload


def retrieve_memory(
    *,
    paper: Paper,
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    embedding_client: Any = None,
    completion_client: CompletionClient | None = None,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Read a frozen snapshot; no KnowledgeItem generation or write is exposed."""
    snapshot_artifact = read_json(snapshot_path)
    if snapshot_artifact.get("role") != role:
        raise ValueError("Memory snapshot role mismatch")
    seed_kind = str(snapshot_artifact.get("seed_kind") or "")
    summarizer = seed_kind == "llm_summarizer"
    if summarizer and completion_client is None:
        raise ValueError("LLM Summarizer read requires its explicit completion client")
    llm_adapter = (
        ToolkitCompletionAdapter(
            completion_client,
            SUMMARIZER_MAX_TOKENS,
            "memory_summarizer",
        )
        if summarizer and completion_client is not None
        else None
    )
    toolkit = MemoryToolkit(
        llm_client=llm_adapter,
        embedding_client=embedding_client,
        max_llm_calls_per_operation=1 if summarizer else 0,
    )
    source = Path(program_path).expanduser().resolve()
    if file_sha256(source) != snapshot_artifact.get("program_sha256"):
        raise ValueError("Memory Program differs from the frozen snapshot")
    with SandboxSession(
        str(source),
        toolkit,
        require_bubblewrap=require_bubblewrap,
    ) as sandbox:
        sandbox.restore(dict(snapshot_artifact.get("snapshot") or {}))
        program = sandbox.program_interface()
        query = deterministic_query(program, paper, role)
        with toolkit.operation("memory_read", max_llm_calls=1 if summarizer else 0):
            context = sandbox.read(query)
        logs = list(sandbox.logs)
    if len(context) > 6000:
        raise ValueError("KnowledgeBase.read() exceeded 6000 characters")
    return {
        "query": _serializable(query),
        "context": context,
        "logs": logs,
        "snapshot_id": snapshot_artifact.get("snapshot_id"),
        "program_version": snapshot_artifact.get("program_version"),
        "program_sha256": snapshot_artifact.get("program_sha256"),
        "seed_kind": seed_kind,
    }
