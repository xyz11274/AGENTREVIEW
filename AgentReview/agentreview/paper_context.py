"""Canonical candidate-visible paper context shared by every review runtime.

This module is deliberately benchmark-agnostic.  It formats only the paper,
candidate-visible metadata and source anchors.  Provenance and frozen retrieval
assets stay out of the returned text and travel through separate host-side
objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .source_map import (
    build_source_anchors,
    extract_title_from_content_list,
    format_source_anchors,
    load_content_list,
)

CANONICAL_PAPER_CONTEXT_VERSION = "canonical-complete-paper-v2"
DEFAULT_PAPER_CONTEXT_MAX_CHARS = None
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


def require_human_review_free(
    payload: Mapping[str, Any],
    *,
    source: str,
) -> None:
    """Require an explicit machine-readable human-review-free marker."""
    if payload.get("contains_human_review_data") is False:
        return
    raise RuntimeError(f"{source} requires contains_human_review_data=false")


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


def _clean_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
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
            "Unknown candidate-visible metadata fields: " + ", ".join(sorted(unknown))
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
) -> dict[str, str]:
    return _clean_metadata(
        {
            "paper_id": paper_id,
            "title": title,
            "venue": venue,
            "year": year,
            "reviewer_role": reviewer_role,
        }
    )


class PaperContextBudgetError(ValueError):
    """The complete paper requires a larger explicitly configured input budget."""

    failure_category = "shared_data"


def text_source_anchors(paper_text: str, block_chars: int = 2000) -> list[dict[str, Any]]:
    """Bind citations to real character intervals when page extraction is absent."""
    if isinstance(block_chars, int) and isinstance(block_chars, bool) is False and block_chars > 0:
        pass
    else:
        raise ValueError("block_chars requires a positive integer")
    anchors: list[dict[str, Any]] = []
    for start in range(0, len(paper_text), block_chars):
        end = min(len(paper_text), start + block_chars)
        excerpt = " ".join(paper_text[start:end].split())[:120]
        if not excerpt:
            continue
        anchors.append(
            {
                "source_id": f"SRC-{len(anchors) + 1:03d}",
                "char_start": start,
                "char_end": end,
                "type": "text",
                "text": excerpt,
            }
        )
    return anchors


def format_canonical_paper_context(
    paper_text: str,
    metadata: Mapping[str, Any],
    source_anchors: Iterable[Mapping[str, Any]],
    *,
    max_chars: int | None = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
) -> str:
    """Preserve complete paper text and enforce an explicit budget before review."""
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
    result = prefix + paper_text + suffix
    if max_chars is None:
        return result
    if isinstance(max_chars, int) and isinstance(max_chars, bool) is False and max_chars > 0:
        if len(result) <= max_chars:
            return result
        raise PaperContextBudgetError(
            f"Complete paper context requires {len(result)} characters; configured budget is {max_chars}"
        )
    raise ValueError("max_chars requires a positive integer or None")


@dataclass(frozen=True)
class CanonicalPaperContext:
    text: str
    candidate_metadata: dict[str, str]
    source_anchors: list[dict[str, Any]] = field(default_factory=list)
    internal_provenance: dict[str, Any] = field(default_factory=dict)
    version: str = CANONICAL_PAPER_CONTEXT_VERSION


def build_canonical_paper_context(
    paper_md_path: str,
    *,
    paper_id: str,
    title: str = "",
    venue: str = "",
    year: Any = "",
    reviewer_role: str = "",
    max_chars: int | None = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    internal_provenance: Mapping[str, Any] | None = None,
) -> CanonicalPaperContext:
    path = Path(paper_md_path).expanduser().resolve()
    paper_text = path.read_text(encoding="utf-8")
    content_list = load_content_list(str(path))
    resolved_title = (
        str(title or "").strip()
        or str(extract_title_from_content_list(content_list) or "").strip()
        or str(paper_id)
    )
    anchors = build_source_anchors(content_list)
    if not anchors:
        anchors = text_source_anchors(paper_text)
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
