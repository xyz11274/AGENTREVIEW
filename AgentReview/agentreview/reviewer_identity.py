"""Frozen identities for five Specialist Reviewers and stateless Meta synthesis."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REVIEWER_IDENTITY_VERSION = "frozen-specialist-identity-v3"
META_SYNTHESIS_IDENTITY_VERSION = "meta-synthesis-identity-v1"
SPECIALIST_REQUEST_PROTOCOL_VERSION = "specialist-review-request-v2"
META_REQUEST_PROTOCOL_VERSION = "meta-synthesis-request-v1"
SPECIALIST_REQUEST_TEMPLATE = (
    "Review task: {task_id}\n\n"
    "{paper_context}\n\n"
    "Return the complete {task_id} specialist review using the frozen output protocol."
)
META_REQUEST_TEMPLATE = (
    "Synthesis task: META\n"
    "Paper id: {paper_id}\n\n"
    "=== FROZEN PAPER ===\n"
    "{paper_context}\n\n"
    "{specialist_block}\n\n"
    "Return the complete Final Review using META Synthesis Request v1."
)
EXPECTED_REVIEW_REQUEST_SHA256 = (
    "28c975c55fb2594346d52d0e548e9d6fe87d7f189d06189113c2f531a18ea3e2"
)
EXPECTED_META_SYNTHESIS_REQUEST_SHA256 = (
    "6beb7fb87506d9413ac1c3f8c7aee5915d130750794ec7fc647e806fbdd19fe5"
)

REVIEWER_SPECS = (
    {
        "name": "Reviewer QUAL",
        "dimension": "QUAL",
        "focus": "technical quality, methodology, experiments, reproducibility, and comparisons to prior work",
    },
    {
        "name": "Reviewer CLAR",
        "dimension": "CLAR",
        "focus": "clarity of writing, notation, algorithm presentation, and figures/tables",
    },
    {
        "name": "Reviewer SIGN",
        "dimension": "SIGN",
        "focus": "significance, impact, state-of-the-art improvement, and practical or societal value",
    },
    {
        "name": "Reviewer ORIG",
        "dimension": "ORIG",
        "focus": "originality in problem setup, method, analysis, experiments, and creative combinations",
    },
    {
        "name": "Reviewer POL",
        "dimension": "POL",
        "focus": "policy, compliance, ethics, privacy, licensing, anonymity, and broader impact issues",
    },
)

SPECIALIST_TASKS: tuple[str, ...] = tuple(
    str(spec["dimension"]) for spec in REVIEWER_SPECS
)
# META is an artifact label, not a Reviewer Role and not part of ALL_TASKS.
META_TASK = "META"
ALL_TASKS: tuple[str, ...] = SPECIALIST_TASKS
META_REVIEWER_NAME = "Final Meta Synthesizer"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def reviewer_request_protocols() -> dict[str, dict[str, str]]:
    """Return protocols owned by the five-role Specialist suite."""
    return {
        "specialist": {
            "version": SPECIALIST_REQUEST_PROTOCOL_VERSION,
            "template_sha256": _sha256_text(SPECIALIST_REQUEST_TEMPLATE),
        }
    }


def meta_synthesis_request_protocol() -> dict[str, str]:
    return {
        "version": META_REQUEST_PROTOCOL_VERSION,
        "template_sha256": _sha256_text(META_REQUEST_TEMPLATE),
    }


def _project_file(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / name


def load_review_request_sections() -> dict[str, str]:
    """Load the five Specialist sections after verifying source identity."""
    source = _project_file("REVIEW_Request.md")
    raw = source.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != EXPECTED_REVIEW_REQUEST_SHA256:
        raise RuntimeError(
            "Frozen Specialist source identity mismatch: "
            f"expected sha256={EXPECTED_REVIEW_REQUEST_SHA256}, got={actual_hash}"
        )
    text = raw.decode("utf-8")
    matches = list(re.finditer(r"(?m)^([1-5])\. ([A-Z]+) ", text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(2)] = text[match.start() : end].strip()
    if tuple(sections) != SPECIALIST_TASKS:
        raise RuntimeError(
            "Frozen Specialist section order mismatch: "
            f"expected={list(SPECIALIST_TASKS)}, got={list(sections)}"
        )
    return sections


def load_meta_synthesis_request() -> str:
    """Load the independent stateless Meta prompt after hash verification."""
    source = _project_file("META_SYNTHESIS_Request_v1.md")
    raw = source.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != EXPECTED_META_SYNTHESIS_REQUEST_SHA256:
        raise RuntimeError(
            "Frozen Meta synthesis source identity mismatch: "
            f"expected sha256={EXPECTED_META_SYNTHESIS_REQUEST_SHA256}, got={actual_hash}"
        )
    return raw.decode("utf-8").strip()


def _specialist_output_format(dimension: str) -> str:
    return (
        "Write your review in the following format:\n"
        "```\n"
        f"Reviewer dimension: {dimension}\n"
        "Paper summary: ...\n\n"
        "Dimension score (1-10): ...\n"
        "Confidence (1-5): ...\n\n"
        "Opinions:\n"
        "- subcategory: <one rubric subcategory code>\n"
        "  polarity: strength | weakness | neutral\n"
        "  claim: <one atomic review opinion>\n"
        "  evidence_anchors: [<copy allowed numeric Source Anchor token>]\n"
        "  confidence: <1-5>\n"
        "  actionable_suggestion: <specific suggestion or N/A>\n"
        "- subcategory: ...\n"
        "  polarity: ...\n"
        "  claim: ...\n"
        "  evidence_anchors: [...]\n"
        "  confidence: ...\n"
        "  actionable_suggestion: ...\n\n"
        "Key questions for the authors:\n"
        "- ...\n\n"
        "Recommendation: Accept / Weak Accept / Borderline / Weak Reject / Reject\n"
        "```"
    )


def _render_specialist_prompt(
    dimension: str,
    section_text: str,
    focus: str,
) -> str:
    return (
        f"You are a specialist reviewer for the {dimension} dimension of a machine learning paper review. "
        f"Your expertise focus is {focus}. You must review strictly according to the rubric below and stay centered on this dimension.\n\n"
        "## Official Rubric Section\n"
        f"{section_text}\n\n"
        "## Reviewing Instructions\n"
        f"- Evaluate the paper only through the {dimension} lens while grounding every claim in the Frozen Paper.\n"
        "- Treat retrieved Review Experience as fallible reviewing guidance, not evidence about the current paper.\n"
        "- Be rigorous, evidence-based, and constructive.\n"
        "- Explicitly comment on the listed sub-dimensions from the rubric.\n"
        "- Base experiments and claims on evidence present in the paper.\n"
        "- Return only the final review, excluding private reasoning, analysis, and <think> tags.\n"
        "- Cite original-paper evidence anchors for every major judgment.\n"
        "- Copy Source Anchors character-for-character from the input's === SOURCE ANCHORS === list.\n"
        "- Use SRC-<digits>(page=<digits>) with the numeric ID and page from one listed token.\n"
        "- Use numeric Source Anchors present in the input list.\n"
        f"{_specialist_output_format(dimension)}\n"
    )


@dataclass(frozen=True)
class FrozenReviewer:
    task_id: str
    reviewer_name: str
    system_prompt: str
    system_prompt_sha256: str

    def identity(self, suite_identity_sha256: str) -> dict[str, str]:
        protocol = reviewer_request_protocols()["specialist"]
        return {
            "identity_version": REVIEWER_IDENTITY_VERSION,
            "task_id": self.task_id,
            "name": self.reviewer_name,
            "system_prompt_sha256": self.system_prompt_sha256,
            "suite_identity_sha256": suite_identity_sha256,
            "source_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
            "request_protocol_version": protocol["version"],
            "request_template_sha256": protocol["template_sha256"],
        }


@dataclass(frozen=True)
class FrozenReviewerSuite:
    reviewers: tuple[FrozenReviewer, ...]

    @property
    def suite_identity_sha256(self) -> str:
        payload = {
            "identity_version": REVIEWER_IDENTITY_VERSION,
            "source_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
            "request_protocols": reviewer_request_protocols(),
            "reviewers": [
                {
                    "task_id": reviewer.task_id,
                    "reviewer_name": reviewer.reviewer_name,
                    "system_prompt_sha256": reviewer.system_prompt_sha256,
                }
                for reviewer in self.reviewers
            ],
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _sha256_text(canonical)

    def task(self, task_id: str) -> FrozenReviewer:
        for reviewer in self.reviewers:
            if reviewer.task_id == task_id:
                return reviewer
        raise ValueError(f"Unknown Specialist task: {task_id}")

    def identity(self, task_id: str) -> dict[str, str]:
        return self.task(task_id).identity(self.suite_identity_sha256)

    def manifest_payload(self) -> dict[str, Any]:
        return {
            "identity_version": REVIEWER_IDENTITY_VERSION,
            "source": {
                "path": "REVIEW_Request.md",
                "sha256": EXPECTED_REVIEW_REQUEST_SHA256,
            },
            "request_protocols": reviewer_request_protocols(),
            "tasks": [
                {
                    "task_id": reviewer.task_id,
                    "reviewer_name": reviewer.reviewer_name,
                    "system_prompt_sha256": reviewer.system_prompt_sha256,
                }
                for reviewer in self.reviewers
            ],
            "suite_identity_sha256": self.suite_identity_sha256,
        }


@dataclass(frozen=True)
class FrozenMetaSynthesizer:
    name: str
    system_prompt: str
    system_prompt_sha256: str

    def identity(self) -> dict[str, str]:
        protocol = meta_synthesis_request_protocol()
        return {
            "identity_version": META_SYNTHESIS_IDENTITY_VERSION,
            "name": self.name,
            "system_prompt_sha256": self.system_prompt_sha256,
            "source_path": "META_SYNTHESIS_Request_v1.md",
            "source_sha256": EXPECTED_META_SYNTHESIS_REQUEST_SHA256,
            "request_protocol_version": protocol["version"],
            "request_template_sha256": protocol["template_sha256"],
        }

    def manifest_payload(self) -> dict[str, Any]:
        return self.identity()


def load_reviewer_suite() -> FrozenReviewerSuite:
    sections = load_review_request_sections()
    reviewers = []
    for spec in REVIEWER_SPECS:
        task_id = str(spec["dimension"])
        prompt = _render_specialist_prompt(
            task_id,
            sections[task_id],
            str(spec["focus"]),
        )
        reviewers.append(
            FrozenReviewer(
                task_id=task_id,
                reviewer_name=str(spec["name"]),
                system_prompt=prompt,
                system_prompt_sha256=_sha256_text(prompt),
            )
        )
    return FrozenReviewerSuite(tuple(reviewers))


def load_meta_synthesizer() -> FrozenMetaSynthesizer:
    prompt = load_meta_synthesis_request()
    return FrozenMetaSynthesizer(
        name=META_REVIEWER_NAME,
        system_prompt=prompt,
        system_prompt_sha256=_sha256_text(prompt),
    )


def validate_reviewer_identity_manifest(path: str | Path) -> dict[str, Any]:
    """Verify a checked-in five-Specialist manifest."""
    manifest_path = Path(path).expanduser().resolve()
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = load_reviewer_suite().manifest_payload()
    if recorded != expected:
        raise ValueError(
            f"Specialist Identity manifest differs from the rendered suite: {manifest_path}"
        )
    return expected


def validate_meta_synthesis_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = load_meta_synthesizer().manifest_payload()
    if recorded != expected:
        raise ValueError(
            f"Meta synthesis manifest differs from the rendered prompt: {manifest_path}"
        )
    return expected


def build_meta_review_question(
    *,
    paper_id: str,
    specialist_reviews: Mapping[str, str],
    paper_context: str,
) -> str:
    """Build a stateless Meta request in stable Specialist order."""
    if len(specialist_reviews) != len(SPECIALIST_TASKS) or set(
        specialist_reviews
    ) != set(SPECIALIST_TASKS):
        raise ValueError(
            "META request requires this Specialist set: "
            + ", ".join(SPECIALIST_TASKS)
        )
    context = str(paper_context).strip()
    if not context:
        raise ValueError("META request requires Frozen Paper context")
    specialist_block = "\n\n".join(
        f"=== {task_id} SPECIALIST REVIEW ===\n{specialist_reviews[task_id]}"
        for task_id in SPECIALIST_TASKS
    )
    return META_REQUEST_TEMPLATE.format(
        paper_id=paper_id,
        paper_context=context,
        specialist_block=specialist_block,
    )
