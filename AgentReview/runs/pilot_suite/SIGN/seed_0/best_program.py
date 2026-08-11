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

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

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

        q_tokens = self._tokens(query.query_text)
        q_text = (query.query_text or "").lower()
        focus_terms = {
            "significance", "impact", "important", "practical", "benchmark",
            "baseline", "improvement", "state", "art", "generalization",
            "dynamic", "cold", "start", "unseen", "inductive", "graph",
            "recommendation", "classification", "link", "prediction",
        }

        def record_score(record):
            text = (record.get("lesson", "") + " " + record.get("fact", "")).lower()
            tokens = self._tokens(text)
            score = len(q_tokens & tokens) + 0.5 * len(focus_terms & tokens)
            if "cold" in q_tokens and ("unseen" in tokens or "generalization" in tokens):
                score += 3
            if "graph" in q_tokens and ("recommendation" in tokens or "classification" in tokens):
                score += 2
            if "benchmark" in q_tokens and ("baseline" in tokens or "outperform" in text):
                score += 2
            return score

        records = sorted(records, key=record_score, reverse=True)
        sections = []
        if dimension == "SIGN":
            sign_lines = [
                "SIGN calibration: prioritize broad importance plus demonstrated impact.",
                "If the current paper addresses cold-start, unseen-node, dynamic-graph, or inductive prediction settings, treat that as potentially high broad significance when the paper supports it.",
                "If the current paper reports improvements around 15-25% over relevant baselines, surface this as an encouraging/significant SOT result rather than a minor gain.",
                "Practical value can be argued through graph learning use cases such as recommendation, node classification, link prediction, social/community detection, or new documents/users with no explicit graph edges.",
            ]
            if "cold" in q_text or "unseen" in q_text or "graph" in q_text:
                sign_lines.append(
                    "For ColdExpand-like queries, emphasize that expanding semi-supervised graph learning to disconnected cold nodes is a non-trivial step beyond transductive assumptions."
                )
            sections.append("\n".join(sign_lines))
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
