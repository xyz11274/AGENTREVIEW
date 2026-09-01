"""Deterministic hit selection followed by one explicit summary call."""

from dataclasses import dataclass
from hashlib import sha256

INSTRUCTION_KNOWLEDGE_ITEM = "Store complete traceable Review Experience for deterministic retrieval."
INSTRUCTION_QUERY = "Use the deterministic title and abstract query supplied by the host."
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


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("research_v2_summarizer")
        self.seen = set()

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(item.content or raw_text or "").strip()
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
        history = "\n\n".join(
            f"[REVIEW EXPERIENCE {item['id']}]\n{item['text']}" for item in selected
        )[:12000]
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
        return str(summary)[:1500]
