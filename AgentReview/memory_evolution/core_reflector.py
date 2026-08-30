"""Aggregate-only Evolution Reflector for Core v1 MemoryPrograms."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .core_search import CandidateOriginatedFailure, CoreCandidate
from .reflector import _apply_v4a_patch


CORE_REFLECTOR_VERSION = "core-v1-aggregate-reflector-v1"
FEEDBACK_FIELDS = {
    "role",
    "overall_fitness",
    "subdimension_scores",
    "failure_codes",
    "evaluable_paper_count",
}


def validate_core_reflector_feedback(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != FEEDBACK_FIELDS:
        raise ValueError("Core Reflector feedback fields mismatch")
    if value["role"] not in {"QUAL", "CLAR", "SIGN", "ORIG", "POL"}:
        raise ValueError("Core Reflector role is invalid")
    if not isinstance(value["overall_fitness"], str):
        raise TypeError("Core Reflector fitness must be canonical Decimal text")
    for field in ("subdimension_scores", "failure_codes"):
        if not isinstance(value[field], Mapping):
            raise TypeError(f"Core Reflector {field} must be an aggregate mapping")
    if any(not isinstance(item, str) for item in value["subdimension_scores"].values()):
        raise TypeError("Core Reflector subdimension scores must be Decimal text")
    if any(type(item) is not int or item < 0 for item in value["failure_codes"].values()):
        raise TypeError("Core Reflector failure-code counts must be non-negative integers")
    if type(value["evaluable_paper_count"]) is not int or value["evaluable_paper_count"] < 0:
        raise TypeError("Core Reflector evaluable-paper count is invalid")
    return {
        "role": value["role"],
        "overall_fitness": value["overall_fitness"],
        "subdimension_scores": dict(sorted(value["subdimension_scores"].items())),
        "failure_codes": dict(sorted(value["failure_codes"].items())),
        "evaluable_paper_count": value["evaluable_paper_count"],
    }


@dataclass(frozen=True)
class CoreReflectorResult:
    program_bytes: bytes
    raw_attempts: tuple[str, ...]


class CoreReflector:
    """Produce one child from frozen aggregate feedback and a parent Program."""

    def __init__(
        self,
        *,
        complete: Callable[[Sequence[Mapping[str, str]]], str],
        validate_program: Callable[[bytes], None],
        max_mutation_attempts: int = 3,
        max_compile_fix_attempts: int = 3,
    ):
        self.complete = complete
        self.validate_program = validate_program
        self.max_mutation_attempts = int(max_mutation_attempts)
        self.max_compile_fix_attempts = int(max_compile_fix_attempts)
        if self.max_mutation_attempts < 1 or self.max_compile_fix_attempts < 0:
            raise ValueError("Core Reflector attempt budgets are invalid")

    @staticmethod
    def _prompt(parent: str, feedback: Mapping[str, Any], attempt: int) -> str:
        return (
            "Improve one Research Validity Core v1 MemoryProgram from aggregate-only "
            "role feedback. Candidate sessions are paper-isolated. Review Memory is "
            "immutable and retrieval is model-free. The Program must declare exactly "
            "one MEMORY_READ_MODE from DIRECT_RETRIEVAL, "
            "POST_RETRIEVAL_LLM_SUMMARY, STRUCTURED_EXPERIENCE_RECALL and implement "
            "build_retrieval_plan(paper_text, role) returning only deterministic "
            "Review Memory API actions. It has no generic LLM, Memory write, external "
            "network, Human/OpenReview/CoCo or hidden rerank capability. Only Summary "
            "mode receives one host-owned post-retrieval condensation call.\n\n"
            "Do not infer any per-paper answer; only the following frozen aggregate "
            "feedback is available:\n"
            + json.dumps(feedback, ensure_ascii=False, sort_keys=True)
            + f"\n\nGLOBAL ATTEMPT INDEX: {attempt}\n\nPARENT PROGRAM:\n"
            + parent
            + "\n\nReturn a commit message followed by exactly one V4A contextual "
            "*** Update File: program.py patch."
        )

    def _repair_prompt(self, code: str, errors: str) -> str:
        return (
            "Repair this Core v1 MemoryProgram with the smallest V4A contextual "
            "Update File patch. Preserve its declared mode unless changing mode is "
            "required by the existing code. Do not add Query/KnowledgeItem classes, "
            "Memory writes, generic llm_completion, network or evaluator access.\n\n"
            f"ERRORS:\n{errors}\n\nPROGRAM:\n{code}"
        )

    def mutate(
        self,
        parent: CoreCandidate,
        feedback: Mapping[str, Any],
        attempt_index: int,
    ) -> bytes:
        aggregate = validate_core_reflector_feedback(feedback)
        parent_text = parent.program_bytes.decode("utf-8", errors="strict")
        raw_attempts = []
        last_error = "Core Reflector did not return a legal Program"
        for _ in range(self.max_mutation_attempts):
            raw = str(
                self.complete(
                    (
                        {
                            "role": "user",
                            "content": self._prompt(parent_text, aggregate, attempt_index),
                        },
                    )
                )
            )
            raw_attempts.append(raw)
            try:
                child_text, _, _ = _apply_v4a_patch(parent_text, raw)
                child = child_text.encode("utf-8")
                self.validate_program(child)
                return child
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                candidate = locals().get("child_text", parent_text)
                for _ in range(self.max_compile_fix_attempts):
                    repair_raw = str(
                        self.complete(
                            (
                                {
                                    "role": "user",
                                    "content": self._repair_prompt(candidate, last_error),
                                },
                            )
                        )
                    )
                    raw_attempts.append(repair_raw)
                    try:
                        candidate, _, _ = _apply_v4a_patch(candidate, repair_raw)
                        repaired = candidate.encode("utf-8")
                        self.validate_program(repaired)
                        return repaired
                    except Exception as repair_exc:
                        last_error = f"{type(repair_exc).__name__}: {repair_exc}"
        raise CandidateOriginatedFailure(
            "Core Reflector/compile-fix exhausted frozen attempts: " + last_error
        )
