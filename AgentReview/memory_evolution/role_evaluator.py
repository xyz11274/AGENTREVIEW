"""Reference-free, paper-grounded Specialist evaluator for Core v1.

No Human/OpenReview/CoCo value is accepted by this API.  Applicability is
Candidate-blind; scoring is Candidate-specific.  Fitness is the frozen two-layer
macro-average: subdimensions within paper, then evaluable papers within role.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple

from agentreview.review_corpus import ROLE_SUBDIMENSIONS, canonical_json_bytes


EVALUATOR_SCHEMA_VERSION = "reference-free-role-evaluator-v1"
DECIMAL_SCALE = Decimal("0.000000000001")
EVENT_CODES = frozenset(
    {
        "MATERIAL_OMISSION",
        "MATERIAL_MISJUDGMENT",
        "FABRICATED_MATERIAL_CLAIM",
        "INVALID_PAPER_EVIDENCE",
        "DUPLICATE_PADDING",
        "ROLE_LEAKAGE_LOCAL",
        "ROLE_LEAKAGE_SEVERE",
        "UNBOUND_EXTERNAL_FACT",
        "INVALID_EXTERNAL_EVIDENCE_ID",
    }
)
N_A_REASONS = frozenset(
    {
        "INTRINSICALLY_NOT_APPLICABLE",
        "NO_MATERIAL_CLAIM_TYPE",
        "NO_OBSERVABLE_VISUAL_INFORMATION",
        "NO_MATERIAL_IMPACT_PATHWAY",
        "POLICY_UNAVAILABLE",
        "EXTERNAL_EVIDENCE_UNAVAILABLE",
        "NO_AUDITED_OVERLAP_EVIDENCE",
    }
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _strict_json_object(raw: str | bytes) -> Dict[str, Any]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="strict")
    else:
        text = str(raw)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Evaluator output must be one strict JSON object") from exc
    if not isinstance(value, dict):
        raise TypeError("Evaluator output must be an object")
    return value


def _fields(value: Any, expected: set[str], label: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise ValueError(
            f"{label} fields mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


@dataclass(frozen=True)
class ApplicabilityDecision:
    subdimension_id: str
    status: str
    n_a_reason: str | None
    paper_anchor_ids: Tuple[str, ...]


@dataclass(frozen=True)
class CanonicalApplicabilityRecord:
    role: str
    paper_id: str
    decisions: Tuple[ApplicabilityDecision, ...]
    evaluator_identity: str
    protocol_identity: str

    @property
    def decision_sha256(self) -> str:
        return _sha(
            {
                "identity_type": "applicability_decision",
                "schema_version": EVALUATOR_SCHEMA_VERSION,
                "role": self.role,
                "paper_id": self.paper_id,
                "protocol_identity": self.protocol_identity,
                "decisions": [
                    {
                        "subdimension_id": item.subdimension_id,
                        "status": item.status,
                        "n_a_reason": item.n_a_reason,
                        "paper_anchor_ids": list(item.paper_anchor_ids),
                    }
                    for item in self.decisions
                ],
            }
        )

    @property
    def applicable(self) -> Tuple[str, ...]:
        return tuple(
            item.subdimension_id
            for item in self.decisions
            if item.status == "APPLICABLE"
        )


@dataclass(frozen=True)
class ScoreDecision:
    subdimension_id: str
    score: int | None
    events: Tuple[Dict[str, Any], ...]
    evidence_limitations: Tuple[str, ...]


@dataclass(frozen=True)
class CanonicalScoringRecord:
    role: str
    paper_id: str
    candidate_id: str
    review_sha256: str
    applicability_decision_sha256: str
    scores: Tuple[ScoreDecision, ...]
    evaluator_identity: str
    protocol_identity: str

    @property
    def paper_fraction(self) -> Fraction | None:
        values = [item.score for item in self.scores if item.score is not None]
        if not values:
            return None
        return Fraction(sum(values), len(values))


def parse_applicability(
    raw: str | bytes,
    *,
    role: str,
    paper_id: str,
    evaluator_identity: str,
    protocol_identity: str,
) -> CanonicalApplicabilityRecord:
    value = _fields(
        _strict_json_object(raw),
        {"role", "paper_id", "decisions"},
        "Applicability output",
    )
    if value["role"] != role or value["paper_id"] != paper_id:
        raise ValueError("Applicability role/paper mismatch")
    if not isinstance(value["decisions"], list):
        raise TypeError("Applicability decisions must be an array")
    decisions = []
    for item in value["decisions"]:
        item = _fields(
            item,
            {"subdimension_id", "status", "n_a_reason", "paper_anchor_ids"},
            "Applicability decision",
        )
        status = item["status"]
        reason = item["n_a_reason"]
        if status not in {"APPLICABLE", "N_A"}:
            raise ValueError("Unknown applicability status")
        if status == "APPLICABLE" and reason is not None:
            raise ValueError("Applicable subdimension cannot have N/A reason")
        if status == "N_A" and reason not in N_A_REASONS:
            raise ValueError("N/A requires one closed-registry reason")
        anchors = item["paper_anchor_ids"]
        if not isinstance(anchors, list) or any(not isinstance(x, str) for x in anchors):
            raise TypeError("Applicability paper anchors must be strings")
        if anchors != sorted(set(anchors)):
            raise ValueError("Applicability paper anchors must be sorted/unique")
        decisions.append(
            ApplicabilityDecision(item["subdimension_id"], status, reason, tuple(anchors))
        )
    if tuple(item.subdimension_id for item in decisions) != ROLE_SUBDIMENSIONS[role]:
        raise ValueError("Applicability vector/order mismatch")
    return CanonicalApplicabilityRecord(
        role,
        paper_id,
        tuple(decisions),
        evaluator_identity,
        protocol_identity,
    )


def parse_scoring(
    raw: str | bytes,
    *,
    applicability: CanonicalApplicabilityRecord,
    candidate_id: str,
    candidate_review: str,
    evaluator_identity: str,
    protocol_identity: str,
) -> CanonicalScoringRecord:
    value = _fields(
        _strict_json_object(raw),
        {"role", "paper_id", "scores"},
        "Scoring output",
    )
    if value["role"] != applicability.role or value["paper_id"] != applicability.paper_id:
        raise ValueError("Scoring role/paper mismatch")
    if not isinstance(value["scores"], list):
        raise TypeError("Scoring vector must be an array")
    status_by_id = {item.subdimension_id: item.status for item in applicability.decisions}
    scores = []
    for item in value["scores"]:
        item = _fields(
            item,
            {"subdimension_id", "score", "events", "evidence_limitations"},
            "Score decision",
        )
        subdimension = item["subdimension_id"]
        score = item["score"]
        expected_status = status_by_id.get(subdimension)
        if expected_status is None:
            raise ValueError("Scoring contains an unknown subdimension")
        if expected_status == "APPLICABLE":
            if type(score) is not int or not 0 <= score <= 4:
                raise TypeError("Applicable score must be JSON integer 0-4")
        elif score is not None:
            raise TypeError("N_A score must be JSON null")
        if not isinstance(item["events"], list):
            raise TypeError("events must be an array")
        events = []
        for event in item["events"]:
            event = _fields(event, {"code", "subdimension"}, "Evaluator event")
            if event["code"] not in EVENT_CODES or event["subdimension"] != subdimension:
                raise ValueError("Invalid evaluator event")
            events.append(dict(event))
        limitations = item["evidence_limitations"]
        if not isinstance(limitations, list) or any(not isinstance(x, str) for x in limitations):
            raise TypeError("evidence_limitations must be strings")
        if limitations != sorted(set(limitations)):
            raise ValueError("evidence_limitations must be sorted/unique")
        scores.append(ScoreDecision(subdimension, score, tuple(events), tuple(limitations)))
    if tuple(item.subdimension_id for item in scores) != ROLE_SUBDIMENSIONS[applicability.role]:
        raise ValueError("Scoring vector/order mismatch")
    return CanonicalScoringRecord(
        applicability.role,
        applicability.paper_id,
        candidate_id,
        hashlib.sha256(candidate_review.encode("utf-8")).hexdigest(),
        applicability.decision_sha256,
        tuple(scores),
        evaluator_identity,
        protocol_identity,
    )


def canonical_fitness(paper_records: Iterable[CanonicalScoringRecord]) -> str:
    fractions = [record.paper_fraction for record in paper_records]
    evaluable = [value for value in fractions if value is not None]
    if not evaluable:
        raise ValueError("Candidate has no evaluable papers")
    exact = sum(evaluable, Fraction(0, 1)) / len(evaluable)
    with localcontext() as context:
        context.prec = 50
        value = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
            DECIMAL_SCALE, rounding=ROUND_HALF_EVEN
        )
    if value == 0:
        value = abs(value)
    return format(value, ".12f")


class ApplicabilityConsistencyGate:
    def __init__(self):
        self._decisions: Dict[Tuple[str, str], str] = {}

    def observe(self, record: CanonicalApplicabilityRecord) -> None:
        key = (record.role, record.paper_id)
        existing = self._decisions.setdefault(key, record.decision_sha256)
        if existing != record.decision_sha256:
            raise RuntimeError("APPLICABILITY_INCONSISTENCY")


class ReferenceFreeRoleEvaluator:
    """Strict two-phase facade around a frozen evaluator completion."""

    def __init__(
        self,
        *,
        complete: Callable[[Sequence[Mapping[str, str]]], str | bytes],
        evaluator_identity: str,
        protocol_identity: str,
        role_rubric: str,
    ):
        self.complete = complete
        self.evaluator_identity = evaluator_identity
        self.protocol_identity = protocol_identity
        self.role_rubric = role_rubric

    def _identical_retry(self, messages, parse):
        errors = []
        for _ in range(2):
            raw = self.complete(messages)
            try:
                return parse(raw)
            except (ValueError, TypeError, UnicodeError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        raise RuntimeError("Evaluator produced two illegal outputs: " + " | ".join(errors))

    def applicability(
        self,
        *,
        role: str,
        paper_id: str,
        paper: str,
        structural_context: str,
        frozen_external_evidence: str,
    ) -> CanonicalApplicabilityRecord:
        system = (
            "You are a frozen, reference-free specialist applicability evaluator. "
            "Human/OpenReview reviews, CoCo taxonomy, Split/Classify and CoCo scores "
            "are unavailable and forbidden. The candidate review is also unavailable."
        )
        user = canonical_json_bytes(
            {
                "role": role,
                "paper_id": paper_id,
                "role_rubric": self.role_rubric,
                "paper": paper,
                "structural_context": structural_context,
                "frozen_external_evidence": frozen_external_evidence,
                "required_subdimension_order": list(ROLE_SUBDIMENSIONS[role]),
                "required_output_fields": ["role", "paper_id", "decisions"],
            }
        ).decode("utf-8")
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        return self._identical_retry(
            messages,
            lambda raw: parse_applicability(
                raw,
                role=role,
                paper_id=paper_id,
                evaluator_identity=self.evaluator_identity,
                protocol_identity=self.protocol_identity,
            ),
        )

    def score(
        self,
        *,
        applicability: CanonicalApplicabilityRecord,
        candidate_id: str,
        candidate_review: str,
        paper: str,
        structural_context: str,
        frozen_external_evidence: str,
    ) -> CanonicalScoringRecord:
        system = (
            "You are a frozen, reference-free specialist scoring evaluator. Score "
            "only against the paper-grounded role rubric. Human/OpenReview reviews, "
            "CoCo taxonomy, Split/Classify and CoCo scores are unavailable and forbidden."
        )
        user = canonical_json_bytes(
            {
                "role": applicability.role,
                "paper_id": applicability.paper_id,
                "role_rubric": self.role_rubric,
                "paper": paper,
                "structural_context": structural_context,
                "frozen_external_evidence": frozen_external_evidence,
                "applicability_decision_sha256": applicability.decision_sha256,
                "applicability": [
                    {"subdimension_id": item.subdimension_id, "status": item.status}
                    for item in applicability.decisions
                ],
                "candidate_review": candidate_review,
                "required_output_fields": ["role", "paper_id", "scores"],
            }
        ).decode("utf-8")
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        return self._identical_retry(
            messages,
            lambda raw: parse_scoring(
                raw,
                applicability=applicability,
                candidate_id=candidate_id,
                candidate_review=candidate_review,
                evaluator_identity=self.evaluator_identity,
                protocol_identity=self.protocol_identity,
            ),
        )
