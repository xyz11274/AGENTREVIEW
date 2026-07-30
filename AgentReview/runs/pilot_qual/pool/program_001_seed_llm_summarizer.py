import re
from dataclasses import dataclass


INSTRUCTION_KNOWLEDGE_ITEM = (
    "Preserve the reviewer-level episode text as content and extract topic, "
    "reviewer role, and review dimension."
)
INSTRUCTION_QUERY = (
    "Rewrite the question as a focused request for relevant past evidence and "
    "include the target review dimension when present."
)
INSTRUCTION_RESPONSE = (
    "Use the query-focused same-dimension memory summary to answer directly and accurately."
)
ALWAYS_ON_KNOWLEDGE = (
    "Memory may guide what to inspect, but only the current paper can support a "
    "review claim. Never copy a prior paper's conclusion into this review."
)


@dataclass
class KnowledgeItem:
    topic: str = ""
    content: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.history_by_dimension = {}
        self.all_history = []

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = raw_text or item.content
        if not text:
            return
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(text)
        ).upper()
        entry = f"[{dimension}] {text}"
        self.history_by_dimension.setdefault(dimension, []).append(entry)
        self.all_history.append(entry)

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension or self._dimension(query.query_text)
        ).upper()
        values = list(self.history_by_dimension.get(dimension, []))
        if not values:
            values = list(self.all_history)
        history = "\n\n".join(values)[:30000]
        if not history:
            return ""
        prompt = (
            "Create a query-focused memory summary for a peer-review agent. "
            "Prefer same-dimension memories, preserve paper names and concrete "
            "evidence expectations, and do not invent facts.\n\n"
            "Target dimension:\n"
            + dimension
            + "\n\nQuery:\n"
            + query.query_text
            + "\n\nMemory history:\n"
            + history
        )
        try:
            summary = self.toolkit.llm_completion(
                [{"role": "user", "content": prompt}]
            )
            return str(summary)[:3000]
        except Exception:
            return history[:3000]
