import re
from dataclasses import dataclass, field


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
    "dimension when possible. Distinguish reusable advice from paper-specific facts. "
    "For QUAL graph/cold-start papers, specifically check inductive baselines, "
    "realistic link-prediction evaluation, scalability, and statistical rigor."
)
ALWAYS_ON_KNOWLEDGE = (
    "Apply reusable lessons conditionally and ground the final review in the "
    "current paper; remembered facts about other papers are not current evidence. "
    "For graph cold-start/link-prediction submissions, inspect whether the paper "
    "compares to inductive methods such as GraphSAGE/GAT-style embedding baselines, "
    "uses realistic negative-edge imbalance and ranking metrics beyond AUC, reports "
    "runtime/scalability, and tests significance over seeds."
)


@dataclass
class KnowledgeItem:
    lesson_learned: str = field(default="", metadata={"description": "Reusable reviewer lesson."})
    fact_to_remember: str = field(default="", metadata={"description": "Paper-specific fact to remember."})
    reviewer_name: str = field(default="", metadata={"description": "Reviewer role or name."})
    reviewer_dimension: str = field(default="", metadata={"description": "Review dimension such as QUAL."})
    signals: str = field(default="", metadata={"description": "Optional compact raw reviewer signals."})


@dataclass
class Query:
    query_text: str = field(default="", metadata={"description": "Semantic retrieval query."})
    reviewer_dimension: str = field(default="", metadata={"description": "Requested review dimension."})


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
            "signals": (item.signals or self._extract_signals(raw_text, dimension)).strip(),
        }
        if not record["lesson"] and not record["fact"] and not record["signals"]:
            return
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

    @staticmethod
    def _extract_signals(raw_text: str, dimension: str) -> str:
        text = raw_text or ""
        marker = "VERIFIED HUMAN REFERENCE OPINIONS"
        if marker in text:
            text = text.split(marker, 1)[1]
        wanted = []
        keywords = [
            "baseline", "compare", "ablation", "statistical", "significance",
            "seed", "runtime", "scalability", "reproduc", "hyperparam",
            "metric", "auc", "precision", "imbalance", "inductive",
            "graphsage", "gat", "threshold", "dataset",
        ]
        current_tag = ""
        for line in text.splitlines():
            stripped = line.strip()
            tag_match = re.match(r"\[([A-Z]+)-", stripped)
            if tag_match:
                current_tag = tag_match.group(1)
            if dimension != "GENERAL" and current_tag and current_tag != dimension:
                continue
            lower = stripped.lower()
            if stripped.startswith("-") and any(key in lower for key in keywords):
                cleaned = re.sub(r"\s+", " ", stripped[1:].strip())
                if cleaned and cleaned not in wanted:
                    wanted.append(cleaned)
            if len(wanted) >= 6:
                break
        return " | ".join(wanted)

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

        sections = []
        q_lower = (query.query_text or "").lower()
        if dimension == "QUAL" and (
            "cold" in q_lower or "graph" in q_lower or "link prediction" in q_lower
        ):
            sections.append(
                "Targeted QUAL checklist for graph cold-start/link-prediction papers: "
                "check missing inductive baselines (GraphSAGE, GAT/GCN edge embeddings, "
                "PinSAGE/HAN/DynRep when relevant), dataset diversity beyond citation "
                "networks, realistic positive/negative edge imbalance, ranking metrics "
                "such as AP/RR/Precision in addition to AUC, threshold tuning, "
                "runtime/scalability of all-pairs link scoring, and statistical "
                "significance or variance across seeds."
            )
        for index, record in enumerate(records[:8], start=1):
            header = f"{index}. [{record['dimension']}]"
            if record["reviewer"]:
                header += f" {record['reviewer']}"
            lines = [header]
            if record["lesson"]:
                lines.append("Lesson: " + record["lesson"])
            if record["fact"]:
                lines.append("Fact: " + record["fact"])
            if record.get("signals"):
                lines.append("Signals: " + record["signals"])
            sections.append("\n".join(lines))
        return "\n\n".join(sections)[:3000]
