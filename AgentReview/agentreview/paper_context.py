"""Canonical candidate-visible paper context shared by every review runtime.

This module is deliberately benchmark-agnostic.  It formats only the paper,
candidate-visible metadata and source anchors.  Provenance and frozen retrieval
assets stay out of the returned text and travel through separate host-side
objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .source_map import (
    build_source_anchors,
    extract_title_from_content_list,
    format_source_anchors,
    load_content_list,
)


CANONICAL_PAPER_CONTEXT_VERSION = "canonical-rich-paper-v1"
DEFAULT_PAPER_CONTEXT_MAX_CHARS = 60000
CANDIDATE_VISIBLE_METADATA_FIELDS = (
    "paper_id",
    "title",
    "venue",
    "year",
    "reviewer_role",
)
INTERNAL_PROVENANCE_FIELDS = frozenset(
    {
        "paper_md_path",
        "markdown_path",
        "source_path",
        "split",
        "sha256",
        "asset_version",
        "run_id",
        "manifest",
        "manifest_path",
        "work_family",
    }
)


def clip_text(text: str, max_chars: int, marker: str = "context clipped") -> str:
    """Deterministically retain the beginning and end within ``max_chars``."""
    value = str(text or "")
    limit = max(0, int(max_chars))
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    separator = f"\n\n[{marker}]\n\n"
    if limit <= len(separator) + 2:
        return value[:limit]
    available = limit - len(separator)
    head = available // 2
    return value[:head] + separator + value[-(available - head) :]


def _clean_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    raw = dict(metadata or {})
    leaked = INTERNAL_PROVENANCE_FIELDS & set(raw)
    # Provenance may be supplied to a caller, but it must never be silently
    # accepted as candidate-visible metadata.
    if leaked:
        raise ValueError(
            "Internal provenance fields cannot be candidate-visible: "
            + ", ".join(sorted(leaked))
        )
    unknown = set(raw) - set(CANDIDATE_VISIBLE_METADATA_FIELDS)
    if unknown:
        raise ValueError(
            "Unknown candidate-visible metadata fields: "
            + ", ".join(sorted(unknown))
        )
    return {
        name: str(raw.get(name) or "").strip()
        for name in CANDIDATE_VISIBLE_METADATA_FIELDS
    }


def candidate_visible_metadata(
    *,
    paper_id: str,
    title: str,
    venue: str = "",
    year: Any = "",
    reviewer_role: str = "",
) -> Dict[str, str]:
    return _clean_metadata(
        {
            "paper_id": paper_id,
            "title": title,
            "venue": venue,
            "year": year,
            "reviewer_role": reviewer_role,
        }
    )


def _fallback_anchors(paper_text: str, max_anchors: int = 40) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []
    for start in range(0, min(len(paper_text), 40000), 1000):
        excerpt = paper_text[start : start + 500].replace("\n", " ").strip()
        if not excerpt:
            continue
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


def format_canonical_paper_context(
    paper_text: str,
    metadata: Mapping[str, Any],
    source_anchors: Iterable[Mapping[str, Any]],
    *,
    max_chars: int = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
) -> str:
    """Format the one canonical Reviewer/MemoryProgram paper input.

    The total result, rather than only the Markdown section, is bounded.  This
    makes Evolution, historical replay and deployment use identical clipping.
    Semantic Scholar results are intentionally absent; they are an optional
    capability and are never default context.
    """
    visible = _clean_metadata(metadata)
    metadata_text = "\n".join(
        f"{name}: {visible[name] or '(unknown)'}"
        for name in CANDIDATE_VISIBLE_METADATA_FIELDS
    )
    anchors = [dict(value) for value in source_anchors]
    anchors_text = format_source_anchors(anchors)
    prefix = (
        f"Context protocol: {CANONICAL_PAPER_CONTEXT_VERSION}\n"
        "=== CANDIDATE-VISIBLE METADATA ===\n"
        f"{metadata_text}\n"
        "=== END CANDIDATE-VISIBLE METADATA ===\n\n"
        "=== BEGIN PAPER (MD) ===\n"
    )
    suffix = (
        "\n=== END PAPER (MD) ===\n\n"
        "=== SOURCE ANCHORS ===\n"
        f"{anchors_text}\n"
        "=== END SOURCE ANCHORS ==="
    )
    limit = max(1, int(max_chars))
    paper_budget = max(0, limit - len(prefix) - len(suffix))
    clipped_paper = clip_text(
        paper_text,
        paper_budget,
        marker="paper markdown clipped by canonical builder",
    )
    result = prefix + clipped_paper + suffix
    # Extremely large anchor blocks are clipped only as a final safety guard.
    return clip_text(
        result,
        limit,
        marker="canonical paper context clipped at safety boundary",
    )


@dataclass(frozen=True)
class CanonicalPaperContext:
    text: str
    candidate_metadata: Dict[str, str]
    source_anchors: List[Dict[str, Any]] = field(default_factory=list)
    internal_provenance: Dict[str, Any] = field(default_factory=dict)
    version: str = CANONICAL_PAPER_CONTEXT_VERSION


def build_canonical_paper_context(
    paper_md_path: str,
    *,
    paper_id: str,
    title: str = "",
    venue: str = "",
    year: Any = "",
    reviewer_role: str = "",
    max_chars: int = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    internal_provenance: Optional[Mapping[str, Any]] = None,
) -> CanonicalPaperContext:
    path = Path(paper_md_path).expanduser().resolve()
    paper_text = path.read_text(encoding="utf-8", errors="ignore")
    content_list = load_content_list(str(path))
    resolved_title = (
        str(title or "").strip()
        or str(extract_title_from_content_list(content_list) or "").strip()
        or str(paper_id)
    )
    anchors = build_source_anchors(content_list)
    if not anchors:
        anchors = _fallback_anchors(paper_text)
    visible = candidate_visible_metadata(
        paper_id=paper_id,
        title=resolved_title,
        venue=venue,
        year=year,
        reviewer_role=reviewer_role,
    )
    return CanonicalPaperContext(
        text=format_canonical_paper_context(
            paper_text,
            visible,
            anchors,
            max_chars=max_chars,
        ),
        candidate_metadata=visible,
        source_anchors=anchors,
        internal_provenance=dict(internal_provenance or {}),
    )
