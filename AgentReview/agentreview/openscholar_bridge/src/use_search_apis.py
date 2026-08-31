"""Optional live-retrieval helpers retained for OpenScholar compatibility.

The formal AgentReview pipeline consumes frozen Semantic Scholar assets. This
module provides an explicit, activatable interface for legacy live retrieval.
Credentials and endpoints are resolved when a request is sent so callers can
configure each run without embedding secrets in source code.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests
from agentreview.openscholar_bridge.src.utils import load_jsonlines

SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"
YOU_SEARCH_URL = "https://api.ydc-index.io/search"
DEFAULT_MODEL_NAME = "yi-lightning"
DEFAULT_TIMEOUT_SECONDS = 30

KEYWORD_EXTRACTION_PROMPT = """
Suggest short, comma-separated Semantic Scholar queries that retrieve papers
relevant to the research question.

Question: How have prior work incorporated personality attributes to train personalized dialogue generation models?
Search queries: personalized dialogue generation, personalized language models, personalized dialogue

Question: How do retrieval-augmented LMs perform well in knowledge-intensive tasks?
Search queries: retrieval-augmented LMs, knowledge-intensive tasks, retrieval-augmented generation

Question: {question}
Search queries:
"""


def _configured_value(explicit_value: str | None, environment_name: str) -> str:
    """Return an explicit setting or the environment value available now."""
    value = (
        explicit_value
        if explicit_value is not None
        else os.environ.get(environment_name, "")
    )
    return value.strip()


def _semantic_scholar_headers(s2_api_key: str | None = None) -> dict[str, str]:
    api_key = _configured_value(s2_api_key, "S2_API_KEY")
    if api_key:
        return {"x-api-key": api_key}
    return {}


def _validated_search_data(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        data = payload.get("data", [])
        if isinstance(data, list):
            return [dict(paper) for paper in data if isinstance(paper, Mapping)]
    return []


def is_integer_string(value: str) -> bool:
    return value.isdigit()


def get_paper_data(
    paper_id: str,
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Retrieve one paper by Semantic Scholar ID or numeric Corpus ID."""
    normalized_id = str(paper_id).strip()
    if not normalized_id:
        raise ValueError("paper_id must contain a Semantic Scholar identifier")

    resource_id = (
        f"CorpusID:{normalized_id}"
        if is_integer_string(normalized_id)
        else normalized_id
    )
    try:
        response = requests.get(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/{resource_id}",
            params={
                "fields": (
                    "title,year,abstract,url,authors.name,citationCount,openAccessPdf"
                )
            },
            headers=_semantic_scholar_headers(s2_api_key),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, TypeError, ValueError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def call_api(
    input_query: str,
    client: Any,
    model_name: str = DEFAULT_MODEL_NAME,
    max_tokens: int = 1500,
) -> str:
    """Call an OpenAI-compatible chat client for keyword extraction."""
    if client is None:
        raise ValueError("client is required for LLM keyword extraction")
    if not model_name:
        raise ValueError("model_name is required for LLM keyword extraction")

    chat_completion = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": input_query},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    content = chat_completion.choices[0].message.content
    if not isinstance(content, str):
        raise TypeError("keyword extraction response must contain text")
    return content


def get_citations(paper_id: str, *, s2_api_key: str | None = None) -> int:
    paper_data = get_paper_data(paper_id, s2_api_key=s2_api_key)
    if isinstance(paper_data, Mapping):
        citation_count = paper_data.get("citationCount", 0)
        if isinstance(citation_count, int):
            return citation_count
    return 0


def search_paper_via_query(
    query: str,
    max_paper_num: int = 60,
    min_citation_count: int = 1,
    sort: str | None = None,
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Search Semantic Scholar once and return validated paper objects."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if max_paper_num < 1:
        raise ValueError("max_paper_num must be at least 1")

    marker = "Search queries:"
    normalized_query = query.split(marker, 1)[1] if marker in query else query
    normalized_query = normalized_query.strip()
    if not normalized_query:
        return []

    query_params: dict[str, Any] = {
        "query": normalized_query,
        "limit": max_paper_num,
        "minCitationCount": min_citation_count,
        "fields": (
            "paperId,title,year,abstract,authors.name,citationCount,url,externalIds"
        ),
    }
    if sort:
        query_params["sort"] = sort

    try:
        response = requests.get(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search",
            params=query_params,
            headers=_semantic_scholar_headers(s2_api_key),
            timeout=timeout,
        )
        response.raise_for_status()
        return _validated_search_data(response.json())
    except (requests.RequestException, TypeError, ValueError):
        return []


def _author_names(authors: Any) -> list[str]:
    if not isinstance(authors, Sequence) or isinstance(authors, (str, bytes)):
        return []

    names: list[str] = []
    for author in authors:
        name = author.get("name", "") if isinstance(author, Mapping) else author
        normalized_name = str(name).strip()
        if normalized_name:
            names.append(normalized_name)
    return names


def _prepare_paper_record(
    paper: Mapping[str, Any], *, title_query: bool
) -> dict[str, Any] | None:
    paper_id = str(paper.get("paperId", "")).strip()
    if not paper_id:
        return None

    prepared = dict(paper)
    prepared["paperId"] = paper_id
    prepared["abstract"] = paper.get("abstract") or ""
    prepared["text"] = prepared["abstract"]
    prepared["authors"] = _author_names(paper.get("authors", []))
    prepared["citationCount"] = paper.get("citationCount") or 0
    prepared["externalIds"] = paper.get("externalIds") or {}
    prepared["title_query"] = title_query
    return prepared


def search_paper_via_titles(
    title_list: Sequence[str],
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Match paper titles and return unique, normalized paper records."""
    papers_by_id: dict[str, dict[str, Any]] = {}
    for title in title_list:
        normalized_title = str(title).strip()
        if not normalized_title:
            continue
        try:
            response = requests.get(
                f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search/match",
                params={
                    "query": normalized_title,
                    "fields": (
                        "paperId,title,year,abstract,authors.name,citationCount,"
                        "url,externalIds,corpusId"
                    ),
                },
                headers=_semantic_scholar_headers(s2_api_key),
                timeout=timeout,
            )
            response.raise_for_status()
            matches = _validated_search_data(response.json())
        except (requests.RequestException, TypeError, ValueError):
            continue

        for match in matches[:1]:
            prepared = _prepare_paper_record(match, title_query=True)
            if prepared is not None:
                papers_by_id.setdefault(prepared["paperId"], prepared)
    return list(papers_by_id.values())


def retrieve_keywords(question: str, client: Any, model_name: str) -> list[str]:
    """Generate up to five normalized Semantic Scholar queries."""
    response_text = call_api(
        KEYWORD_EXTRACTION_PROMPT.format_map({"question": question}),
        client,
        model_name=model_name,
    )
    marker = "Search queries:"
    query_text = (
        response_text.rsplit(marker, 1)[1] if marker in response_text else response_text
    )
    queries = [
        part.strip(" \t\n-*")
        for part in re.split(r",|\n", query_text)
        if part.strip(" \t\n-*")
    ]
    return queries[:5]


def _terms(value: Sequence[str] | str | None) -> list[str]:
    candidates = [value] if isinstance(value, str) else list(value or [])
    return [
        str(candidate).strip() for candidate in candidates if str(candidate).strip()
    ]


def _format_paper_context(paper: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "semantic_scholar_id": paper["paperId"],
        "type": "ss_abstract",
        "year": paper.get("year") or "",
        "authors": paper.get("authors", []),
        "title": paper.get("title") or "",
        "text": paper.get("text") or paper.get("abstract") or "",
        "url": paper.get("url") or "",
        "citation_counts": paper.get("citationCount") or 0,
        "abstract": paper.get("abstract") or "",
        "title_query": bool(paper.get("title_query")),
    }


def search_semantic_scholar(
    question: str,
    new_keywords: Sequence[str] | str | None,
    new_titles: Sequence[str] | str | None,
    *,
    s2_api_key: str | None = None,
    max_paper_num: int = 60,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Retrieve keyword and title matches with one stable result schema."""
    keywords = _terms(new_keywords)
    if not keywords and question.strip():
        keywords = [question.strip()]

    keyword_papers_by_id: dict[str, dict[str, Any]] = {}
    for keyword in keywords:
        for paper in search_paper_via_query(
            keyword,
            max_paper_num=max_paper_num,
            s2_api_key=s2_api_key,
        ):
            prepared = _prepare_paper_record(paper, title_query=False)
            if prepared is not None:
                keyword_papers_by_id.setdefault(prepared["paperId"], prepared)

    title_papers_by_id: dict[str, dict[str, Any]] = {}
    for paper in search_paper_via_titles(_terms(new_titles), s2_api_key=s2_api_key):
        paper_id = paper["paperId"]
        if paper_id not in keyword_papers_by_id:
            title_papers_by_id.setdefault(paper_id, paper)

    keyword_papers = [
        _format_paper_context(paper) for paper in keyword_papers_by_id.values()
    ]
    title_papers = [
        _format_paper_context(paper) for paper in title_papers_by_id.values()
    ]
    return keyword_papers, title_papers, keywords


def _batch_paper_data(
    paper_ids: Sequence[str],
    *,
    identifier_prefix: str,
    fields: str,
    s2_api_key: str | None,
    timeout: float,
) -> dict[str, Any]:
    normalized_ids = [str(paper_id).strip() for paper_id in paper_ids]
    normalized_ids = [paper_id for paper_id in normalized_ids if paper_id]
    if not normalized_ids:
        return {}

    response = requests.post(
        f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/batch",
        params={"fields": fields},
        json={
            "ids": [f"{identifier_prefix}:{paper_id}" for paper_id in normalized_ids]
        },
        headers=_semantic_scholar_headers(s2_api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise TypeError("Semantic Scholar batch response must be a list")
    return dict(zip(normalized_ids, payload))


def batch_paper_data(
    arxiv_ids: Sequence[str],
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return _batch_paper_data(
        arxiv_ids,
        identifier_prefix="ARXIV",
        fields="referenceCount,citationCount,title,url,publicationDate,abstract",
        s2_api_key=s2_api_key,
        timeout=timeout,
    )


def batch_paper_data_pubmed(
    pubmed_ids: Sequence[str],
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return _batch_paper_data(
        pubmed_ids,
        identifier_prefix="PMID",
        fields="referenceCount,citationCount,title,url,publicationDate,abstract",
        s2_api_key=s2_api_key,
        timeout=timeout,
    )


def batch_paper_data_SS_ID(
    paper_ids: Sequence[str],
    *,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return _batch_paper_data(
        paper_ids,
        identifier_prefix="CorpusId",
        fields=(
            "referenceCount,citationCount,title,url,publicationDate,abstract,"
            "year,authors.name"
        ),
        s2_api_key=s2_api_key,
        timeout=timeout,
    )


def parsing_paragraph(
    link: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> list[str]:
    """Retrieve paragraph text from an ar5iv HTML page."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(
            "Install beautifulsoup4 to activate ar5iv paragraph retrieval"
        ) from exc

    response = requests.get(link, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    subsections = soup.find_all(class_="ltx_para", id=re.compile(r"^S\d+\.+(p|S)"))
    return [re.sub(r"\n", "", subsection.text) for subsection in subsections]


def retrieve_passages(arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
    return {
        arxiv_id: parsing_paragraph(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}")
        for arxiv_id in arxiv_ids
    }


def retrieve_passages_single_paper(arxiv_id: str) -> list[str]:
    return parsing_paragraph(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}")


def get_pubmed_abstract_title(
    pmid: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[str | None, str | None]:
    """Retrieve a PubMed title and its complete abstract."""
    try:
        response = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml"},
            timeout=timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError):
        return None, None

    title_element = root.find(".//ArticleTitle")
    if title_element is None or not title_element.text:
        return None, None
    abstract = " ".join(
        element.text
        for element in root.findall(".//AbstractText")
        if isinstance(element.text, str)
    )
    return title_element.text, abstract


def _load_google_search() -> Callable[..., Iterable[Any]]:
    try:
        from googlesearch import search
    except ImportError as exc:
        raise RuntimeError(
            "Install googlesearch-python to activate Google paper retrieval"
        ) from exc
    return search


def _paper_ids_from_urls(urls: Iterable[str]) -> tuple[list[str], list[str]]:
    arxiv_ids: dict[str, None] = {}
    pubmed_ids: dict[str, None] = {}
    for url in urls:
        arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", url)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1).removesuffix(".pdf").rstrip("/")
            arxiv_ids.setdefault(re.sub(r"v\d+$", "", arxiv_id), None)

        pubmed_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
        if pubmed_match:
            pubmed_ids.setdefault(pubmed_match.group(1), None)
    return list(arxiv_ids), list(pubmed_ids)


def _contexts_from_urls(
    urls: Iterable[str], *, source: str, s2_api_key: str | None
) -> list[dict[str, Any]]:
    arxiv_ids, pubmed_ids = _paper_ids_from_urls(urls)
    passages = retrieve_passages(arxiv_ids) if arxiv_ids else {}
    arxiv_metadata = (
        batch_paper_data(arxiv_ids, s2_api_key=s2_api_key) if arxiv_ids else {}
    )
    contexts: list[dict[str, Any]] = []
    for arxiv_id in arxiv_ids:
        metadata = arxiv_metadata.get(arxiv_id)
        if not isinstance(metadata, Mapping):
            continue
        for paragraph in passages.get(arxiv_id, []):
            contexts.append(
                {
                    "title": metadata.get("title") or "",
                    "text": paragraph,
                    "type": f"{source}_arxiv",
                    "url": metadata.get("url") or "",
                    "citation_counts": metadata.get("citationCount") or 0,
                    "abstract": metadata.get("abstract") or "",
                }
            )

    pubmed_metadata = (
        batch_paper_data_pubmed(pubmed_ids, s2_api_key=s2_api_key) if pubmed_ids else {}
    )
    for pubmed_id in pubmed_ids:
        title, abstract = get_pubmed_abstract_title(pubmed_id)
        metadata = pubmed_metadata.get(pubmed_id)
        if title is None or abstract is None or not isinstance(metadata, Mapping):
            continue
        contexts.append(
            {
                "title": title,
                "text": abstract,
                "type": f"{source}_pubmed",
                "url": metadata.get("url") or "",
                "citation_counts": metadata.get("citationCount") or 0,
                "abstract": abstract,
            }
        )
    return contexts


def search_google_non_restricted(
    query: str,
    *,
    search_provider: Callable[..., Iterable[Any]] | None = None,
    s2_api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Search Google through the optional ``googlesearch-python`` provider."""
    provider = search_provider or _load_google_search()
    results = provider(
        f"site:arxiv.org OR site:pubmed.ncbi.nlm.nih.gov {query}", advanced=True
    )
    urls = [str(getattr(result, "url", "")) for result in results]
    return _contexts_from_urls(urls, source="google_search", s2_api_key=s2_api_key)


def search_youcom_non_restricted(
    query: str,
    *,
    api_key: str | None = None,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Search You.com with an explicit key or the current ``YOU_API_KEY``."""
    resolved_api_key = _configured_value(api_key, "YOU_API_KEY")
    if not resolved_api_key:
        raise ValueError("Set YOU_API_KEY to activate You.com paper retrieval")

    response = requests.get(
        YOU_SEARCH_URL,
        params={
            "query": f"site:arxiv.org OR site:pubmed.ncbi.nlm.nih.gov {query}",
            "num_web_results": 20,
        },
        headers={"X-API-Key": resolved_api_key},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    hits = payload.get("hits") if isinstance(payload, Mapping) else None
    if not isinstance(hits, list):
        raise TypeError("You.com search response must contain a hits list")
    urls = [str(hit.get("url", "")) for hit in hits if isinstance(hit, Mapping)]
    return _contexts_from_urls(urls, source="you.com", s2_api_key=s2_api_key)


def retrieve_pes2o_passages(
    query: str,
    n_docs: int,
    domains: Any,
    *,
    index_url: str | None = None,
    s2_api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Retrieve PES2O passages from an explicitly configured index endpoint."""
    endpoint = _configured_value(index_url, "PES2O_INDEX_URL")
    if not endpoint:
        raise ValueError("Set PES2O_INDEX_URL to activate PES2O retrieval")

    response = requests.post(
        endpoint,
        json={"query": query, "n_docs": n_docs, "domains": domains},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(results, Mapping):
        raise TypeError("PES2O response must contain a results object")
    passages = results.get("passages")
    paper_ids = results.get("pes2o IDs")
    if not isinstance(passages, list) or not isinstance(paper_ids, list):
        raise TypeError("PES2O results must contain passage and paper ID lists")

    contexts: list[dict[str, Any]] = []
    for passage, paper_id in zip(passages, paper_ids):
        paper = get_paper_data(str(paper_id), s2_api_key=s2_api_key)
        if isinstance(paper, Mapping):
            context = dict(paper)
            context["text"] = str(passage)
            contexts.append(context)
    return contexts


def _load_input_data(input_path: Path) -> list[dict[str, Any]]:
    if input_path.suffix == ".jsonl":
        payload: Any = load_jsonlines(input_path)
    elif input_path.suffix == ".json":
        with input_path.open(encoding="utf-8") as input_file:
            payload = json.load(input_file)
        if isinstance(payload, Mapping) and "data" in payload:
            payload = payload["data"]
    elif input_path.suffix == ".tsv":
        with input_path.open(encoding="utf-8", newline="") as input_file:
            payload = list(csv.DictReader(input_file, delimiter="\t"))
    else:
        raise ValueError("input_file must use .jsonl, .json, or .tsv")

    if not isinstance(payload, list) or not all(
        isinstance(item, Mapping) for item in payload
    ):
        raise ValueError("input_file must contain a list of objects")
    return [dict(item) for item in payload]


def _write_output(output_path: Path, input_data: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump({"data": input_data}, output_file, ensure_ascii=False)


def _openai_client(base_url: str | None, api_key: str) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install openai to activate LLM keyword extraction") from exc
    return OpenAI(base_url=base_url, api_key=api_key)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_file", type=Path, required=True)
    parser.add_argument("--output_file", type=Path, required=True)
    parser.add_argument("--api_key_fp", type=Path)
    parser.add_argument("--base_url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--model_name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--sample_n", type=int, default=-1)
    parser.add_argument("--max_papers", type=int, default=60)
    parser.add_argument("--api", choices=("together", "anyscale"))
    parser.add_argument("--use_google", action="store_true")
    parser.add_argument("--you_search", action="store_true")
    parser.add_argument(
        "--use_semantic_scholar",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--use_llm_keywords", action="store_true")
    args = parser.parse_args()

    client = None
    if args.use_llm_keywords:
        openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if args.api_key_fp:
            openai_api_key = args.api_key_fp.read_text(encoding="utf-8").strip()
        if not openai_api_key:
            parser.error(
                "Set OPENAI_API_KEY or --api_key_fp to use LLM keyword extraction"
            )
        provider_base_urls = {
            "together": "https://api.together.xyz",
            "anyscale": "https://api.endpoints.anyscale.com/v1",
        }
        base_url = provider_base_urls.get(args.api, args.base_url)
        client = _openai_client(base_url, openai_api_key)

    try:
        input_data = _load_input_data(args.input_file)
    except ValueError as exc:
        parser.error(str(exc))

    if args.sample_n > 0:
        random.shuffle(input_data)
        input_data = input_data[: args.sample_n]

    for index, item in enumerate(input_data):
        query_value = item.get("input") or item.get("question") or item.get("query")
        if not isinstance(query_value, str) or not query_value.strip():
            raise ValueError(
                "each input item must contain input, question, or query text"
            )
        query = query_value.strip()
        item["input"] = query

        retrieved_passages: list[dict[str, Any]] = []
        if args.use_google:
            retrieved_passages.extend(search_google_non_restricted(query))
        if args.you_search:
            retrieved_passages.extend(search_youcom_non_restricted(query))
        if args.use_semantic_scholar:
            keywords = (
                retrieve_keywords(query, client, args.model_name)
                if args.use_llm_keywords
                else [query]
            )
            keyword_papers, title_papers, _ = search_semantic_scholar(
                query,
                new_keywords=keywords,
                new_titles=[],
                max_paper_num=args.max_papers,
            )
            retrieved_passages.extend(keyword_papers)
            retrieved_passages.extend(title_papers)

        existing_contexts = item.get("ctxs")
        if isinstance(existing_contexts, list):
            context_ids = [
                context["pes2o_paper_id"]
                for context in existing_contexts
                if isinstance(context, Mapping)
                and isinstance(context.get("pes2o_paper_id"), str)
            ]
            metadata_by_id = batch_paper_data_SS_ID(context_ids) if context_ids else {}
            for context in existing_contexts:
                if not isinstance(context, dict):
                    continue
                paper_id = context.get("pes2o_paper_id")
                metadata = metadata_by_id.get(paper_id)
                if isinstance(metadata, Mapping):
                    context.update(
                        {
                            "abstract": metadata.get("abstract") or "",
                            "citation_counts": metadata.get("citationCount") or 0,
                            "title": metadata.get("title") or "",
                            "url": metadata.get("url") or "",
                            "type": "dense_retriever",
                        }
                    )
            item["ctxs"] = existing_contexts + retrieved_passages
        else:
            item["ctxs"] = retrieved_passages

        original_contexts = item.get("orig_ctxs")
        if isinstance(original_contexts, list):
            item["ctxs"] = original_contexts + item["ctxs"]

        if index % 20 == 0:
            _write_output(args.output_file, input_data)

    _write_output(args.output_file, input_data)


if __name__ == "__main__":
    main()
