"""M★-aligned role-local pool, Reflector, patcher and lineage."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentreview.reviewer_runtime.sandbox import SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .constants import REFLECTOR_MAX_TOKENS, ROLES
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient
from .memory import SEED_NAMES, seed_program_path
from .patcher import AppliedPatch, apply_v4a_patch
from .program_checks import static_check

MAX_MUTATION_ATTEMPTS = 20


@dataclass(frozen=True)
class PoolEntry:
    candidate_id: str
    role: str
    program_path: str
    program_sha256: str
    parent_id: str | None
    static_score: float
    seed_kind: str
    lineage: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvolutionPool:
    role: str
    entries: list[PoolEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown Specialist role: {self.role}")
        if any(entry.role != self.role for entry in self.entries):
            raise ValueError("Evolution pool contains a cross-role candidate")

    def add(self, entry: PoolEntry) -> None:
        if entry.role != self.role:
            raise ValueError("Evolution pool role mismatch")
        if any(value.candidate_id == entry.candidate_id for value in self.entries):
            raise ValueError(f"Duplicate candidate_id: {entry.candidate_id}")
        self.entries.append(entry)

    def select_parent(
        self,
        *,
        temperature: float = 0.15,
        seed: int,
    ) -> PoolEntry:
        if not self.entries:
            raise RuntimeError("Evolution pool is empty")
        temperature = max(float(temperature), 1e-6)
        logits = [entry.static_score / temperature for entry in self.entries]
        offset = max(logits)
        weights = [math.exp(value - offset) for value in logits]
        return random.Random(seed).choices(self.entries, weights=weights, k=1)[0]

    def best(self) -> PoolEntry:
        if not self.entries:
            raise RuntimeError("Evolution pool is empty")
        return max(self.entries, key=lambda value: (value.static_score, value.candidate_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "research-v2-role-pool-v1",
            "role": self.role,
            "entries": [asdict(entry) for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvolutionPool":
        return cls(
            role=str(value.get("role") or ""),
            entries=[PoolEntry(**dict(item)) for item in value.get("entries") or []],
        )


def static_mean(evaluation_paths: list[str | Path]) -> float:
    if not evaluation_paths:
        raise ValueError("Static score requires at least one Internal Evaluation")
    values = [float(read_json(path).get("review_quality")) for path in evaluation_paths]
    if any(value < 1.0 or value > 5.0 for value in values):
        raise ValueError("Static review_quality values must be in [1, 5]")
    return sum(values) / len(values)


def initialize_pool(
    *,
    role: str,
    seed_static_evaluations: dict[str, list[str | Path]],
    pool_path: str | Path,
) -> EvolutionPool:
    """Create the three-seed role-local pool after their static evaluations exist."""
    if set(seed_static_evaluations) != set(SEED_NAMES):
        raise ValueError(f"Initial pool requires exactly these seeds: {SEED_NAMES}")
    pool = EvolutionPool(role)
    for seed_name in SEED_NAMES:
        program = seed_program_path(seed_name).resolve()
        pool.add(
            PoolEntry(
                candidate_id=f"seed-{seed_name}",
                role=role,
                program_path=str(program),
                program_sha256=file_sha256(program),
                parent_id=None,
                static_score=static_mean(seed_static_evaluations[seed_name]),
                seed_kind=seed_name,
                lineage=[
                    {
                        "candidate_id": f"seed-{seed_name}",
                        "parent_id": None,
                        "change": {"title": f"Initial {seed_name} seed", "details": []},
                    }
                ],
            )
        )
    atomic_write_json(pool_path, pool.to_dict())
    return pool


def feedback_packet(
    *,
    parent: PoolEntry,
    evaluation_paths: list[str | Path],
    review_paths: list[str | Path],
    maximum_cases: int = 5,
) -> dict[str, Any]:
    reviews = {str(value.get("review_id")): value for value in map(read_json, review_paths)}
    evaluations = [read_json(path) for path in evaluation_paths]
    if any(str(value.get("role") or "") != parent.role for value in reviews.values()):
        raise ValueError("Evolution feedback contains a cross-role Review")
    if any(str(value.get("role") or "") != parent.role for value in evaluations):
        raise ValueError("Evolution feedback contains a cross-role Internal Evaluation")
    evaluations.sort(key=lambda value: (float(value.get("review_quality", 5.0)), str(value.get("review_id"))))
    cases = []
    for value in evaluations[:maximum_cases]:
        review = reviews.get(str(value.get("review_id")))
        if review is None:
            raise ValueError("Internal Evaluation has no matching complete Review")
        retrieval = dict(review.get("retrieval_provenance") or {})
        memory = dict(retrieval.get("memory") or {})
        s2 = dict(retrieval.get("s2") or {})
        cases.append(
            {
                "paper_id": review.get("paper_id"),
                "complete_review": review.get("review_text"),
                "memory_query": memory.get("query"),
                "memory_hit_log": memory.get("logs"),
                "s2_result_ids": s2.get("result_ids"),
                "summarizer_output": memory.get("context")
                if memory.get("seed_kind") == "llm_summarizer"
                else None,
                "review_quality": value.get("review_quality"),
                "major_missing_points": value.get("major_missing_points"),
                "factual_evidence_issues": value.get("factual_evidence_issues"),
                "improvement_suggestions": value.get("improvement_suggestions"),
            }
        )
    return {
        "parent": {
            "candidate_id": parent.candidate_id,
            "program_sha256": parent.program_sha256,
            "static_mean_review_quality": parent.static_score,
            "seed_kind": parent.seed_kind,
            "lineage": parent.lineage,
        },
        "rotating_failure_cases": cases,
    }


def _reflector_prompt(parent_code: str, packet: dict[str, Any]) -> str:
    return f"""You are the M★ Reflector for one Specialist Reviewer's MemoryProgram. Improve the Program using only the saved Review and Internal Evaluation feedback below.

The candidate-visible Program consists of Program instructions, KnowledgeItem, Query, and KnowledgeBase.write/read. Preserve the M★ interfaces. Review Run constructs Query deterministically and uses a frozen read-only snapshot. Generative KnowledgeItem creation is allowed only during offline Memory Build. Retrieval hit selection must remain deterministic. Only an explicit Summarizer Program may call one LLM after it freezes and logs hit IDs. Never access CoCo, human reviews, other roles, files, network, subprocesses, eval, or exec.

Return exactly one commit message followed by one V4A patch for program.py:
*** Commit Message
Title: <one-line change>
- <root cause>
- <specific change>
*** Begin Patch
*** Update File: program.py
@@
 context
-old
+new
 context
*** End Patch

Feedback packet:
{json.dumps(packet, ensure_ascii=False)}

Parent program:
```python
{parent_code}
```
""".strip()


def _repair_prompt(parent_code: str, response: str, error: str) -> str:
    return f"""Repair one failed M★ V4A candidate. Use only the parent, failed patch, and compiler/protocol error below. Return exactly one valid V4A Update File patch for program.py and no Markdown fence.

Error:
{error}

Parent program:
{parent_code}

Failed response:
{response}
""".strip()


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
    return dataclass_type(**values)


def validate_program(
    path: str | Path,
    *,
    require_bubblewrap: bool,
) -> list[str]:
    source = Path(path).expanduser().resolve()
    errors = static_check(source.read_text(encoding="utf-8"))
    if errors:
        return errors

    class SmokeLLM:
        def llm_completion(self, messages, **kwargs):
            return "smoke summary"

    toolkit = MemoryToolkit(llm_client=SmokeLLM(), max_llm_calls_per_operation=1)
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
            with toolkit.operation("protocol_smoke_write", max_llm_calls=1):
                sandbox.write(item, "traceable Review Experience")
            with toolkit.operation("protocol_smoke_read", max_llm_calls=1):
                result = sandbox.read(query)
            if not isinstance(result, str):
                errors.append("KnowledgeBase.read() did not return str")
            elif len(result) > 6000:
                errors.append("KnowledgeBase.read() exceeded 6000 characters")
    except Exception as exc:
        errors.append(f"protocol smoke failed: {type(exc).__name__}: {exc}")
    return errors


def _write_program(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") == code:
            return
        raise FileExistsError(f"Candidate Program already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(code, encoding="utf-8")
    temporary.replace(path)


def propose_candidate(
    *,
    role: str,
    pool: EvolutionPool,
    evaluation_paths: list[str | Path],
    review_paths: list[str | Path],
    attempt: int,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    require_bubblewrap: bool = True,
) -> dict[str, Any]:
    """Consume exactly one attempt and save success or failure; no candidate is pooled yet."""
    if pool.role != role:
        raise ValueError("Evolution attempt role differs from its pool")
    if not 1 <= int(attempt) <= MAX_MUTATION_ATTEMPTS:
        raise ValueError(f"Evolution attempt must be in [1, {MAX_MUTATION_ATTEMPTS}]")
    parent = pool.select_parent(seed=attempt)
    parent_path = Path(parent.program_path).expanduser().resolve()
    parent_code = parent_path.read_text(encoding="utf-8")
    packet = feedback_packet(
        parent=parent,
        evaluation_paths=evaluation_paths,
        review_paths=review_paths,
    )
    prompt = _reflector_prompt(parent_code, packet)
    raw = completion_client.complete(
        [{"role": "user", "content": prompt}],
        max_tokens=REFLECTOR_MAX_TOKENS,
        component="reflector",
    )
    repair_used = False
    errors: list[str] = []
    applied: AppliedPatch | None = None
    candidate_id = hashlib.sha256(
        f"{run_id}\n{role}\n{attempt}\n{parent.candidate_id}".encode("utf-8")
    ).hexdigest()[:24]
    program_path = (
        Path(output_root).expanduser().resolve()
        / "programs"
        / role
        / candidate_id
        / "program.py"
    )
    for response in (raw,):
        try:
            applied = apply_v4a_patch(parent_code, response)
            _write_program(program_path, applied.code)
            errors = validate_program(
                program_path,
                require_bubblewrap=require_bubblewrap,
            )
        except Exception as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
        if applied is not None and not errors:
            break
    if errors:
        repair_used = True
        repair_response = completion_client.complete(
            [
                {
                    "role": "user",
                    "content": _repair_prompt(parent_code, raw, "\n".join(errors)),
                }
            ],
            max_tokens=REFLECTOR_MAX_TOKENS,
            component="reflector_repair",
        )
        try:
            applied = apply_v4a_patch(parent_code, repair_response)
            if program_path.exists():
                program_path.unlink()
            _write_program(program_path, applied.code)
            errors = validate_program(
                program_path,
                require_bubblewrap=require_bubblewrap,
            )
            raw = raw + "\n\n--- EXPLICIT REPAIR ---\n\n" + repair_response
        except Exception as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
    status = "proposed" if applied is not None and not errors else "failed"
    outcome = {
        "protocol": "research-v2-evolution-attempt-v1",
        "run_id": run_id,
        "attempt": attempt,
        "role": role,
        "candidate_id": candidate_id,
        "parent_id": parent.candidate_id,
        "status": status,
        "repair_used": repair_used,
        "program_path": str(program_path) if status == "proposed" else "",
        "program_sha256": file_sha256(program_path) if status == "proposed" else "",
        "change": {
            "title": applied.title if applied else "",
            "details": applied.details if applied else [],
        },
        "errors": errors,
        "raw_reflector_output": raw,
        "feedback_sha256": value_sha256(packet),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(
        Path(output_root).expanduser().resolve()
        / "evolution"
        / role
        / run_id
        / f"attempt-{attempt:03d}.json",
        outcome,
    )
    return outcome


def commit_candidate(
    *,
    pool: EvolutionPool,
    attempt_path: str | Path,
    static_evaluation_paths: list[str | Path],
    pool_path: str | Path,
) -> PoolEntry:
    attempt = read_json(attempt_path)
    if attempt.get("status") != "proposed":
        raise ValueError("Only a successfully proposed candidate can enter the pool")
    if attempt.get("role") != pool.role:
        raise ValueError("Evolution attempt role differs from its pool")
    program_path = str(attempt.get("program_path") or "")
    if not program_path or file_sha256(program_path) != attempt.get("program_sha256"):
        raise ValueError("Evolution attempt Program source no longer matches its artifact")
    score = static_mean(static_evaluation_paths)
    parent = next(
        value for value in pool.entries if value.candidate_id == attempt.get("parent_id")
    )
    entry = PoolEntry(
        candidate_id=str(attempt["candidate_id"]),
        role=pool.role,
        program_path=str(attempt["program_path"]),
        program_sha256=str(attempt["program_sha256"]),
        parent_id=parent.candidate_id,
        static_score=score,
        seed_kind=parent.seed_kind,
        lineage=[
            *parent.lineage,
            {
                "candidate_id": attempt["candidate_id"],
                "parent_id": parent.candidate_id,
                "change": attempt.get("change"),
                "static_score": score,
            },
        ],
    )
    pool.add(entry)
    atomic_write_json(pool_path, pool.to_dict(), immutable=False)
    return entry
