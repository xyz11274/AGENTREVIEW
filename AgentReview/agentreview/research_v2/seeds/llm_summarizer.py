"""Deterministic hit selection followed by one explicit summary call."""

from dataclasses import dataclass
from hashlib import sha256

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Store complete traceable Review Experience for deterministic retrieval."
)
INSTRUCTION_QUERY = (
    "Use the deterministic title and abstract query supplied by the host."
)
INSTRUCTION_RESPONSE = "Summarize only the already frozen retrieved Review Experience."
ALWAYS_ON_KNOWLEDGE = "The summary is guidance, not evidence about the Frozen Paper."


@dataclass
class KnowledgeItem:
    topic: str = ""
    content: str = ""
    reviewer_dimension: str = ""


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

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(raw_text or item.content or "").strip()
        role = str(item.reviewer_dimension or "").upper()
        if not text or not role:
            return
        item_id = sha256(f"{role}\n{item.topic}\n{text}".encode("utf-8")).hexdigest()
        if item_id in self.seen:
            return
        self.seen.add(item_id)
        self.collection.add(
            [text],
            self.toolkit.embed([text]),
            [{"role": role, "topic": item.topic}],
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
        entries = [f"[REVIEW EXPERIENCE {item['id']}]\n{item['text']}" for item in selected]
        entries = self.toolkit.select_memory_entries(entries, purpose="summary_input")
        if not entries:
            return ""
        history = "\n\n".join(entries)
        prompt = (
            "Summarize the fixed Review Experience hits below into reusable peer-review "
            "guidance for the supplied query. Do not filter, add, or replace hits. Do not "
            "claim that prior-paper facts apply to the current paper. Preserve explicit "
            "corrections and distinguish them from the original erroneous claims. Cite "
            "the source REVIEW EXPERIENCE IDs. Return concise, complete guidance.\n\nRole:\n"
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
        return str(summary)
