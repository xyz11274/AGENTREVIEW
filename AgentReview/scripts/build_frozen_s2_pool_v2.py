#!/usr/bin/env python3
"""Build or verify the canonical, query-bound, time-safe Frozen S2 v2 pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.assets import (  # noqa: E402
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
)
from memory_evolution.toolkit import validate_frozen_s2_pool  # noqa: E402
from scripts.s2_search import (  # noqa: E402
    ENDPOINT,
    FIELDS,
    canonical_sha256,
    publication_time,
    request_search,
)

POOL_VERSION = "frozen-s2-temporal-v2"
LIVE_RETRIEVAL_VERSION = "s2-graph-title-search-v2-query-bound"


def query_sha256(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def record_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("record_sha256", None)
    return canonical_sha256(payload)


def is_polluted_query(title: str) -> bool:
    stripped = str(title).strip()
    return stripped.startswith("{") and "value" in stripped and stripped.endswith("}")


def canonical_target_title(target: Mapping[str, Any]) -> str:
    title = target.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Malformed canonical title for {target.get('paper_id')!r}")
    title = title.strip()
    if is_polluted_query(title):
        raise ValueError(
            f"Polluted object-serialization title for {target.get('paper_id')!r}"
        )
    return title


def iter_targets(zones: Mapping[str, Iterable[dict[str, Any]]]):
    for zone in FOUR_ZONES:
        yield from zones[zone]


def _load_records(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("records"), list):
            return payload["records"]
        return [
            {"paper_id": paper_id, **value}
            for paper_id, value in payload.items()
            if isinstance(value, dict)
        ]
    raise ValueError(f"Unsupported S2 payload: {path}")


def _results(value: Mapping[str, Any]) -> list | None:
    results = value.get("retrieved_papers")
    return results if isinstance(results, list) else None


def results_are_time_safe(
    results: Iterable[Mapping[str, Any]], cutoff_text: str
) -> bool:
    cutoff = date.fromisoformat(str(cutoff_text)[:10])
    try:
        return all(publication_time(dict(result)) <= cutoff for result in results)
    except (TypeError, ValueError):
        return False


def build_v2_record(
    target: Mapping[str, Any],
    *,
    top_k: int,
    results: list,
    retrieval_method: str,
    retrieval_version: str,
    retrieval_origin: str,
    raw_api_response_sha256: str,
    rejected_future: int = 0,
    rejected_unknown: int = 0,
) -> dict[str, Any]:
    title = canonical_target_title(target)
    config = {
        "endpoint": ENDPOINT,
        "query_mode": "exact canonical v2 target title",
        "fields": FIELDS,
        "top_k": top_k,
        "future_result_policy": "reject",
        "unknown_publication_time_policy": "reject",
        "query_identity_policy": "canonical title + SHA256",
    }
    record = {
        "paper_id": str(target["paper_id"]),
        "target_identity": {
            "paper_id": str(target["paper_id"]),
            "title": title,
            "venue": target["venue"],
            "year": target["year"],
            "zone": target["zone"],
        },
        "paper_review_cutoff": target["paper_review_cutoff"],
        "canonical_query_title": title,
        "query": title,
        "query_sha256": query_sha256(title),
        "retrieval_method": retrieval_method,
        "retrieval_version": retrieval_version,
        "retrieval_origin": retrieval_origin,
        "retrieval_config": config,
        "top_k": top_k,
        "retrieved_papers": results,
        "result_count": len(results),
        "result_sha256": canonical_sha256(results),
        "raw_api_response_sha256": raw_api_response_sha256,
        "future_results_rejected": rejected_future,
        "unknown_date_results_rejected": rejected_unknown,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_version": POOL_VERSION,
        "status": "completed",
    }
    record["record_sha256"] = record_sha256(record)
    return record


def valid_v2_work_record(
    value: Mapping[str, Any], target: Mapping[str, Any], *, top_k: int
) -> bool:
    title = canonical_target_title(target)
    results = _results(value)
    expected_identity = {
        "paper_id": str(target["paper_id"]),
        "title": title,
        "venue": target["venue"],
        "year": target["year"],
        "zone": target["zone"],
    }
    return bool(
        value.get("paper_id") == target["paper_id"]
        and value.get("target_identity") == expected_identity
        and value.get("paper_review_cutoff") == target["paper_review_cutoff"]
        and value.get("canonical_query_title") == title
        and value.get("query") == title
        and value.get("query_sha256") == query_sha256(title)
        and value.get("pool_version") == POOL_VERSION
        and value.get("top_k") == top_k
        and isinstance(value.get("retrieval_method"), str)
        and bool(value.get("retrieval_method"))
        and isinstance(value.get("retrieval_version"), str)
        and bool(value.get("retrieval_version"))
        and results is not None
        and value.get("result_count") == len(results)
        and value.get("result_sha256") == canonical_sha256(results)
        and value.get("record_sha256") == record_sha256(value)
        and results_are_time_safe(results, str(target["paper_review_cutoff"]))
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def audit_pool(
    output: Path,
    sidecar_path: Path,
    *,
    split_path: Path,
    targets: list,
    top_k: int,
) -> dict[str, Any]:
    cutoffs = {
        str(target["paper_id"]): str(target["paper_review_cutoff"])
        for target in targets
    }
    queries = {
        str(target["paper_id"]): canonical_target_title(target) for target in targets
    }
    temporal = validate_frozen_s2_pool(
        str(output),
        expected_cutoffs=cutoffs,
        required_ids=set(cutoffs),
        expected_pool_version=POOL_VERSION,
        expected_queries=queries,
    )
    entries = list(_load_records(output))
    malformed_titles = []
    for target in targets:
        try:
            canonical_target_title(target)
        except ValueError as exc:
            malformed_titles.append(
                {"paper_id": target.get("paper_id"), "error": str(exc)}
            )
    origin_counts: dict[str, int] = {}
    for value in entries:
        origin = str(value.get("retrieval_origin") or "unspecified")
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    top_k_mismatches = [
        str(value.get("paper_id") or "")
        for value in entries
        if value.get("top_k") != top_k
    ]
    record_identity_mismatches = [
        str(target["paper_id"])
        for target in targets
        if not valid_v2_work_record(
            next(
                (
                    value
                    for value in entries
                    if str(value.get("paper_id")) == str(target["paper_id"])
                ),
                {},
            ),
            target,
            top_k=top_k,
        )
    ]
    query_audit = {
        "protocol": "frozen-s2-v2-query-integrity-audit-v1",
        "required_count": len(targets),
        "checked_count": len(entries),
        "retrieval_origin_counts": dict(sorted(origin_counts.items())),
        "malformed_titles": malformed_titles,
        "canonical_mismatches": temporal["canonical_mismatches"],
        "polluted_query_titles": temporal["polluted_query_titles"],
        "top_k_mismatches": top_k_mismatches,
        "record_identity_mismatches": record_identity_mismatches,
    }
    query_audit["passed"] = all(
        (
            query_audit["checked_count"] == query_audit["required_count"],
            len(query_audit["malformed_titles"]) == 0,
            len(query_audit["canonical_mismatches"]) == 0,
            len(query_audit["polluted_query_titles"]) == 0,
            len(query_audit["top_k_mismatches"]) == 0,
            len(query_audit["record_identity_mismatches"]) == 0,
            temporal["passed"] is True,
        )
    )
    try:
        asset_path = str(output.relative_to(PROJECT_ROOT))
    except ValueError:
        asset_path = str(output)
    try:
        manifest_path = str(split_path.relative_to(PROJECT_ROOT))
    except ValueError:
        manifest_path = str(split_path)
    report = {
        "pool_version": POOL_VERSION,
        "paper_count": len(entries),
        "top_k": top_k,
        "temporal_split_manifest": manifest_path,
        "temporal_split_manifest_sha256": sha256_file(split_path),
        "asset": asset_path,
        "asset_sha256": sha256_file(output),
        "temporal_audit": temporal,
        "query_integrity_audit": query_audit,
    }
    if not temporal["passed"] or not query_audit["passed"]:
        raise RuntimeError(f"Frozen S2 v2 audit failed: {report}")
    if sidecar_path.is_file():
        recorded = json.loads(sidecar_path.read_text(encoding="utf-8"))
        for field in (
            "pool_version",
            "paper_count",
            "temporal_split_manifest_sha256",
            "asset_sha256",
        ):
            if recorded.get(field) != report.get(field):
                raise RuntimeError(f"Frozen S2 v2 sidecar mismatch: {field}")
        if not (recorded.get("query_integrity_audit") or {}).get("passed"):
            raise RuntimeError("Frozen S2 v2 sidecar query audit is not passed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or verify the query-bound Frozen S2 v2 pool."
    )
    parser.add_argument(
        "--temporal-split-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v2/manifest.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "configs/frozen_s2_temporal_v2.jsonl"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--min-interval-seconds", type=float, default=1.1)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.top_k < 1 or args.max_attempts < 1:
        raise ValueError("top-k and max-attempts must be positive")
    if args.min_interval_seconds < 1.0:
        raise ValueError("min-interval-seconds must be at least 1.0")

    split_path = Path(args.temporal_split_manifest).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    sidecar_path = output.with_suffix(output.suffix + ".manifest.json")
    split_summary, zones = load_temporal_split_manifest(str(split_path))
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise ValueError("Frozen S2 v2 requires coco temporal split v2")
    split_audit = audit_four_zone_isolation(zones)
    if not split_audit["passed"]:
        raise RuntimeError(f"Temporal split v2 audit failed: {split_audit}")
    targets = list(iter_targets(zones))

    if args.check_only:
        if not output.is_file() or not sidecar_path.is_file():
            raise FileNotFoundError("Frozen S2 v2 output/sidecar is incomplete")
        print(
            json.dumps(
                audit_pool(
                    output,
                    sidecar_path,
                    split_path=split_path,
                    targets=targets,
                    top_k=args.top_k,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if output.is_file() and not sidecar_path.exists():
        # Recover the narrow crash window after the fully audited JSONL was
        # atomically installed and before its sidecar was committed.
        report = audit_pool(
            output,
            sidecar_path,
            split_path=split_path,
            targets=targets,
            top_k=args.top_k,
        )
        _atomic_json(sidecar_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if output.exists() or sidecar_path.exists():
        raise FileExistsError(
            "Refusing to overwrite completed Frozen S2 v2; use --check-only"
        )

    work_dir = output.with_suffix(output.suffix + ".work")
    work_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("S2_API_KEY", "").strip()
    for index, target in enumerate(targets, start=1):
        paper_id = str(target["paper_id"])
        work_path = work_dir / (hashlib.sha256(paper_id.encode()).hexdigest() + ".json")
        if work_path.is_file():
            existing = json.loads(work_path.read_text(encoding="utf-8"))
            if valid_v2_work_record(existing, target, top_k=args.top_k):
                continue
            raise RuntimeError(f"Incompatible query-bound v2 work record: {paper_id}")
        payload = request_search(
            title=canonical_target_title(target),
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
            normalized["year"] = published.year
            accepted.append(normalized)
        record = build_v2_record(
            target,
            top_k=args.top_k,
            results=accepted,
            retrieval_method="Semantic Scholar Graph API canonical title search",
            retrieval_version=LIVE_RETRIEVAL_VERSION,
            retrieval_origin="live_s2_query",
            raw_api_response_sha256=canonical_sha256(payload),
            rejected_future=rejected_future,
            rejected_unknown=rejected_unknown,
        )
        time.sleep(args.min_interval_seconds)
        _atomic_json(work_path, record)
        print(f"[{index}/{len(targets)}] v2 frozen {paper_id}", flush=True)

    ordered = []
    for target in targets:
        paper_id = str(target["paper_id"])
        work_path = work_dir / (hashlib.sha256(paper_id.encode()).hexdigest() + ".json")
        value = json.loads(work_path.read_text(encoding="utf-8"))
        if not valid_v2_work_record(value, target, top_k=args.top_k):
            raise RuntimeError(f"Invalid query-bound v2 work record: {paper_id}")
        ordered.append(value)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for value in ordered:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, output)
    report = audit_pool(
        output,
        sidecar_path,
        split_path=split_path,
        targets=targets,
        top_k=args.top_k,
    )
    _atomic_json(sidecar_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
