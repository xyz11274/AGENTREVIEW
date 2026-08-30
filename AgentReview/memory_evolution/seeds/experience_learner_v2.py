"""Experience Learner v2 / Structured Experience Recall seed.

There is no generated KnowledgeItem, lesson or fact. Retrieved immutable
reviews are grouped deterministically by their existing role subdimensions.
"""

MEMORY_PROGRAM_PROTOCOL = "research-validity-core-v1"
MEMORY_READ_MODE = "STRUCTURED_EXPERIENCE_RECALL"
INSTRUCTION_RESPONSE = (
    "Write only the current role's review. Inspect the structured historical "
    "review sections as precedents without copying paper-specific conclusions."
)
ALWAYS_ON_KNOWLEDGE = (
    "Structured recall does not convert prior reviews into evidence. Every "
    "material judgment must remain grounded in the current paper."
)


def build_retrieval_plan(paper_text, role):
    query = role + " structured review experience\n" + paper_text[:4096]
    return {
        "actions": [
            {
                "op": "vector_search",
                "query": query,
                "subdimension_filter": None,
                "top_k": 5,
            },
            {
                "op": "hydrate_full_records_from_last_search",
                "max_records": 3,
            },
        ]
    }
