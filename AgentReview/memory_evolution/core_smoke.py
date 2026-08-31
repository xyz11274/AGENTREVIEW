"""Synthetic production-path smoke for the Core v1 composition root."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agentreview.frozen_evidence import materialize_frozen_evidence_pool
from agentreview.review_corpus import (
    ROLE_SUBDIMENSIONS,
    CanonicalFullReviewRecord,
    FrozenTokenizer,
    canonical_json_bytes,
    materialize_corpus,
)
from agentreview.review_memory import ReviewMemoryBudget, ReviewMemoryIndex, ReviewMemoryStore
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .b_local_manifest import materialize_b_local_manifest
from .core_application import (
    CoreApplicationDependencies,
    CoreRuntimeLimits,
    CoreV1FormalApplication,
)
from .core_scheduler import ROLE_ORDER


_DIGEST = "a" * 64


def _full_review(role: str) -> CanonicalFullReviewRecord:
    payload = {
        "role": role,
        "paper_id": "A-SMOKE-001",
        "subdimension_sections": [
            {
                "subdimension_id": subdimension,
                "review_text": (
                    f"{subdimension}: inspect the method claim at SRC-001. "
                    "This is immutable no-memory synthetic review content."
                ),
                "paper_anchor_ids": ["SRC-001"],
            }
            for subdimension in ROLE_SUBDIMENSIONS[role]
        ],
        "overall_assessment": f"Synthetic {role} assessment.",
    }
    return CanonicalFullReviewRecord.from_model_response(
        canonical_json_bytes(payload),
        expected_role=role,
        expected_paper_id="A-SMOKE-001",
        paper_identity_sha256=_DIGEST,
        structural_context_sha256=_DIGEST,
        reviewer_identity_sha256=_DIGEST,
        prompt_identity_sha256=_DIGEST,
        request_identity_sha256=_DIGEST,
        allowed_paper_anchor_ids=("SRC-001",),
    )


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [float(value + 1) for value in digest[:8]]
    return values


def _embed_texts(texts: Sequence[str]) -> list[list[float]]:
    return [_vector(text) for text in texts]


class _FakeQwen:
    """Deterministic transport; request schemas remain the production schemas."""

    def llm_completion(self, messages, **kwargs):
        agent = kwargs.get("agent_name")
        if agent == "Post-Retrieval Memory Summarizer":
            return "Synthetic grounded memory condensation."
        if agent == "Memory Response":
            return "Grounded specialist review citing SRC-001 without external references."
        if agent == "Core Evolution Reflector":
            prompt = str(messages[-1]["content"])
            mode_line = next(
                line for line in prompt.splitlines() if line.startswith("MEMORY_READ_MODE =")
            )
            return (
                "*** Commit Message\n"
                "Title: Add deterministic smoke child marker\n"
                "- preserve retrieval semantics\n"
                "- exercise admission path\n"
                "*** Begin Patch\n"
                "*** Update File: program.py\n"
                "@@\n"
                f"-{mode_line}\n"
                f"+{mode_line}\n"
                "+CORE_V1_SMOKE_CHILD = 1\n"
                "*** End Patch"
            )
        if agent == "Core Role Evaluator":
            request = json.loads(messages[1]["content"])
            role = request["role"]
            paper_id = request["paper_id"]
            if request["required_output_fields"][-1] == "decisions":
                return json.dumps(
                    {
                        "role": role,
                        "paper_id": paper_id,
                        "decisions": [
                            {
                                "subdimension_id": value,
                                "status": "APPLICABLE",
                                "n_a_reason": None,
                                "paper_anchor_ids": ["SRC-001"],
                            }
                            for value in ROLE_SUBDIMENSIONS[role]
                        ],
                    },
                    separators=(",", ":"),
                )
            return json.dumps(
                {
                    "role": role,
                    "paper_id": paper_id,
                    "scores": [
                        {
                            "subdimension_id": value,
                            "score": 3,
                            "events": [],
                            "evidence_limitations": [],
                        }
                        for value in ROLE_SUBDIMENSIONS[role]
                    ],
                },
                separators=(",", ":"),
            )
        raise AssertionError(f"Unexpected synthetic Qwen request class: {agent}")


def _build_dependencies(counter: dict[str, int]):
    tokenizer = FrozenTokenizer(list, "core-v1-smoke-char-tokenizer")
    corpus = materialize_corpus(
        [_full_review(role) for role in ROLE_ORDER],
        tokenizer=tokenizer,
        max_projection_tokens=10_000,
    )
    manifest_sha = corpus.manifest["corpus_manifest_sha256"]
    indices = {
        role: ReviewMemoryIndex.build(
            role=role,
            fragments=corpus.fragments,
            embed=_embed_texts,
            embedding_identity="core-v1-smoke-embedding",
            source_manifest_sha256=manifest_sha,
        )
        for role in ROLE_ORDER
    }
    memory = ReviewMemoryStore(
        corpus,
        indices,
        embed_query=_vector,
        token_count=len,
        query_token_count=len,
    )
    papers = tuple(
        {
            "paper_id": f"B-SMOKE-{index:03d}",
            "family_id": f"FAMILY-SMOKE-{index:03d}",
            "reviewer_question": (
                f"Review B-SMOKE-{index:03d}. Paper body: method and evidence at SRC-001."
            ),
            "paper_text": "Synthetic paper method and evidence at SRC-001.",
            "structural_context": "SRC-001 section=method offset=0",
            "cutoff": "2025-01-01",
        }
        for index in (1, 2)
    )
    eligibility = {
        role: {
            paper["paper_id"]: {"eligible": True, "reason_code": "SMOKE_EVALUABLE"}
            for paper in papers
        }
        for role in ROLE_ORDER
    }
    thresholds = {
        role: {"min_static_evaluable": 1, "min_full_pool_evaluable": 1}
        for role in ROLE_ORDER
    }
    b_local = materialize_b_local_manifest(
        papers=papers,
        eligibility=eligibility,
        thresholds=thresholds,
        selection_seed="core-v1-production-smoke",
        attempts=1,
        static_size=1,
        rotating_size=1,
        source_manifest_sha256=hashlib.sha256(canonical_json_bytes(papers)).hexdigest(),
        rubric_protocol_version="core-v1-smoke-rubrics",
    )
    evidence = materialize_frozen_evidence_pool(
        pool_version="core-v1-smoke-evidence-v1",
        paper_sets=tuple(
            {
                "target_paper_id": paper["paper_id"],
                "target_family_id": paper["family_id"],
                "cutoff": paper["cutoff"],
                "partition_availability": {
                    "PRIOR_WORK": "available",
                    "POLICY": "unavailable",
                    "ARTIFACT_LICENSE": "unavailable",
                },
            }
            for paper in papers
        ),
        items=(),
        projection_identity="core-v1-smoke-evidence-projection",
    )
    qwen = _FakeQwen()

    def protected_loader():
        counter["protected"] += 1
        return {"C": "synthetic protected input", "human": "synthetic reference"}

    dependencies = CoreApplicationDependencies(
        memory_store=memory,
        b_local_manifest=b_local,
        evidence_pool=evidence,
        role_rubrics={role: f"Frozen synthetic {role} rubric" for role in ROLE_ORDER},
        toolkit_factory=lambda role: MemoryToolkit(
            llm_client=qwen,
            llm_model="fake-qwen-core-v1",
        ),
        protected_loader=protected_loader,
        official_runner=lambda suite, protected: {
            "status": "REPORT_ONLY",
            "suite_identity_sha256": suite.suite_identity_sha256,
            "protected_input_keys": sorted(protected),
        },
        token_count=len,
    )
    return dependencies


def run_production_integration_smoke(output_dir: str | Path) -> Mapping[str, Any]:
    """Run all five roles through the same production composition root."""
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    counter = {"protected": 0}
    limits = CoreRuntimeLimits(
        memory_budget=ReviewMemoryBudget(8, 16, 4, 1_000_000, 8, 20_000),
        max_reviewer_memory_context_tokens=1_000_000,
        max_summarizer_input_tokens=1_000_000,
        max_summarizer_query_tokens_total=100_000,
        qwen_capacity=1,
        reviewer_timeout_seconds=10,
        reviewer_memory_mb=256,
    )
    with CoreV1FormalApplication(
        output_dir=target,
        dependencies=_build_dependencies(counter),
        limits=limits,
        run_seed=7,
        attempts=1,
        require_bubblewrap=False,
    ) as application:
        validation = dict(application.validate_inputs())
        for role in ROLE_ORDER:
            application.run_role(role)
        protected_before = counter["protected"]
        qwen_count = application.qwen_execution_count
        role_audits = {role: dict(application.role_audits[role]) for role in ROLE_ORDER}
    # Recreate the production application between CLI-like stages.  Suite
    # freeze must consume durable winner records, not process memory.
    with CoreV1FormalApplication(
        output_dir=target,
        dependencies=_build_dependencies(counter),
        limits=limits,
        run_seed=7,
        attempts=1,
        require_bubblewrap=False,
    ) as suite_application:
        suite = suite_application.freeze_suite()
    protected_after_suite = counter["protected"]
    # Official is another fresh process-equivalent application and therefore
    # must load the immutable frozen Suite before authorization.
    with CoreV1FormalApplication(
        output_dir=target,
        dependencies=_build_dependencies(counter),
        limits=limits,
        run_seed=7,
        attempts=1,
        require_bubblewrap=False,
    ) as official_application:
        official = dict(official_application.run_official())
    report = {
        "status": "PASS",
        "production_composition_root": "CoreV1FormalApplication",
        "validation": validation,
        "role_order": list(ROLE_ORDER),
        "roles": role_audits,
        "suite_role_order": [winner.role for winner in suite.winners],
        "suite_identity_sha256": suite.suite_identity_sha256,
        "protected_loader_calls_before_authorization": protected_before,
        "protected_loader_calls_after_suite_freeze": protected_after_suite,
        "protected_loader_calls_after_official": counter["protected"],
        "official_result": official,
        "qwen_execution_count": qwen_count,
    }
    path = target / "integration_smoke_report.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(canonical_json_bytes(report))
    temporary.replace(path)
    return report
