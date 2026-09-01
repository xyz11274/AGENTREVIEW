"""Frozen Semantic Scholar assets and exact paper-id runtime lookup."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from .data import canonical_title
from .io import file_sha256, value_sha256, write_derived_jsonl


class S2SearchClient(Protocol):
    def search_title(self, title: str, *, limit: int) -> list[dict[str, Any]]: ...


class SemanticScholarSearchClient:
    """Data-preparation-only Semantic Scholar Graph API client."""

    def __init__(self, api_key_env: str = "S2_API_KEY"):
        import requests

        self.requests = requests
        self.api_key = os.environ.get(api_key_env, "")

    def search_title(self, title: str, *, limit: int) -> list[dict[str, Any]]:
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        response = self.requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": title,
                "limit": int(limit),
                "fields": "paperId,externalIds,title,abstract,year,publicationDate,venue,authors,url",
            },
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError("Semantic Scholar search response lacks data list")
        return [dict(value) for value in values if isinstance(value, dict)]


def _family_title(value: str) -> str:
    text = canonical_title(value)
    text = re.sub(r"\bv\d+\b$", "", text)
    return " ".join(text.split())


def _external_ids(value: dict[str, Any]) -> set[str]:
    external = value.get("externalIds") or value.get("external_ids") or {}
    if not isinstance(external, dict):
        return set()
    return {
        f"{str(key).casefold()}:{str(item).casefold()}"
        for key, item in external.items()
        if item not in (None, "")
    }


def build_frozen_s2_pool(
    *,
    targets: list[dict[str, Any]],
    output_path: str | Path,
    client: S2SearchClient,
    pool_version: str,
    raw_limit: int = 50,
    top_k: int = 5,
) -> dict[str, Any]:
    """Query once by exact title, self-filter, preserve order and freeze locally."""
    records = []
    for target in targets:
        paper_id = str(target.get("paper_id") or "")
        title = str(
            target.get("canonical_query_title") or target.get("title") or ""
        ).strip()
        if not paper_id or not title:
            raise ValueError("Frozen S2 target requires paper_id and title")
        raw = client.search_title(title, limit=raw_limit)
        target_title = canonical_title(title)
        target_family = _family_title(title)
        target_external = _external_ids(target)
        target_s2_id = str(target.get("s2_paper_id") or "")
        rejected = []
        selected = []
        seen_ids: set[str] = set()
        seen_titles: set[str] = set()
        for index, value in enumerate(raw):
            result_id = str(value.get("paperId") or value.get("paper_id") or "")
            result_title = str(value.get("title") or "")
            result_title_key = canonical_title(result_title)
            reason = ""
            if target_s2_id and result_id == target_s2_id:
                reason = "target_s2_id"
            elif result_title_key and result_title_key == target_title:
                reason = "exact_canonical_title"
            elif result_title_key and _family_title(result_title) == target_family:
                reason = "same_family_title"
            elif target_external and target_external & _external_ids(value):
                reason = "shared_external_id"
            elif result_id and result_id in seen_ids:
                reason = "duplicate_s2_id"
            elif result_title_key and result_title_key in seen_titles:
                reason = "duplicate_canonical_title"
            if reason:
                rejected.append(
                    {"raw_rank": index, "paper_id": result_id, "reason": reason}
                )
                continue
            if result_id:
                seen_ids.add(result_id)
            if result_title_key:
                seen_titles.add(result_title_key)
            selected.append(value)
            if len(selected) >= top_k:
                break
        record = {
            "pool_version": pool_version,
            "paper_id": paper_id,
            "canonical_query_title": title,
            "query_method": "semantic_scholar_exact_title_v1",
            "raw_limit": raw_limit,
            "top_k": top_k,
            "raw_result_count": len(raw),
            "raw_response_sha256": value_sha256(raw),
            "rejected": rejected,
            "retrieved_papers": selected,
            "result_sha256": value_sha256(selected),
        }
        record["record_sha256"] = value_sha256(record)
        records.append(record)
    write_derived_jsonl(output_path, records)
    return {
        "pool_version": pool_version,
        "record_count": len(records),
        "output_path": str(Path(output_path).expanduser().resolve()),
        "sha256": file_sha256(output_path),
        "rejection_counts": {
            reason: sum(
                1
                for record in records
                for value in record["rejected"]
                if value["reason"] == reason
            )
            for reason in sorted(
                {
                    value["reason"]
                    for record in records
                    for value in record["rejected"]
                }
            )
        },
    }


class FrozenS2Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Frozen S2 Pool not found: {self.path}")
        self.sha256 = file_sha256(self.path)
        self.pool: dict[str, dict[str, Any]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                paper_id = str(
                    value.get("paper_id") or value.get("id") or value.get("query_id") or ""
                )
                if not paper_id or paper_id in self.pool:
                    raise ValueError(
                        f"Frozen S2 Pool missing/duplicate paper_id at line {line_number}"
                    )
                self.pool[paper_id] = value

    def retrieve(
        self,
        *,
        paper_id: str,
        target_title: str,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if paper_id not in self.pool:
            raise KeyError(f"Frozen S2 Pool has no exact paper_id entry: {paper_id}")
        record = self.pool[paper_id]
        values = (
            record.get("retrieved_papers")
            or record.get("papers")
            or record.get("results")
            or []
        )
        if not isinstance(values, list):
            raise ValueError(f"Frozen S2 results are not a list for {paper_id}")
        target_key = canonical_title(target_title)
        selected = []
        seen_ids: set[str] = set()
        seen_titles: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            result_id = str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
            result_title = str(value.get("title") or "")
            title_key = canonical_title(result_title)
            if target_key and title_key == target_key:
                raise ValueError(
                    f"Frozen S2 Pool contains an exact-title self match for {paper_id}: {result_title}"
                )
            if (result_id and result_id in seen_ids) or (title_key and title_key in seen_titles):
                raise ValueError(f"Frozen S2 Pool contains duplicate evidence for {paper_id}")
            if result_id:
                seen_ids.add(result_id)
            if title_key:
                seen_titles.add(title_key)
            selected.append(dict(value))
            if len(selected) >= max(0, int(limit)):
                break
        provenance = {
            "pool_path": str(self.path),
            "pool_sha256": self.sha256,
            "pool_version": record.get("pool_version"),
            "paper_id": paper_id,
            "result_ids": [
                str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
                for value in selected
            ],
        }
        return selected, provenance


def format_s2_evidence(values: list[dict[str, Any]]) -> str:
    sections = []
    for index, value in enumerate(values, start=1):
        title = str(value.get("title") or "(untitled)")
        paper_id = str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
        year = str(value.get("year") or value.get("publication_year") or "")
        abstract = str(value.get("abstract") or "").strip()
        sections.append(
            "\n".join(
                [
                    f"[FROZEN S2 EVIDENCE {index}]",
                    f"paper_id: {paper_id}",
                    f"title: {title}",
                    f"year: {year}",
                    f"abstract: {abstract}",
                ]
            )
        )
    return "\n\n".join(sections)
