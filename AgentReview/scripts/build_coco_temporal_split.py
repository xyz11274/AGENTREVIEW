#!/usr/bin/env python3
"""Freeze the CoCo source pool into chronological, paper-disjoint A/B/C/D zones."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from memory_evolution.assets import (
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    audit_four_zone_isolation,
    normalize_title,
    sha256_file,
)


def _family_components(records):
    parents = list(range(len(records)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    owners = {}
    identifiers = []
    for index, record in enumerate(records):
        values = {
            "title:" + record["normalized_title"],
            "paperhash:" + normalize_title(record.get("paperhash")),
        }
        values = {value for value in values if not value.endswith(":")}
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


def _write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build(
    coco_root: Path,
    output_dir: Path,
    *,
    historical_through_year: int,
    heldout_from_year: int,
) -> dict:
    validation_year = int(historical_through_year) + 1
    if int(heldout_from_year) != validation_year + 1:
        raise ValueError(
            "This protocol requires one complete validation year between "
            "Historical and held-out D"
        )
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
                title = str(content.get("title") or "")
                if not paper_id or not title:
                    raise ValueError(f"Missing identity at {source}:{line_number}")
                records.append(
                    {
                        "paper_id": paper_id,
                        "title": title,
                        "normalized_title": normalize_title(title),
                        "paperhash": str(content.get("paperhash") or ""),
                        "venue": venue.lower(),
                        "year": year,
                        "paper_review_cutoff": f"{year - 1}-12-31",
                        "cutoff_precision": "conservative_year_level",
                        "cutoff_basis": (
                            "exact submission/review dates absent from frozen CoCo record; "
                            "use end of prior calendar year"
                        ),
                        "source_asset": f"external://cocoreviewbench/benchmark/{source.name}",
                        "source_line": line_number,
                        "source_record_sha256": hashlib.sha256(
                            line.rstrip("\n").encode("utf-8")
                        ).hexdigest(),
                    }
                )
    if len(records) != 3900:
        raise ValueError(f"Expected frozen 3900-paper pool, found {len(records)}")
    family_ids = _family_components(records)
    for record, family in zip(records, family_ids):
        record["paper_family"] = family

    by_family = defaultdict(list)
    for record in records:
        by_family[record["paper_family"]].append(record)
    zone_by_family = {}
    validation_families = defaultdict(list)
    for family, values in by_family.items():
        years = {int(value["year"]) for value in values}
        if max(years) <= historical_through_year:
            zone_by_family[family] = FOUR_ZONES[0]
        elif min(years) >= heldout_from_year:
            zone_by_family[family] = FOUR_ZONES[3]
        elif years == {validation_year}:
            venue = sorted({value["venue"] for value in values})[0]
            validation_families[venue].append(family)
        else:
            raise ValueError(f"Work family crosses temporal boundary: {family}: {years}")
    for venue, families in validation_families.items():
        ordered = sorted(
            families,
            key=lambda value: hashlib.sha256(
                f"{TEMPORAL_SPLIT_VERSION}:{venue}:{value}".encode()
            ).hexdigest(),
        )
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            raise ValueError(f"Cannot split {venue} 2023 families evenly")
        for family in ordered[:midpoint]:
            zone_by_family[family] = FOUR_ZONES[1]
        for family in ordered[midpoint:]:
            zone_by_family[family] = FOUR_ZONES[2]

    zones = {zone: [] for zone in FOUR_ZONES}
    for record in records:
        zone = zone_by_family[record["paper_family"]]
        output = dict(record)
        output.pop("paperhash", None)
        output["zone"] = zone
        zones[zone].append(output)
    for zone in FOUR_ZONES:
        zones[zone].sort(
            key=lambda value: (
                value["paper_review_cutoff"],
                value["venue"],
                value["paper_id"],
            )
        )

    audit = audit_four_zone_isolation(zones)
    if not audit["passed"]:
        raise RuntimeError(f"Generated split failed isolation audit: {audit}")
    summary = {
        "version": TEMPORAL_SPLIT_VERSION,
        "status": "frozen_before_formal_evolution",
        "source_pool": "external://cocoreviewbench/benchmark",
        "source_pool_count": len(records),
        "split_rule": {
            "A": f"all work families whose papers are <= {historical_through_year}",
            "B_C": (
                f"{validation_year} families, deterministic SHA256-balanced "
                "50/50 within venue"
            ),
            "D": f"all work families whose papers are >= {heldout_from_year}",
            "family_atomic": True,
            "random_sampling": False,
        },
        "paper_review_cutoff_policy": {
            "priority": ["submission_date", "review_deadline", "review_period"],
            "available_in_source": False,
            "fallback": "December 31 of the calendar year preceding venue year",
            "precision": "conservative_year_level",
        },
        "source_sha256": source_hashes,
        "zones": {},
        "audit": audit,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for zone, values in zones.items():
        path = output_dir / f"{zone}.jsonl"
        _write_jsonl(path, values)
        cells = sorted({(value["venue"], value["year"]) for value in values})
        summary["zones"][zone] = {
            "path": path.name,
            "sha256": sha256_file(path),
            "count": len(values),
            "venue_year": [
                {"venue": venue, "year": year} for venue, year in cells
            ],
            "cutoff_min": values[0]["paper_review_cutoff"],
            "cutoff_max": values[-1]["paper_review_cutoff"],
        }
    summary_path = output_dir / "manifest.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coco-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--historical-through-year", type=int, required=True)
    parser.add_argument("--heldout-from-year", type=int, required=True)
    args = parser.parse_args()
    value = build(
        Path(args.coco_root).expanduser().resolve(),
        Path(args.output_dir).expanduser().resolve(),
        historical_through_year=args.historical_through_year,
        heldout_from_year=args.heldout_from_year,
    )
    print(json.dumps(value["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
