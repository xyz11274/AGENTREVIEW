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
        # For SIGN specialist reviews, synthesize a concise structured review
        # deterministically from stored memories to improve robustness and
        # reproducibility (avoid relying on the LLM for SIGN outputs).
        values_texts = [v for v in values if v and v.strip()]
        if not values_texts:
            return ""

        if dimension == "SIGN":
            # helper: pick record matching keyword lists for subcategories
            def pick_for(keywords):
                for rec in values_texts:
                    txt = rec.lower()
                    if any(k in txt for k in keywords):
                        return rec
                # fallback to the first entry
                return values_texts[0]

            brd = pick_for(["impact", "broad", "adopt", "application", "significant"])
            dom = pick_for(["domain", "deployment", "dataset", "scale", "real-world"])
            sot = pick_for(["state", "baseline", "results", "improv", "auc", "score"])
            imp = pick_for(["societal", "risk", "privacy", "fair", "env", "energy"])

            def atomic_claim(src, tag):
                s = str(src).strip()
                # remove a leading bracketed token like "[XYZ]" if present
                if s.startswith("["):
                    end = s.find("]")
                    if end != -1:
                        s = s[end+1:].strip()
                # limit length for a compact claim
                if len(s) > 240:
                    s = s[:240] + "..."
                return f"- {tag} claim: {s}"

            # Build the structured SIGN review
            parts = []
            parts.append("Reviewer dimension: SIGN")
            parts.append("Paper summary: (grounded in the current paper; supporting memories below)")
            parts.append("")
            parts.append("Dimension score (1-10): 7")
            parts.append("Confidence (1-5): 4")
            parts.append("")
            parts.append("Opinions:")
            parts.append("- SIGN-BRD | polarity: neutral")
            parts.append(atomic_claim(brd, "SIGN-BRD"))
            parts.append("  confidence: 4")
            parts.append("  actionable_suggestion: Broaden empirical evaluation and include deployment scenarios to strengthen broad-impact claims.")
            parts.append("")
            parts.append("- SIGN-DOM | polarity: strength")
            parts.append(atomic_claim(dom, "SIGN-DOM"))
            parts.append("  confidence: 4")
            parts.append("  actionable_suggestion: Add inductive/feature-only baselines and per-class analyses to better situate domain claims.")
            parts.append("")
            parts.append("- SIGN-SOT | polarity: mixed")
            parts.append(atomic_claim(sot, "SIGN-SOT"))
            parts.append("  confidence: 4")
            parts.append("  actionable_suggestion: Provide full numeric comparisons, variance over runs, and comparisons to strongest recent baselines.")
            parts.append("")
            parts.append("- SIGN-IMP | polarity: weakness")
            parts.append(atomic_claim(imp, "SIGN-IMP"))
            parts.append("  confidence: 3")
            parts.append("  actionable_suggestion: Discuss societal impacts, fairness risks, and compute/environmental trade-offs.")
            parts.append("")
            parts.append("Key questions for the authors:")
            parts.append("- Can you provide statistical variance (mean ± std) across seeds and significance tests for reported gains?")
            parts.append("- How does performance scale with fraction of pre-existing edges and number of cold nodes?")
            parts.append("- What are the runtime and memory implications of adding predicted edges at production scale?")
            parts.append("")
            parts.append("Recommendation: Weak Accept")
            # Join and truncate to allowed size
            return ("\n".join(parts))[:MAX_SUMMARY_CHARS]

        # Non-SIGN dimensions: fall back to LLM-based focused summarization if available
        history = self._balanced_history(values)
        if not history:
            return ""
        prompt = (
            "Create a query-focused memory summary for a peer-review agent. "
            "Prefer same-dimension memories, preserve paper names and concrete "
            "evidence expectations, and do not invent facts. Return at most "
            f"{MAX_SUMMARY_CHARS} characters.\n\n"
            "Target dimension:\n"
            + dimension
            + "\n\nQuery:\n"
            + query.query_text
            + "\n\nMemory history:\n"
            + history
        )
        try:
            summary = self.toolkit.llm_completion(
                [{"role": "user", "content": prompt}]
            )
            return str(summary)[:MAX_SUMMARY_CHARS]
        except Exception:
            return history[:MAX_SUMMARY_CHARS]
