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
    reviewer_dimension: str = ""


@dataclass
class Query:
    query_text: str = ""
    reviewer_dimension: str = ""


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.history_by_dimension = {}
        self.all_history = []

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
        text = str(raw_text or "")
        if not text:
            return
        dimension = (
            item.reviewer_dimension
            or self._dimension(text)
        ).upper()
        entry = f"[{dimension}] {text}"
        self.history_by_dimension.setdefault(dimension, []).append(entry)
        self.all_history.append(entry)

    def read(self, query: Query) -> str:
        dimension = (
            query.reviewer_dimension or self._dimension(query.query_text)
        ).upper()
        values = list(self.history_by_dimension.get(dimension, []))
        if not values:
            values = list(self.all_history)
        history = self._balanced_history(values)
        if not history:
            return ""
        # Provide a deterministic, focused POL checklist to support reviewers
        # without invoking the LLM. This extracts common policy-relevant cues
        # such as dataset mentions, licenses, IRB/ethics, PII, anonymization,
        # promises to release code/data, and public URLs that might break
        # double-blind.
        if dimension == "POL":
            text = history.lower()
            checklist = []
            # dataset provenance and named datasets (common video datasets)
            if re.search(r"\b(bdd100k|bdd100k dataset|bdd100k)\b", text):
                checklist.append("- Evidence: BDD100K mentioned. Request exact license link and compliance statement.")
            elif re.search(r"\b(kitti|cityscapes|imagenet|bdd|bdd1000)\b", text):
                checklist.append("- Evidence: common dataset mentioned. Ask authors to cite exact variant and license.")
            else:
                checklist.append("- Missing: No explicit dataset provenance mentioned. Request exact datasets, collectors, and licences.")
            # licensing / redistribution
            if re.search(r"\b(mit|apache|cc-?by|cc0|gpl|license|licen[sc]e|redistribu)\b", text):
                checklist.append("- Evidence: Licensing language detected. Request exact license IDs for code/models/data and confirm third-party compatibility.")
            else:
                checklist.append("- Missing: No license statements found. Request explicit licenses for released code/data/models.")
            # IRB / ethics / consent / PII
            if re.search(r"\b(irb|ethic|ethics|consent|pii|personal|identif|anonym|privacy)\b", text):
                checklist.append("- Evidence: Ethics/privacy language present. Provide IRB identifiers or justify exemption; clarify PII handling/consent.")
            else:
                checklist.append("- Missing: No ethics/IRB/privacy statement found. Ask authors to state whether human-identifiable data are present and how consent/PII were handled.")
            # Identifiability: faces, license plates, people
            if re.search(r"\b(face|person|driver|license plate|plate|vehicle id|pii)\b", text):
                checklist.append("- Attention: Potential identifiable people/plates in datasets. Request de-identification steps or justification of lawful use.")
            # Anonymity / double-blind leakage
            if re.search(r"https?://", history) or re.search(r"\b(anonymous author|anonymous authors)\b", history.lower()) is False and re.search(r"\banonymous\b", history.lower()):
                # If any URL present or explicit non-anonymized artifacts, flag it
                checklist.append("- Attention: Public URLs or non-anonymized text detected in memories. Confirm double-blind compliance (no author-identifying files/URLs).")
            # Promises to release assets
            if re.search(r"\b(release|publicly available|will be made available|code will be|dataset will be|open-source)\b", text):
                checklist.append("- Action: Authors promise release. Request timeline, access modality, and license for releases.")
            else:
                checklist.append("- Missing: No clear release statement. If authors plan to release code/data, request specifics and licences.")
            # Summarize a few concrete memory lines as supporting evidence
            evidence_lines = []
            for idx, entry in enumerate(values[:6], start=1):
                snippet = entry.replace("\n", " ").strip()
                evidence_lines.append(f"{idx}. {snippet[:280]}")
            result = ["POL checklist (deterministic):"] + checklist + ["", "Supporting memory excerpts:"] + evidence_lines
            return ("\n".join(result))[:MAX_SUMMARY_CHARS]

        # For other dimensions, use LLM-based summarization prompt (single call)
        prompt = (
            "Create a query-focused memory summary for a peer-review agent. "
            "Prefer same-dimension memories, preserve paper names and concrete "
            "evidence expectations, and do not invent facts. Return at most "
            f"{MAX_SUMMARY_CHARS} characters.\n\n"
            "Target dimension:\n"
            + dimension
            + "\n\nQuery:\n"
            + query.query_text
            + "\n\nMemory history:\n"
            + history
        )
        try:
            summary = self.toolkit.llm_completion(
                [{"role": "user", "content": prompt}]
            )
            return str(summary)[:MAX_SUMMARY_CHARS]
        except Exception:
            return history[:MAX_SUMMARY_CHARS]
