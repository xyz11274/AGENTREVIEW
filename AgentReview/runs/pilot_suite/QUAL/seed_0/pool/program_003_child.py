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
    "dimension when possible. Distinguish reusable advice from paper-specific facts."
)
ALWAYS_ON_KNOWLEDGE = (
    "Apply reusable lessons conditionally and ground the final review in the "
    "current paper; remembered facts about other papers are not current evidence."
)


@dataclass
class KnowledgeItem:
    lesson_learned: str = ""
    fact_to_remember: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""
    reference_opinions: str = ""


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
    def _topic_terms(text: str) -> set:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", (text or "").lower())
        stop = {
            "the", "and", "for", "with", "that", "this", "from", "paper",
            "review", "task", "authors", "method", "results", "model",
            "using", "used", "should", "could", "would", "their", "there",
            "which", "when", "more", "also", "qual",
        }
        return {w for w in words if w not in stop}

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    @staticmethod
    def _extract_reference_opinions(raw_text: str, dimension: str) -> str:
        text = raw_text or ""
        marker = "VERIFIED HUMAN REFERENCE OPINIONS"
        if marker not in text:
            return ""
        tail = text.split(marker, 1)[1]
        lines = []
        current = ""
        wanted = dimension.upper()
        for line in tail.splitlines():
            stripped = line.strip()
            match = re.match(r"\[([A-Z]+)-[A-Z]+\]", stripped)
            if match:
                current = match.group(1)
                continue
            if current == wanted and stripped.startswith("- "):
                bullet = stripped[2:].strip()
                if bullet:
                    lines.append(bullet)
            if len(lines) >= 10:
                break
        return "\n".join(lines)

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()
        references = (
            (item.reference_opinions or "").strip()
            or self._extract_reference_opinions(raw_text, dimension)
        )
        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "references": references,
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
            "terms": self._topic_terms(" ".join([
                item.lesson_learned or "",
                item.fact_to_remember or "",
                references,
                raw_text[:2000] if raw_text else "",
            ])),
        }
        if not record["lesson"] and not record["fact"] and not record["references"]:
            return
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

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

        query_terms = self._topic_terms(query.query_text)
        if query_terms:
            records = sorted(
                records,
                key=lambda record: len(query_terms & record.get("terms", set())),
                reverse=True,
            )

        sections = []
        for index, record in enumerate(records[:8], start=1):
            header = f"{index}. [{record['dimension']}]"
            if record["reviewer"]:
                header += f" {record['reviewer']}"
            lines = [header]
            if record["lesson"]:
                lines.append("Lesson: " + record["lesson"])
            if record["fact"]:
                lines.append("Fact: " + record["fact"])
            if record.get("references"):
                ref_lines = [
                    "- " + line
                    for line in record["references"].splitlines()
                    if line.strip()
                ][:4]
                if ref_lines:
                    lines.append("Prior human-review patterns:\n" + "\n".join(ref_lines))
            sections.append("\n".join(lines))
        return "\n\n".join(sections)[:3000]
