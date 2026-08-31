"""Stable compatibility wrapper for optional live OpenScholar retrieval."""

from __future__ import annotations

from typing import Any

from agentreview.openscholar_bridge.src import use_search_apis

DEFAULT_MODEL_NAME = "yi-lightning"


def retrieve_with_openscholar(
    query: str,
    title_query: str | None = None,
    use_llm_keywords: bool = False,
    client: Any = None,
    model_name: str | None = None,
    max_papers: int = 8,
    s2_api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve papers through title matching, LLM keywords, or a direct query."""
    if max_papers < 1:
        raise ValueError("max_papers must be at least 1")

    if title_query:
        _, title_papers, _ = use_search_apis.search_semantic_scholar(
            question="",
            new_keywords=[],
            new_titles=[title_query],
            max_paper_num=max_papers,
            s2_api_key=s2_api_key,
        )
        return title_papers[:max_papers]

    if use_llm_keywords:
        if client is None:
            raise ValueError("client is required when use_llm_keywords is enabled")
        resolved_model_name = model_name or DEFAULT_MODEL_NAME
        keywords = use_search_apis.retrieve_keywords(
            question=query,
            client=client,
            model_name=resolved_model_name,
        )
        keyword_papers, title_papers, _ = use_search_apis.search_semantic_scholar(
            question=query,
            new_keywords=keywords,
            new_titles=[],
            max_paper_num=max_papers,
            s2_api_key=s2_api_key,
        )
        return (keyword_papers + title_papers)[:max_papers]

    keyword_papers, _, _ = use_search_apis.search_semantic_scholar(
        question=query,
        new_keywords=[query],
        new_titles=[],
        max_paper_num=max_papers,
        s2_api_key=s2_api_key,
    )
    return keyword_papers[:max_papers]
