# AgentReview/agentreview/utility/s2_retrieval.py

from agentreview.openscholar_bridge.src import use_search_apis

def retrieve_with_openscholar(
    query: str,
    title_query: str = None,
    use_llm_keywords: bool = False,
    client=None,
    model_name=None,
    max_papers: int = 8,
):
    """
    Minimal wrapper around OpenScholar Semantic Scholar pipeline.
    Returns a list of papers (dicts).
    """

    if title_query:
        papers = use_search_apis.search_paper_via_query(
            query=title_query,
            max_paper_num=max_papers,
            min_citation_count=0,
            sort=None,
        )
        papers = papers or []
        for paper in papers:
            paper["title_query"] = True
        return papers

    # 情况 1：不用 LLM 生成关键词（最稳，推荐先用）
    if not use_llm_keywords:
        papers = use_search_apis.search_paper_via_query(
            query=query,
            max_paper_num=max_papers
        )
        return papers or []

    # 情况 2：完全复用 OpenScholar 的 LLM → S2 流程
    keywords = use_search_apis.retrieve_keywords(
        question=query,
        client=client,
        model_name=model_name
    )

    keyword_papers, title_papers, _ = use_search_apis.search_semantic_scholar(
        question=query,
        new_keywords=keywords,
        new_titles=[]
    )

    return keyword_papers + title_papers
