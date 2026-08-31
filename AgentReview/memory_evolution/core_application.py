"""Production composition root for Research Validity Core v1.

This module performs dependency wiring only.  Search, Reviewer Memory,
reference-free evaluation, finalist/winner selection and protected Official
access remain owned by their existing deep modules.  In particular, this
module accepts no Human/OpenReview/CoCo value on its Evolution path.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence

from agentreview.final_suite import (
    FinalReviewSuite,
    ProtectedOfficialAccess,
    RoleWinner,
    freeze_final_suite,
)
from agentreview.frozen_evidence import FrozenEvidencePool
from agentreview.review_corpus import ROLE_SUBDIMENSIONS, canonical_json_bytes
from agentreview.review_memory import ReviewMemoryBudget, ReviewMemoryStore
from agentreview.reviewer_runtime.memory_program_v2 import (
    CORE_V1_PROTOCOL,
    CoreV1ReviewerSession,
    MemoryReadMode,
)
from agentreview.reviewer_runtime.models import ReviewRequest
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

from .b_local_manifest import BLocalManifest
from .core_formal import (
    CorePaperInput,
    CoreRoleEvolutionRunner,
    CoreRoleRunResult,
    core_v1_seed_paths,
    freeze_suite_from_role_runs,
)
from .core_reflector import CoreReflector, FEEDBACK_FIELDS
from .core_scheduler import GlobalQwenService, ROLE_ORDER, ScheduledToolkitProxy
from .core_search import SEED_ORDER
from .role_evaluator import ReferenceFreeRoleEvaluator


APPLICATION_SCHEMA_VERSION = "research-validity-core-v1-application-v1"


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _literal_assignments(program_bytes: bytes) -> Dict[str, Any]:
    text = program_bytes.decode("utf-8", errors="strict")
    tree = ast.parse(text, filename="program.py")
    output: Dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            try:
                output[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return output


def validate_core_program(program_bytes: bytes) -> None:
    """Mechanical admission check before the sandbox runtime check."""
    values = _literal_assignments(program_bytes)
    if values.get("MEMORY_PROGRAM_PROTOCOL") != CORE_V1_PROTOCOL:
        raise ValueError("Program does not declare the Core v1 protocol")
    try:
        MemoryReadMode(values.get("MEMORY_READ_MODE"))
    except ValueError as exc:
        raise ValueError("Program does not declare one closed memory_read_mode") from exc
    if "build_retrieval_plan" not in program_bytes.decode("utf-8", errors="strict"):
        raise ValueError("Program lacks build_retrieval_plan")
    compile(program_bytes, "program.py", "exec")


def core_program_behavior_manifest(program_bytes: bytes) -> Mapping[str, Any]:
    values = _literal_assignments(program_bytes)
    validate_core_program(program_bytes)
    return {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "memory_program_protocol": values["MEMORY_PROGRAM_PROTOCOL"],
        "memory_read_mode": values["MEMORY_READ_MODE"],
        "allowed_capability": (
            "post_retrieval_summary"
            if values["MEMORY_READ_MODE"] == MemoryReadMode.POST_RETRIEVAL_LLM_SUMMARY.value
            else "model_free_review_memory"
        ),
    }


@dataclass(frozen=True)
class CoreRuntimeLimits:
    memory_budget: ReviewMemoryBudget
    max_reviewer_memory_context_tokens: int
    max_summarizer_input_tokens: int
    max_summarizer_query_tokens_total: int
    qwen_capacity: int = 1
    reviewer_timeout_seconds: float = 60.0
    reviewer_memory_mb: int = 1024


@dataclass(frozen=True)
class CoreApplicationDependencies:
    memory_store: ReviewMemoryStore
    b_local_manifest: BLocalManifest
    evidence_pool: FrozenEvidencePool
    role_rubrics: Mapping[str, str]
    toolkit_factory: Callable[[str], MemoryToolkit]
    protected_loader: Callable[[], Any]
    official_runner: Callable[[FinalReviewSuite, Any], Mapping[str, Any]]
    token_count: Callable[[str], int]


class CoreV1FormalApplication:
    """Narrow lifecycle API for validation, role runs, Suite and Official."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        dependencies: CoreApplicationDependencies,
        limits: CoreRuntimeLimits,
        run_seed: int = 0,
        attempts: int | None = None,
        require_bubblewrap: bool = True,
    ):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.dependencies = dependencies
        self.limits = limits
        self.run_seed = int(run_seed)
        self.attempts = (
            dependencies.b_local_manifest.attempts if attempts is None else int(attempts)
        )
        self.require_bubblewrap = bool(require_bubblewrap)
        self._service = GlobalQwenService(limits.qwen_capacity)
        self._toolkits = {
            role: ScheduledToolkitProxy(
                dependencies.toolkit_factory(role), self._service, role
            )
            for role in ROLE_ORDER
        }
        self._role_results: Dict[str, CoreRoleRunResult] = {}
        self._role_audits: Dict[str, Dict[str, Any]] = {}
        self._suite: FinalReviewSuite | None = None
        self._official_access = ProtectedOfficialAccess(dependencies.protected_loader)
        self.validate_inputs()

    def close(self) -> None:
        service = getattr(self, "_service", None)
        if service is not None:
            service.close()
            self._service = None

    def __enter__(self) -> "CoreV1FormalApplication":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def validate_inputs(self) -> Mapping[str, Any]:
        manifest = self.dependencies.b_local_manifest
        evidence = self.dependencies.evidence_pool
        if manifest.attempts != self.attempts:
            raise ValueError("Application attempt budget differs from B-local schedule")
        if tuple(self.dependencies.role_rubrics) != tuple(ROLE_SUBDIMENSIONS):
            raise ValueError("Role rubric order/coverage mismatch")
        if set(item.target_paper_id for item in evidence.manifests) != set(
            item.paper_id for item in manifest.papers
        ):
            raise ValueError("Frozen Evidence targets differ from B-local papers")
        missing_indices = set(ROLE_ORDER) - set(self.dependencies.memory_store.indices)
        if missing_indices:
            raise ValueError(f"Review Memory role indices are missing: {sorted(missing_indices)}")
        return {
            "status": "PASS",
            "application_schema_version": APPLICATION_SCHEMA_VERSION,
            "b_local_manifest_sha256": manifest.manifest_sha256,
            "evidence_pool_sha256": evidence.pool_sha256,
            "corpus_manifest_sha256": self.dependencies.memory_store.materialization.manifest[
                "corpus_manifest_sha256"
            ],
            "role_order": list(ROLE_ORDER),
        }

    def _paper_input(self, paper_id: str) -> CorePaperInput:
        paper = self.dependencies.b_local_manifest.paper(paper_id)
        return CorePaperInput(
            paper_id=paper.paper_id,
            reviewer_question=paper.reviewer_question,
            paper_text=paper.paper_text,
            structural_context=paper.structural_context,
            frozen_external_evidence=self.dependencies.evidence_pool.render_full_context(
                paper.paper_id
            ),
        )

    def _program_path(self, program_bytes: bytes) -> Path:
        digest = hashlib.sha256(program_bytes).hexdigest()
        path = self.output_dir / "programs" / f"{digest}.py"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".py.tmp")
            with temporary.open("wb") as handle:
                handle.write(program_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        elif path.read_bytes() != program_bytes:
            raise RuntimeError("Immutable Program artifact collision")
        return path

    def _seed_bytes(self) -> Dict[str, bytes]:
        return {
            key: path.read_bytes()
            for key, path in zip(SEED_ORDER, core_v1_seed_paths())
        }

    def run_role(self, role: str) -> CoreRoleRunResult:
        if role in self._role_results:
            return self._role_results[role]
        if role not in ROLE_ORDER:
            raise ValueError("Unknown Core specialist role")
        view = self.dependencies.b_local_manifest.role_view(role)
        static = tuple(self._paper_input(value) for value in view.static_paper_ids)
        rotating = tuple(
            tuple(self._paper_input(value) for value in batch)
            for batch in view.rotating_schedule
        )
        full_pool = tuple(self._paper_input(value) for value in view.full_pool_paper_ids)
        toolkit = self._toolkits[role]
        counters = {
            "reviewer_session_count": 0,
            "evaluator_applicability_count": 0,
            "evaluator_scoring_count": 0,
        }
        reflected_fields: list[list[str]] = []

        def evaluator_complete(messages):
            user = json.loads(messages[1]["content"])
            if user["required_output_fields"][-1] == "decisions":
                counters["evaluator_applicability_count"] += 1
            else:
                counters["evaluator_scoring_count"] += 1
            return toolkit.llm_completion(
                list(messages), agent_name="Core Role Evaluator"
            )

        evaluator = ReferenceFreeRoleEvaluator(
            complete=evaluator_complete,
            evaluator_identity="core-v1-evaluator:" + role,
            protocol_identity="core-v1-role-rubric:" + role,
            role_rubric=self.dependencies.role_rubrics[role],
        )

        def validate(program_bytes: bytes) -> None:
            validate_core_program(program_bytes)
            self._program_path(program_bytes)

        reflector = CoreReflector(
            complete=lambda messages: toolkit.llm_completion(
                list(messages), agent_name="Core Evolution Reflector"
            ),
            validate_program=validate,
            max_mutation_attempts=1,
            max_compile_fix_attempts=0,
        )

        def review_candidate(program_bytes, candidate_id, paper):
            counters["reviewer_session_count"] += 1
            path = self._program_path(program_bytes)
            with CoreV1ReviewerSession(
                task_id=role,
                memory_program=str(path),
                toolkit=toolkit,
                memory_store=self.dependencies.memory_store,
                memory_budget=self.limits.memory_budget,
                candidate_id=candidate_id,
                max_reviewer_memory_context_tokens=(
                    self.limits.max_reviewer_memory_context_tokens
                ),
                max_summarizer_input_tokens=self.limits.max_summarizer_input_tokens,
                max_summarizer_query_tokens_total=(
                    self.limits.max_summarizer_query_tokens_total
                ),
                token_count=self.dependencies.token_count,
                require_bubblewrap=self.require_bubblewrap,
                timeout_seconds=self.limits.reviewer_timeout_seconds,
                memory_mb=self.limits.reviewer_memory_mb,
            ) as session:
                result = session.review(
                    ReviewRequest(
                        paper_id=paper.paper_id,
                        task_id=role,
                        question=paper.reviewer_question,
                        run_identity=f"core-v1:{role}",
                        provenance={
                            "b_local_manifest_sha256": (
                                self.dependencies.b_local_manifest.manifest_sha256
                            ),
                            "evidence_pool_sha256": self.dependencies.evidence_pool.pool_sha256,
                        },
                    )
                )
            return result.review_text

        def mutate(parent, feedback, attempt_index):
            reflected_fields.append(sorted(feedback))
            return reflector.mutate(parent, feedback, attempt_index)

        role_run_identity = _sha(
            {
                "identity_type": "core_v1_role_run",
                "schema_version": APPLICATION_SCHEMA_VERSION,
                "role": role,
                "run_seed": self.run_seed,
                "b_local_manifest_sha256": self.dependencies.b_local_manifest.manifest_sha256,
                "evidence_pool_sha256": self.dependencies.evidence_pool.pool_sha256,
            }
        )
        runner = CoreRoleEvolutionRunner(
            role=role,
            role_run_identity=role_run_identity,
            run_seed=self.run_seed,
            static_papers=static,
            rotating_schedule=rotating,
            full_pool_papers=full_pool,
            evaluator=evaluator,
            review_candidate=review_candidate,
            mutate=mutate,
            validate_program=validate,
            behavior_manifest=core_program_behavior_manifest,
            attempts=self.attempts,
            min_static_evaluable=view.min_static_evaluable,
            min_full_pool_evaluable=view.min_full_pool_evaluable,
        )
        result = runner.run(self._seed_bytes())
        self._role_results[role] = result
        audit = {
            "runner_class": type(runner).__name__,
            **counters,
            "attempt_count": len(result.search.attempts),
            "attempts": [
                {
                    "global_attempt_index": item.global_attempt_index,
                    "parent_candidate_id": item.parent_candidate_id,
                    "child_candidate_id": item.child_candidate_id,
                    "disposition": item.disposition,
                    "error_code": item.error_code,
                }
                for item in result.search.attempts
            ],
            "reflector_feedback_fields": (
                reflected_fields[0] if reflected_fields else sorted(FEEDBACK_FIELDS)
            ),
            "winner": asdict(result.winner),
        }
        self._role_audits[role] = audit
        _atomic_json(self.output_dir / "role_winners" / f"{role}.json", {
            "schema_version": APPLICATION_SCHEMA_VERSION,
            "role_run_identity": result.role_run_identity,
            "winner": asdict(result.winner),
            "audit": audit,
        })
        return result

    def freeze_suite(self) -> FinalReviewSuite:
        if set(self._role_results) == set(ROLE_ORDER):
            suite = freeze_suite_from_role_runs(self._role_results)
        else:
            winners: Dict[str, RoleWinner] = {}
            for role in ROLE_ORDER:
                path = self.output_dir / "role_winners" / f"{role}.json"
                if not path.is_file():
                    raise RuntimeError("Final Suite requires five durable role winners")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or set(value) != {
                    "schema_version", "role_run_identity", "winner", "audit"
                }:
                    raise ValueError("Durable role winner schema mismatch")
                if value["schema_version"] != APPLICATION_SCHEMA_VERSION:
                    raise ValueError("Durable role winner version mismatch")
                winner = RoleWinner(**value["winner"])
                if winner.role != role or winner.role_run_identity != value["role_run_identity"]:
                    raise ValueError("Durable role winner identity mismatch")
                winners[role] = winner
            suite = freeze_final_suite(winners)
        self._suite = suite
        _atomic_json(
            self.output_dir / "final_suite.json",
            {
                "schema_version": APPLICATION_SCHEMA_VERSION,
                "suite_identity_sha256": suite.suite_identity_sha256,
                "payload": suite.payload,
            },
        )
        return suite

    def run_official(self) -> Mapping[str, Any]:
        if self._suite is None:
            path = self.output_dir / "final_suite.json"
            if not path.is_file():
                raise PermissionError("Official requires a frozen Final Suite")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != {
                "schema_version", "suite_identity_sha256", "payload"
            }:
                raise ValueError("Frozen Final Suite schema mismatch")
            payload = value["payload"]
            suite = FinalReviewSuite(
                winners=tuple(RoleWinner(**item) for item in payload["winners"]),
                meta_enabled=payload["meta_enabled"],
                schema_version=payload["schema_version"],
            )
            if suite.suite_identity_sha256 != value["suite_identity_sha256"]:
                raise ValueError("Frozen Final Suite identity mismatch")
            self._suite = suite
        self._official_access.authorize(self._suite)
        protected = self._official_access.read_protected_inputs(self._suite)
        result = dict(self.dependencies.official_runner(self._suite, protected))
        self._official_access.complete(self._suite)
        _atomic_json(self.output_dir / "official_result.json", result)
        return result

    @property
    def role_audits(self) -> Mapping[str, Mapping[str, Any]]:
        return dict(self._role_audits)

    @property
    def qwen_execution_count(self) -> int:
        service = self._service
        return 0 if service is None else len(service.execution_records)
