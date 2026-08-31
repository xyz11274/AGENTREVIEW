"""The executable contract implemented by every MemoryProgram."""

from dataclasses import dataclass
from typing import Any, Protocol

INSTRUCTION_KNOWLEDGE_ITEM: str = ""
INSTRUCTION_QUERY: str = ""
INSTRUCTION_RESPONSE: str = ""
ALWAYS_ON_KNOWLEDGE: str = ""


@dataclass
class KnowledgeItem:
    """Structured knowledge extracted from one episode."""

    content: str = ""


@dataclass
class Query:
    """Structured retrieval request derived from a task question."""

    query_text: str = ""


class KnowledgeBase(Protocol):
    """Runtime interface for storage and retrieval logic."""

    def __init__(self, toolkit: Any): ...

    def write(self, item: KnowledgeItem, raw_text: str) -> None: ...

    def read(self, query: Query) -> str: ...
