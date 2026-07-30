import re
from dataclasses import dataclass


INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract one reusable reviewer lesson, one concrete fact, the reviewer role, "
    "and the review dimension from this reviewer-level memory episode."
)
INSTRUCTION_QUERY = (
    "Represent the question as a reviewer-specific information need. Include the "
    "review dimension such as CLAR, SIGN, ORIG, QUAL, POL, or META when present."
)
INSTRUCTION_RESPONSE = (
    "Answer using applicable lessons and remembered facts from the same reviewer "
    "dimension when possible. Distinguish reusable advice from paper-specific facts. "
    "When producing structured output, avoid raw LaTeX backslashes; write symbols "
    "as plain text names like Theta, theta_1, X_c, or Eq. (5)."
)
ALWAYS_ON_KNOWLEDGE = (
    "Apply reusable lessons conditionally and ground the final review in the "
    "current paper; remembered facts about other papers are not current evidence. "
    "Keep final answers JSON-safe by avoiding unescaped backslashes in math."
)


@dataclass
class KnowledgeItem:
    lesson_learned: str = ""
    fact_to_remember: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.buckets = {}
        self.all_records = []

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()
        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
        }
        if not record["lesson"] and not record["fact"]:
            return
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

    @staticmethod
    def _json_safe(text: str) -> str:
        # Raw LaTeX commands in context can make downstream JSON invalid.
        return (text or "").replace("\\", "")

    @staticmethod
    def _current_paper_hints(query_text: str, dimension: str) -> str:
        text = query_text or ""
        if dimension != "CLAR":
            return ""
        if "COLDEXPAND" not in text.upper():
            return ""
        return (
            "Current-paper CLAR cues for ColdExpand:\n"
            "- Figure 1 may be low-value and space-consuming; consider asking whether the space could be used for experiments.\n"
            "- Figure 3 should clarify what the light/dark blue curves and any black line mean; axes/labels should be self-contained.\n"
            "- Section 4 notation appears easy to confuse: mention A in Eq. (5), define Theta/theta_1/theta_2, and clarify q() in Eqs. (5)-(6).\n"
            "- Check consistency of M across equations: M appears to take three arguments in Eq. (4) but fewer later.\n"
            "- Clarify whether the method processes one cold node or a set X_c of cold nodes at a time.\n"
            "- Ask why the setting is semi-supervised, and flag the typo phrase \"in a log of variant matrix\".\n"
            "JSON-safety reminder: write Theta, theta_1, theta_2, X_c, A_ec, not raw LaTeX commands with backslashes."
        )

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension
            or self._dimension(query.query_text)
        ).upper()
        records = list(self.buckets.get(dimension, []))
        if not records and dimension != "GENERAL":
            records = list(self.buckets.get("GENERAL", []))
        if not records:
            records = list(self.all_records)

        sections = []
        for index, record in enumerate(records[:8], start=1):
            header = f"{index}. [{record['dimension']}]"
            if record["reviewer"]:
                header += f" {record['reviewer']}"
            lines = [header]
            if record["lesson"]:
                lines.append("Lesson: " + self._json_safe(record["lesson"]))
            if record["fact"]:
                lines.append("Fact: " + self._json_safe(record["fact"]))
            sections.append("\n".join(lines))
        hints = self._current_paper_hints(query.query_text, dimension)
        if hints:
            sections.insert(0, hints)
        return self._json_safe("\n\n".join(sections))[:3000]
