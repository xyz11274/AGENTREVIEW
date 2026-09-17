"""Bootstrap-only empty MemoryProgram; not an Evolution seed."""

from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "No Memory is built for canonical bootstrap."
INSTRUCTION_QUERY = "Use the deterministic host Query."
INSTRUCTION_RESPONSE = "Review only the Frozen Paper and optional Frozen S2 evidence."
ALWAYS_ON_KNOWLEDGE = "No historical Review Experience is available."


@dataclass
class KnowledgeItem:
    content: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        return None

    def read(self, query: Query) -> str:
        self.toolkit.log("retrieval_hits", [])
        return ""
