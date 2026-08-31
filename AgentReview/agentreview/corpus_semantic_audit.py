"""Deterministic Core-v1 boundary audit for no-memory Corpus Reviews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .review_corpus import CanonicalFullReviewRecord


SEMANTIC_AUDIT_VERSION = "core-v1-corpus-semantic-boundary-audit-v2"


@dataclass(frozen=True)
class SemanticBoundaryViolation:
    paper_id: str
    role: str
    subdimension_id: str
    code: str


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _sentences(text: str) -> Sequence[str]:
    return tuple(
        item.strip()
        for item in re.split(r"(?<=[.!?。！？])\s+|\n+", _normalized(text))
        if item.strip()
    )


def _limited(sentence: str) -> bool:
    return any(
        marker in sentence
        for marker in (
            "cannot assess",
            "cannot confirm",
            "cannot determine",
            "cannot verify",
            "insufficient evidence",
            "insufficient to determine",
            "does not provide sufficient evidence",
            "evidence is insufficient",
            "does not include a frozen",
            "not observable",
            "not available",
            "no frozen policy",
            "no frozen venue",
            "without frozen policy",
            "without a frozen policy",
            "without a frozen venue",
            "without the specific review policy",
            "without the review policy",
            "no policy compliance judgment can be made",
            "does not establish",
            "does not prove",
            "does not contain",
            "must not infer",
            "must not conclude",
        )
    )


def semantic_boundary_violations(
    record: CanonicalFullReviewRecord,
) -> tuple[SemanticBoundaryViolation, ...]:
    """Return only protocol-defined overclaim classes, never quality scores."""

    violations = []
    by_subdimension = {
        section.subdimension_id: section.review_text
        for section in record.subdimension_sections
    }
    overall = record.overall_assessment or ""

    def add(subdimension: str, code: str) -> None:
        violations.append(
            SemanticBoundaryViolation(
                paper_id=record.paper_id,
                role=record.role,
                subdimension_id=subdimension,
                code=code,
            )
        )

    if record.role == "CLAR":
        text = by_subdimension["CLAR-FIG"]
        visual_terms = re.compile(
            r"\b(?:figure|figures|plot|plots|image|images)\b.*"
            r"\b(?:clear|clearly|legible|illegible|readable|unreadable|color|colour|"
            r"layout|font|overlap|axis|axes|misleading|well[- ]designed|well[- ]labeled)\b"
        )
        if any(
            visual_terms.search(sentence)
            and not _limited(sentence)
            and not re.search(
                r"\b(?:reference to|caption|textual description|machine-readable)\b",
                sentence,
            )
            for sentence in (*_sentences(text), *_sentences(overall))
        ):
            add("CLAR-FIG", "UNOBSERVED_VISUAL_PROPERTY_JUDGMENT")

    if record.role == "POL":
        plagiarism = by_subdimension["POL-PLG"]
        clearance = re.compile(
            r"\b(?:no (?:evidence of )?(?:plagiarism|dual submission)|"
            r"appears original|is original|free of plagiarism|plagiarism compliant)\b"
        )
        if any(
            clearance.search(sentence) and not _limited(sentence)
            for sentence in (*_sentences(plagiarism), *_sentences(overall))
        ):
            add("POL-PLG", "PLAGIARISM_CLEARANCE_FROM_ABSENCE")

        anonymity = by_subdimension["POL-ANO"]
        anonymity_claim = re.compile(
            r"\b(?:violat\w*|breach\w*|compli\w*)\b.*\b(?:anonymous|anonymity|double[- ]blind)\b"
            r"|\b(?:anonymous|anonymity|double[- ]blind)\b.*\b(?:violat\w*|breach\w*|compli\w*)\b"
        )
        anonymity_is_limited = _limited(_normalized(anonymity))
        unbound_anonymity_section_claim = (
            not anonymity_is_limited
            and any(
                anonymity_claim.search(sentence)
                for sentence in _sentences(anonymity)
            )
        )
        unbound_anonymity_overall_claim = any(
            anonymity_claim.search(sentence) and not _limited(sentence)
            for sentence in _sentences(overall)
        )
        if unbound_anonymity_section_claim or unbound_anonymity_overall_claim:
            add("POL-ANO", "ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY")

        documentation = by_subdimension["POL-IMP"]
        obligation = re.compile(
            r"\b(?:required|must include|should include|failure to provide|missing required)\b.*"
            r"\b(?:broader impact|checklist|irb|ethics approval|licen[cs]e identifier|consent identifier)\b"
        )
        documentation_is_limited = _limited(_normalized(documentation))
        unbound_documentation_claim = (
            not documentation_is_limited
            and any(obligation.search(sentence) for sentence in _sentences(documentation))
        )
        unbound_overall_claim = any(
            obligation.search(sentence) and not _limited(sentence)
            for sentence in _sentences(overall)
        )
        if unbound_documentation_claim or unbound_overall_claim:
            add("POL-IMP", "DOCUMENTATION_OBLIGATION_WITHOUT_FROZEN_POLICY")

        ethics = _normalized(by_subdimension["POL-ETH"])
        if "modern ethical standard" in ethics:
            add("POL-ETH", "GENERIC_EXTERNAL_ETHICS_STANDARD")

        data_clearance = re.compile(
            r"\b(?:no|without)\b.*\b(?:privacy|consent|licen[cs]e|data)\b.*"
            r"\b(?:violation|breach|issue|problem)s?\b"
        )
        if any(
            data_clearance.search(sentence) and not _limited(sentence)
            for sentence in _sentences(overall)
        ):
            add("POL-DAT", "DATA_COMPLIANCE_CLEARANCE_WITHOUT_EVIDENCE")

        external_norm = re.compile(
            r"\b(?:standard academic practices?|norms? of the field|field norms?|"
            r"accepted practice(?:s)? at the time)\b"
        )
        if any(external_norm.search(sentence) for sentence in _sentences(overall)):
            add("POL-IMP", "UNBOUND_EXTERNAL_POLICY_NORM")

    if record.role == "ORIG":
        global_novelty = re.compile(
            r"\b(?:first[- ]ever|globally novel|unprecedented|no prior work|"
            r"previous work has never|has never been proposed)\b"
        )
        for subdimension, text in by_subdimension.items():
            for sentence in _sentences(text):
                if not global_novelty.search(sentence):
                    continue
                if not any(
                    marker in sentence
                    for marker in (
                        "paper claims",
                        "paper reports",
                        "paper presents",
                        "authors claim",
                        "authors report",
                        "authors present",
                        "cannot confirm",
                        "cannot verify",
                    )
                ):
                    add(subdimension, "UNQUALIFIED_GLOBAL_NOVELTY_CLAIM")
                    break

    if record.role == "SIGN":
        text = by_subdimension["SIGN-SOT"]
        sota = re.compile(
            r"\b(?:is (?:the )?state[- ]of[- ]the[- ]art|achieves? (?:global )?sota|"
            r"establishes? (?:a )?new state[- ]of[- ]the[- ]art)\b"
        )
        if any(
            sota.search(sentence)
            and not any(
                marker in sentence
                for marker in (
                    "paper claims",
                    "paper reports",
                    "authors claim",
                    "authors report",
                    "cannot confirm",
                    "cannot verify",
                )
            )
            for sentence in _sentences(text)
        ):
            add("SIGN-SOT", "UNQUALIFIED_GLOBAL_SOTA_CLAIM")

    return tuple(violations)


def audit_semantic_boundaries(
    records: Iterable[CanonicalFullReviewRecord],
) -> tuple[SemanticBoundaryViolation, ...]:
    return tuple(
        violation
        for record in records
        for violation in semantic_boundary_violations(record)
    )
