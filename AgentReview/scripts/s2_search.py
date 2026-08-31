"""Semantic Scholar title-search primitives shared by frozen-asset builders."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from typing import Any

import requests

ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = (
    "paperId,title,year,publicationDate,abstract,authors.name,"
    "citationCount,url,externalIds"
)


def canonical_sha256(value: Any) -> str:
    """Return the SHA256 of one canonical JSON representation."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def publication_time(paper: dict[str, Any]) -> date:
    """Resolve a Semantic Scholar result to a conservative publication date."""
    raw = paper.get("publicationDate") or paper.get("publication_date")
    if raw:
        return date.fromisoformat(str(raw)[:10])
    year = paper.get("year") or paper.get("publication_year")
    if year:
        return date(int(year), 12, 31)
    raise ValueError("Semantic Scholar result lacks publication date/year")


def normalize_search_payload(payload: Any) -> dict[str, Any]:
    """Validate an S2 search response and canonicalize its zero-result form."""
    if not isinstance(payload, dict):
        raise ValueError("Semantic Scholar search payload must be an object")
    total = payload.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("Semantic Scholar search payload has invalid total")
    if "data" in payload:
        if not isinstance(payload["data"], list):
            raise ValueError("Semantic Scholar search payload data must be a list")
        return dict(payload)
    if total == 0:
        normalized = dict(payload)
        normalized["data"] = []
        return normalized
    raise ValueError("Semantic Scholar search payload has positive total but no data")


def retry_delay(response: Any, attempt: int, min_interval_seconds: float) -> float:
    """Resolve a bounded retry delay from the response or exponential backoff."""
    retry_after = response.headers.get("Retry-After", "").strip()
    try:
        delay = float(retry_after)
    except ValueError:
        delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
    return max(min_interval_seconds, min(delay, 300.0))


def request_search(
    *,
    title: str,
    top_k: int,
    api_key: str,
    max_attempts: int,
    min_interval_seconds: float,
) -> dict[str, Any]:
    """Run one bounded Semantic Scholar title search."""
    headers = {"x-api-key": api_key} if api_key else {}
    for attempt in range(1, max_attempts + 1):
        response = requests.get(
            ENDPOINT,
            params={"query": title, "limit": top_k, "fields": FIELDS},
            headers=headers,
            timeout=60,
        )
        if response.status_code == 200:
            try:
                return normalize_search_payload(response.json())
            except (ValueError, TypeError) as exc:
                final_attempt = attempt == max_attempts
                if final_attempt:
                    raise RuntimeError(
                        "Semantic Scholar returned a malformed search payload "
                        f"after {max_attempts} attempts: {exc}"
                    ) from exc
                time.sleep(retry_delay(response, attempt, min_interval_seconds))
                continue

        transient = response.status_code == 429 or 500 <= response.status_code <= 599
        final_attempt = attempt == max_attempts
        if transient and not final_attempt:
            time.sleep(retry_delay(response, attempt, min_interval_seconds))
            continue
        key_hint = " Set S2_API_KEY." if response.status_code in {403, 429} else ""
        raise RuntimeError(
            f"Semantic Scholar search failed: HTTP {response.status_code}."
            f"{key_hint} Response={response.text[:300]!r}"
        )
    raise AssertionError("unreachable")
