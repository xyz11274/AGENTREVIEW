"""Shared-pool M★ search semantics for Research Validity Core v1.

This module is intentionally independent of evaluator and model transports.  It
freezes the semantics that affect Candidate birth, RNG, parent selection,
admission, global top-3 finalists and the role winner.  Operational journaling
may wrap these transitions without changing them.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple


CORE_SEARCH_VERSION = "shared-pool-mstar-core-v1"
SEED_ORDER = ("vector_search", "llm_summarizer", "experience_learner")


class CandidateOriginatedFailure(RuntimeError):
    """A frozen Candidate/Program failure that consumes the current attempt."""


class RoleFormalValidityFailure(RuntimeError):
    """A role-wide validity failure that must stop the formal role run."""


class FormalInfrastructureFailure(RuntimeError):
    """A recoverable infrastructure failure that must not consume an attempt."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_fitness(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("fitness must be a canonical Decimal string")
    whole, dot, fraction = value.partition(".")
    if dot != "." or len(fraction) != 12 or not whole.isdigit() or not fraction.isdigit():
        raise ValueError("fitness must have exactly 12 decimal places")
    if not 0.0 <= float(value) <= 4.0:
        raise ValueError("fitness is outside [0,4]")
    return value


def program_identity(program_bytes: bytes, behavior_manifest: Mapping[str, Any]) -> str:
    return _digest(
        {
            "identity_type": "core_v1_program",
            "schema_version": CORE_SEARCH_VERSION,
            "program_bytes_sha256": hashlib.sha256(program_bytes).hexdigest(),
            "program_byte_length": len(program_bytes),
            "behavior_manifest": dict(behavior_manifest),
        }
    )


def candidate_identity(
    *,
    role: str,
    formal_run_seed: int,
    birth_kind: str,
    birth_slot: str | int,
    parent_candidate_id: str | None,
    program_identity_sha256: str,
    controller_identity: str,
) -> str:
    return "cand_" + _digest(
        {
            "identity_type": "canonical_candidate",
            "schema_version": CORE_SEARCH_VERSION,
            "role": role,
            "formal_run_seed": formal_run_seed,
            "birth_kind": birth_kind,
            "birth_slot": birth_slot,
            "parent_candidate_id": parent_candidate_id,
            "program_identity_sha256": program_identity_sha256,
            "controller_identity": controller_identity,
        }
    )


@dataclass(frozen=True)
class CoreCandidate:
    candidate_id: str
    program_identity_sha256: str
    program_bytes: bytes
    memory_seed_key: str
    parent_candidate_id: str | None
    birth_slot: str | int
    static_25_fitness: str
    admission_ordinal: int


@dataclass(frozen=True)
class AttemptRecord:
    global_attempt_index: int
    parent_candidate_id: str
    rng_pre_state: Tuple[Any, ...]
    rng_post_state: Tuple[Any, ...]
    candidate_vector: Tuple[str, ...]
    fitness_vector: Tuple[str, ...]
    fitness_float_hex: Tuple[str, ...]
    normalized_probabilities: Tuple[float, ...]
    disposition: str
    child_candidate_id: str | None
    duplicate_target_candidate_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class FinalistEvaluation:
    candidate_id: str
    static_25_fitness: str
    full_pool_fitness: str


@dataclass(frozen=True)
class CoreSearchResult:
    eligible_pool: Tuple[CoreCandidate, ...]
    attempts: Tuple[AttemptRecord, ...]
    finalists: Tuple[FinalistEvaluation, ...]
    winner: FinalistEvaluation


class SharedPoolMStar:
    def __init__(
        self,
        *,
        role: str,
        run_seed: int,
        controller_identity: str,
        static_evaluate: Callable[[bytes, str], str],
        rotating_feedback: Callable[[CoreCandidate, int], Mapping[str, Any]],
        mutate: Callable[[CoreCandidate, Mapping[str, Any], int], bytes],
        validate_program: Callable[[bytes], None],
        full_pool_evaluate: Callable[[CoreCandidate], str],
        behavior_manifest: Callable[[bytes], Mapping[str, Any]],
        attempts: int = 20,
        tau: float = 0.15,
        finalist_k: int = 3,
    ):
        self.role = role
        self.run_seed = int(run_seed)
        self.controller_identity = controller_identity
        self.static_evaluate = static_evaluate
        self.rotating_feedback = rotating_feedback
        self.mutate = mutate
        self.validate_program = validate_program
        self.full_pool_evaluate = full_pool_evaluate
        self.behavior_manifest = behavior_manifest
        self.attempt_budget = int(attempts)
        self.tau = float(tau)
        self.finalist_k = int(finalist_k)
        if self.attempt_budget < 0 or self.tau != 0.15 or self.finalist_k != 3:
            raise ValueError("Core v1 freezes attempts>=0, tau=0.15 and finalist_k=3")

    @staticmethod
    def _parent(pool: Sequence[CoreCandidate], rng: random.Random):
        values = [float(candidate.static_25_fitness) for candidate in pool]
        logits = [value / 0.15 for value in values]
        maximum = max(logits)
        weights = [math.exp(value - maximum) for value in logits]
        total = sum(weights)
        probabilities = tuple(value / total for value in weights)
        chosen = rng.choices(list(pool), weights=weights, k=1)[0]
        return chosen, probabilities

    def _seed(self, seed_key: str, program_bytes: bytes, ordinal: int) -> CoreCandidate:
        self.validate_program(program_bytes)
        identity = program_identity(program_bytes, self.behavior_manifest(program_bytes))
        candidate_id = candidate_identity(
            role=self.role,
            formal_run_seed=self.run_seed,
            birth_kind="INITIAL_SEED",
            birth_slot=seed_key,
            parent_candidate_id=None,
            program_identity_sha256=identity,
            controller_identity=self.controller_identity,
        )
        fitness = _validate_fitness(self.static_evaluate(program_bytes, candidate_id))
        return CoreCandidate(
            candidate_id,
            identity,
            program_bytes,
            seed_key,
            None,
            seed_key,
            fitness,
            ordinal,
        )

    def run(self, initial_seeds: Mapping[str, bytes]) -> CoreSearchResult:
        if tuple(initial_seeds) != SEED_ORDER:
            raise ValueError("Mandatory initial seeds/order mismatch")
        pool = []
        seen_programs: Dict[str, CoreCandidate] = {}
        for ordinal, seed_key in enumerate(SEED_ORDER):
            candidate = self._seed(seed_key, initial_seeds[seed_key], ordinal)
            if candidate.program_identity_sha256 in seen_programs:
                raise RuntimeError("Mandatory initial seeds have duplicate Program identity")
            pool.append(candidate)
            seen_programs[candidate.program_identity_sha256] = candidate

        rng = random.Random(self.run_seed)
        attempt_records = []
        for attempt_index in range(1, self.attempt_budget + 1):
            pre_state = rng.getstate()
            vector = tuple(candidate.candidate_id for candidate in pool)
            fitnesses = tuple(candidate.static_25_fitness for candidate in pool)
            float_hex = tuple(float(value).hex() for value in fitnesses)
            parent, probabilities = self._parent(pool, rng)
            post_state = rng.getstate()
            child_id = None
            duplicate_target = None
            disposition = "ATTEMPT_CONSUMED_CANDIDATE_FAILURE"
            error_code = None
            try:
                feedback = dict(self.rotating_feedback(parent, attempt_index))
                child_bytes = self.mutate(parent, feedback, attempt_index)
                self.validate_program(child_bytes)
                child_program_identity = program_identity(
                    child_bytes, self.behavior_manifest(child_bytes)
                )
                child_id = candidate_identity(
                    role=self.role,
                    formal_run_seed=self.run_seed,
                    birth_kind="CHILD",
                    birth_slot=attempt_index,
                    parent_candidate_id=parent.candidate_id,
                    program_identity_sha256=child_program_identity,
                    controller_identity=self.controller_identity,
                )
                duplicate = seen_programs.get(child_program_identity)
                if duplicate is not None:
                    disposition = "DUPLICATE_PROGRAM_MUTATION"
                    duplicate_target = duplicate.candidate_id
                else:
                    fitness = _validate_fitness(
                        self.static_evaluate(child_bytes, child_id)
                    )
                    child = CoreCandidate(
                        child_id,
                        child_program_identity,
                        child_bytes,
                        parent.memory_seed_key,
                        parent.candidate_id,
                        attempt_index,
                        fitness,
                        len(pool),
                    )
                    pool.append(child)
                    seen_programs[child_program_identity] = child
                    disposition = "CANDIDATE_ADMITTED"
            except CandidateOriginatedFailure as exc:
                error_code = type(exc).__name__
            attempt_records.append(
                AttemptRecord(
                    attempt_index,
                    parent.candidate_id,
                    pre_state,
                    post_state,
                    vector,
                    fitnesses,
                    float_hex,
                    probabilities,
                    disposition,
                    child_id,
                    duplicate_target,
                    error_code,
                )
            )

        # Strict Program dedup is defensive here because admission already dedups.
        unique = {candidate.program_identity_sha256: candidate for candidate in pool}
        eligible = tuple(unique.values())
        if len(eligible) < self.finalist_k:
            raise RuntimeError("Fewer than three unique eligible Candidates")
        ranked = sorted(
            eligible,
            key=lambda item: (-Decimal(item.static_25_fitness), item.candidate_id),
        )
        finalist_candidates = ranked[: self.finalist_k]
        finalist_evaluations = tuple(
            FinalistEvaluation(
                candidate.candidate_id,
                candidate.static_25_fitness,
                _validate_fitness(self.full_pool_evaluate(candidate)),
            )
            for candidate in finalist_candidates
        )
        winner = sorted(
            finalist_evaluations,
            key=lambda item: (-Decimal(item.full_pool_fitness), item.candidate_id),
        )[0]
        return CoreSearchResult(
            tuple(pool), tuple(attempt_records), finalist_evaluations, winner
        )
