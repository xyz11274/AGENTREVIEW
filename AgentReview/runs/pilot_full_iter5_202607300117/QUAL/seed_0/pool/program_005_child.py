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
        # Debug logging to help trace memory writes for reviewer debugging
        try:
            self.toolkit.logger.debug(f"MemoryWrite dim={dimension} reviewer={record['reviewer']} lesson={record['lesson'][:200]!r} fact={record['fact'][:200]!r}")
        except Exception:
            # Avoid raising if logger is unavailable
            pass

    def read(self, query: Query) -> str:
        # Normalize query text and determine dimension hint
        qtext = (query.query_text or "").strip()
        query.reviewer_dimension = (query.reviewer_dimension or "").upper().strip()
        dimension = (
            query.reviewer_dimension
            or self._dimension(qtext)
        ).upper()
        # Log the incoming query for reproducibility
        try:
            self.toolkit.logger.debug(f"MemoryRead dim_hint={query.reviewer_dimension!r} resolved_dim={dimension} query_len={len(qtext)}")
        except Exception:
            pass
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
                lines.append("Lesson: " + record["lesson"])
            if record["fact"]:
                lines.append("Fact: " + record["fact"])
            sections.append("\n".join(lines))
        result = "\n\n".join(sections)[:3000]
        # Log summary of retrieval result (length and number of entries)
        try:
            self.toolkit.logger.debug(f"MemoryReadResult dim={dimension} returned_records={len(sections)} result_len={len(result)}")
        except Exception:
            pass
        return result
