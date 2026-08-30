"""Final Suite freeze and protected Official CoCo boundary for Core v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Mapping, Tuple


FINAL_ROLE_ORDER = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
SUITE_SCHEMA_VERSION = "final-review-suite-core-v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class RoleWinner:
    role: str
    role_run_identity: str
    candidate_id: str
    program_identity_sha256: str
    full_pool_fitness: str


@dataclass(frozen=True)
class FinalReviewSuite:
    winners: Tuple[RoleWinner, ...]
    meta_enabled: bool = False
    schema_version: str = SUITE_SCHEMA_VERSION

    def __post_init__(self):
        if tuple(value.role for value in self.winners) != FINAL_ROLE_ORDER:
            raise ValueError("Final Suite role order/coverage mismatch")
        if self.meta_enabled:
            raise ValueError("Research Validity Core v1 freezes META off")
        for winner in self.winners:
            if not winner.role_run_identity or not winner.candidate_id:
                raise ValueError("Final Suite winner identity is incomplete")
            if len(winner.program_identity_sha256) != 64:
                raise ValueError("Final Suite Program identity must be full SHA-256")

    @property
    def payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role_order": list(FINAL_ROLE_ORDER),
            "meta_enabled": self.meta_enabled,
            "winners": [asdict(value) for value in self.winners],
        }

    @property
    def suite_identity_sha256(self) -> str:
        return _sha(
            {
                "identity_type": "final_review_suite",
                "schema_version": self.schema_version,
                "payload": self.payload,
            }
        )


def freeze_final_suite(winners: Mapping[str, RoleWinner]) -> FinalReviewSuite:
    if set(winners) != set(FINAL_ROLE_ORDER):
        raise ValueError("Exactly five role winners are required")
    return FinalReviewSuite(tuple(winners[role] for role in FINAL_ROLE_ORDER))


def assemble_specialist_review_bundle(
    *, paper_id: str, specialist_reviews: Mapping[str, str]
) -> str:
    if set(specialist_reviews) != set(FINAL_ROLE_ORDER):
        raise ValueError("Official bundle requires five Specialist reviews")
    blocks = [f"FINAL_SPECIALIST_REVIEW_BUNDLE paper_id={paper_id}"]
    for role in FINAL_ROLE_ORDER:
        text = specialist_reviews[role]
        if not isinstance(text, str) or not text:
            raise ValueError(f"Official {role} review is empty")
        raw = text.encode("utf-8", errors="strict")
        blocks.append(
            f"ROLE_REVIEW role={role} utf8_byte_length={len(raw)}\n{text}"
        )
    return "\n\n".join(blocks)


class ProtectedOfficialAccess:
    """Fail-closed C/Human/CoCo accessor activated only after Suite freeze."""

    def __init__(self, loader: Callable[[], Any]):
        self._loader = loader
        self._suite_identity = None
        self._authorized = False
        self._started = False
        self._completed = False

    def authorize(self, suite: FinalReviewSuite) -> str:
        if self._authorized:
            if self._suite_identity != suite.suite_identity_sha256:
                raise RuntimeError("Official authorization Suite identity changed")
            return self._suite_identity
        self._suite_identity = suite.suite_identity_sha256
        self._authorized = True
        return self._suite_identity

    def read_protected_inputs(self, suite: FinalReviewSuite):
        if not self._authorized or self._suite_identity != suite.suite_identity_sha256:
            raise PermissionError("Protected C/Human/CoCo access is not authorized")
        if self._completed:
            raise RuntimeError("Official evaluation already completed")
        self._started = True
        return self._loader()

    def complete(self, suite: FinalReviewSuite) -> None:
        if not self._started or self._suite_identity != suite.suite_identity_sha256:
            raise RuntimeError("Official evaluation was not started for this Suite")
        self._completed = True

    @property
    def sealed(self) -> bool:
        return self._started
