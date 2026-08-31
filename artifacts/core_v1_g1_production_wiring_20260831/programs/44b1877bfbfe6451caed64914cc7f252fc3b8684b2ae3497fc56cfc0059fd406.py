"""Vector Search v2: deterministic Core v1 direct retrieval seed."""

MEMORY_PROGRAM_PROTOCOL = "research-validity-core-v1"
MEMORY_READ_MODE = "DIRECT_RETRIEVAL"
CORE_V1_SMOKE_CHILD = 1
INSTRUCTION_RESPONSE = (
    "Write only the current role's review. Use retrieved reviews as fallible "
    "inspection precedents, never as evidence about the current paper. Ground "
    "all material claims in the current paper."
)
ALWAYS_ON_KNOWLEDGE = (
    "Retrieved A-paper reviews are no-memory historical review records. They "
    "may suggest what to inspect but cannot establish a fact about this paper."
)


def build_retrieval_plan(paper_text, role):
    query = role + " peer review inspection precedents\n" + paper_text[:4096]
    return {
        "actions": [
            {
                "op": "vector_search",
                "query": query,
                "subdimension_filter": None,
                "top_k": 5,
            }
        ]
    }
