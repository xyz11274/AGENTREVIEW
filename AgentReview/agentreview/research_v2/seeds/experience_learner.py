"""Offline generated structured experience with deterministic read."""

from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = (
    "From the complete Specialist Review and Internal Evaluation, produce one reusable "
    "review experience. Do not copy the source paper."
)
INSTRUCTION_QUERY = "Use the deterministic title and abstract query supplied by the host."
INSTRUCTION_RESPONSE = "Apply retrieved experience conditionally and verify against the Frozen Paper."
ALWAYS_ON_KNOWLEDGE = "Historical Review Experience is guidance, never current-paper evidence."


@dataclass
class KnowledgeItem:
    trigger: str = ""
    review_heuristic: str = ""
    evidence_to_check: str = ""
    failure_mode: str = ""
    why_it_matters: str = ""
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.by_role = {}

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        role = str(item.reviewer_dimension or "").upper()
        if not role:
            return
        record = {
            "trigger": item.trigger.strip(),
            "review_heuristic": item.review_heuristic.strip(),
            "evidence_to_check": item.evidence_to_check.strip(),
            "failure_mode": item.failure_mode.strip(),
            "why_it_matters": item.why_it_matters.strip(),
        }
        if any(record.values()):
            self.by_role.setdefault(role, []).append(record)

    def read(self, query: Query) -> str:
        role = str(query.reviewer_dimension or "").upper()
        records = list(self.by_role.get(role, []))[:8]
        self.toolkit.log(
            "retrieval_hits",
            [{"id": f"{role}:{index}", "score": None, "role": role} for index, _ in enumerate(records)],
        )
        sections = []
        for index, record in enumerate(records, start=1):
            sections.append(
                "\n".join(
                    [
                        f"[REVIEW EXPERIENCE {role}:{index}]",
                        f"Trigger: {record['trigger']}",
                        f"Heuristic: {record['review_heuristic']}",
                        f"Evidence to check: {record['evidence_to_check']}",
                        f"Failure mode: {record['failure_mode']}",
                        f"Why it matters: {record['why_it_matters']}",
                    ]
                )
            )
        return "\n\n".join(sections)[:6000]
