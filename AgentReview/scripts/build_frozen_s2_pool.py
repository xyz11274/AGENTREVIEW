#!/usr/bin/env python3
"""Build the resumable, paper-specific, time-safe Semantic Scholar pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.assets import FOUR_ZONES, load_temporal_split_manifest, sha256_file
from memory_evolution.toolkit import validate_frozen_s2_pool


POOL_VERSION = "frozen-s2-temporal-v1"
RETRIEVAL_VERSION = "s2-graph-title-search-v1"
ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = (
    "paperId,title,year,publicationDate,abstract,authors.name,"
    "citationCount,url,externalIds"
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def publication_time(paper: Dict[str, Any]) -> date:
    raw = paper.get("publicationDate") or paper.get("publication_date")
    if raw:
        return date.fromisoformat(str(raw)[:10])
    year = paper.get("year") or paper.get("publication_year")
    if year:
        return date(int(year), 12, 31)
    raise ValueError("Semantic Scholar result lacks publication date/year")


def request_search(
    *,
    title: str,
    top_k: int,
    api_key: str,
    max_attempts: int,
    min_interval_seconds: float,
) -> Dict[str, Any]:
    headers = {"x-api-key": api_key} if api_key else {}
    retryable = {429, 500, 502, 503, 504}
    for attempt in range(1, max_attempts + 1):
        response = requests.get(
            ENDPOINT,
            params={"query": title, "limit": top_k, "fields": FIELDS},
            headers=headers,
            timeout=60,
        )
        if response.status_code == 200:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise RuntimeError("Semantic Scholar returned an invalid search payload")
            return payload
        if response.status_code not in retryable or attempt == max_attempts:
            key_hint = " Set S2_API_KEY." if response.status_code in {403, 429} else ""
            raise RuntimeError(
                f"Semantic Scholar search failed: HTTP {response.status_code}."
                f"{key_hint} Response={response.text[:300]!r}"
            )
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            delay = float(retry_after)
        except ValueError:
            delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
        time.sleep(max(min_interval_seconds, min(delay, 300.0)))
    raise AssertionError("unreachable")


def valid_work_record(
    value: Dict[str, Any], target: Dict[str, Any], top_k: int
) -> bool:
    results = value.get("retrieved_papers")
    return bool(
        value.get("paper_id") == target["paper_id"]
        and value.get("paper_review_cutoff") == target["paper_review_cutoff"]
        and value.get("pool_version") == POOL_VERSION
        and value.get("retrieval_version") == RETRIEVAL_VERSION
        and value.get("top_k") == top_k
        and isinstance(results, list)
        and value.get("result_sha256") == canonical_sha256(results)
    )


def iter_targets(zones: Dict[str, Iterable[Dict[str, Any]]]):
    for zone in FOUR_ZONES:
        for value in zones[zone]:
            yield value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--temporal-split-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v1/manifest.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "configs/frozen_s2_temporal_v1.jsonl"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--min-interval-seconds", type=float, default=1.1)
    args = parser.parse_args()
    if args.top_k < 1 or args.max_attempts < 1:
        raise ValueError("top-k and max-attempts must be positive")
    if args.min_interval_seconds < 1.0:
        raise ValueError("min-interval-seconds must be at least 1.0")

    split_summary, zones = load_temporal_split_manifest(args.temporal_split_manifest)
    targets = list(iter_targets(zones))
    output = Path(args.output).expanduser().resolve()
    work_dir = output.with_suffix(output.suffix + ".work")
    work_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("S2_API_KEY", "").strip()

    for index, target in enumerate(targets, start=1):
        paper_id = str(target["paper_id"])
        work_path = work_dir / (hashlib.sha256(paper_id.encode()).hexdigest() + ".json")
        if work_path.is_file():
            existing = json.loads(work_path.read_text(encoding="utf-8"))
            if valid_work_record(existing, target, args.top_k):
                continue
            raise RuntimeError(f"Incompatible S2 work record for {paper_id}")

        payload = request_search(
            title=str(target["title"]),
            top_k=args.top_k,
            api_key=api_key,
            max_attempts=args.max_attempts,
            min_interval_seconds=args.min_interval_seconds,
        )
        cutoff = date.fromisoformat(str(target["paper_review_cutoff"])[:10])
        accepted = []
        rejected_future = 0
        rejected_unknown = 0
        for paper in payload["data"]:
            try:
                published = publication_time(paper)
            except (TypeError, ValueError):
                rejected_unknown += 1
                continue
            if published > cutoff:
                rejected_future += 1
                continue
            normalized = dict(paper)
            normalized["publication_date"] = published.isoformat()
            normalized["year"] = int(published.year)
            accepted.append(normalized)

        retrieval_config = {
            "endpoint": ENDPOINT,
            "query_mode": "exact target title",
            "fields": FIELDS,
            "top_k": args.top_k,
            "future_result_policy": "reject",
            "unknown_publication_time_policy": "reject",
        }
        record = {
            "paper_id": paper_id,
            "target_identity": {
                "paper_id": paper_id,
                "title": target["title"],
                "venue": target["venue"],
                "year": target["year"],
                "zone": target["zone"],
            },
            "paper_review_cutoff": target["paper_review_cutoff"],
            "retrieval_method": "Semantic Scholar Graph API paper title search",
            "retrieval_version": RETRIEVAL_VERSION,
            "retrieval_config": retrieval_config,
            "top_k": args.top_k,
            "query": target["title"],
            "retrieved_papers": accepted,
            "result_sha256": canonical_sha256(accepted),
            "raw_api_response_sha256": canonical_sha256(payload),
            "future_results_rejected": rejected_future,
            "unknown_date_results_rejected": rejected_unknown,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "pool_version": POOL_VERSION,
            "status": "completed",
        }
        atomic_json(work_path, record)
        print(f"[{index}/{len(targets)}] frozen {paper_id}", flush=True)
        time.sleep(args.min_interval_seconds)

    ordered = []
    for target in targets:
        paper_id = str(target["paper_id"])
        work_path = work_dir / (hashlib.sha256(paper_id.encode()).hexdigest() + ".json")
        value = json.loads(work_path.read_text(encoding="utf-8"))
        if not valid_work_record(value, target, args.top_k):
            raise RuntimeError(f"Invalid S2 work record for {paper_id}")
        ordered.append(value)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for value in ordered:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, output)

    cutoffs = {str(value["paper_id"]): value["paper_review_cutoff"] for value in targets}
    audit = validate_frozen_s2_pool(
        str(output), expected_cutoffs=cutoffs, required_ids=set(cutoffs)
    )
    if not audit["passed"]:
        raise RuntimeError(f"Frozen S2 audit failed: {audit}")
    manifest = {
        "pool_version": POOL_VERSION,
        "paper_count": len(ordered),
        "top_k": args.top_k,
        "temporal_split_manifest": str(Path(args.temporal_split_manifest).resolve()),
        "temporal_split_manifest_sha256": sha256_file(
            Path(args.temporal_split_manifest).resolve()
        ),
        "source_pool_count": split_summary["source_pool_count"],
        "asset": str(output),
        "asset_sha256": sha256_file(output),
        "temporal_audit": audit,
    }
    atomic_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
