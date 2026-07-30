import re
import textwrap
from dataclasses import dataclass
from typing import List, Dict, Any


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
        self.buckets = {}
        self.all_records = []

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
        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
        }
        if not record["lesson"] and not record["fact"]:
            return
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension
            or self._dimension(query.query_text)
        ).upper()
        records = list(self.buckets.get(dimension, []))
        if not records and dimension != "GENERAL":
            records = list(self.buckets.get("GENERAL", []))
        if not records:
            records = list(self.all_records)

        # Build compact memory context from up to 8 same-dimension records
        selected: List[Dict[str, str]] = records[:8]
        memory_snippets: List[str] = []
        for idx, r in enumerate(selected, start=1):
            reviewer = (r.get("reviewer") or "").strip()
            header = f"{idx}. [{r.get('dimension','')}]"
            if reviewer:
                header += f" {reviewer}"
            parts = [header]
            if r.get("lesson"):
                parts.append("Lesson: " + r["lesson"])
            if r.get("fact"):
                parts.append("Fact: " + r["fact"])
            memory_snippets.append("\n".join(parts))
        memory_context = "\n\n".join(memory_snippets)

        # Prepare an LLM prompt that asks for a concise CLAR-style summary and
        # concrete, actionable fixes (notation, figures, algorithms) based on the
        # query and the stored memories. We may call the LLM at most once.
        prompt_lines = [
            "INSTRUCTIONS FOR REVIEWER SUMMARY (do not invent facts):",
            INSTRUCTION_KNOWLEDGE_ITEM,
            "",
            "FOCUSED QUERY:",
            query.query_text or "(no query text provided)",
            "",
            "REVIEW DIMENSION:",
            dimension,
            "",
            "MEMORY HIGHLIGHTS (same-dimension up to 8):",
            memory_context or "(no stored memories)",
            "",
            "TASK: Produce a short, reviewer-focused CLAR summary (<= 3000 chars).",
            "- First, write a 1-2 sentence overall assessment targeted to the query.",
            "- Then list 4 concise, actionable issues to fix (notation, figures, algorithms, experiments).",
            "- For each issue give exact locations (section, equation, figure) if present in memories.",
            "- End with 2 targeted questions for the authors that improve clarity/reproducibility.",
            "",
            ALWAYS_ON_KNOWLEDGE,
        ]
        prompt = "\n".join(prompt_lines)

        # Call the toolkit LLM once; fall back to formatted memory if call fails.
        try:
            response = self.toolkit.llm_completion([{"role": "user", "content": prompt}])
            # Ensure we return a str and trim to the allowed length
            out = str(response or "").strip()
            if not out:
                raise ValueError("LLM returned empty")
            return out[:3000]
        except Exception:
            # Fall back: return a compact, human-readable memory summary
            fallback = textwrap.shorten(
                " | ".join(memory_snippets) or "(no memories available)", width=2900, placeholder="..."
            )
            return f"MEMORY FALLBACK SUMMARY (LLM unavailable):\n\n{fallback}"[:3000]
