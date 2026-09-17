import json
from dataclasses import dataclass
from hashlib import sha256

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract concise, role-specific review heuristics from the provided text. "
    "Focus on actionable patterns, common pitfalls, and best practices relevant to the reviewer dimension. "
    "Do not include paper-specific facts or prose. Output a JSON object with keys: 'topic', 'content'."
)
INSTRUCTION_QUERY = (
    "Formulate a precise query to retrieve relevant review experience for the specified dimension. "
    "Focus on methodological, experimental, or ethical aspects relevant to the query text."
)
INSTRUCTION_RESPONSE = (
    "Synthesize the retrieved review experience into actionable guidance for the current review. "
    "Ensure the guidance is general and does not reference specific details from the retrieved papers. "
    "Maintain a professional, constructive tone."
)
ALWAYS_ON_KNOWLEDGE = "Ground every judgment in the Frozen Paper. Use memory only for general review heuristics."


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
        self.collection = toolkit.create_vector_collection("research_v2_vector")
        self.seen = set()

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(item.content or raw_text or "").strip()
        role = str(item.reviewer_dimension or "").upper()
        if not text or not role:
            return

        # Use one LLM call to extract structured knowledge
        prompt = f"{INSTRUCTION_KNOWLEDGE_ITEM}\n\nRole: {role}\nText: {text[:2000]}"
        try:
            response = self.toolkit.llm(prompt, max_tokens=500)
            data = json.loads(response)
            topic = data.get("topic", item.topic or "General")
            content = data.get("content", text[:1000])
        except Exception:
            topic = item.topic or "General"
            content = text[:1000]

        # Chunk content for storage
        chunks = [
            content[start : start + 1200].strip()
            for start in range(0, len(content), 1200)
            if content[start : start + 1200].strip()
        ]
        fresh, ids, metadatas = [], [], []
        for index, chunk in enumerate(chunks):
            item_id = sha256(
                f"{role}\n{topic}\n{index}\n{chunk}".encode("utf-8")
            ).hexdigest()
            if item_id in self.seen:
                continue
            self.seen.add(item_id)
            fresh.append(chunk)
            ids.append(item_id)
            metadatas.append({"role": role, "topic": topic})
        if fresh:
            self.collection.add(fresh, self.toolkit.embed(fresh), metadatas, ids)

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
        self.toolkit.log(
            "retrieval_hits",
            [
                {"id": item["id"], "score": item["score"], "role": role}
                for item in selected
            ],
        )
        context = "\n\n".join(
            f"[REVIEW EXPERIENCE {item['id']}]\n{item['text']}" for item in selected
        )
        return context[:6000]