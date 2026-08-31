"""Canonical formatting for reviewer-memory episodes."""

from pathlib import Path
from typing import Any

from agentreview.memory_library import infer_reviewer_dimension
from agentreview.paper_context import DEFAULT_PAPER_CONTEXT_MAX_CHARS


def build_reviewer_memory_episode_text(
    record: dict[str, Any], reviewer_name: str, review: str
) -> str:
    """Format one self-generated experience through the canonical paper input."""
    dimension = infer_reviewer_dimension(reviewer_name) or "META"
    paper_path = Path(str(record.get("paper_md_path") or "")).expanduser()
    visible = dict(record.get("candidate_visible_metadata") or {})
    paper_id = str(record.get("sample_id") or record.get("paper_id") or "")
    title = str(record.get("paper_title") or visible.get("title") or paper_id)
    if paper_path.is_file():
        from agentreview.paper_context import build_canonical_paper_context

        canonical = build_canonical_paper_context(
            str(paper_path),
            paper_id=paper_id,
            title=title,
            venue=str(visible.get("venue") or ""),
            year=visible.get("year") or "",
            reviewer_role=dimension,
            max_chars=int(
                record.get("paper_context_max_chars") or DEFAULT_PAPER_CONTEXT_MAX_CHARS
            ),
            internal_provenance={"paper_md_path": str(paper_path.resolve())},
        ).text
    else:
        canonical = f"paper_id: {paper_id}\ntitle: {title}\nreviewer_role: {dimension}"
    return (
        f"{canonical}\n\n"
        "SELF-GENERATED REVIEW EXPERIENCE\n"
        f"Reviewer name: {reviewer_name}\n"
        f"{review}"
    ).strip()
