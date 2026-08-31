"""Disk-backed production dependency loader for the Core v1 CLI.

All numerical limits and provider choices must already be frozen in the input
JSON.  This loader never derives or guesses formal values.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from agentreview.backends.openai import OpenAIChat
from agentreview.frozen_evidence import FrozenEvidencePool
from agentreview.review_corpus import CorpusMaterialization, ROLE_SUBDIMENSIONS
from agentreview.review_memory import ReviewMemoryBudget, ReviewMemoryIndex, ReviewMemoryStore
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .b_local_manifest import BLocalManifest
from .core_application import (
    CoreApplicationDependencies,
    CoreRuntimeLimits,
    CoreV1FormalApplication,
)


RUNTIME_CONFIG_VERSION = "research-validity-core-v1-runtime-config-v1"


def _callable(value: str):
    module_name, separator, attribute = str(value).partition(":")
    if not separator:
        raise ValueError("Configured callable must be MODULE:ATTRIBUTE")
    result = getattr(importlib.import_module(module_name), attribute)
    if not callable(result):
        raise TypeError(f"Configured value is not callable: {value}")
    return result


def load_core_application_from_config(
    *, config_path: str | Path, output_dir: str | Path
) -> CoreV1FormalApplication:
    source = Path(config_path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    required = {
        "schema_version", "corpus_directory", "review_memory_indices",
        "b_local_manifest", "frozen_evidence_pool", "role_rubrics",
        "embedding_query_factory", "embedding_query_config", "qwen",
        "limits", "run_seed", "attempts", "require_bubblewrap",
        "protected_input_paths", "official_runner",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Core runtime config fields mismatch")
    if value["schema_version"] != RUNTIME_CONFIG_VERSION:
        raise ValueError("Unsupported Core runtime config version")
    indices_spec = value["review_memory_indices"]
    rubrics_spec = value["role_rubrics"]
    if tuple(indices_spec) != tuple(ROLE_SUBDIMENSIONS) or tuple(rubrics_spec) != tuple(
        ROLE_SUBDIMENSIONS
    ):
        raise ValueError("Core runtime role index/rubric order mismatch")
    corpus = CorpusMaterialization.load(value["corpus_directory"])
    indices = {
        role: ReviewMemoryIndex.load(indices_spec[role]) for role in ROLE_SUBDIMENSIONS
    }
    embed_query = _callable(value["embedding_query_factory"])(
        dict(value["embedding_query_config"])
    )
    if not callable(embed_query):
        raise TypeError("Embedding query factory did not return a callable")

    qwen = dict(value["qwen"])
    qwen_required = {
        "model", "base_url", "temperature", "max_tokens", "request_timeout_seconds",
        "connect_timeout_seconds", "read_timeout_seconds", "write_timeout_seconds",
        "pool_timeout_seconds", "api_max_retries", "reasoning_effort",
        "reasoning_effort_supported", "retry_max_tokens",
    }
    if set(qwen) != qwen_required:
        raise ValueError("Core runtime Qwen fields mismatch")

    def toolkit_factory(role: str):
        backend = OpenAIChat(
            model=qwen["model"],
            openai_base_url=qwen["base_url"],
            openai_client_type="openai",
            temperature=qwen["temperature"],
            max_tokens=qwen["max_tokens"],
            request_timeout_seconds=qwen["request_timeout_seconds"],
            connect_timeout_seconds=qwen["connect_timeout_seconds"],
            read_timeout_seconds=qwen["read_timeout_seconds"],
            write_timeout_seconds=qwen["write_timeout_seconds"],
            pool_timeout_seconds=qwen["pool_timeout_seconds"],
            api_max_retries=qwen["api_max_retries"],
            reasoning_effort=qwen["reasoning_effort"],
            reasoning_effort_supported=qwen["reasoning_effort_supported"],
            retry_max_tokens=qwen["retry_max_tokens"],
        )
        return MemoryToolkit(llm_client=backend, llm_model=qwen["model"])

    # The embedding factory may expose the frozen tokenizer counter on its
    # returned callable; requiring it prevents a guessed character budget.
    token_count = getattr(embed_query, "token_count", None)
    if not callable(token_count):
        raise TypeError("Embedding query callable must expose frozen token_count(text)")
    memory_store = ReviewMemoryStore(
        corpus,
        indices,
        embed_query=embed_query,
        token_count=token_count,
        query_token_count=token_count,
    )
    protected_paths = tuple(Path(item).expanduser().resolve() for item in value["protected_input_paths"])

    def protected_loader():
        # Deliberately lazy: paths are not opened until ProtectedOfficialAccess
        # has authorized the frozen Suite.
        return tuple(path.read_bytes() for path in protected_paths)

    official_runner = _callable(value["official_runner"])
    limits = dict(value["limits"])
    budget_fields = {
        "max_api_calls", "max_delivered_fragments", "max_full_record_hydrations",
        "max_delivered_tokens", "max_top_k", "max_memory_query_tokens_per_call",
    }
    if set(limits.get("memory_budget", {})) != budget_fields:
        raise ValueError("Core runtime Memory budget fields mismatch")
    scalar_limit_fields = {
        "memory_budget", "max_reviewer_memory_context_tokens",
        "max_summarizer_input_tokens", "max_summarizer_query_tokens_total",
        "qwen_capacity", "reviewer_timeout_seconds", "reviewer_memory_mb",
    }
    if set(limits) != scalar_limit_fields:
        raise ValueError("Core runtime limit fields mismatch")
    runtime_limits = CoreRuntimeLimits(
        memory_budget=ReviewMemoryBudget(**limits["memory_budget"]),
        max_reviewer_memory_context_tokens=limits["max_reviewer_memory_context_tokens"],
        max_summarizer_input_tokens=limits["max_summarizer_input_tokens"],
        max_summarizer_query_tokens_total=limits["max_summarizer_query_tokens_total"],
        qwen_capacity=limits["qwen_capacity"],
        reviewer_timeout_seconds=limits["reviewer_timeout_seconds"],
        reviewer_memory_mb=limits["reviewer_memory_mb"],
    )
    dependencies = CoreApplicationDependencies(
        memory_store=memory_store,
        b_local_manifest=BLocalManifest.load(value["b_local_manifest"]),
        evidence_pool=FrozenEvidencePool.load(value["frozen_evidence_pool"]),
        role_rubrics={
            role: Path(rubrics_spec[role]).read_text(encoding="utf-8")
            for role in ROLE_SUBDIMENSIONS
        },
        toolkit_factory=toolkit_factory,
        protected_loader=protected_loader,
        official_runner=official_runner,
        token_count=token_count,
    )
    return CoreV1FormalApplication(
        output_dir=output_dir,
        dependencies=dependencies,
        limits=runtime_limits,
        run_seed=value["run_seed"],
        attempts=value["attempts"],
        require_bubblewrap=value["require_bubblewrap"],
    )
