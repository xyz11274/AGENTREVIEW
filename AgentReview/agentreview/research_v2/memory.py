"""Offline M★ Memory build and read-only deterministic Review retrieval."""

from __future__ import annotations

import hashlib
import fcntl
import json
from contextlib import contextmanager
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
from .program_checks import program_capability_manifest

SEED_NAMES = ("vector_search", "llm_summarizer", "experience_learner")
BUILD_KINDS = ("no_memory", *SEED_NAMES)
ITEM_CHECKPOINT_PROTOCOL = "research-v2-memory-item-checkpoint-v1"
DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY = {
    "provider": "deterministic-fallback-v1"
}


def _runtime_embedding_identity(
    *,
    embedding_client: Any,
    embedding_identity: dict[str, Any] | None,
    operation: str,
) -> dict[str, Any]:
    if embedding_client is None:
        fallback_identity = dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY)
        if (
            embedding_identity is not None
            and dict(embedding_identity) != fallback_identity
        ):
            raise ValueError(
                f"Memory {operation} without an embedding client requires "
                "the deterministic fallback embedding_identity"
            )
        return fallback_identity
    if not embedding_identity:
        raise ValueError(
            f"Memory {operation} with an embedding client requires "
            "embedding_identity"
        )
    return dict(embedding_identity)


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
    title = str(
        (review.get("paper") or {}).get("title") or review.get("paper_title") or ""
    )
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


def validate_memory_snapshot_artifact(
    value: Any,
    *,
    path: str | Path,
) -> dict[str, Any]:
    """Validate the content-addressed envelope shared by all snapshot consumers."""
    if not isinstance(value, dict):
        raise TypeError(f"Memory snapshot must be an object: {path}")
    if value.get("snapshot_protocol") != "research-v2-memory-snapshot-v1":
        raise ValueError(f"Unsupported Memory snapshot protocol: {path}")
    if value.get("role") not in ROLES:
        raise ValueError(f"Memory snapshot has an invalid role: {path}")
    if not isinstance(value.get("program_path"), str) or not value["program_path"]:
        raise ValueError(f"Memory snapshot lacks program_path: {path}")
    program_sha256 = value.get("program_sha256")
    if (
        not isinstance(program_sha256, str)
        or len(program_sha256) != 64
        or any(character not in "0123456789abcdef" for character in program_sha256)
    ):
        raise ValueError(f"Memory snapshot has an invalid program_sha256: {path}")
    for field_name in ("source_review_ids", "source_evaluation_ids", "items"):
        if not isinstance(value.get(field_name), list):
            raise TypeError(f"Memory snapshot {field_name} must be a list: {path}")
    if not isinstance(value.get("snapshot"), dict):
        raise TypeError(f"Memory snapshot state must be an object: {path}")
    manifest = value.get("capability_manifest")
    if manifest is not None and (
        not isinstance(manifest, dict)
        or manifest.get("program_sha256") != program_sha256
    ):
        raise ValueError(f"Memory snapshot capability manifest differs: {path}")
    recorded = value.get("snapshot_id")
    unhashed = {name: item for name, item in value.items() if name != "snapshot_id"}
    if not isinstance(recorded, str) or recorded != value_sha256(unhashed):
        raise ValueError(f"Memory snapshot identity mismatch: {path}")
    return value


def _checkpoint_path(output_path: str | Path, ordinal: int, review_id: str) -> Path:
    output = Path(output_path).expanduser().resolve()
    source_key = hashlib.sha256(review_id.encode("utf-8")).hexdigest()[:16]
    return output.parent / f"{output.name}.items" / f"{ordinal:05d}-{source_key}.json"


@contextmanager
def _checkpoint_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_checkpoint(path: Path, expected_identity: dict[str, Any]) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"Memory item checkpoint must be an object: {path}")
    checkpoint_id = value.get("checkpoint_id")
    unhashed = {name: item for name, item in value.items() if name != "checkpoint_id"}
    if not isinstance(checkpoint_id, str) or checkpoint_id != value_sha256(unhashed):
        raise ValueError(f"Memory item checkpoint identity mismatch: {path}")
    if value.get("protocol") != ITEM_CHECKPOINT_PROTOCOL:
        raise ValueError(f"Unsupported Memory item checkpoint protocol: {path}")
    if value.get("input_identity") != expected_identity:
        raise ValueError(f"Memory item checkpoint input identity differs: {path}")
    item = value.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
        raise TypeError(f"Memory item checkpoint payload is invalid: {path}")
    return value


def _load_hashed_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    raw = source.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"Memory source artifact must be an object: {source}")
    return {
        "path": source,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "artifact": value,
    }


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
    embedding_identity: dict[str, Any] | None = None,
    completion_client: CompletionClient | None = None,
    completion_identity: dict[str, Any] | None = None,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Build one role-local snapshot; this is the only stage allowed to write KB."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    if seed_kind not in BUILD_KINDS and completion_client is None:
        raise ValueError(
            "Evolved KnowledgeItem build requires an offline completion client"
        )
    normalized_embedding_identity = (
        {"provider": "none"}
        if seed_kind == "no_memory"
        else _runtime_embedding_identity(
            embedding_client=embedding_client,
            embedding_identity=embedding_identity,
            operation="build",
        )
    )
    review_sources = [_load_hashed_json(path) for path in review_paths]
    reviews = [value["artifact"] for value in review_sources]
    if any(str(review.get("role")) != role for review in reviews):
        raise ValueError("Memory build received a cross-role Review")
    review_id_list = [str(review.get("review_id") or "") for review in reviews]
    if any(not value for value in review_id_list):
        raise ValueError("Memory build received a Review without review_id")
    if len(review_id_list) != len(set(review_id_list)):
        raise ValueError("Memory build received duplicate source Review IDs")
    evaluation_sources: dict[str, dict[str, Any]] = {}
    for path in evaluation_paths or []:
        loaded_source = _load_hashed_json(path)
        source = loaded_source["path"]
        artifact = loaded_source["artifact"]
        review_id = str(artifact.get("review_id") or "")
        if not review_id:
            raise ValueError(
                "Memory build received an Internal Evaluation without review_id"
            )
        if review_id in evaluation_sources:
            raise ValueError(
                f"Memory build received duplicate Internal Evaluations for {review_id}"
            )
        evaluation_sources[review_id] = {
            "path": source,
            "sha256": loaded_source["sha256"],
            "artifact": artifact,
        }
    evaluations = {
        review_id: value["artifact"] for review_id, value in evaluation_sources.items()
    }
    if any(str(value.get("role") or "") != role for value in evaluations.values()):
        raise ValueError("Memory build received a cross-role Internal Evaluation")
    review_ids = set(review_id_list)
    unknown_evaluations = set(evaluations) - review_ids
    if unknown_evaluations:
        raise ValueError(
            "Memory build received Internal Evaluation without its source Review: "
            + ", ".join(sorted(unknown_evaluations))
        )
    requires_item_induction = seed_kind not in {
        "no_memory",
        "vector_search",
        "llm_summarizer",
    }
    if (
        requires_item_induction
        and completion_client is not None
        and not completion_identity
    ):
        raise ValueError(
            "Memory item induction with a completion client requires completion_identity"
        )
    normalized_completion_identity = dict(completion_identity or {"provider": "none"})
    llm_adapter = (
        ToolkitCompletionAdapter(
            completion_client, SUMMARIZER_MAX_TOKENS, "memory_build"
        )
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
    capability_manifest = (
        None
        if seed_kind == "no_memory"
        else program_capability_manifest(path=program_source)
    )
    build_identity = None
    if seed_kind != "no_memory":
        build_identity = {
            "protocol": "research-v2-memory-build-input-v1",
            "role": role,
            "program_path": str(program_source),
            "program_sha256": file_sha256(program_source),
            "capability_manifest": capability_manifest,
            "seed_kind": seed_kind,
            "program_version": program_version,
            "embedding_identity": normalized_embedding_identity,
            "completion_identity": (
                normalized_completion_identity if requires_item_induction else None
            ),
            "sources": [
                {
                    "ordinal": ordinal,
                    "review_id": review_id_list[ordinal],
                    "review_sha256": review_source["sha256"],
                    "evaluation_id": str(
                        (
                            evaluation_sources.get(review_id_list[ordinal], {}).get(
                                "artifact", {}
                            )
                            or {}
                        ).get("evaluation_id")
                        or ""
                    ),
                    "evaluation_sha256": str(
                        evaluation_sources.get(review_id_list[ordinal], {}).get(
                            "sha256", ""
                        )
                    ),
                }
                for ordinal, review_source in enumerate(review_sources)
            ],
        }
        existing_output = Path(output_path).expanduser().resolve()
        if existing_output.is_file():
            existing = validate_memory_snapshot_artifact(
                read_json(existing_output), path=existing_output
            )
            if existing.get("build_identity") != build_identity:
                raise ValueError(
                    f"Memory snapshot build identity differs: {existing_output}"
                )
            return existing
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
        for ordinal, review in enumerate(reviews):
            review_id = review_id_list[ordinal]
            evaluation = evaluations.get(review_id)
            raw_text = _experience_source(review, evaluation)
            evaluation_id = str((evaluation or {}).get("evaluation_id") or "")
            if build_identity is None:
                if seed_kind in {"vector_search", "llm_summarizer"}:
                    item = _deterministic_item(program, review=review, role=role)
                else:
                    if completion_client is None:
                        raise ValueError(
                            "Experience Learner requires an offline completion client"
                        )
                    with toolkit.operation(
                        "knowledge_item_generation", max_llm_calls=1
                    ):
                        item = task_agent.extract_knowledge_item(program, raw_text)
                envelope = {
                    "item_id": hashlib.sha256(
                        f"{program_version}\n{role}\n{review_id}\n{evaluation_id}".encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                    "role": role,
                    "source_review_ids": [review_id],
                    "source_evaluation_ids": [evaluation_id] if evaluation_id else [],
                    "derivation_version": program_version,
                    "payload": _serializable(item),
                }
            else:
                item_identity = {
                    name: value
                    for name, value in build_identity.items()
                    if name != "sources"
                }
                item_identity["source"] = build_identity["sources"][ordinal]
                checkpoint_path = _checkpoint_path(output_path, ordinal, review_id)
                with _checkpoint_lock(checkpoint_path):
                    if checkpoint_path.is_file():
                        checkpoint = _load_checkpoint(checkpoint_path, item_identity)
                        envelope = dict(checkpoint["item"])
                    else:
                        if seed_kind in {"vector_search", "llm_summarizer"}:
                            item = _deterministic_item(
                                program, review=review, role=role
                            )
                        else:
                            if completion_client is None:
                                raise ValueError(
                                    "Experience Learner requires an offline completion client"
                                )
                            with toolkit.operation(
                                "knowledge_item_generation", max_llm_calls=1
                            ):
                                item = task_agent.extract_knowledge_item(
                                    program, raw_text
                                )
                        envelope = {
                            "item_id": value_sha256(item_identity),
                            "role": role,
                            "source_review_ids": [review_id],
                            "source_evaluation_ids": (
                                [evaluation_id] if evaluation_id else []
                            ),
                            "derivation_version": program_version,
                            "payload": _serializable(item),
                        }
                        checkpoint = {
                            "protocol": ITEM_CHECKPOINT_PROTOCOL,
                            "input_identity": item_identity,
                            "item": envelope,
                        }
                        checkpoint["checkpoint_id"] = value_sha256(checkpoint)
                        atomic_write_json(checkpoint_path, checkpoint)
                    item = program.KnowledgeItem(**dict(envelope["payload"]))
            with toolkit.operation("memory_write", max_llm_calls=0):
                sandbox.write(item, raw_text)
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
    if capability_manifest is not None:
        payload["capability_manifest"] = capability_manifest
        payload["embedding_identity"] = normalized_embedding_identity
        if requires_item_induction:
            payload["completion_identity"] = normalized_completion_identity
        payload["build_identity"] = build_identity
    payload["snapshot_id"] = value_sha256(payload)
    atomic_write_json(output_path, payload)
    return validate_memory_snapshot_artifact(read_json(output_path), path=output_path)


def retrieve_memory(
    *,
    paper: Paper,
    role: str,
    program_path: str | Path,
    snapshot_path: str | Path,
    embedding_client: Any = None,
    embedding_identity: dict[str, Any] | None = None,
    completion_client: CompletionClient | None = None,
    prepare_review_plan: bool = False,
    s2_retrieval_pool: dict[str, list[dict[str, Any]]] | None = None,
    s2_retrieval_pool_sha256: str = "",
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Read a frozen snapshot and optionally execute its autonomous Review Plan.

    Memory-only callers retain the original read interface. Review preparation
    opts into ``plan_review`` and supplies a host-owned, current-paper S2 pool;
    the candidate can consult that capability only inside the planning scope.
    """
    snapshot_artifact = validate_memory_snapshot_artifact(
        read_json(snapshot_path), path=snapshot_path
    )
    if snapshot_artifact.get("role") != role:
        raise ValueError("Memory snapshot role mismatch")
    seed_kind = str(snapshot_artifact.get("seed_kind") or "")
    if seed_kind != "no_memory":
        runtime_embedding_identity = _runtime_embedding_identity(
            embedding_client=embedding_client,
            embedding_identity=embedding_identity,
            operation="retrieval",
        )
        if snapshot_artifact.get("embedding_identity") != runtime_embedding_identity:
            raise ValueError(
                "Memory retrieval embedding identity differs from snapshot"
            )
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
        s2_retrieval_pool=s2_retrieval_pool,
        s2_retrieval_pool_sha256=s2_retrieval_pool_sha256,
    )
    resources_before = toolkit.resource_snapshot()
    source = Path(program_path).expanduser().resolve()
    if file_sha256(source) != snapshot_artifact.get("program_sha256"):
        raise ValueError("Memory Program differs from the frozen snapshot")
    recorded_capabilities = snapshot_artifact.get("capability_manifest")
    if recorded_capabilities is not None:
        current_capabilities = program_capability_manifest(path=source)
        if current_capabilities != recorded_capabilities:
            raise ValueError("Memory Program capability identity differs from snapshot")
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
        if len(context) > 6000:
            raise ValueError("KnowledgeBase.read() exceeded 6000 characters")
        review_plan = None
        s2_trace = {"paper_id": paper.paper_id, "calls": []}
        if prepare_review_plan:
            with toolkit.paper_scope(paper.paper_id):
                with toolkit.operation(
                    "review_plan",
                    max_llm_calls=0,
                    allowed_capabilities={"retrieve_current_s2"},
                ):
                    review_plan = sandbox.plan_review(query, context)
                s2_trace = toolkit.s2_trace_snapshot()
        logs = list(sandbox.logs)
    result = {
        "query": _serializable(query),
        "context": context,
        "logs": logs,
        "snapshot_id": snapshot_artifact.get("snapshot_id"),
        "program_version": snapshot_artifact.get("program_version"),
        "program_sha256": snapshot_artifact.get("program_sha256"),
        "seed_kind": seed_kind,
    }
    if prepare_review_plan:
        result["review_plan"] = review_plan
        result["s2_trace"] = s2_trace
        result["resources"] = toolkit.resource_delta(resources_before)
    return result
