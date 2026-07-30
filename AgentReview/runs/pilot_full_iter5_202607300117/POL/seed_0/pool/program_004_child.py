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

        # If POL dimension, produce a focused checklist and surface red flags
        if dimension == "POL":
            # aggregate text snippets to search for key policy evidence
            combined = " ".join(
                (rec.get("lesson", "") + " " + rec.get("fact", "") + " " + (rec.get("reviewer") or ""))
                for rec in records
            ).lower()
            checklist = []
            # Check for dataset provenance mentions
            if re.search(r"\b(dji|drone|lidar|airsim|aus|dataset|synthetic|real-?world)\b", combined):
                checklist.append("- Evidence: Paper mentions drone/LiDAR or AUS dataset. Request explicit provenance: who collected the real-world data and under what permissions?")
                # additional probe for simulation vs real capture
                if re.search(r"\bairsim\b", combined) or re.search(r"\bunreal(engine)?\b", combined):
                    checklist.append("  - Note: If AUS uses AirSim / Unreal assets, request explicit statements about third-party asset licensing and redistribution constraints.")
                if re.search(r"\bcolmap\b", combined) or re.search(r"\blidar\b", combined):
                    checklist.append("  - Note: Real-world capture (LiDAR/COLMAP) may involve privacy or airspace permissions; request permits/approvals.")
            else:
                checklist.append("- Missing: No explicit mention of real-world data provenance found. Ask authors to state data collectors and permissions.")
            # Check for licensing and redistribution notes
            if re.search(r"\b(license|mit|cc-by|cc0|restricted|redistribut)\b", combined):
                checklist.append("- Evidence: Licence language appears present. Request the exact dataset and code licences and confirm third-party asset compatibility.")
            else:
                checklist.append("- Missing: No explicit dataset/code license found. Request explicit license identifiers for AUS, simulator assets (AirSim/Unreal), and released code.")
            # Check for IRB/ethics or privacy handling
            if re.search(r"\b(ethic|irb|consent|privacy|anonymi|personal|pii|human subjects|medical|mri|adni)\b", combined):
                checklist.append("- Evidence: Ethics/privacy language or medical data mentions present. Provide exact IRB/ethics approval identifiers, data-use agreements, or justification for exemption.")
                if re.search(r"\b(adni|mri)\b", combined):
                    checklist.append("  - Action: For medical imaging (e.g., ADNI/MRI) explicitly state provenance, consent terms, and any IRB approvals or data-use restrictions.")
            else:
                checklist.append("- Missing: No IRB/ethics or privacy/PII handling statements found. Ask for anonymization steps, consent, or IRB details if real captures include people or private property.")
            # Check for anonymized public links that may deanonymize authors
            if re.search(r"\banonymous\.4open\.science\b", combined) or re.search(r"https?://\S+", combined):
                checklist.append("- Attention: Public anonymous link(s) / URLs detected. Confirm these artifacts remain deanonymized (no git history, metadata, or institution-identifying files) during double-blind review.")
                checklist.append("  - Action: Authors should verify repository metadata, commit history, and dataset metadata do not leak identities while under double-blind review.")
            # Check for promises to release code/data and ask for license and access plan
            if re.search(r"\b(release|publicly available|will be made)\b", combined):
                checklist.append("- Action: Authors promise to release code/data. Request release plan, access modality, and licence used; ensure third-party assets are redistributable.")
            else:
                checklist.append("- Missing: No clear statement on data/code release. If claiming release, request timeline and licence.")
            # Check for double-blind preservation in manuscript text
            if re.search(r"\banonymous author(s)?\b", combined) or re.search(r"\bdouble-?blind\b", combined):
                checklist.append("- Strength: Double-blind anonymity is preserved in manuscript text. Confirm any external artifacts (supplementary repos) also preserve anonymity.")
            # Summarize recorded lessons/facts briefly (up to top 6)
            summary_lines = ["POL-focused memory summary and checklist:"]
            summary_lines += checklist
            summary_lines.append("") 
            summary_lines.append("Relevant remembered facts (short):")
            for idx, rec in enumerate(records[:6], start=1):
                line = f"{idx}. [{rec['dimension']}]"
                if rec["reviewer"]:
                    line += f" {rec['reviewer']}"
                if rec["fact"]:
                    line += " Fact: " + rec["fact"]
                if rec["lesson"]:
                    line += " Lesson: " + rec["lesson"]
                summary_lines.append(line)
            return "\n".join(summary_lines)[:3000]

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
