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
    # Optional short, focused lesson the reviewer expressed (if available).
    lesson_learned: str = ""
    # Optional concrete fact/assertion to remember from this episode.
    fact_to_remember: str = ""
    # Optional reviewer identifier (e.g., 'Reviewer (QUAL)') for attribution.
    reviewer_name: str = ""
    # Optional explicit routing dimension (e.g., CLAR, SIGN, ORIG, QUAL, POL, META).
    reviewer_dimension: str = ""
    # Unix timestamp when the memory was observed; 0.0 if unknown.
    timestamp: float = 0.0
    # Importance score in [0.0,1.0] to bias retrieval; 0.0 if unspecified.
    importance: float = 0.0
    # Short topical tags to help matching (e.g., dataset, method, failure-mode).
    tags: list[str] = None


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""
    # Optional tag filter to bias retrieval (single tag or comma-separated).
    tag_filter: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        # Per-dimension structured records
        self.history_by_dimension: dict[str, list[dict]] = {}
        # Global flat list for fallback
        self.all_history: list[dict] = []

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
        text = str(raw_text or "").strip()
        # If no content and no structured fields, nothing to store.
        # Avoid using getattr (forbidden); check known structured fields directly.
        has_structured = any((item.lesson_learned, item.fact_to_remember, item.reviewer_name))
        if not text and not (item.lesson_learned or item.fact_to_remember):
            return

        # Resolve dimension from explicit field or from raw text heuristics.
        dimension = (item.reviewer_dimension or self._dimension(text)).upper()

        # Normalize tags
        tags = list(item.tags) if item.tags else []

        # Ensure numeric defaults for timestamp/importance
        try:
            ts = float(item.timestamp or 0.0)
        except Exception:
            ts = 0.0
        try:
            imp = float(item.importance or 0.0)
        except Exception:
            imp = 0.0
        imp = max(0.0, min(1.0, imp))

        record = {
            "dimension": dimension,
            "raw": text,
            "lesson": (item.lesson_learned or "").strip(),
            "fact": (item.fact_to_remember or "").strip(),
            "reviewer": (item.reviewer_name or "").strip(),
            "timestamp": ts,
            "importance": imp,
            "tags": tags,
        }

        # Append into per-dimension and global stores
        self.history_by_dimension.setdefault(dimension, []).append(record)
        self.all_history.append(record)

        # Best-effort debug logging without raising errors.
        try:
            self.toolkit.logger.debug(
                f"Memory write dim={dimension} reviewer={record.get('reviewer','')} "
                f"imp={imp:.2f} ts={int(ts)} tags={tags}"
            )
        except Exception:
            pass

    def read(self, query: Query) -> str:
        # Determine requested dimension
        dimension = (query.reviewer_dimension or self._dimension(query.query_text)).upper()

        # Candidate selection: prefer same-dimension, then GENERAL, then all history.
        candidates = list(self.history_by_dimension.get(dimension, []))
        if not candidates and dimension != "GENERAL":
            candidates = list(self.history_by_dimension.get("GENERAL", []))
        if not candidates:
            candidates = list(self.all_history)

        qtext_lower = (query.query_text or "").lower()
        tag_filter = (query.tag_filter or "").lower()

        # Scoring: dimension match (already filtered), tag match, importance, recency.
        def candidate_score(rec):
            tag_match = 0
            try:
                for t in rec.get("tags", []) or []:
                    if t and t.lower() in qtext_lower:
                        tag_match = 1
                        break
                if tag_filter and tag_filter in (",".join(rec.get("tags", [])).lower()):
                    tag_match = 1
            except Exception:
                tag_match = 0
            imp = float(rec.get("importance") or 0.0)
            ts = float(rec.get("timestamp") or 0.0)
            return (tag_match, imp, ts)

        candidates_sorted = sorted(candidates, key=candidate_score, reverse=True)

        # Format top results compactly within 3000 chars.
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
            if rec.get("raw"):
                # include a short raw excerpt to preserve context, not full text
                snippet = rec.get("raw")[:300].replace("\n", " ")
                out_lines.append("  Excerpt: " + snippet)
            out_lines.append("")  # separator
            current = "\n".join(out_lines)
            if len(current) > 2900:
                break

        history = "\n".join(out_lines).strip()
        if not history:
            return ""

        # Use LLM once to produce a concise, query-focused memory summary.
        prompt = (
            "Create a concise query-focused memory summary for a peer-review agent. "
            "Prefer same-dimension memories, preserve reviewer names and concrete "
            "evidence expectations, and do not invent facts. Return at most "
            f"{MAX_SUMMARY_CHARS} characters.\n\n"
            "Target dimension:\n"
            + dimension
            + "\n\nQuery:\n"
            + query.query_text
            + ("\n\nTag filter:\n" + tag_filter if tag_filter else "")
            + "\n\nCandidate memories:\n"
            + history
        )

        try:
            # at most one llm call per read
            summary = self.toolkit.llm_completion([{"role": "user", "content": prompt}])
            return str(summary)[:3000]
        except Exception:
            # If LLM fails, return the compact history as a usable fallback.
            return history[:3000]
