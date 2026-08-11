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
    # Unix timestamp when the memory was created or observed (float seconds).
    timestamp: float = 0.0
    # A lightweight, user-provided or heuristic importance score (higher = more important).
    importance: float = 0.0
    # Short tags to help retrieval grouping (e.g., dataset, method, failure-mode).
    tags: list[str] = None


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
        import time

        dimension = (
            item.reviewer_dimension
            or self._dimension(item.reviewer_name)
            or self._dimension(raw_text)
        ).upper()

        # Normalize optional tags list
        tags = list(item.tags) if item.tags else []

        # If caller didn't supply timestamp / importance, derive light heuristics.
        ts = float(item.timestamp) if item.timestamp and item.timestamp > 0 else time.time()
        # Heuristic importance: longer lesson or fact -> slightly higher importance.
        base_importance = float(item.importance or 0.0)
        length_bonus = max(len((item.lesson_learned or "")) + len((item.fact_to_remember or "")), 0) / 1000.0
        importance = min(1.0, base_importance + length_bonus)

        record = {
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "dimension": dimension,
            "timestamp": ts,
            "importance": importance,
            "tags": tags,
        }

        # Avoid storing empty memories
        if not record["lesson"] and not record["fact"]:
            return

        # Append to per-dimension bucket and global list
        self.buckets.setdefault(dimension, []).append(record)
        self.all_records.append(record)

        # Log a compact debug line for inspection by toolkit consumers.
        try:
            self.toolkit.logger.debug(
                f"Memory write dim={dimension} reviewer={record['reviewer']!s} "
                f"importance={importance:.3f} ts={int(ts)} tags={tags}"
            )
        except Exception:
            # Logger is best-effort; do not raise if unavailable.
            pass

    def read(self, query: Query) -> str:
        # Determine target dimension
        dimension = (query.reviewer_dimension or self._dimension(query.query_text)).upper()

        # Candidate selection: prefer same-dimension, then GENERAL, then all.
        candidates = list(self.buckets.get(dimension, []))
        if not candidates and dimension != "GENERAL":
            candidates = list(self.buckets.get("GENERAL", []))
        if not candidates:
            candidates = list(self.all_records)

        # Score and sort candidates by:
        #  1) exact-dimension match (bool -> 1/0),
        #  2) importance (higher better),
        #  3) recency (timestamp newer better)
        def candidate_score(rec):
            dim_match = 1 if rec.get("dimension") == dimension else 0
            imp = float(rec.get("importance") or 0.0)
            ts = float(rec.get("timestamp") or 0.0)
            # We weight dimension match highest, then importance, then recency.
            return (dim_match, imp, ts)

        candidates_sorted = sorted(candidates, key=candidate_score, reverse=True)

        # Format top-K entries compactly; keep within 3000 char budget.
        out_lines = []
        max_entries = 12  # upper bound; will be trimmed by char limit
        for idx, rec in enumerate(candidates_sorted[:max_entries], start=1):
            header = f"{idx}. [{rec.get('dimension','GENERAL')}]"
            reviewer = rec.get("reviewer")
            if reviewer:
                header += f" {reviewer}"
            meta = []
            if rec.get("tags"):
                meta.append("tags=" + ",".join(rec.get("tags")))
            if rec.get("importance") is not None:
                meta.append(f"imp={rec.get('importance'):.2f}")
            if rec.get("timestamp"):
                try:
                    meta.append(f"ts={int(rec.get('timestamp'))}")
                except Exception:
                    pass
            header_line = header + (" {" + " ".join(meta) + "}" if meta else "")
            out_lines.append(header_line)
            if rec.get("lesson"):
                out_lines.append("  Lesson: " + rec.get("lesson"))
            if rec.get("fact"):
                out_lines.append("  Fact: " + rec.get("fact"))
            out_lines.append("")  # blank separator

            current = "\n".join(out_lines)
            if len(current) > 2900:
                # stop early to reserve a small safety margin
                break

        result = "\n".join(out_lines).strip()
        # Final trim to 3000 chars to satisfy constraints
        return result[:3000]
