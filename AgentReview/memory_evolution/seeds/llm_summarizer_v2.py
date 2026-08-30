"""LLM Summarizer v2: deterministic retrieval then one explicit summary."""

MEMORY_PROGRAM_PROTOCOL = "research-validity-core-v1"
MEMORY_READ_MODE = "POST_RETRIEVAL_LLM_SUMMARY"
INSTRUCTION_RESPONSE = (
    "Write only the current role's review. The memory summary is a compressed "
    "set of fallible review precedents, not evidence about the current paper."
)
ALWAYS_ON_KNOWLEDGE = (
    "Only the current paper supports review judgments. Do not treat a summary "
    "omission or a prior-paper statement as a current-paper fact."
)


def build_retrieval_plan(paper_text, role):
    query = role + " query-focused review precedents\n" + paper_text[:4096]
    return {
        "actions": [
            {
                "op": "vector_search",
                "query": query,
                "subdimension_filter": None,
                "top_k": 8,
            }
        ]
    }
