#!/usr/bin/env python3
"""Build/query-migrate the query-bound, time-safe Frozen S2 v2 pool.

This tool never modifies the v1 pool or its work directory. Clean v1 records
whose actual query equals the v2 canonical title are copied into a separate v2
checkpoint; all other targets are retrieved using the canonical v2 title.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.assets import (
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    TEMPORAL_SPLIT_V1_VERSION,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
)
from memory_evolution.toolkit import validate_frozen_s2_pool
from scripts.build_frozen_s2_pool import (
    ENDPOINT,
    FIELDS,
    canonical_sha256,
    publication_time,
    request_search,
)


POOL_VERSION = "frozen-s2-temporal-v2"
LIVE_RETRIEVAL_VERSION = "s2-graph-title-search-v2-query-bound"
REUSE_RETRIEVAL_VERSION = "s2-v1-result-migration-v2-query-bound"
LEGACY_POOL_VERSION = "frozen-s2-temporal-v1"
DEPRECATION_REASON = "query_title_serialization_contamination"


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


def iter_targets(zones: Mapping[str, Iterable[Dict[str, Any]]]):
    for zone in FOUR_ZONES:
        for target in zones[zone]:
            yield target


def _load_records(path: Path) -> Iterable[Dict[str, Any]]:
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
    raise ValueError(f"Unsupported legacy S2 payload: {path}")


def _legacy_equivalence_sha(value: Mapping[str, Any], source: str) -> str:
    """Ignore only the final-asset-only derived result_count field."""
    normalized = dict(value)
    results = normalized.get("retrieved_papers")
    if not isinstance(results, list):
        raise ValueError(f"Legacy S2 record has invalid results: {source}")
    if "result_count" in normalized and normalized["result_count"] != len(results):
        raise ValueError(f"Legacy S2 record has invalid result_count: {source}")
    normalized.pop("result_count", None)
    return canonical_sha256(normalized)


def load_legacy_records(pool_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load v1 final/work records read-only and reject conflicting duplicates."""
    records: Dict[str, Dict[str, Any]] = {}
    sources = []
    if pool_path.is_file():
        sources.extend((value, str(pool_path)) for value in _load_records(pool_path))
    work_dir = pool_path.with_suffix(pool_path.suffix + ".work")
    if work_dir.is_dir():
        for work_path in sorted(work_dir.glob("*.json")):
            sources.append(
                (json.loads(work_path.read_text(encoding="utf-8")), str(work_path))
            )
    for value, source in sources:
        paper_id = str(value.get("paper_id") or "")
        if not paper_id:
            raise ValueError(f"Legacy S2 record lacks paper_id: {source}")
        previous = records.get(paper_id)
        if previous is not None:
            if _legacy_equivalence_sha(
                previous, "previous final/work record"
            ) != _legacy_equivalence_sha(value, source):
                raise ValueError(f"Conflicting legacy S2 records for {paper_id}")
            # Final JSONL is loaded first and is the frozen v1 asset of record.
            # Do not replace it with an older work representation.
            continue
        _legacy_equivalence_sha(value, source)
        records[paper_id] = value
    return records


def _results(value: Mapping[str, Any]) -> Optional[list]:
    results = value.get("retrieved_papers")
    return results if isinstance(results, list) else None


def results_are_time_safe(results: Iterable[Mapping[str, Any]], cutoff_text: str) -> bool:
    cutoff = date.fromisoformat(str(cutoff_text)[:10])
    try:
        return all(publication_time(dict(result)) <= cutoff for result in results)
    except (TypeError, ValueError):
        return False


def reusable_legacy_record(
    value: Optional[Mapping[str, Any]],
    target: Mapping[str, Any],
    *,
    top_k: int,
) -> bool:
    if not value:
        return False
    results = _results(value)
    title = canonical_target_title(target)
    return bool(
        value.get("paper_id") == target["paper_id"]
        and value.get("pool_version") == LEGACY_POOL_VERSION
        and value.get("paper_review_cutoff") == target["paper_review_cutoff"]
        and value.get("top_k") == top_k
        and value.get("query") == title
        and isinstance(value.get("retrieval_method"), str)
        and isinstance(value.get("retrieval_version"), str)
        and results is not None
        and value.get("result_sha256") == canonical_sha256(results)
        and results_are_time_safe(results, str(target["paper_review_cutoff"]))
    )


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
    legacy_record: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
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
    if legacy_record is not None:
        record["reuse_provenance"] = {
            "source_pool_version": LEGACY_POOL_VERSION,
            "source_record_sha256": canonical_sha256(legacy_record),
            "source_query": legacy_record.get("query"),
            "reason": "actual v1 query exactly matches canonical v2 title",
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


def actual_v1_query(
    paper_id: str,
    v1_targets: Mapping[str, Mapping[str, Any]],
    legacy_records: Mapping[str, Mapping[str, Any]],
) -> str:
    record = legacy_records.get(paper_id)
    if record is not None and isinstance(record.get("query"), str):
        return str(record["query"])
    return str(v1_targets[paper_id].get("title") or "")


def query_change_plan(
    targets: Iterable[Mapping[str, Any]],
    v1_targets: Mapping[str, Mapping[str, Any]],
    legacy_records: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    plan = {}
    for target in targets:
        paper_id = str(target["paper_id"])
        if paper_id not in v1_targets:
            raise ValueError(f"v2 target absent from v1 split: {paper_id}")
        canonical = canonical_target_title(target)
        old_query = actual_v1_query(paper_id, v1_targets, legacy_records)
        plan[paper_id] = {
            "v1_actual_query": old_query,
            "v2_canonical_query": canonical,
            "affected": old_query != canonical,
        }
    if not set(plan).issubset(v1_targets):
        raise ValueError("v2 representative target is absent from the v1 source split")
    return plan


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
    plan: Mapping[str, Mapping[str, Any]],
    top_k: int,
) -> Dict[str, Any]:
    cutoffs = {
        str(target["paper_id"]): str(target["paper_review_cutoff"])
        for target in targets
    }
    queries = {
        str(target["paper_id"]): canonical_target_title(target)
        for target in targets
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
    origins = {str(value["paper_id"]): str(value.get("retrieval_origin") or "") for value in entries}
    top_k_mismatches = [
        str(value.get("paper_id") or "")
        for value in entries
        if value.get("top_k") != top_k
    ]
    query_audit = {
        "protocol": "frozen-s2-v2-query-integrity-audit-v1",
        "required_count": len(targets),
        "checked_count": len(entries),
        "affected_target_count": sum(
            1 for value in plan.values() if value["affected"]
        ),
        "reused_target_count": sum(
            1 for value in origins.values() if value == "reused_clean_v1"
        ),
        "re_retrieved_target_count": sum(
            1 for value in origins.values() if value == "re_retrieved_query_changed"
        ),
        "newly_retrieved_target_count": sum(
            1 for value in origins.values() if value == "retrieved_missing_clean_v1"
        ),
        "malformed_titles": malformed_titles,
        "canonical_mismatches": temporal["canonical_mismatches"],
        "polluted_query_titles": temporal["polluted_query_titles"],
        "top_k_mismatches": top_k_mismatches,
    }
    accounted_count = (
        query_audit["reused_target_count"]
        + query_audit["re_retrieved_target_count"]
        + query_audit["newly_retrieved_target_count"]
    )
    query_audit["passed"] = not (
        query_audit["required_count"] != len(targets)
        or query_audit["checked_count"] != query_audit["required_count"]
        or accounted_count != query_audit["checked_count"]
        or query_audit["re_retrieved_target_count"]
        != query_audit["affected_target_count"]
        or query_audit["malformed_titles"]
        or query_audit["canonical_mismatches"]
        or query_audit["polluted_query_titles"]
        or query_audit["top_k_mismatches"]
        or not temporal["passed"]
    )
    report = {
        "pool_version": POOL_VERSION,
        "paper_count": len(entries),
        "top_k": top_k,
        "temporal_split_manifest": str(split_path),
        "temporal_split_manifest_sha256": sha256_file(split_path),
        "asset": str(output),
        "asset_sha256": sha256_file(output),
        "temporal_audit": temporal,
        "query_integrity_audit": query_audit,
        "legacy_v1_policy": {
            "deprecated_for_formal_use": True,
            "reason": DEPRECATION_REASON,
        },
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
        description="Selectively migrate/retrieve the query-bound Frozen S2 v2 pool."
    )
    parser.add_argument(
        "--temporal-split-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v2/manifest.json"),
    )
    parser.add_argument(
        "--baseline-temporal-split-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v1/manifest.json"),
    )
    parser.add_argument(
        "--legacy-s2-pool",
        default=str(PROJECT_ROOT / "configs/frozen_s2_temporal_v1.jsonl"),
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
    baseline_path = Path(args.baseline_temporal_split_manifest).expanduser().resolve()
    legacy_path = Path(args.legacy_s2_pool).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    sidecar_path = output.with_suffix(output.suffix + ".manifest.json")
    split_summary, zones = load_temporal_split_manifest(str(split_path))
    baseline_summary, baseline_zones = load_temporal_split_manifest(str(baseline_path))
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise ValueError("Frozen S2 v2 requires coco temporal split v2")
    if baseline_summary.get("version") != TEMPORAL_SPLIT_V1_VERSION:
        raise ValueError("Frozen S2 v2 migration baseline must be temporal split v1")
    split_audit = audit_four_zone_isolation(zones)
    if not split_audit["passed"]:
        raise RuntimeError(f"Temporal split v2 audit failed: {split_audit}")
    comparison = split_summary.get("v1_v2_comparison_audit") or {}
    if not comparison.get("passed"):
        raise RuntimeError("Temporal split v2 lacks a passed v1/v2 comparison audit")
    targets = list(iter_targets(zones))
    v1_targets = {
        str(target["paper_id"]): target for target in iter_targets(baseline_zones)
    }
    legacy_records = load_legacy_records(legacy_path)
    plan = query_change_plan(targets, v1_targets, legacy_records)

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
                    plan=plan,
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
        # atomically installed but before its sidecar was committed. This is
        # read-only with respect to v1 and performs no live retrieval.
        report = audit_pool(
            output,
            sidecar_path,
            split_path=split_path,
            targets=targets,
            plan=plan,
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
        legacy = legacy_records.get(paper_id)
        if reusable_legacy_record(legacy, target, top_k=args.top_k):
            results = list(_results(legacy) or [])
            record = build_v2_record(
                target,
                top_k=args.top_k,
                results=results,
                retrieval_method="Provenance-safe reuse of clean Frozen S2 v1 result",
                retrieval_version=REUSE_RETRIEVAL_VERSION,
                retrieval_origin="reused_clean_v1",
                raw_api_response_sha256=str(
                    legacy.get("raw_api_response_sha256") or ""
                ),
                rejected_future=int(legacy.get("future_results_rejected") or 0),
                rejected_unknown=int(legacy.get("unknown_date_results_rejected") or 0),
                legacy_record=legacy,
            )
        else:
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
            origin = (
                "re_retrieved_query_changed"
                if plan[paper_id]["affected"]
                else "retrieved_missing_clean_v1"
            )
            record = build_v2_record(
                target,
                top_k=args.top_k,
                results=accepted,
                retrieval_method="Semantic Scholar Graph API canonical title search",
                retrieval_version=LIVE_RETRIEVAL_VERSION,
                retrieval_origin=origin,
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
        plan=plan,
        top_k=args.top_k,
    )
    _atomic_json(sidecar_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
