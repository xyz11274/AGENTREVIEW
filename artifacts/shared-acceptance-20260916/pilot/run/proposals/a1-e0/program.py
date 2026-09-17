"""Deterministic hit selection followed by one explicit summary call."""

from dataclasses import dataclass
from hashlib import sha256

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Store complete traceable Review Experience for deterministic retrieval. "
    "Ensure content is self-contained and specific to the reviewer dimension."
)
INSTRUCTION_QUERY = (
    "Use the deterministic title and abstract query supplied by the host. "
    "Focus on the specific reviewer dimension provided."
)
INSTRUCTION_RESPONSE = (
    "Summarize only the already frozen retrieved Review Experience. "
    "Do not add new information. Keep the summary concise and actionable."
)
ALWAYS_ON_KNOWLEDGE = (
    "The summary is guidance, not evidence about the Frozen Paper. "
    "Use it to structure your review, but ground all claims in the paper itself."
)


@dataclass
class KnowledgeItem:
    topic: str = ""
    content: str = ""
    reviewer_dimension: str = ""
    source: str = ""  # Added for traceability


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


def plan_review(toolkit, query, memory_context):
    evidence = toolkit.retrieve_current_s2(limit=5)
    selected_ids = []
    for item in evidence:
        result_id = str(
            item.get("paperId") or item.get("paper_id") or item.get("id") or ""
        )
        if result_id and result_id not in selected_ids:
            selected_ids.append(result_id)
    return {
        "protocol": "evolvable-review-plan-v1",
        "s2_disposition": "use" if selected_ids else "consulted_no_use",
        "selected_s2_ids": selected_ids,
        "decision_label": "seed_always_consult_top5",
        "usage_instruction": (
            "Use selected frozen S2 evidence only for related-work context; cite its "
            "IDs and do not treat it as evidence for claims about the current paper."
        ),
        "context_order": ["memory", "s2", "strategy"],
        "review_strategy": (
            "Review the Frozen Paper for the assigned specialist dimension. Apply "
            "Memory conditionally and keep current-paper claims grounded in the paper."
        ),
    }


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("research_v2_summarizer")
        self.seen = set()
        self.build_llm_calls_remaining = 2  # Reserved 1 for host induction, 1 for write

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(item.content or raw_text or "").strip()
        role = str(item.reviewer_dimension or "").upper()
        if not text or not role:
            return
        
        # Check budget before using LLM for write
        if self.build_llm_calls_remaining <= 0:
            return

        item_id = sha256(f"{role}\n{item.topic}\n{text}".encode("utf-8")).hexdigest()
        if item_id in self.seen:
            return
        self.seen.add(item_id)
        
        # Use LLM to refine/summarize the item for better retrieval
        prompt = (
            f"Refine the following review experience for the {role} dimension. "
            f"Make it concise, actionable, and specific.\n\n"
            f"Topic: {item.topic}\n"
            f"Content: {text}\n\n"
            f"Return only the refined content."
        )
        refined_text = self.toolkit.llm_completion(
            [{"role": "user", "content": prompt}],
            agent_name="Research v2 Memory Writer",
        )
        self.build_llm_calls_remaining -= 1
        
        self.collection.add(
            [refined_text],
            self.toolkit.embed([refined_text]),
            [{"role": role, "topic": item.topic, "source": item.source}],
            [item_id],
        )

    def read(self, query: Query) -> str:
        if not query.query_text:
            return ""
        role = str(query.reviewer_dimension or "").upper()
        embedding = self.toolkit.embed([query.query_text])[0]
        results = self.collection.query(embedding, top_k=20)
        selected = [
            result
            for result in results
            if str((result.get("metadata") or {}).get("role") or "") == role
        ][:5]
        frozen_hits = [
            {"id": item["id"], "score": item["score"], "role": role}
            for item in selected
        ]
        self.toolkit.log("retrieval_hits", frozen_hits)
        if not selected:
            return ""
        
        # Stage 1: Retrieve and format hits
        history = "\n\n".join(
            f"[REVIEW EXPERIENCE {item['id']}]\n{item['text']}" for item in selected
        )[:12000]
        
        # Stage 2: Summarize hits
        prompt = (
            "Summarize the fixed Review Experience hits below into reusable peer-review "
            "guidance for the supplied query. Do not filter, add, or replace hits. Do not "
            "claim that prior-paper facts apply to the current paper. Return at most 1500 "
            "characters.\n\nRole:\n"
            + role
            + "\n\nQuery:\n"
            + query.query_text
            + "\n\nFrozen hit set:\n"
            + history
        )
        summary = self.toolkit.llm_completion(
            [{"role": "user", "content": prompt}],
            agent_name="Research v2 Memory Summarizer",
        )
        self.toolkit.log("summarizer_output", str(summary))
        return str(summary)[:6000]