import re
from dataclasses import dataclass


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
MAX_HISTORY_CHARS = 12000
MAX_SUMMARY_CHARS = 1500


@dataclass
class KnowledgeItem:
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.history_by_dimension = {}
        self.all_history = []

    @staticmethod
    def _dimension(text: str) -> str:
        upper = (text or "").upper()
        for dim in ["CLAR", "SIGN", "ORIG", "QUAL", "POL", "META"]:
            if re.search(rf"\b{dim}\b", upper):
                return dim
        return "GENERAL"

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

    @staticmethod
    def _notation_table() -> str:
        """
        Return a short, reviewer-friendly notation table to include in prompts.
        Kept intentionally compact so it can be prepended to LLM queries.
        """
        lines = [
            "Notation (brief):",
            "- o_t : raw observation (image) at time t",
            "- a_t : discrete action at time t",
            "- r_t : scalar reward at time t",
            "- z_t : latent state (discrete vector) at time t",
            "- \\ell : context horizon (number of prior steps included)",
            "- 3\\ell - 1 : token count when feeding (z,a,r) modalities (last reward omitted)",
            "- h_t : deterministic hidden state from transformer aggregation",
            "- f_\\psi(·) : aggregation model (Transformer-XL here)",
            "- p_\\phi(·) : observation encoder/decoder (VAE)",
            "- Algorithm 1 : training pseudocode referenced in appendix",
        ]
        return "\\n".join(lines)

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(raw_text or "")
        if not text:
            return
        dimension = (
            item.reviewer_dimension
            or self._dimension(text)
        ).upper()
        entry = f"[{dimension}] {text}"
        self.history_by_dimension.setdefault(dimension, []).append(entry)
        self.all_history.append(entry)

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension or self._dimension(query.query_text)
        ).upper()
        values = list(self.history_by_dimension.get(dimension, []))
        if not values:
            values = list(self.all_history)
        history = self._balanced_history(values)
        if not history:
            return ""
        # include a compact notation table so the LLM can ground ambiguous symbols
        notation = self._notation_table()
        prompt = (
            "Create a query-focused memory summary for a peer-review agent. "
            "Prefer same-dimension memories, preserve paper names and concrete "
            "evidence expectations, and do not invent facts. When relevant, call out\n"
            "missing pseudocode (e.g., Algorithm 1), unclear tensor shapes, and exact\n"
            "locations (section/equation/figure) that require fixes. Return at most "
            f"{MAX_SUMMARY_CHARS} characters.\n\n"
            "Notation/context (use to explain unclear symbols):\n"
            + notation
            + "\n\nTarget dimension:\n"
            + dimension
            + "\n\nQuery:\n"
            + query.query_text
            + "\n\nMemory history (same-dimension prioritized):\n"
            + history
        )
        try:
            summary = self.toolkit.llm_completion(
                [{"role": "user", "content": prompt}]
            )
            return str(summary)[:MAX_SUMMARY_CHARS]
        except Exception:
            return history[:MAX_SUMMARY_CHARS]
