"""Benchmark-agnostic construction of canonical single-task review requests."""

from typing import Any, Iterable, Mapping

from agentreview.paper_context import (
    DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    candidate_visible_metadata,
    format_canonical_paper_context,
)


def build_review_question(
    *,
    task_id: str,
    paper_id: str,
    title: str,
    paper_text: str,
    source_anchors: Iterable[Mapping[str, Any]],
    venue: str = "",
    year: Any = "",
    max_paper_chars: int = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
) -> str:
    visible = candidate_visible_metadata(
        paper_id=paper_id,
        title=title,
        venue=venue,
        year=year,
        reviewer_role=task_id,
    )
    context = format_canonical_paper_context(
        paper_text,
        visible,
        source_anchors,
        max_chars=max_paper_chars,
    )
    return (
        f"Review task: {task_id}\n\n{context}\n\n"
        f"Return the complete {task_id} specialist review using the frozen output protocol."
    )


def fallback_source_anchors(paper_text: str, max_anchors: int = 40) -> list[dict]:
    anchors = []
    for start in range(0, min(len(paper_text), 40000), 1000):
        excerpt = paper_text[start : start + 500].replace("\n", " ").strip()
        if excerpt:
            anchors.append(
                {
                    "source_id": f"SRC-{len(anchors) + 1:03d}",
                    "page_idx": 0,
                    "type": "text",
                    "text": excerpt,
                }
            )
        if len(anchors) >= max_anchors:
            break
    return anchors
