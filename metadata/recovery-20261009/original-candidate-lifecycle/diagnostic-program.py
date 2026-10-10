"""Dimension-aware Review-Pattern memory with traceable review episodes."""

from dataclasses import dataclass, field
from hashlib import sha256

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Write one compact, dimension-tagged Review-Pattern card (transferable "
    "pattern, polarity, subcategory, evidence type required, concrete revision "
    "template) plus the verbatim episode that supports it. Never store "
    "current-paper prose as knowledge and never copy another paper's anchors, "
    "numbers, or SRC ids into a card."
)
INSTRUCTION_QUERY = (
    "Query with the current paper title/abstract together with the assigned "
    "dimension's rubric terms, so retrieval matches how this dimension reviews "
    "papers rather than how topically similar other papers are."
)
INSTRUCTION_RESPONSE = (
    "Use retrieved cards only as a checklist of review patterns. Derive every "
    "current-paper judgment, quote, number, and citation from the Frozen Paper; "
    "never import a claim, quantity, anchor, or SRC id from memory, and cover "
    "each rubric item with an explicit strength or weakness."
)
ALWAYS_ON_KNOWLEDGE = (
    "Ground every judgment in the Frozen Paper. Memory supplies review patterns, "
    "never evidence: do not transfer another paper's claims, numbers, anchors, "
    "or SRC ids. Tag every opinion with its dimension and subcategory, state "
    "polarity and confidence, and give each weakness a concrete fix."
)


@dataclass
class KnowledgeItem:
    topic: str = ""
    content: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""
    kind: str = "card"
    pattern: str = ""
    polarity: str = ""
    subcategory: str = ""
    evidence_requirement: str = ""
    revision_template: str = ""
    source_id: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


def plan_review(toolkit, query, memory_context):
    evidence = toolkit.retrieve_current_s2(limit=5)
    selected_ids = []
    for item in evidence:
        result_id = str(
            item.get("paperId") or item.get("paper_id") or item.get("id") or ""
        )
        if result_id and result_id not in selected_ids:
            selected_ids.append(result_id)
    return {
        "protocol": "evolvable-review-plan-v1",
        "s2_disposition": "use" if selected_ids else "consulted_no_use",
        "selected_s2_ids": selected_ids,
        "decision_label": "seed_always_consult_top5",
        "usage_instruction": (
            "Use selected frozen S2 evidence only for related-work context; cite its "
            "IDs and do not treat it as evidence for claims about the current paper."
        ),
        "context_order": ["memory", "s2", "strategy"],
        "review_strategy": (
            "Review the Frozen Paper for the assigned specialist dimension. Apply "
            "Memory conditionally and keep current-paper claims grounded in the paper."
        ),
    }


class KnowledgeBase:
    CARD_LIMIT = 6
    EPISODE_LIMIT = 1
    DUP_THRESHOLD = 0.85

    DIMENSION_RUBRIC = {
        "QUAL": (
            "Assess method soundness: assumptions and their scope, derivations that "
            "follow from stated conditions, the gap between a theorem and the "
            "algorithm actually deployed, protocol completeness, baselines that are "
            "competitive and independently tuned, seeds/variance/significance for "
            "each superiority claim, ablations isolating each contribution, "
            "reproducibility details, and claims scoped to reported evidence."
        ),
        "CLAR": (
            "Assess readability: section organization, self-containedness of the "
            "main text (pseudocode, figures, captions, metrics, axis units), notation "
            "and terminology consistency, definitions before use, and internal "
            "cross-references that resolve without the appendix."
        ),
        "SIGN": (
            "Assess significance: breadth and timeliness of the problem, domain "
            "impact against the strongest baseline, magnitude/consistency/statistical "
            "support of reported gains, and explicit practical, societal, compute, "
            "and accessibility implications."
        ),
        "ORIG": (
            "Assess originality: novelty against the closest prior art, whether the "
            "problem formulation is new or reused, depth of analysis versus "
            "assertion, presence of critical or negative findings, and whether the "
            "experimental protocol adds value beyond existing setups."
        ),
        "POL": (
            "Assess policy compliance: double-blind anonymity (author block, "
            "acknowledgements, self-identifying links), data and code licensing, "
            "ethics/fairness/harm, broader-impact and checklist completeness, "
            "originality versus self-plagiarism or dual submission, and honest "
            "reporting of reproducibility-relevant detail."
        ),
    }

    DIMENSION_TERMS = {
        "QUAL": (
            "soundness assumptions theorem conditions proof experimental protocol "
            "baseline tuning seeds variance significance ablation reproducibility "
            "claim scope"
        ),
        "CLAR": (
            "organization self-contained main text pseudocode caption notation "
            "consistency terminology cross reference definition readability"
        ),
        "SIGN": (
            "importance broad impact domain state of the art magnitude statistical "
            "support societal compute hardware accessibility"
        ),
        "ORIG": (
            "novelty prior art problem formulation depth of analysis negative result "
            "protocol reuse combination rationale"
        ),
        "POL": (
            "anonymity double blind acknowledgements licensing data ethics fairness "
            "harm broader impact checklist originality"
        ),
    }

    STOPWORDS = frozenset(
        "the a an and or of to in for with on by is are was were be been this that "
        "these those it its as at from not no but if then than so such can could may "
        "might will would should we our they their paper method results show shown "
        "using used use also more most other which while when where".split()
    )

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("research_v3_pattern_vector")
        self.seen = set()
        self.signatures = {}

    @staticmethod
    def _normalize(text):
        cleaned = []
        for char in str(text or "").lower():
            if ("a" <= char <= "z") or ("0" <= char <= "9"):
                cleaned.append(char)
            else:
                cleaned.append(" ")
        return " ".join("".join(cleaned).split())

    @classmethod
    def _significant(cls, text, limit=32):
        counts = {}
        for token in cls._normalize(text).split():
            if len(token) > 2 and token not in cls.STOPWORDS:
                counts[token] = counts.get(token, 0) + 1
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [token for token, _ in ranked[:limit]]

    @classmethod
    def _overlap(cls, query_keys, keyword_field):
        item_keys = {key for key in str(keyword_field or "").split(",") if key}
        if not query_keys or not item_keys:
            return 0.0
        return len(query_keys & item_keys) / float(len(query_keys))

    @staticmethod
    def _jaccard(left, right):
        union = left | right
        return len(left & right) / float(len(union)) if union else 0.0

    @staticmethod
    def _field(block, name):
        target = str(name or "").lower()
        for line in str(block or "").splitlines():
            lowered = line.lower()
            index = lowered.find(target)
            while index != -1:
                before = lowered[index - 1] if index > 0 else " "
                if not (before.isalnum() or before == "_"):
                    cursor = index + len(target)
                    while cursor < len(lowered) and lowered[cursor] in " \t":
                        cursor += 1
                    if cursor < len(lowered) and lowered[cursor] == ":":
                        value = line[cursor + 1:].strip()
                        if value:
                            return value
                index = lowered.find(target, index + 1)
        return ""

    @staticmethod
    def _is_pattern_start(line):
        stripped = str(line or "").lstrip()
        if not stripped.startswith("-"):
            return False
        return stripped[1:].lstrip().lower().startswith("subcategory:")

    @classmethod
    def _patterns(cls, text, limit=6):
        blocks, current = [], []
        for line in str(text or "").splitlines():
            if cls._is_pattern_start(line):
                blocks.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        blocks.append("\n".join(current))
        patterns = []
        for block in blocks:
            claim = cls._field(block, "claim")
            if not claim:
                continue
            fix = cls._field(block, "actionable_suggestion")
            patterns.append(
                {
                    "subcategory": cls._field(block, "subcategory"),
                    "polarity": cls._field(block, "polarity").lower(),
                    "claim": claim[:240],
                    "fix": fix[:200] if fix else "",
                }
            )
            if len(patterns) >= limit:
                break
        return patterns

    def _is_duplicate(self, role, text):
        signature = frozenset(self._significant(text, 60))
        for prior in self.signatures.get(role, []):
            if self._jaccard(signature, frozenset(prior)) >= self.DUP_THRESHOLD:
                return True
        self.signatures.setdefault(role, []).append(sorted(signature))
        return False

    def _card_text(self, item, text, role, topic, source_id):
        lines = [f"DIMENSION: {role}", f"SOURCE: {source_id[:16]}"]
        for label, value, limit in (
            ("PATTERN", item.pattern, 400),
            ("POLARITY", item.polarity, 20),
            ("SUBCATEGORY", item.subcategory, 40),
            ("EVIDENCE REQUIRED", item.evidence_requirement, 300),
            ("REVISION TEMPLATE", item.revision_template, 300),
        ):
            value = str(value or "").strip()
            if value:
                lines.append(f"{label}: {value[:limit]}")
        for entry in self._patterns(text):
            fix = str(entry["fix"] or "").strip()
            if not fix or fix.upper() == "N/A":
                fix = "state the concrete fix the authors must make"
            lines.append(
                f"- [{entry['subcategory'] or role}|{entry['polarity'] or 'neutral'}] "
                f"{entry['claim']} => FIX: {fix}"
            )
        lines.append("KEYWORDS: " + ", ".join(self._significant(f"{topic} {text}", 24)))
        return "\n".join(lines)

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(raw_text or item.content or "").strip()
        role = str(item.reviewer_dimension or "").upper()
        if not text or not role:
            return
        if self._is_duplicate(role, text):
            self.toolkit.log(
                "memory_write_skipped", {"role": role, "reason": "near_duplicate"}
            )
            return
        topic = str(item.topic or "").strip()
        source_id = str(item.source_id or "").strip() or sha256(
            text.encode("utf-8")
        ).hexdigest()[:16]
        keywords = ",".join(self._significant(f"{topic} {text}", 24))
        entries = [
            ("card", self._card_text(item, text, role, topic, source_id)),
            ("episode", text),
        ]
        fresh, ids, metadatas = [], [], []
        for kind, body in entries:
            if not body.strip():
                continue
            item_id = sha256(
                f"{role}\n{topic}\n{kind}\n{body}".encode("utf-8")
            ).hexdigest()
            if item_id in self.seen:
                continue
            self.seen.add(item_id)
            fresh.append(body)
            ids.append(item_id)
            metadatas.append(
                {
                    "role": role,
                    "kind": kind,
                    "topic": topic[:200],
                    "subcategory": str(item.subcategory or "")[:60],
                    "polarity": str(item.polarity or "")[:20],
                    "source_id": source_id[:40],
                    "keywords": keywords[:400],
                }
            )
        if fresh:
            self.collection.add(fresh, self.toolkit.embed(fresh), metadatas, ids)
            self.toolkit.log(
                "memory_write",
                {
                    "role": role,
                    "entries": len(fresh),
                    "kinds": [meta["kind"] for meta in metadatas],
                },
            )

    def _rank(self, role, query_text):
        queries = [query_text]
        terms = self.DIMENSION_TERMS.get(role, "")
        if terms:
            queries.append(f"{query_text} {terms}".strip())
        query_keys = set(self._significant(query_text, 24))
        merged = {}
        for text in queries:
            if not text:
                continue
            embedding = self.toolkit.embed([text])[0]
            for result in self.collection.query(embedding, top_k=30):
                metadata = result.get("metadata") or {}
                if str(metadata.get("role") or "").upper() != role:
                    continue
                item_id = str(result.get("id") or "")
                if not item_id:
                    continue
                try:
                    base = float(result.get("score"))
                except (TypeError, ValueError):
                    base = 0.0
                record = {
                    "id": item_id,
                    "kind": str(metadata.get("kind") or ""),
                    "source_id": str(metadata.get("source_id") or ""),
                    "text": str(result.get("text") or ""),
                    "score": base + 0.2 * self._overlap(query_keys, metadata.get("keywords")),
                }
                if item_id not in merged or record["score"] > merged[item_id]["score"]:
                    merged[item_id] = record
        return sorted(merged.values(), key=lambda record: (-record["score"], record["id"]))

    def read(self, query: Query) -> str:
        query_text = str(query.query_text or "").strip()
        role = str(query.reviewer_dimension or "").upper()
        rubric = self.DIMENSION_RUBRIC.get(role)
        if not query_text and not rubric:
            return ""
        ranked = self._rank(role, query_text) if query_text else []
        cards = [record for record in ranked if record["kind"] != "episode"][
            : self.CARD_LIMIT
        ]
        episodes, seen_sources = [], set()
        for record in ranked:
            if record["kind"] != "episode" or record["source_id"] in seen_sources:
                continue
            seen_sources.add(record["source_id"])
            episodes.append(record)
            if len(episodes) >= self.EPISODE_LIMIT:
                break
        self.toolkit.log(
            "retrieval_hits",
            [
                {
                    "id": record["id"],
                    "score": round(record["score"], 4),
                    "kind": record["kind"],
                    "role": role,
                }
                for record in cards + episodes
            ],
        )
        entries = []
        if rubric:
            entries.append(
                f"[REVIEW RUBRIC {role or 'GENERAL'}]\n{rubric}\n"
                "Cover every rubric item with an explicit current-paper strength "
                "or weakness; never reuse memory text as evidence."
            )
        for record in cards:
            entries.append(f"[PATTERN CARD {record['id'][:12]}]\n{record['text']}")
        for record in episodes:
            entries.append(
                f"[EPISODE TRACE {record['id'][:12]}]\n"
                "Trace only: do not copy its claims, numbers, anchors, or SRC ids.\n"
                f"{record['text']}"
            )
        return "\n\n".join(self.toolkit.select_memory_entries(entries))
