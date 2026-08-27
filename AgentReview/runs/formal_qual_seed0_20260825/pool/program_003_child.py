import re
from dataclasses import dataclass
from hashlib import sha256


INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract one reusable reviewer lesson, one concrete fact, the reviewer role, "
    "and the review dimension from this reviewer-level memory episode."
)
INSTRUCTION_QUERY = (
    "Represent the question as a reviewer-specific information need. Include the "
    "review dimension such as CLAR, SIGN, ORIG, QUAL, POL, or META when present."
)
INSTRUCTION_RESPONSE = (
    "Answer using applicable lessons and remembered facts from the same reviewer "
    "dimension when possible. Distinguish reusable advice from paper-specific facts."
)
ALWAYS_ON_KNOWLEDGE = (
    "Apply reusable lessons conditionally and ground the final review in the "
    "current paper; remembered facts about other papers are not current evidence."
)


@dataclass
class KnowledgeItem:
    lesson_learned: str = ""
    fact_to_remember: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("mstar_review_memory")
        self.seen_ids = set()

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()
        lesson = (item.lesson_learned or "").strip()
        fact = (item.fact_to_remember or "").strip()
        reviewer = (item.reviewer_name or "").strip()
        
        if not lesson and not fact:
            return

        # Create a combined text for embedding to capture both lesson and fact context
        # We store them as separate documents to allow fine-grained retrieval if needed,
        # but for simplicity and density, we combine them into one record per write.
        # However, the schema suggests they are distinct. Let's store them as separate entries
        # or one combined entry? The parent stored them in one dict.
        # Let's store one entry per write, combining lesson and fact into the document text.
        
        doc_text = ""
        if lesson:
            doc_text += f"Lesson: {lesson}\n"
        if fact:
            doc_text += f"Fact: {fact}\n"
            
        if not doc_text.strip():
            return

        # Generate a unique ID based on content to avoid duplicates
        content_hash = sha256((dimension + reviewer + doc_text).encode("utf-8")).hexdigest()
        if content_hash in self.seen_ids:
            return
        self.seen_ids.add(content_hash)

        metadata = {
            "dimension": dimension,
            "reviewer": reviewer,
            "lesson": lesson,
            "fact": fact
        }

        embeddings = self.toolkit.embed([doc_text])
        self.collection.add(
            documents=[doc_text],
            embeddings=embeddings,
            metadatas=[metadata],
            ids=[content_hash]
        )

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension
            or self._dimension(query.query_text)
        ).upper()
        
        if not query.query_text:
            return ""

        embedding = self.toolkit.embed([query.query_text])[0]
        
        # Retrieve top 10 to have a buffer, then filter/rank
        results = self.collection.query(
            query_embeddings=[embedding],
            top_k=10
        )

        # Chroma returns lists of lists
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        
        if not docs:
            return ""

        # Filter for same dimension if possible, otherwise keep all
        # We want to prioritize same dimension, but if there are few, include others.
        # Simple strategy: Sort by dimension match first, then by similarity (implicit in order).
        # Since Chroma returns in order of similarity, we can just take the top ones.
        # But we might want to ensure we don't miss a highly relevant cross-dimension fact.
        # Let's just take the top 5 most similar.
        
        selected = list(zip(docs, metas))[:5]
        
        sections = []
        for index, (doc, meta) in enumerate(selected, start=1):
            dim = meta.get("dimension", "GENERAL")
            reviewer = meta.get("reviewer", "")
            header = f"{index}. [{dim}]"
            if reviewer:
                header += f" {reviewer}"
            sections.append(f"{header}\n{doc.strip()}")
            
        return "\n\n".join(sections)[:3000]
