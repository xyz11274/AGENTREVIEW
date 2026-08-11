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

    @staticmethod
    def _normalize_tokens(text: str):
        stop = {
            "the", "and", "for", "with", "that", "this", "from", "into", "when",
            "your", "you", "are", "is", "was", "were", "have", "has", "had",
            "but", "not", "only", "also", "too", "can", "could", "should",
            "paper", "authors", "study", "method", "model", "results", "task",
            "review", "reviewer", "section", "figure", "table", "dataset",
            "training", "experiment", "experiments", "baseline", "baselines",
        }
        toks = re.findall(r"[a-z0-9][a-z0-9\-+]*", (text or "").lower())
        return [tok for tok in toks if len(tok) > 2 and tok not in stop]

    def _record_text(self, record):
        parts = [record["lesson"], record["fact"], record["topic"]]
        return " ".join(part for part in parts if part).strip()

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()
        topic = (item.reviewer_name or "").strip() or "review_memory"
        source_text = raw_text or ""
        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
            "topic": topic,
            "tokens": self._normalize_tokens(
                " ".join(
                    [
                        topic,
                        dimension,
                        source_text,
                        item.lesson_learned or "",
                        item.fact_to_remember or "",
                    ]
                )
            ),
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
        q_tokens = set(self._normalize_tokens(query.query_text))
        records = list(self.buckets.get(dimension, []))
        if not records and dimension != "GENERAL":
            records = list(self.buckets.get("GENERAL", []))
        if not records:
            records = list(self.all_records)

        def score(record):
            toks = set(record.get("tokens", ()))
            overlap = len(q_tokens & toks)
            dim_bonus = 3 if record.get("dimension") == dimension else 0
            query_text = query.query_text.lower()
            text = self._record_text(record).lower()
            focused_bonus = 0
            for phrase in ("baseline", "ablation", "reproducibility", "comparison", "statistical", "runtime", "seed"):
                if phrase in query_text and phrase in text:
                    focused_bonus += 2
            return (dim_bonus + overlap + focused_bonus, len(toks))

        records.sort(key=score, reverse=True)

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
        return "\n\n".join(sections)[:3000]
