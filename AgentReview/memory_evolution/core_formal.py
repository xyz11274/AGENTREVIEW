"""Research Validity Core v1 formal semantic orchestrator.

This module is the new-formal composition root.  It intentionally accepts no
Human/OpenReview/CoCo assets and never imports the legacy CoCo evaluator.  I/O,
model transport and durable journals wrap the injected callbacks; the research
semantics live here and in the Core v1 modules it composes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple
from pathlib import Path

from agentreview.final_suite import RoleWinner, freeze_final_suite
from agentreview.review_corpus import ROLE_SUBDIMENSIONS

from .core_search import (
    CandidateOriginatedFailure,
    CoreCandidate,
    CoreSearchResult,
    RoleFormalValidityFailure,
    SEED_ORDER,
    SharedPoolMStar,
)
from .role_evaluator import (
    ApplicabilityConsistencyGate,
    CanonicalApplicabilityRecord,
    CanonicalScoringRecord,
    ReferenceFreeRoleEvaluator,
    canonical_fitness,
)


CORE_V1_SEED_FILES = (
    "vector_search_v2.py",
    "llm_summarizer_v2.py",
    "experience_learner_v2.py",
)


def core_v1_seed_paths() -> Tuple[Path, ...]:
    root = Path(__file__).resolve().parent / "seeds"
    paths = tuple(root / name for name in CORE_V1_SEED_FILES)
    if not all(path.is_file() for path in paths):
        raise FileNotFoundError("Research Validity Core v1 seed asset is missing")
    return paths


@dataclass(frozen=True)
class CorePaperInput:
    paper_id: str
    reviewer_question: str
    paper_text: str
    structural_context: str
    frozen_external_evidence: str = ""

    def __post_init__(self):
        if not self.paper_id or not self.reviewer_question or not self.paper_text:
            raise ValueError("Core formal paper identity/question/text are required")


@dataclass(frozen=True)
class CandidatePaperEvaluation:
    candidate_id: str
    paper_id: str
    review_text: str
    applicability: CanonicalApplicabilityRecord
    scoring: CanonicalScoringRecord


@dataclass(frozen=True)
class CandidateBatchEvaluation:
    candidate_id: str
    stage: str
    paper_ids: Tuple[str, ...]
    records: Tuple[CandidatePaperEvaluation, ...]
    fitness: str


@dataclass(frozen=True)
class CoreRoleRunResult:
    role: str
    role_run_identity: str
    search: CoreSearchResult
    winner: RoleWinner
    evaluations: Tuple[CandidateBatchEvaluation, ...]


def _unique_papers(values: Iterable[CorePaperInput], label: str) -> Tuple[CorePaperInput, ...]:
    output = tuple(values)
    ids = tuple(item.paper_id for item in output)
    if not output or len(set(ids)) != len(ids):
        raise ValueError(f"{label} must be non-empty with unique paper ids")
    return output


class CoreRoleEvolutionRunner:
    """Run one role under the frozen shared-pool Core v1 semantics."""

    def __init__(
        self,
        *,
        role: str,
        role_run_identity: str,
        run_seed: int,
        static_papers: Iterable[CorePaperInput],
        rotating_schedule: Sequence[Iterable[CorePaperInput]],
        full_pool_papers: Iterable[CorePaperInput],
        evaluator: ReferenceFreeRoleEvaluator,
        review_candidate: Callable[[bytes, str, CorePaperInput], str],
        mutate: Callable[[CoreCandidate, Mapping[str, Any], int], bytes],
        validate_program: Callable[[bytes], None],
        behavior_manifest: Callable[[bytes], Mapping[str, Any]],
        attempts: int = 20,
    ):
        if role not in ROLE_SUBDIMENSIONS:
            raise ValueError(f"Unknown specialist role: {role}")
        self.role = role
        self.role_run_identity = role_run_identity
        self.run_seed = int(run_seed)
        self.static_papers = _unique_papers(static_papers, "static papers")
        self.full_pool_papers = _unique_papers(full_pool_papers, "full-pool papers")
        self.rotating_schedule = tuple(
            _unique_papers(values, f"rotating batch {index}")
            for index, values in enumerate(rotating_schedule)
        )
        if attempts and len(self.rotating_schedule) != attempts:
            raise ValueError("Rotating schedule must contain exactly one batch per attempt")
        self.evaluator = evaluator
        self.review_candidate = review_candidate
        self.mutate_callback = mutate
        self.validate_program = validate_program
        self.behavior_manifest = behavior_manifest
        self.attempts = int(attempts)
        self.applicability_gate = ApplicabilityConsistencyGate()
        self._evaluations = []

    def _evaluate(
        self,
        program_bytes: bytes,
        candidate_id: str,
        papers: Sequence[CorePaperInput],
        stage: str,
    ) -> CandidateBatchEvaluation:
        records = []
        for paper in papers:
            # review_candidate must create a fresh Candidate × paper × role
            # Memory/Reviewer session.  The evaluator sessions below are also
            # separate and applicability is Candidate-blind by API construction.
            review = self.review_candidate(program_bytes, candidate_id, paper)
            if not isinstance(review, str) or not review:
                raise CandidateOriginatedFailure(
                    "Candidate Reviewer returned an empty review"
                )
            try:
                applicability = self.evaluator.applicability(
                    role=self.role,
                    paper_id=paper.paper_id,
                    paper=paper.paper_text,
                    structural_context=paper.structural_context,
                    frozen_external_evidence=paper.frozen_external_evidence,
                )
                self.applicability_gate.observe(applicability)
                scoring = self.evaluator.score(
                    applicability=applicability,
                    candidate_id=candidate_id,
                    candidate_review=review,
                    paper=paper.paper_text,
                    structural_context=paper.structural_context,
                    frozen_external_evidence=paper.frozen_external_evidence,
                )
            except RuntimeError as exc:
                raise RoleFormalValidityFailure(str(exc)) from exc
            records.append(
                CandidatePaperEvaluation(
                    candidate_id,
                    paper.paper_id,
                    review,
                    applicability,
                    scoring,
                )
            )
        result = CandidateBatchEvaluation(
            candidate_id=candidate_id,
            stage=stage,
            paper_ids=tuple(item.paper_id for item in papers),
            records=tuple(records),
            fitness=canonical_fitness(item.scoring for item in records),
        )
        self._evaluations.append(result)
        return result

    def _rotating_feedback(self, parent: CoreCandidate, attempt_index: int) -> Mapping[str, Any]:
        batch = self._evaluate(
            parent.program_bytes,
            parent.candidate_id,
            self.rotating_schedule[attempt_index - 1],
            f"ROTATING_{attempt_index}",
        )
        by_subdimension: Dict[str, list[int]] = defaultdict(list)
        event_codes = Counter()
        for record in batch.records:
            for decision in record.scoring.scores:
                if decision.score is not None:
                    by_subdimension[decision.subdimension_id].append(decision.score)
                event_codes.update(event["code"] for event in decision.events)
        return {
            "role": self.role,
            "overall_fitness": batch.fitness,
            "subdimension_scores": {
                subdimension: self._aggregate_integer_scores(values)
                for subdimension, values in sorted(by_subdimension.items())
            },
            "failure_codes": dict(sorted(event_codes.items())),
            "evaluable_paper_count": sum(
                item.scoring.paper_fraction is not None for item in batch.records
            ),
        }

    @staticmethod
    def _aggregate_integer_scores(values: Sequence[int]) -> str:
        exact = Fraction(sum(values), len(values))
        with localcontext() as context:
            context.prec = 50
            value = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
                Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
            )
        return format(abs(value) if value == 0 else value, ".12f")

    def run(self, initial_seeds: Mapping[str, bytes]) -> CoreRoleRunResult:
        if tuple(initial_seeds) != SEED_ORDER:
            raise ValueError("Core role runner requires the three mandatory seeds in order")

        def static(program: bytes, candidate_id: str) -> str:
            return self._evaluate(
                program, candidate_id, self.static_papers, "STATIC_25"
            ).fitness

        def full(candidate: CoreCandidate) -> str:
            return self._evaluate(
                candidate.program_bytes,
                candidate.candidate_id,
                self.full_pool_papers,
                "FULL_POOL_FINALIST",
            ).fitness

        search = SharedPoolMStar(
            role=self.role,
            run_seed=self.run_seed,
            controller_identity=self.role_run_identity,
            static_evaluate=static,
            rotating_feedback=self._rotating_feedback,
            mutate=self.mutate_callback,
            validate_program=self.validate_program,
            full_pool_evaluate=full,
            behavior_manifest=self.behavior_manifest,
            attempts=self.attempts,
            tau=0.15,
            finalist_k=3,
        ).run(initial_seeds)
        candidate = next(
            item for item in search.eligible_pool if item.candidate_id == search.winner.candidate_id
        )
        winner = RoleWinner(
            role=self.role,
            role_run_identity=self.role_run_identity,
            candidate_id=candidate.candidate_id,
            program_identity_sha256=candidate.program_identity_sha256,
            full_pool_fitness=search.winner.full_pool_fitness,
        )
        return CoreRoleRunResult(
            self.role,
            self.role_run_identity,
            search,
            winner,
            tuple(self._evaluations),
        )


def freeze_suite_from_role_runs(
    role_runs: Mapping[str, CoreRoleRunResult],
):
    if set(role_runs) != set(ROLE_SUBDIMENSIONS):
        raise ValueError("Final Suite requires exactly five completed role runs")
    return freeze_final_suite(
        {role: result.winner for role, result in role_runs.items()}
    )
