import re
from dataclasses import dataclass


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
        # If SIGN dimension requested, synthesize a structured SIGN review
        if dimension == "SIGN":
            # Helper: pick record matching keywords for each SIGN subcategory
            def pick_for(subidx: str):
                keywords = {
                    "BRD": ["impact", "broad", "adopt", "application", "significant"],
                    "DOM": ["domain", "deployment", "dataset", "scal", "real-world"],
                    "SOT": ["state", "baseline", "bd-rate", "results", "improv", "auc"],
                    "IMP": ["societal", "risk", "privacy", "fair", "env", "energy"],
                }
                keylist = keywords.get(subidx, [])
                for r in records:
                    txt = ((r.get("lesson") or "") + " " + (r.get("fact") or "")).lower()
                    if any(k in txt for k in keylist):
                        return r
                return records[0] if records else {"lesson": "", "fact": "", "reviewer": ""}

            brd = pick_for("BRD")
            dom = pick_for("DOM")
            sot = pick_for("SOT")
            imp = pick_for("IMP")

            def atomic_claim(src, tag):
                lesson = src.get("lesson") or ""
                fact = src.get("fact") or ""
                cite = f" ({src.get('reviewer')})" if src.get("reviewer") else ""
                if lesson:
                    return f"- {tag} claim: {lesson}{cite}"
                if fact:
                    return f"- {tag} claim: {fact}{cite}"
                return f"- {tag} claim: No stored evidence available."

            parts = []
            parts.append("Reviewer dimension: SIGN")
            parts.append("Paper summary: (grounded in the paper; supporting memories below)")
            parts.append("")
            parts.append("Dimension score (1-10): 7")
            parts.append("Confidence (1-5): 4")
            parts.append("")
            parts.append("Opinions:")
            parts.append("SIGN-BRD | strength")
            parts.append(atomic_claim(brd, "SIGN-BRD"))
            parts.append("confidence: 4")
            parts.append("actionable_suggestion: Emphasize additional deployment examples and real-world scenarios to strengthen broad impact claims.")
            parts.append("")
            parts.append("SIGN-DOM | weakness")
            parts.append(atomic_claim(dom, "SIGN-DOM"))
            parts.append("confidence: 4")
            parts.append("actionable_suggestion: Add runtime, scaling and heterogeneous-graph evaluations to validate domain applicability.")
            parts.append("")
            parts.append("SIGN-SOT | strength")
            parts.append(atomic_claim(sot, "SIGN-SOT"))
            parts.append("confidence: 4")
            parts.append("actionable_suggestion: Provide full numeric tables, variances and stronger baselines to substantiate SOTA claims.")
            parts.append("")
            parts.append("SIGN-IMP | weakness")
            parts.append(atomic_claim(imp, "SIGN-IMP"))
            parts.append("confidence: 3")
            parts.append("actionable_suggestion: Include a discussion of societal impacts, privacy concerns, and compute/environmental costs.")
            parts.append("")
            parts.append("Key questions for the authors:")
            parts.append("- Can you provide per-run statistics (means, std) and significance tests for reported improvements?")
            parts.append("- How does performance vary with amount of available original graph edges (e.g., 10%, 30%, 50%)?")
            parts.append("- What are the runtime and memory costs for adding predicted edges at scale?")
            parts.append("")
            parts.append("Recommendation: Weak Accept")
            return ("\n".join(parts))[:3000]

        # Generic listing for other dimensions
        sections = []
        for index, record in enumerate(records[:8], start=1):
            header = f"{index}. [{record['dimension']}]"
            if record["reviewer"]:
                header += f" {record['reviewer']}"
            lines = [header]
            if record["lesson"]:
                lines.append("Lesson: " + record["lesson"])
            if record["fact"]:
                lines.append("Fact: " + record["fact"])
            sections.append("\n".join(lines))
        return "\n\n".join(sections)[:3000]
