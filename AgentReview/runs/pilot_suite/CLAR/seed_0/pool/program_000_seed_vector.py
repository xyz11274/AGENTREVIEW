import re
from dataclasses import dataclass
from hashlib import sha256


INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract a concise topic, reviewer role, review dimension, and the episode "
    "content that should be remembered for future peer review."
)
INSTRUCTION_QUERY = (
    "Turn the question into a concise semantic retrieval query and include the "
    "review dimension such as CLAR, SIGN, ORIG, QUAL, POL, or META when present."
)
INSTRUCTION_RESPONSE = (
    "Answer the question using same-dimension memory context when relevant. "
    "Do not claim facts unsupported by the retrieved context."
)
ALWAYS_ON_KNOWLEDGE = (
    "Treat retrieved memories as fallible review precedents, never as evidence "
    "about the current paper. Verify every judgment against the current paper."
)


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


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("mstar_vector_search")
        self.seen = set()

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = raw_text or item.content
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(text)
        ).upper()
        chunks = [
            text[start : start + 500].strip()
            for start in range(0, len(text), 500)
            if text[start : start + 500].strip()
        ]
        fresh = []
        ids = []
        metadatas = []
        for chunk in chunks:
            chunk_id = sha256((dimension + "\n" + chunk).encode("utf-8")).hexdigest()
            if chunk_id not in self.seen:
                self.seen.add(chunk_id)
                fresh.append(chunk)
                ids.append(chunk_id)
                metadatas.append(
                    {
                        "topic": item.topic,
                        "reviewer_name": item.reviewer_name,
                        "reviewer_dimension": dimension,
                    }
                )
        if not fresh:
            return
        embeddings = self.toolkit.embed(fresh)
        self.collection.add(fresh, embeddings, metadatas, ids)

    def read(self, query: Query) -> str:
        if not query.query_text:
            return ""
        dimension = (
            query.reviewer_dimension or self._dimension(query.query_text)
        ).upper()
        embedding = self.toolkit.embed([query.query_text])[0]
        results = self.collection.query(embedding, top_k=20)
        same_dimension = [
            result
            for result in results
            if (result.get("metadata") or {}).get("reviewer_dimension") == dimension
        ]
        selected = same_dimension[:5] if same_dimension else results[:5]
        context = "\n\n".join(
            f"[{(result.get('metadata') or {}).get('reviewer_dimension', 'GENERAL')}] "
            + result["text"]
            for result in selected
        )
        return context[:3000]
