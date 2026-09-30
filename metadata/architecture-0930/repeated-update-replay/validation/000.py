"""Deterministic embedding retrieval over traceable Review Experience."""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import List

# These four strings steer the task agent. The knowledge-item instruction tells
# the build step what a memory entry *is*; the query instruction shapes how the
# agent builds a retrieval query; the response instruction constrains how it may
# use retrieved text; ALWAYS_ON_KNOWLEDGE is injected into every task-agent
# action and carries the dimension playbook and behavioral rules, so it is the
# highest-leverage lever for review quality.
INSTRUCTION_KNOWLEDGE_ITEM = (
    "Store each Review Experience as the COMPLETE specialist review for exactly "
    "one reviewer dimension: its paper summary, every subcategory opinion with "
    "polarity, claim, evidence anchors and confidence, the key questions for the "
    "authors, and the recommendation. Keep the full review intact as one "
    "retrieval unit and never splice in prose from the current paper."
)
INSTRUCTION_QUERY = (
    "Build the retrieval query from the current paper's title and abstract plus "
    "the assigned reviewer dimension, so that same-dimension Review Experience "
    "from comparable papers is recalled."
)
INSTRUCTION_RESPONSE = (
    "Use retrieved Review Experience only as a structural style template: mirror "
    "its organization, subcategory coverage, and level of detail, but re-derive "
    "every claim, evidence anchor, and suggestion from the current Frozen Paper. "
    "Never copy another paper's content, numbers, or anchors."
)
ALWAYS_ON_KNOWLEDGE = (
    "Ground every judgment in the Frozen Paper and cite concrete SRC-xxx anchors "
    "(equation, theorem, table, figure, section, or appendix). Use retrieved "
    "Review Experience only as a style/structure template; never import its "
    "paper content, numbers, or anchors as evidence about the current paper.\n"
    "Cover ALL subcategories of the assigned dimension, recording for each at "
    "least one strength or weakness with polarity, a specific claim, an evidence "
    "anchor, and a confidence. Dimension subcategories:\n"
    "- CLAR: CLAR-WRT (writing, organization, flow), CLAR-NOT (notation, "
    "definitions, mathematical clarity), CLAR-FIG (figures, tables, captions, "
    "standalone readability).\n"
    "- ORIG: ORIG-PROB (problem framing), ORIG-MTH (methodological novelty), "
    "ORIG-ANL (analytical/theoretical insight), ORIG-COM (creative combination), "
    "ORIG-EXP (experimental/benchmark contribution), ORIG-NEG (negative/critical "
    "results).\n"
    "- QUAL: QUAL-MET (method soundness and completeness), QUAL-EXP "
    "(experimental design and coverage), QUAL-REP (reproducibility and details), "
    "QUAL-CMP (comparison to baselines/prior art), QUAL-STA (statistical rigor "
    "and significance).\n"
    "- POL: POL-ANO (anonymity), POL-DAT (data licensing, privacy, consent), "
    "POL-ETH (ethics, fairness, harms), POL-IMP (broader impact and checklists), "
    "POL-PLG (plagiarism, overlap, dual submission).\n"
    "- SIGN: SIGN-BRD (broad cross-area impact), SIGN-DOM (domain advance), "
    "SIGN-SOT (improvement over state of the art), SIGN-IMP (practical and "
    "societal impact).\n"
    "Rules:\n"
    "1. Be concrete and paper-specific: every claim must be traceable to the "
    "current paper. Do not emit generic statements that could apply to any paper.\n"
    "2. Do not invent facts, citations, numbers, results, or experiments. If a "
    "detail is not in the Frozen Paper, say it is unspecified instead of guessing.\n"
    "3. Report omissions explicitly (e.g., missing code release, no significance "
    "tests, no broader-impact or licence statement) rather than skipping them.\n"
    "4. Pair every weakness with one concrete, actionable revision suggestion.\n"
    "5. Calibrate the final recommendation to the balance of strengths and "
    "weaknesses you actually found, and end with the most pointed questions for "
    "the authors."
)


@dataclass
class KnowledgeItem:
    topic: str = ""
    content: str = ""
    reviewer_name: str = ""
    reviewer_dimension: str = ""
    # Subcategory codes this review covers (e.g. CLAR-WRT, QUAL-STA). Kept so
    # retrieval/analysis can reason about coverage; optional and default-empty.
    subcategories: List[str] = field(default_factory=list)


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""
    # Optional hint: the subcategory codes the current review must cover. It
    # lets the task agent steer its own retrieval query; it is not required for
    # the embedding lookup itself.
    subcategories: List[str] = field(default_factory=list)


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
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.create_vector_collection("research_v2_vector")
        self.seen = set()

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        text = str(raw_text or item.content or "").strip()
        role = str(item.reviewer_dimension or "").upper()
        if not text or not role:
            return
        # One specialist review is ONE retrieval unit. Keeping the paper summary,
        # every opinion, its evidence anchors, the author questions, and the
        # recommendation together lets the task agent imitate the whole review
        # structure; splitting the episode would recall only fragments.
        item_id = sha256(f"{role}\n{item.topic}\n{text}".encode("utf-8")).hexdigest()
        if item_id in self.seen:
            return
        self.seen.add(item_id)
        # Store dimension + topic as filterable metadata and the covered
        # subcategory codes (joined into a string because vector metadata must
        # stay scalar) for downstream inspection.
        metadata = {
            "role": role,
            "topic": item.topic,
            "reviewer_name": item.reviewer_name,
            "subcategories": ",".join(item.subcategories or []),
        }
        self.collection.add([text], self.toolkit.embed([text]), [metadata], [item_id])

    def read(self, query: Query) -> str:
        if not query.query_text:
            return ""
        role = str(query.reviewer_dimension or "").upper()
        embedding = self.toolkit.embed([query.query_text])[0]
        # Over-fetch and keep ONLY the current dimension's experience. We do not
        # hardcode a small top-k: select_memory_entries enforces the model
        # context budget and keeps whole entries, so the richest same-dimension
        # examples survive instead of an arbitrary prefix that may drop them.
        results = self.collection.query(embedding, top_k=50)
        selected = [
            result
            for result in results
            if str((result.get("metadata") or {}).get("role") or "") == role
        ]
        self.toolkit.log(
            "retrieval_hits",
            [
                {"id": item["id"], "score": item["score"], "role": role}
                for item in selected
            ],
        )
        if not selected:
            return ""
        entries = [
            f"[REVIEW EXPERIENCE {item['id']}]\n{item['text']}" for item in selected
        ]
        # select_memory_entries chooses complete entries within the context
        # budget, so no entry is ever character-truncated here.
        return "\n\n".join(
            self.toolkit.select_memory_entries(entries, purpose="context")
        )
