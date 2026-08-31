"""Backward-compatible MemoryProgram protocol import path."""

from agentreview.reviewer_runtime.program_protocol import (
    ALWAYS_ON_KNOWLEDGE,
    INSTRUCTION_KNOWLEDGE_ITEM,
    INSTRUCTION_QUERY,
    INSTRUCTION_RESPONSE,
    KnowledgeBase,
    KnowledgeItem,
    Query,
)

__all__ = [
    "ALWAYS_ON_KNOWLEDGE",
    "INSTRUCTION_KNOWLEDGE_ITEM",
    "INSTRUCTION_QUERY",
    "INSTRUCTION_RESPONSE",
    "KnowledgeBase",
    "KnowledgeItem",
    "Query",
]
