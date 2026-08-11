import hashlib
import re
from pathlib import Path
from typing import Dict, List

from agentreview.config import AgentConfig


PLAYER_BACKEND = {
    "backend_type": "openai-chat",
    "temperature": 0.3,
    "max_tokens": 2048,
}


REVIEWER_SPECS = [
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
]

META_REVIEWER_NAME = "Final Meta-Reviewer"
EXPECTED_REVIEW_REQUEST_SHA256 = (
    "609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42"
)


def _review_request_path() -> Path:
    return Path(__file__).resolve().parent.parent / "REVIEW_Request.md"


def load_review_request_sections() -> Dict[str, str]:
    review_request_path = _review_request_path()
    raw = review_request_path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != EXPECTED_REVIEW_REQUEST_SHA256:
        raise RuntimeError(
            "Frozen reviewer rubric changed: "
            f"expected sha256={EXPECTED_REVIEW_REQUEST_SHA256}, got {actual_hash}"
        )
    text = raw.decode("utf-8")

    matches = list(re.finditer(r"(?m)^([1-5])\. ([A-Z]+) ", text))
    sections: Dict[str, str] = {}

    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        code = match.group(2)
        sections[code] = text[start:end].strip()

    expected = {spec["dimension"] for spec in REVIEWER_SPECS}
    if set(sections) != expected:
        raise RuntimeError(
            f"Frozen reviewer rubric sections mismatch: expected={sorted(expected)}, "
            f"got={sorted(sections)}"
        )
    return sections


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


def get_specialist_reviewer_description(dimension: str, section_text: str, focus: str) -> str:
    return (
        f"You are a specialist reviewer for the {dimension} dimension of a machine learning paper review. "
        f"Your expertise focus is {focus}. You must review strictly according to the rubric below and stay centered on this dimension.\n\n"
        "## Official Rubric Section\n"
        f"{section_text}\n\n"
        "## Reviewing Instructions\n"
        f"- Evaluate the paper only through the {dimension} lens while still grounding every claim in the paper content and retrieved references.\n"
        "- Be rigorous, evidence-based, and constructive.\n"
        "- Explicitly comment on the listed sub-dimensions from the rubric.\n"
        "- Do not invent experiments or claims that are not supported by the paper.\n"
        "- Return only the final review. Do not include private reasoning, analysis, or <think> tags.\n"
        "- Every major judgment must cite original-paper evidence anchors.\n"
        "- Source Anchors may only be copied character-for-character from the input's === SOURCE ANCHORS === list.\n"
        "- The only legal syntax is SRC-<digits>(page=<digits>), and both the numeric ID and page must match one listed token.\n"
        "- Never invent named anchors such as SRC-ABST, SRC-ABSTRACT, SRC-INTRO, SRC-EXP, SRC-EXPERIMENTS, SRC-METHOD, or SRC-AUTHORS, and never use an unlisted numeric ID such as SRC-999.\n"
        f"{_specialist_output_format(dimension)}\n"
    )


def get_meta_reviewer_description() -> str:
    return (
        "You are the final meta-reviewer. You have access to the full paper, the retrieved related-work memory, "
        "and the five specialist reviews for QUAL, CLAR, SIGN, ORIG, and POL. Your task is to synthesize them into one final overall review.\n\n"
        "## Meta-Review Instructions\n"
        "- Read all five specialist reviews carefully and resolve agreements or disagreements explicitly.\n"
        "- Produce an overall judgment that is stricter than an average summary; weigh technical flaws heavily.\n"
        "- Ground the final recommendation in the evidence provided by the specialists and the paper itself.\n"
        "- Mention policy/compliance concerns clearly if any are raised.\n"
        "- Source Anchors may only be copied character-for-character from the input's === SOURCE ANCHORS === list.\n"
        "- The only legal syntax is SRC-<digits>(page=<digits>), and both the numeric ID and page must match one listed token.\n"
        "- Never invent named anchors such as SRC-ABST, SRC-ABSTRACT, SRC-INTRO, SRC-EXP, SRC-EXPERIMENTS, SRC-METHOD, or SRC-AUTHORS, and never use an unlisted numeric ID such as SRC-999.\n"
        "- Return only the final meta-review. Do not include private reasoning, analysis, or <think> tags.\n"
        "Write your review in the following format:\n"
        "```\n"
        "Synthesis:\n"
        "- QUAL: ...\n"
        "- CLAR: ...\n"
        "- SIGN: ...\n"
        "- ORIG: ...\n"
        "- POL: ...\n\n"
        "Evidence anchors used:\n"
        "- <copy one allowed numeric Source Anchor token>: ...\n"
        "- <copy one allowed numeric Source Anchor token>: ...\n\n"
        "Final score (1-10): ...\n"
        "Confidence (1-5): ...\n"
        "Final recommendation: Accept / Weak Accept / Borderline / Weak Reject / Reject\n"
        "Actionable suggestions:\n"
        "- ...\n"
        "```"
    )


def build_specialist_reviewer_configs() -> List[AgentConfig]:
    sections = load_review_request_sections()
    reviewer_configs: List[AgentConfig] = []

    for spec in REVIEWER_SPECS:
        role_desc = get_specialist_reviewer_description(
            dimension=spec["dimension"],
            section_text=sections[spec["dimension"]],
            focus=spec["focus"],
        )
        reviewer_configs.append(
            AgentConfig(
                name=spec["name"],
                role_desc=role_desc,
                backend=PLAYER_BACKEND,
                metadata={
                    "review_dimension": spec["dimension"],
                    "focus": spec["focus"],
                    "review_request_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
                },
            )
        )

    return reviewer_configs


def build_meta_reviewer_config() -> AgentConfig:
    role_desc = get_meta_reviewer_description()
    return AgentConfig(
        name=META_REVIEWER_NAME,
        role_desc=role_desc,
        backend=PLAYER_BACKEND,
        metadata={
            "review_dimension": "META",
            "review_request_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        },
    )
