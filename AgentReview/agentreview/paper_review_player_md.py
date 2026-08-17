# AgentReview/agentreview/paper_review_player_md.py
import hashlib
import json
from pathlib import Path
from typing import List

from agentreview.agent import Player
from agentreview.message import Message

# 🔹 新增：引入我们写的 OpenScholar wrapper
from agentreview.paper_context import (
    CANONICAL_PAPER_CONTEXT_VERSION,
    DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    build_canonical_paper_context,
)
from agentreview.source_map import extract_title_from_content_list, load_content_list


def extract_title_from_md(text: str) -> str:
    """
    极简标题提取：
    - 优先取第一行的 '# xxx'
    - 否则取第一行非空文本
    """
    lines = text.splitlines()
    title_lines = []
    collecting = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if collecting and title_lines:
                break
            continue
        if line.startswith("#"):
            collecting = True
            title_lines.append(line.lstrip("#").strip())
            continue
        if collecting:
            if line.startswith("#"):
                break
            title_lines.append(line)
            continue
        return line

    if title_lines:
        return " ".join(title_lines).strip()
    return "machine learning"


def format_refs(papers, max_abs_chars: int = 1200) -> str:
    if not papers:
        return "References:\n(No related papers retrieved.)"

    lines = ["References:"]
    for i, p in enumerate(papers):
        lines.append(f"[{i}] Title: {p.get('title')}")
        if p.get("year"):
            lines.append(f"    Year: {p['year']}")
        citation_count = p.get("citationCount")
        if citation_count is None:
            citation_count = p.get("citation_counts")
        if citation_count is not None:
            lines.append(f"    Citations: {citation_count}")
        if p.get("url"):
            lines.append(f"    URL: {p['url']}")
        abstract = (p.get("abstract") or "").strip()
        if abstract:
            if len(abstract) > max_abs_chars:
                abstract = abstract[:max_abs_chars] + "..."
            lines.append(f"    Abstract: {abstract}")
        lines.append("")
    return "\n".join(lines)


class MarkdownPaperExtractorPlayer(Player):
    """
    A lightweight paper extractor for single-paper review that reads a Markdown file
    through the shared canonical rich-paper builder.  Frozen Semantic Scholar
    data is retained as a separate capability and is never injected by default.
    """

    def __init__(self, paper_md_path: str, **kwargs):
        super().__init__(**kwargs)
        self.paper_md_path = paper_md_path
        self.last_memory = None

    def act(self, observation: List[Message]) -> str:
        p = Path(self.paper_md_path)
        frozen_bundle_path = getattr(self.args, "frozen_context_bundle_path", None)
        if frozen_bundle_path:
            bundle_path = Path(frozen_bundle_path).expanduser().resolve()
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            if bundle.get("contains_human_review_data") is not False:
                raise RuntimeError(
                    f"Frozen context bundle is not marked human-review-free: {bundle_path}"
                )
            expected_path = (
                bundle_path.parent / bundle["paper_markdown_relative_path"]
            ).resolve()
            if p.resolve() != expected_path:
                raise RuntimeError(
                    f"Frozen Markdown path mismatch: {p.resolve()} != {expected_path}"
                )
            actual_sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if actual_sha != bundle.get("paper_markdown_sha256"):
                raise RuntimeError(
                    f"Frozen Markdown checksum mismatch for {bundle.get('paper_id')}"
                )
            self.last_memory = dict(bundle["last_memory"])
            self.last_memory["paper_md_path"] = str(p.resolve())
            self.last_memory["paper_context_bundle"] = str(bundle_path)
            self.last_memory["paper_context_bundle_sha256"] = hashlib.sha256(
                bundle_path.read_bytes()
            ).hexdigest()
            visible = dict(bundle.get("candidate_visible_metadata") or {})
            canonical = build_canonical_paper_context(
                str(p),
                paper_id=str(bundle.get("paper_id") or visible.get("paper_id") or p.stem),
                title=str(visible.get("title") or self.last_memory.get("paper_title") or ""),
                venue=str(visible.get("venue") or ""),
                year=visible.get("year") or "",
                reviewer_role="SPECIALIST",
                max_chars=getattr(
                    self.args,
                    "paper_context_max_chars",
                    DEFAULT_PAPER_CONTEXT_MAX_CHARS,
                ),
                internal_provenance={
                    "split": bundle.get("split"),
                    "bundle_path": str(bundle_path),
                },
            )
            self.last_memory["source_anchors"] = canonical.source_anchors
            self.last_memory["candidate_visible_metadata"] = canonical.candidate_metadata
            self.last_memory["paper_context_version"] = canonical.version
            return canonical.text

        text = p.read_text(encoding="utf-8", errors="ignore")
        content_list = load_content_list(str(p))

        # ① 提取一个 query（标题）
        title = extract_title_from_content_list(content_list) or extract_title_from_md(text)
        paper_id = str(getattr(self.args, "paper_id", "") or p.parent.name or p.stem)
        canonical = build_canonical_paper_context(
            str(p),
            paper_id=paper_id,
            title=title,
            venue=str(getattr(self.args, "conference", "") or ""),
            year=str(getattr(self.args, "paper_year", "") or ""),
            reviewer_role="SPECIALIST",
            max_chars=getattr(
                self.args,
                "paper_context_max_chars",
                DEFAULT_PAPER_CONTEXT_MAX_CHARS,
            ),
            internal_provenance={"paper_md_path": str(p.resolve())},
        )

        self.last_memory = {
            "paper_md_path": str(p),
            "paper_title": title,
            "paper_id": paper_id,
            "semantic_scholar_query": None,
            "semantic_scholar_queries": [],
            "keyword_model_name": getattr(self.args, "keyword_model_name", None),
            "retrieved_papers": [],
            "references_text": "",
            "source_anchors": canonical.source_anchors,
            "candidate_visible_metadata": canonical.candidate_metadata,
            "paper_context_version": CANONICAL_PAPER_CONTEXT_VERSION,
            "s2_usage": {
                "available_as_frozen_capability": bool(
                    getattr(self.args, "s2_retrieval_pool", None)
                ),
                "used": False,
                "default_injection": False,
            },
            "memory_usage": {
                "local_paper_memory": "Stored in this memory.json for reruns and audit.",
                "global_memory_library": "Aggregated into memory_library.jsonl and memory_index.json for retrieval by sample_id/title/reviewer.",
                "review_runtime_use": "The extractor message provides canonical paper content and source anchors; S2 is optional and separate.",
            },
        }
        return canonical.text
