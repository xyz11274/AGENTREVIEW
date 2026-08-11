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
        # If the query dimension is POL, produce a focused checklist and surface red flags
        if dimension == "POL":
            combined = " ".join(
                (rec.get("lesson", "") + " " + rec.get("fact", "") + " " + (rec.get("reviewer") or ""))
                for rec in records
            ).lower()
            checklist = []
            # Data provenance and real-world capture indicators
            if re.search(r"\b(dji|drone|lidar|airsim|aus|dataset|synthetic|real-?world)\b", combined):
                checklist.append("- Evidence: Paper mentions drone/LiDAR or the AUS dataset. Request explicit provenance and capture permissions for real-world data.")
                if re.search(r"\bairsim\b", combined) or re.search(r"\bunreal\b", combined):
                    checklist.append("  - Note: AUS uses AirSim/Unreal assets. Ask authors to state third-party asset licensing and redistribution constraints.")
                if re.search(r"\bcolmap\b", combined) or re.search(r"\blidar\b", combined):
                    checklist.append("  - Note: Real-world capture (LiDAR/COLMAP) may require airspace/property permissions; request permit documentation.")
            else:
                checklist.append("- Missing: No explicit mention of real-world data provenance. Ask authors to state who collected real captures and under what permissions.")

            # Licensing / redistribution
            if re.search(r"\b(license|mit|bsd|cc-by|cc0|restricted|redistribut)\b", combined):
                checklist.append("- Evidence: Licence language detected. Request exact dataset and code licenses and confirm compatibility with third-party assets.")
            else:
                checklist.append("- Missing: No explicit dataset/code license found. Request license identifiers for AUS, simulator assets, and released code.")

            # Ethics / IRB / privacy / PII
            if re.search(r"\b(ethic|irb|consent|privacy|anonymi|personal|pii|human subjects|medical|mri|adni)\b", combined):
                checklist.append("- Evidence: Ethics/privacy language present. Ask for IRB/ethics approval identifiers or statement of exemption and consent/DUA terms.")
                if re.search(r"\b(adni|mri)\b", combined):
                    checklist.append("  - Action: For medical imaging (e.g., ADNI/MRI) explicitly state provenance, consent terms, and data-use restrictions.")
            else:
                checklist.append("- Missing: No IRB/ethics or privacy/PII handling statements found. Request anonymization steps and approvals if human-related data is used.")

            # Double-blind / anonymity artifacts
            if re.search(r"\banonymous\.4open\.science\b", combined) or re.search(r"https?://\S+", combined):
                checklist.append("- Attention: Repository URLs / anonymous links detected. Confirm repositories/supplementary materials do not leak author identity (git history, metadata).")
            else:
                checklist.append("- Missing: No statements about preserving anonymity for external artifacts. If linking repositories, ensure they are sanitized for double-blind review.")

            # Promises to release code/data
            if re.search(r"\b(release|publicly available|will be made|available upon request)\b", combined):
                checklist.append("- Action: Authors promise to release code/data. Request release plan, access modality, and exact license; ensure third-party assets are redistributable.")
            else:
                checklist.append("- Missing: No clear release plan. If claiming future release, request timeline and licensing.")

            # Aggregate top remembered facts (short)
            facts_summary = []
            for idx, rec in enumerate(records[:6], start=1):
                parts = [f"{idx}. [{rec['dimension']}]"]
                if rec.get("reviewer"):
                    parts.append(rec["reviewer"])
                if rec.get("fact"):
                    parts.append("Fact: " + rec["fact"])
                if rec.get("lesson"):
                    parts.append("Lesson: " + rec["lesson"])
                facts_summary.append(" ".join(parts))

            out_lines = ["POL-focused memory summary and checklist:"]
            out_lines.extend(checklist)
            out_lines.append("")
            out_lines.append("Relevant remembered facts (short):")
            out_lines.extend(facts_summary or ["- (no remembered POL facts)"])
            return "\n".join(out_lines)[:3000]

        # Default behavior: return concise remembered lessons/facts
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
