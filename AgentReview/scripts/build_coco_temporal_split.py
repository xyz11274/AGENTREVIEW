#!/usr/bin/env python3
"""Build a family-deduplicated chronological CoCo A/B/C/D target split."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from memory_evolution.assets import (
    FOUR_ZONES,
    PROJECT_ROOT,
    TEMPORAL_SPLIT_VERSION,
    TEMPORAL_SPLIT_V1_VERSION,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    normalize_title,
    sha256_file,
)


SUPERSEDES = "coco_temporal_split_v1"
SUPERSESSION_REASON = (
    "canonicalize OpenReview API v2 scalar fields and enforce one "
    "representative target per paper family"
)
REPRESENTATIVE_PROTOCOL = "coco-family-representative-v1"


def _extract_coco_scalar(
    content: Mapping[str, Any], field: str, paper_id: str
) -> str:
    """Extract a required string or OpenReview-v2 ``{value: string}`` field."""
    if field not in content:
        raise ValueError(f"CoCo paper {paper_id!r} is missing content.{field}")
    raw = content[field]
    if isinstance(raw, str):
        value = raw.strip()
    elif isinstance(raw, dict):
        if "value" not in raw:
            raise ValueError(
                f"CoCo paper {paper_id!r} content.{field} object lacks value"
            )
        nested = raw["value"]
        if not isinstance(nested, str):
            raise ValueError(
                f"CoCo paper {paper_id!r} content.{field}.value must be a string"
            )
        value = nested.strip()
    else:
        raise ValueError(
            f"CoCo paper {paper_id!r} content.{field} must be a string or object"
        )
    if not value:
        raise ValueError(f"CoCo paper {paper_id!r} has an empty {field}")
    return value


def extract_coco_title(content: Mapping[str, Any], paper_id: str) -> str:
    """Canonical title parser used at the raw CoCo ingestion boundary."""
    return _extract_coco_scalar(content, "title", paper_id)


def extract_coco_paperhash(content: Mapping[str, Any], paper_id: str) -> str:
    """Canonical paperhash parser used before rebuilding paper families."""
    return _extract_coco_scalar(content, "paperhash", paper_id)


def _family_components(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Cluster transitive exact normalized-title/paperhash matches."""
    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    owners: Dict[str, int] = {}
    identifiers = []
    for index, record in enumerate(records):
        values = {
            "title:" + str(record["normalized_title"]),
            "paperhash:" + normalize_title(record["paperhash"]),
        }
        identifiers.append(values)
        for value in values:
            if value in owners:
                union(index, owners[value])
            else:
                owners[value] = index
    by_root = defaultdict(set)
    for index, values in enumerate(identifiers):
        by_root[find(index)].update(values)
    family_ids = {
        root: hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()
        for root, values in by_root.items()
    }
    return [family_ids[find(index)] for index in range(len(records))]


def _accepted_or_published_rank(record: Mapping[str, Any]) -> int:
    status = str(record.get("_decision") or "").strip().casefold()
    return int(status.startswith("accept") or "published" in status)


def _explicit_version_rank(record: Mapping[str, Any]) -> int:
    match = re.search(r"v(\d+)(?=\.[^.]+$)", str(record.get("_pdf_path") or ""), re.I)
    return int(match.group(1)) if match else 0


def representative_rank(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Return only score/review-independent representative ranking features."""
    return {
        "accepted_or_published": _accepted_or_published_rank(record),
        "venue_year": int(record["year"]),
        "explicit_source_version": _explicit_version_rank(record),
        "stable_paper_id": str(record["paper_id"]),
        "stable_source_record_sha256": str(record["source_record_sha256"]),
    }


def select_family_representative(
    records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Select one representative by one global deterministic ranking rule."""
    if not records:
        raise ValueError("Cannot select a representative from an empty family")
    return sorted(
        records,
        key=lambda value: (
            -_accepted_or_published_rank(value),
            -int(value["year"]),
            -_explicit_version_rank(value),
            str(value["paper_id"]),
            str(value["source_record_sha256"]),
        ),
    )[0]


def _source_provenance(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "paper_id": record["paper_id"],
        "canonical_title": record["title"],
        "canonical_paperhash": record["paperhash"],
        "venue": record["venue"],
        "year": record["year"],
        "source_asset": record["source_asset"],
        "source_line": record["source_line"],
        "source_record_sha256": record["source_record_sha256"],
        "representative_rank": representative_rank(record),
    }


def _selection_reason(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "protocol": REPRESENTATIVE_PROTOCOL,
        "ranking_order": [
            "accepted_or_published descending",
            "venue_year descending",
            "explicit_source_version descending",
            "paper_id ascending",
            "source_record_sha256 ascending",
        ],
        "selected_rank": representative_rank(record),
        "forbidden_inputs": [
            "benchmark_score",
            "review_content",
            "metareview_content",
            "opinion_annotations",
        ],
    }


def _load_source_records(coco_root: Path) -> tuple[list[Dict[str, Any]], Dict[str, str]]:
    records = []
    source_hashes = {}
    pattern = re.compile(r"benchmark_(ICLR|NeurIPS)_(\d{4})\.jsonl$")
    for source in sorted((coco_root / "benchmark").glob("benchmark_*.jsonl")):
        match = pattern.fullmatch(source.name)
        if not match:
            continue
        venue, year_text = match.groups()
        year = int(year_text)
        source_hashes[source.name] = sha256_file(source)
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                content = value.get("content") or {}
                paper_id = str(value.get("id") or "")
                if not paper_id:
                    raise ValueError(f"Missing identity at {source}:{line_number}")
                title = extract_coco_title(content, paper_id)
                records.append(
                    {
                        "paper_id": paper_id,
                        "title": title,
                        "normalized_title": normalize_title(title),
                        "paperhash": extract_coco_paperhash(content, paper_id),
                        "venue": venue.lower(),
                        "year": year,
                        "paper_review_cutoff": f"{year - 1}-12-31",
                        "cutoff_precision": "conservative_year_level",
                        "cutoff_basis": (
                            "exact submission/review dates absent from frozen CoCo record; "
                            "use end of prior calendar year"
                        ),
                        "source_asset": (
                            f"external://cocoreviewbench/benchmark/{source.name}"
                        ),
                        "source_line": line_number,
                        "source_record_sha256": hashlib.sha256(
                            line.rstrip("\n").encode("utf-8")
                        ).hexdigest(),
                        "_decision": value.get("decision"),
                        "_pdf_path": value.get("PDF_path"),
                    }
                )
    if len(records) != 3900:
        raise ValueError(f"Expected frozen 3900-record pool, found {len(records)}")
    family_ids = _family_components(records)
    for record, family_id in zip(records, family_ids):
        record["paper_family"] = family_id
    return records, source_hashes


def _representatives(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    by_family = defaultdict(list)
    for record in records:
        by_family[str(record["paper_family"])].append(record)
    representatives = []
    family_entries = []
    for family_id in sorted(by_family):
        members = by_family[family_id]
        selected = select_family_representative(members)
        excluded = sorted(
            str(value["paper_id"])
            for value in members
            if value["paper_id"] != selected["paper_id"]
        )
        provenance = [
            _source_provenance(value)
            for value in sorted(members, key=lambda item: str(item["paper_id"]))
        ]
        reason = _selection_reason(selected)
        output = {
            key: value
            for key, value in selected.items()
            if not key.startswith("_") and key != "paperhash"
        }
        output.update(
            {
                "family_id": family_id,
                "paper_family": family_id,
                "representative_paper_id": selected["paper_id"],
                "excluded_paper_ids": excluded,
                "representative_selection_reason": reason,
                "family_source_provenance": provenance,
            }
        )
        representatives.append(output)
        family_entries.append(
            {
                "family_id": family_id,
                "representative_paper_id": selected["paper_id"],
                "excluded_paper_ids": excluded,
                "representative_selection_reason": reason,
                "source_provenance": provenance,
            }
        )
    return representatives, family_entries


def audit_representative_selection(
    source_records: Sequence[Mapping[str, Any]],
    representatives: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    by_family = defaultdict(list)
    for record in source_records:
        by_family[str(record["paper_family"])].append(record)
    selected_by_family = {
        str(record["family_id"]): str(record["paper_id"])
        for record in representatives
    }
    violations = []
    determining_stages = Counter()
    for family_id, members in by_family.items():
        expected = select_family_representative(members)
        if selected_by_family.get(family_id) != expected["paper_id"]:
            violations.append(
                {
                    "family_id": family_id,
                    "expected": expected["paper_id"],
                    "actual": selected_by_family.get(family_id),
                }
            )
        if len(members) == 1:
            determining_stages["single_record_family"] += 1
            continue
        remaining = list(members)
        for stage, key in (
            ("accepted_or_published", _accepted_or_published_rank),
            ("venue_year", lambda value: int(value["year"])),
            ("explicit_source_version", _explicit_version_rank),
        ):
            best = max(key(value) for value in remaining)
            narrowed = [value for value in remaining if key(value) == best]
            if len(narrowed) == 1:
                determining_stages[stage] += 1
                break
            remaining = narrowed
        else:
            determining_stages["stable_paper_id_or_source_hash"] += 1
    return {
        "protocol": REPRESENTATIVE_PROTOCOL,
        "family_count": len(by_family),
        "selected_representative_count": len(selected_by_family),
        "ranking_order": _selection_reason(representatives[0])["ranking_order"],
        "forbidden_inputs": _selection_reason(representatives[0])[
            "forbidden_inputs"
        ],
        "manual_exception_count": 0,
        "selection_violations": violations,
        "determining_stage_counts": dict(sorted(determining_stages.items())),
        "passed": not violations and len(by_family) == len(selected_by_family),
    }


def _assign_zones(
    representatives: Sequence[Dict[str, Any]],
    family_entries: Sequence[Dict[str, Any]],
    baseline_zones: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    historical_through_year: int,
    heldout_from_year: int,
) -> Dict[str, list[Dict[str, Any]]]:
    validation_year = historical_through_year + 1
    zones = {zone: [] for zone in FOUR_ZONES}
    family_by_id = {value["family_id"]: value for value in family_entries}
    baseline_by_paper = _by_paper(baseline_zones)
    for record in representatives:
        year = int(record["year"])
        paper_id = str(record["paper_id"])
        if paper_id not in baseline_by_paper:
            raise ValueError(f"Representative absent from frozen v1: {paper_id}")
        zone = baseline_by_paper[paper_id][0]
        valid_zone = (
            (year <= historical_through_year and zone == FOUR_ZONES[0])
            or (year == validation_year and zone in FOUR_ZONES[1:3])
            or (year >= heldout_from_year and zone == FOUR_ZONES[3])
        )
        if not valid_zone:
            raise ValueError(
                f"Representative v1 zone/year violates temporal protocol: "
                f"{paper_id}: zone={zone}, year={year}"
            )
        zones[zone].append(record)
    for zone in FOUR_ZONES:
        for record in zones[zone]:
            record["zone"] = zone
            family_by_id[record["family_id"]]["zone"] = zone
        zones[zone].sort(
            key=lambda value: (
                value["paper_review_cutoff"],
                value["venue"],
                value["paper_id"],
            )
        )
    return zones


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _by_paper(zones: Mapping[str, Sequence[Mapping[str, Any]]]):
    values = {}
    for zone in FOUR_ZONES:
        for record in zones.get(zone, []):
            paper_id = str(record.get("paper_id") or "")
            if not paper_id or paper_id in values:
                raise ValueError(f"Missing or duplicate split paper_id: {paper_id!r}")
            values[paper_id] = (zone, record)
    return values


def compare_split_versions(
    v1_zones: Mapping[str, Sequence[Mapping[str, Any]]],
    v2_zones: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    expected_source_record_count: int = 3900,
) -> Dict[str, Any]:
    """Audit provenance-safe v1-record to v2-family-target accounting."""
    old = _by_paper(v1_zones)
    new = _by_paper(v2_zones)
    old_ids, new_ids = set(old), set(new)
    extra = sorted(new_ids - old_ids)
    accounted = []
    malformed_family_metadata = []
    immutable_mismatches = []
    title_changes = []
    zone_changes = []
    for paper_id, (new_zone, record) in sorted(new.items()):
        excluded = record.get("excluded_paper_ids")
        if (
            not isinstance(excluded, list)
            or record.get("representative_paper_id") != paper_id
            or record.get("family_id") != record.get("paper_family")
        ):
            malformed_family_metadata.append(paper_id)
            excluded = []
        accounted.extend([paper_id, *map(str, excluded)])
        if paper_id not in old:
            continue
        old_zone, old_record = old[paper_id]
        fields = {
            "venue": (old_record.get("venue"), record.get("venue")),
            "year": (old_record.get("year"), record.get("year")),
            "paper_review_cutoff": (
                old_record.get("paper_review_cutoff"),
                record.get("paper_review_cutoff"),
            ),
        }
        changed = {
            field: {"v1": values[0], "v2": values[1]}
            for field, values in fields.items()
            if values[0] != values[1]
        }
        if changed:
            immutable_mismatches.append({"paper_id": paper_id, "fields": changed})
        if old_record.get("title") != record.get("title"):
            title_changes.append(
                {
                    "paper_id": paper_id,
                    "v1_title": old_record.get("title"),
                    "v2_title": record.get("title"),
                }
            )
        if old_zone != new_zone:
            zone_changes.append(
                {"paper_id": paper_id, "v1_zone": old_zone, "v2_zone": new_zone}
            )
    accounting_counts = defaultdict(int)
    for paper_id in accounted:
        accounting_counts[paper_id] += 1
    duplicate_accounting = sorted(
        paper_id for paper_id, count in accounting_counts.items() if count != 1
    )
    accounted_ids = set(accounting_counts)
    unaccounted = sorted(old_ids - accounted_ids)
    unknown_accounting = sorted(accounted_ids - old_ids)
    family_ids = [
        str(record.get("family_id") or "")
        for zone in FOUR_ZONES
        for record in v2_zones.get(zone, [])
    ]
    isolation = audit_four_zone_isolation(v2_zones)
    passed = not (
        len(old) != expected_source_record_count
        or extra
        or malformed_family_metadata
        or immutable_mismatches
        or duplicate_accounting
        or unaccounted
        or unknown_accounting
        or not all(family_ids)
        or len(set(family_ids)) != len(family_ids)
        or not isolation["passed"]
    )
    return {
        "protocol": "coco-v1-record-to-v2-family-target-audit-v1",
        "v1_source_record_count": len(old),
        "v2_unique_family_count": len(set(family_ids)),
        "v2_formal_target_count": len(new),
        "deduplicated_record_count": len(old) - len(new),
        "excluded_paper_ids": sorted(old_ids - new_ids),
        "extra_paper_ids": extra,
        "unaccounted_paper_ids": unaccounted,
        "unknown_accounted_paper_ids": unknown_accounting,
        "duplicate_accounting_paper_ids": duplicate_accounting,
        "malformed_family_metadata": malformed_family_metadata,
        "immutable_field_mismatches": immutable_mismatches,
        "title_changed_count": len(title_changes),
        "title_changes": title_changes,
        "representative_zone_changed_count": len(zone_changes),
        "representative_zone_changes": zone_changes,
        "zone_counts": {
            "v1_records": {
                zone: len(v1_zones.get(zone, [])) for zone in FOUR_ZONES
            },
            "v2_family_targets": {
                zone: len(v2_zones.get(zone, [])) for zone in FOUR_ZONES
            },
        },
        "cross_zone_paper_family_overlap_count": sum(
            len(value["values"])
            for value in isolation["overlaps"]
            if value["field"] == "paper_family"
        ),
        "cross_zone_normalized_title_overlap_count": sum(
            len(value["values"])
            for value in isolation["overlaps"]
            if value["field"] == "normalized_title"
        ),
        "passed": passed,
    }


def construct_split(
    coco_root: Path,
    *,
    baseline_manifest_path: Path,
    historical_through_year: int,
    heldout_from_year: int,
) -> tuple[Dict[str, Any], Dict[str, list[Dict[str, Any]]], list[Dict[str, Any]]]:
    validation_year = historical_through_year + 1
    if heldout_from_year != validation_year + 1:
        raise ValueError(
            "This protocol requires one complete validation year between "
            "Historical and held-out D"
        )
    baseline_summary, baseline_zones = load_temporal_split_manifest(
        str(baseline_manifest_path)
    )
    if baseline_summary.get("version") != TEMPORAL_SPLIT_V1_VERSION:
        raise ValueError("The v2 migration baseline must be coco temporal split v1")
    records, source_hashes = _load_source_records(coco_root)
    representatives, family_entries = _representatives(records)
    representative_audit = audit_representative_selection(records, representatives)
    zones = _assign_zones(
        representatives,
        family_entries,
        baseline_zones,
        historical_through_year=historical_through_year,
        heldout_from_year=heldout_from_year,
    )
    isolation = audit_four_zone_isolation(zones)
    comparison = compare_split_versions(baseline_zones, zones)
    if not isolation["passed"] or not comparison["passed"] or not representative_audit["passed"]:
        raise RuntimeError(
            f"Family-deduplicated temporal split audit failed: "
            f"isolation={isolation}, comparison={comparison}"
        )
    excluded_records = [
        {
            "family_id": family["family_id"],
            "representative_paper_id": family["representative_paper_id"],
            **source,
        }
        for family in family_entries
        for source in family["source_provenance"]
        if source["paper_id"] in family["excluded_paper_ids"]
    ]
    summary = {
        "version": TEMPORAL_SPLIT_VERSION,
        "status": "frozen_before_formal_evolution",
        "supersedes": SUPERSEDES,
        "reason": SUPERSESSION_REASON,
        "superseded_manifest": str(baseline_manifest_path.resolve()),
        "superseded_manifest_sha256": sha256_file(baseline_manifest_path),
        "source_pool": "external://cocoreviewbench/benchmark",
        "source_record_count": len(records),
        "source_pool_count": len(records),
        "unique_paper_family_count": len(family_entries),
        "formal_target_count": len(representatives),
        "deduplicated_record_count": len(records) - len(representatives),
        "target_unit": "one representative paper per paper family",
        "representative_selection_protocol": {
            "version": REPRESENTATIVE_PROTOCOL,
            "ranking_order": _selection_reason(representatives[0])["ranking_order"],
            "forbidden_inputs": _selection_reason(representatives[0])[
                "forbidden_inputs"
            ],
            "manual_exceptions_allowed": False,
        },
        "split_rule": {
            "A": f"representatives whose venue year <= {historical_through_year}",
            "B_C": (
                f"{validation_year} representatives inherit their frozen v1 "
                "B/C membership; no re-sampling"
            ),
            "D": f"representatives whose venue year >= {heldout_from_year}",
            "family_atomic": True,
            "one_target_per_family": True,
            "random_sampling": False,
        },
        "paper_review_cutoff_policy": {
            "priority": ["submission_date", "review_deadline", "review_period"],
            "available_in_source": False,
            "fallback": "December 31 of the calendar year preceding venue year",
            "precision": "conservative_year_level",
        },
        "source_sha256": source_hashes,
        "zones": {
            zone: {
                "count": len(zones[zone]),
                "venue_year": [
                    {"venue": venue, "year": year}
                    for venue, year in sorted(
                        {(value["venue"], value["year"]) for value in zones[zone]}
                    )
                ],
                "cutoff_min": min(
                    value["paper_review_cutoff"] for value in zones[zone]
                ),
                "cutoff_max": max(
                    value["paper_review_cutoff"] for value in zones[zone]
                ),
            }
            for zone in FOUR_ZONES
        },
        "audit": isolation,
        "v1_v2_comparison_audit": comparison,
        "deduplication_audit": {
            "protocol": "one-paper-family-one-target-audit-v1",
            "source_record_count": len(records),
            "unique_paper_family_count": len(family_entries),
            "formal_target_count": len(representatives),
            "deduplicated_record_count": len(excluded_records),
            "excluded_records": excluded_records,
            "cross_zone_paper_family_overlap_count": comparison[
                "cross_zone_paper_family_overlap_count"
            ],
            "cross_zone_normalized_title_overlap_count": comparison[
                "cross_zone_normalized_title_overlap_count"
            ],
            "passed": comparison["passed"],
        },
        "representative_selection_audit": representative_audit,
    }
    return summary, zones, family_entries


def build(
    coco_root: Path,
    output_dir: Path,
    *,
    baseline_manifest_path: Path,
    historical_through_year: int,
    heldout_from_year: int,
) -> dict:
    summary, zones, family_entries = construct_split(
        coco_root,
        baseline_manifest_path=baseline_manifest_path,
        historical_through_year=historical_through_year,
        heldout_from_year=heldout_from_year,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    family_path = output_dir / "paper_families.jsonl"
    _write_jsonl(family_path, family_entries)
    summary["families"] = {
        "path": family_path.name,
        "sha256": sha256_file(family_path),
        "count": len(family_entries),
    }
    for zone, values in zones.items():
        path = output_dir / f"{zone}.jsonl"
        _write_jsonl(path, values)
        summary["zones"][zone].update(
            {"path": path.name, "sha256": sha256_file(path)}
        )
    summary_path = output_dir / "manifest.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _report(value: Mapping[str, Any]) -> Dict[str, Any]:
    comparison = value["v1_v2_comparison_audit"]
    removed_count = comparison["deduplicated_record_count"]
    zone_change_count = comparison["representative_zone_changed_count"]
    return {
        "source_record_count": value["source_record_count"],
        "unique_paper_family_count": value["unique_paper_family_count"],
        "formal_target_count": value["formal_target_count"],
        "deduplicated_record_count": value["deduplicated_record_count"],
        "zone_counts": value["v1_v2_comparison_audit"]["zone_counts"],
        "excluded_records": value["deduplication_audit"]["excluded_records"],
        "temporal_audit": value["audit"],
        "deduplication_audit": {
            key: item
            for key, item in value["deduplication_audit"].items()
            if key != "excluded_records"
        },
        "representative_selection_audit": value["representative_selection_audit"],
        "v1_v2_membership_changes": {
            "total_changed_source_record_count": removed_count + zone_change_count,
            "removed_duplicate_submission_count": removed_count,
            "removed_duplicate_submission_ids": comparison["excluded_paper_ids"],
            "representative_zone_changed_count": zone_change_count,
            "representative_zone_changes": comparison["representative_zone_changes"],
        },
        "v1_v2_comparison_passed": comparison["passed"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coco-root", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--baseline-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v1/manifest.json"),
    )
    parser.add_argument("--historical-through-year", type=int, required=True)
    parser.add_argument("--heldout-from-year", type=int, required=True)
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Print family/zone/dedup audits without writing any split asset.",
    )
    args = parser.parse_args()
    coco_root = Path(args.coco_root).expanduser().resolve()
    baseline = Path(args.baseline_manifest).expanduser().resolve()
    if args.analysis_only:
        value, _, _ = construct_split(
            coco_root,
            baseline_manifest_path=baseline,
            historical_through_year=args.historical_through_year,
            heldout_from_year=args.heldout_from_year,
        )
        print(json.dumps(_report(value), ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not args.output_dir:
        parser.error("--output-dir is required unless --analysis-only is used")
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite temporal split output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + ".staging.", dir=output.parent))
    try:
        value = build(
            coco_root,
            staging,
            baseline_manifest_path=baseline,
            historical_through_year=args.historical_through_year,
            heldout_from_year=args.heldout_from_year,
        )
        os.replace(staging, output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(json.dumps(_report(value), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
