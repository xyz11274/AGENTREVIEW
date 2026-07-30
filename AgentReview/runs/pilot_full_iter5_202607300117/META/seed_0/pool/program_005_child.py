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
    # A short, reusable lesson or guideline the reviewer expressed.
    lesson_learned: str = ""  # metadata: a concise actionable lesson
    # A concrete fact the reviewer asserted about a paper or experiment.
    fact_to_remember: str = ""  # metadata: factual memory for grounding
    # Optional reviewer identifier or short role (e.g., 'Reviewer (QUAL)').
    reviewer_name: str = ""  # metadata: reviewer/role label
    # Review dimension tag (CLAR, SIGN, ORIG, QUAL, POL, META) when available.
    reviewer_dimension: str = ""  # metadata: routing dimension
    # Unix timestamp (float seconds) when memory was observed. Use 0.0 if unknown.
    timestamp: float = 0.0  # metadata: recency signal
    # Importance score in [0.0, 1.0] to bias retrieval ranking; default 0.0.
    importance: float = 0.0  # metadata: salience signal
    # Short tags to help retrieval grouping (e.g., dataset, method, failure-mode).
    tags: list[str] = None  # metadata: topical tags for matching


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
        # Resolve routing dimension preferentially from the provided field,
        # then reviewer_name, then raw_text heuristics.
        dimension = (
            (item.reviewer_dimension or "").strip()
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()

        # Normalize tags
        tags = list(item.tags) if item.tags else []

        # Ensure timestamp and importance have sensible numeric defaults.
        ts = float(item.timestamp or 0.0)
        imp = float(item.importance or 0.0)
        if imp < 0.0:
            imp = 0.0
        if imp > 1.0:
            imp = 1.0

        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
            "timestamp": ts,
            "importance": imp,
            "tags": tags,
        }

        # Avoid storing empty memories
        if not record["lesson"] and not record["fact"]:
            return

        # Append to per-dimension bucket and global list
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

        # Best-effort debug logging to help trace retrieval behavior externally.
        try:
            self.toolkit.logger.debug(
                f"Memory write dim={dimension} reviewer={record['reviewer']!s} "
                f"imp={record['importance']:.2f} ts={int(record['timestamp'])} tags={tags}"
            )
        except Exception:
            pass

    def read(self, query: Query) -> str:
        # Determine target dimension (explicit query override preferred).
        dimension = (
            (query.reviewer_dimension or "").strip()
            or self._dimension(query.query_text)
        ).upper()

        # Candidate selection order: same-dimension bucket, GENERAL bucket, then all.
        candidates = list(self.buckets.get(dimension, []))
        if not candidates and dimension != "GENERAL":
            candidates = list(self.buckets.get("GENERAL", []))
        if not candidates:
            candidates = list(self.all_records)

        qtext_lower = (query.query_text or "").lower()

        # Scoring: prefer exact-dimension, then tag-match, then importance, then recency.
        def candidate_score(rec):
            dim_match = 1 if rec.get("dimension") == dimension else 0
            tag_match = 0
            try:
                for t in rec.get("tags", []) or []:
                    if t and t.lower() in qtext_lower:
                        tag_match = 1
                        break
            except Exception:
                tag_match = 0
            imp = float(rec.get("importance") or 0.0)
            ts = float(rec.get("timestamp") or 0.0)
            return (dim_match, tag_match, imp, ts)

        candidates_sorted = sorted(candidates, key=candidate_score, reverse=True)

        # Format top-K entries compactly; keep within 3000 char budget.
        out_lines = []
        max_entries = 12
        for idx, rec in enumerate(candidates_sorted[:max_entries], start=1):
            header = f"{idx}. [{rec.get('dimension','GENERAL')}]"
            reviewer = rec.get("reviewer")
            if reviewer:
                header += f" {reviewer}"
            meta = []
            if rec.get("tags"):
                try:
                    meta.append("tags=" + ",".join(rec.get("tags")))
                except Exception:
                    pass
            if rec.get("importance") is not None:
                meta.append(f"imp={rec.get('importance'):.2f}")
            if rec.get("timestamp"):
                try:
                    meta.append(f"ts={int(rec.get('timestamp'))}")
                except Exception:
                    pass
            header_line = header + ((" {" + " ".join(meta) + "}") if meta else "")
            out_lines.append(header_line)
            if rec.get("lesson"):
                out_lines.append("  Lesson: " + rec.get("lesson"))
            if rec.get("fact"):
                out_lines.append("  Fact: " + rec.get("fact"))
            out_lines.append("")  # separator
            current = "\n".join(out_lines)
            if len(current) > 2900:
                break

        result = "\n".join(out_lines).strip()
        return result[:3000]
