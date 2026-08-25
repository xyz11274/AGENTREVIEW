import re
from dataclasses import dataclass
from typing import List, Optional


INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract only the review dimension needed to route this memory, such as "
    "CLAR, SIGN, ORIG, QUAL, POL, or META. Do not summarize, quote, copy, or "
    "reproduce the episode text."
)
INSTRUCTION_QUERY = (
    "Rewrite the question as a focused request for relevant past evidence and "
    "include the target review dimension when present."
)
INSTRUCTION_RESPONSE = (
    "Use the query-focused same-dimension memory summary to answer directly and accurately."
)
ALWAYS_ON_KNOWLEDGE = (
    "Memory may guide what to inspect, but only the current paper can support a "
    "review claim. Never copy a prior paper's conclusion into this review."
)
MAX_HISTORY_CHARS = 8000
MAX_SUMMARY_CHARS = 1200
MAX_RELEVANT_ENTRIES = 5


@dataclass
class KnowledgeItem:
    reviewer_dimension: str = ""
    content: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""
    focus: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.history_by_dimension = {}
        self.all_history = []
        self._dimension_synonyms = {
            "CLAR": ["CLAR", "CLARITY", "CLARIF", "UNDERSTAND", "READABILITY"],
            "SIGN": ["SIGN", "SIGNIFICANCE", "IMPACT", "NOVELTY", "CONTRIBUTION"],
            "ORIG": ["ORIG", "ORIGINALITY", "NOVEL", "INNOVATION"],
            "QUAL": ["QUAL", "QUALITY", "RIGOR", "METHOD", "EXPERIMENT", "VALIDATION", "EVALUATION"],
            "POL": ["POL", "POLICY", "ETHICS", "SOCIAL", "IMPACT"],
            "META": ["META", "METADATA", "CITATION", "REFERENCE", "FORMAT"]
        }

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

    def _detect_dimension(self, text: str) -> str:
        upper = (text or "").upper()
        for dim, synonyms in self._dimension_synonyms.items():
            for syn in synonyms:
                if re.search(rf"\b{re.escape(syn)}\b", upper):
                    return dim
        return self._dimension(text)

    @staticmethod
    def _excerpt(text: str, budget: int) -> str:
        text = str(text or "")
        budget = max(0, int(budget))
        if len(text) <= budget:
            return text
        marker = "\n...[middle omitted]...\n"
        if budget <= len(marker):
            return text[:budget]
        content_budget = budget - len(marker)
        head_size = content_budget // 2
        tail_size = content_budget - head_size
        return text[:head_size] + marker + text[-tail_size:]

    @classmethod
    def _balanced_history(cls, values) -> str:
        values = [str(value) for value in values if str(value or "").strip()]
        if not values:
            return ""
        separator = "\n\n"
        separator_chars = len(separator) * (len(values) - 1)
        available = max(0, MAX_HISTORY_CHARS - separator_chars)
        per_episode = available // len(values)
        excerpts = [cls._excerpt(value, per_episode) for value in values]
        return separator.join(excerpts)[:MAX_HISTORY_CHARS]

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(raw_text or "")
        if not text:
            return
        dimension = (
            item.reviewer_dimension
            or self._detect_dimension(text)
        ).upper()
        entry = f"[{dimension}] {text}"
        self.history_by_dimension.setdefault(dimension, []).append(entry)
        self.all_history.append(entry)

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension or self._detect_dimension(query.query_text)
        ).upper()
        values = list(self.history_by_dimension.get(dimension, []))
        if not values:
            values = list(self.all_history)
        
        if not values:
            return ""

        # Step 1: Select most relevant entries using LLM
        # We limit the number of entries to consider to avoid context overflow
        candidate_entries = values[-20:]  # Take last 20 entries
        if len(candidate_entries) > MAX_RELEVANT_ENTRIES * 2:
            # If too many, we might need to filter, but for now let's just take the most recent
            pass

        # Format candidates for selection
        candidate_text = "\n".join([f"{i}: {entry}" for i, entry in enumerate(candidate_entries)])
        
        selection_prompt = (
            f"Select the indices of the most relevant memory entries for the query. "
            f"Query: {query.query_text}\n"
            f"Dimension: {dimension}\n"
            f"Entries:\n{candidate_text}\n"
            f"Return only the indices separated by commas, e.g., 0,2,5. Max {MAX_RELEVANT_ENTRIES} indices."
        )

        selected_indices = []
        try:
            selection_response = self.toolkit.llm_completion(
                [{"role": "user", "content": selection_prompt}]
            )
            # Parse indices
            indices_str = str(selection_response).strip()
            # Extract numbers
            numbers = re.findall(r'\d+', indices_str)
            for num in numbers:
                idx = int(num)
                if 0 <= idx < len(candidate_entries) and idx not in selected_indices:
                    selected_indices.append(idx)
                if len(selected_indices) >= MAX_RELEVANT_ENTRIES:
                    break
        except Exception:
            # Fallback: take the last few entries
            selected_indices = list(range(max(0, len(candidate_entries) - MAX_RELEVANT_ENTRIES), len(candidate_entries)))

        if not selected_indices:
            selected_indices = [len(candidate_entries) - 1] if candidate_entries else []

        relevant_entries = [candidate_entries[i] for i in selected_indices]
        history = self._balanced_history(relevant_entries)

        if not history:
            return ""

        # Step 2: Synthesize answer
        synthesis_prompt = (
            "Answer the query using ONLY the provided relevant memory entries. "
            "Be concise and direct. Do not invent facts. "
            f"Query: {query.query_text}\n"
            f"Relevant Memories:\n{history}\n"
            f"Answer:"
        )

        try:
            summary = self.toolkit.llm_completion(
                [{"role": "user", "content": synthesis_prompt}]
            )
            return str(summary)[:MAX_SUMMARY_CHARS]
        except Exception:
            return history[:MAX_SUMMARY_CHARS]
