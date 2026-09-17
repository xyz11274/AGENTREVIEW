"""Freeze the protocol-compatible CoCo-1300 paper set before any Review Run."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .data import unwrap_value
from .io import atomic_write_json, file_sha256, value_sha256, write_derived_jsonl
from .models import Paper

PROTOCOL = "coco_1300_protocol_compatible_resample_v1"
BRACKETS = ("0-3", "3-5", "5-7", "7-10")
GROUP_PATTERN = re.compile(r"^benchmark_(ICLR|NeurIPS)_(\d{4})\.jsonl$")


def _normalized_overall(score: float, conference: str, year: int) -> float:
    denominator = 7.0 if conference == "ICLR" and year == 2020 else 9.0
    return max(0.0, min(10.0, (float(score) - 1.0) * (10.0 / denominator)))


def _overall_scores(record: dict[str, Any], conference: str, year: int) -> list[float]:
    scores: list[float] = []
    for thread in record.get("reviews") or []:
        if not isinstance(thread, list):
            continue
        for post in thread:
            if not isinstance(post, dict) or not str(post.get("role") or "").startswith("Reviewer"):
                continue
            score_map = (post.get("sentence_ids") or {}).get("scores")
            overall = score_map.get("Overall") if isinstance(score_map, dict) else None
            if isinstance(overall, (int, float)) and not isinstance(overall, bool):
                scores.append(_normalized_overall(float(overall), conference, year))
                break
    return scores


def _bracket(score: float) -> str:
    if 0.0 <= score < 3.0:
        return "0-3"
    if 3.0 <= score < 5.0:
        return "3-5"
    if 5.0 <= score < 7.0:
        return "5-7"
    if 7.0 <= score <= 10.0:
        return "7-10"
    raise ValueError(f"Normalized overall score is outside [0, 10]: {score}")


def proportional_quotas(counts: dict[str, int], target: int) -> dict[str, int]:
    """Largest-remainder proportional allocation with fixed bracket tie-breaking."""
    total = sum(counts.get(name, 0) for name in BRACKETS)
    if target <= 0 or target > total:
        raise ValueError(f"Target must be within available records: target={target}, total={total}")
    exact = {name: counts.get(name, 0) * target / total for name in BRACKETS}
    quotas = {name: math.floor(exact[name]) for name in BRACKETS}
    remaining = target - sum(quotas.values())
    order = sorted(BRACKETS, key=lambda name: (-(exact[name] - quotas[name]), BRACKETS.index(name)))
    for name in order[:remaining]:
        quotas[name] += 1
    return quotas


def _selection_key(seed: int, group: str, bracket: str, paper_id: str) -> str:
    return hashlib.sha256(
        f"{PROTOCOL}\nseed={seed}\n{group}\n{bracket}\n{paper_id}".encode("utf-8")
    ).hexdigest()


def _benchmark_files(root: Path) -> list[tuple[str, int, Path]]:
    values = []
    for path in sorted((root / "benchmark").glob("benchmark_*.jsonl")):
        match = GROUP_PATTERN.match(path.name)
        if match:
            values.append((match.group(1), int(match.group(2)), path))
    if len(values) != 13:
        raise ValueError(f"CoCo-1300 requires exactly 13 venue-year files, found {len(values)}")
    return values


def _paper_text_path(root: Path, record: dict[str, Any]) -> Path:
    raw = str(record.get("MD_path") or "").strip()
    path = (root / raw).resolve()
    if not path.is_file() or root not in path.parents:
        raise ValueError(f"Missing or external CoCo paper asset: {raw}")
    return path


def prepare_coco_1300(
    *,
    coco_root: str | Path,
    output_root: str | Path,
    seed: int = 0,
    per_group: int = 100,
) -> dict[str, Any]:
    """Freeze IDs and paper-only assets; human Review/taxonomy fields are never copied."""
    root = Path(coco_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    manifest_groups: dict[str, Any] = {}
    selected_records: list[dict[str, Any]] = []
    s2_queries: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for conference, year, source_path in _benchmark_files(root):
        group = f"{conference}_{year}"
        source_sha256 = file_sha256(source_path)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with source_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                paper_id = str(record.get("id") or "").strip()
                scores = _overall_scores(record, conference, year)
                if not paper_id or not scores:
                    raise ValueError(f"{group}:{line_number} lacks paper ID or reviewer overall score")
                score = sum(scores) / len(scores)
                buckets[_bracket(score)].append(
                    {
                        "paper_id": paper_id,
                        "score": score,
                        "record": record,
                        "source_line": line_number,
                    }
                )
        counts = {name: len(buckets[name]) for name in BRACKETS}
        quotas = proportional_quotas(counts, per_group)
        chosen: list[dict[str, Any]] = []
        for name in BRACKETS:
            ordered = sorted(
                buckets[name],
                key=lambda item: _selection_key(seed, group, name, item["paper_id"]),
            )
            chosen.extend(ordered[: quotas[name]])
        chosen.sort(key=lambda item: item["paper_id"])
        if len(chosen) != per_group:
            raise RuntimeError(f"{group} selected {len(chosen)} records, expected {per_group}")

        selected_counts: dict[str, int] = defaultdict(int)
        group_ids = []
        for item in chosen:
            record = item["record"]
            paper_id = item["paper_id"]
            if paper_id in seen_ids:
                raise ValueError(f"Duplicate CoCo paper ID across groups: {paper_id}")
            seen_ids.add(paper_id)
            title = str(unwrap_value((record.get("content") or {}).get("title")) or "").strip()
            abstract = str(unwrap_value((record.get("content") or {}).get("abstract")) or "").strip()
            text_path = _paper_text_path(root, record)
            paper = Paper(
                paper_id=paper_id,
                title=title,
                abstract=abstract,
                text=text_path.read_text(encoding="utf-8"),
                source={
                    "dataset": "CoCoReviewBench/published-benchmark",
                    "venue_year": group,
                    "benchmark_source_sha256": source_sha256,
                    "source_line": item["source_line"],
                    "md_sha256": file_sha256(text_path),
                },
            )
            payload = paper.to_dict()
            payload["paper_asset_id"] = value_sha256(payload)
            paper_path = output / "data" / "coco_1300" / "papers" / f"{paper_id}.json"
            atomic_write_json(paper_path, payload)
            bracket = _bracket(item["score"])
            selected_counts[bracket] += 1
            group_ids.append(paper_id)
            selected_records.append(
                {
                    "paper_id": paper_id,
                    "paper_asset_id": payload["paper_asset_id"],
                    "path": str(paper_path),
                    "venue_year": group,
                    "score_bracket": bracket,
                }
            )
            s2_queries.append({"paper_id": paper_id, "canonical_query_title": title})
        manifest_groups[group] = {
            "source_path": str(source_path),
            "source_sha256": source_sha256,
            "available_bucket_counts": counts,
            "quota_by_bucket": quotas,
            "selected_bucket_counts": dict(selected_counts),
            "paper_ids": group_ids,
        }

    selected_records.sort(key=lambda item: (item["venue_year"], item["paper_id"]))
    s2_queries.sort(key=lambda item: item["paper_id"])
    write_derived_jsonl(output / "data" / "coco_1300" / "s2_title_queries.jsonl", s2_queries)
    payload = {
        "protocol": PROTOCOL,
        "claim_boundary": "protocol-compatible resample; not the authors' official/original IDs",
        "seed": seed,
        "per_group": per_group,
        "count": len(selected_records),
        "sampling_source_sha256": file_sha256(__file__),
        "groups": manifest_groups,
        "records": selected_records,
    }
    if payload["count"] != 1300:
        raise ValueError(f"CoCo protocol requires 1,300 papers, got {payload['count']}")
    payload["manifest_sha256"] = value_sha256(payload)
    atomic_write_json(output / "data" / "coco_1300" / "manifest.json", payload)
    return payload
