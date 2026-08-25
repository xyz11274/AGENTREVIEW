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

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension
            or self._dimension(query.query_text)
        ).upper()
        # Prioritize exact dimension matches
        primary_records = list(self.buckets.get(dimension, []))
        
        # If primary is sparse, supplement with other dimensions to ensure context
        # This helps when a QUAL query might benefit from CLAR or SIGN insights
        if len(primary_records) < 5:
            other_records = []
            for dim, recs in self.buckets.items():
                if dim != dimension:
                    other_records.extend(recs)
            # Interleave or append top other records
            # Simple strategy: append up to 4 other records
            primary_records.extend(other_records[:4])
            
        # Fallback to all records if still empty
        if not primary_records:
            primary_records = list(self.all_records)

        # Limit to 12 records to balance depth and width
        selected_records = primary_records[:12]

        sections = []
        for index, record in enumerate(selected_records, start=1):
            # Compact format: [DIM] Reviewer: Lesson | Fact
            parts = []
            dim_tag = f"[{record['dimension']}]"
            rev_tag = f" {record['reviewer']}:" if record['reviewer'] else ""
            content_parts = []
            if record["lesson"]:
                content_parts.append(record["lesson"])
            if record["fact"]:
                content_parts.append(record["fact"])
            
            if content_parts:
                content = " | ".join(content_parts)
                sections.append(f"{index}. {dim_tag}{rev_tag} {content}")
        
        return "\n".join(sections)[:3000]
