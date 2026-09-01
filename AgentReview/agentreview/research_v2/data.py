"""Deterministic development split and Frozen Paper preparation."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import SPLIT_SEED, SPLIT_SIZES
from .io import atomic_write_json, file_sha256, value_sha256, write_derived_jsonl
from .models import Paper


def unwrap_value(value: Any) -> Any:
    while isinstance(value, dict) and set(value) == {"value"}:
        value = value["value"]
    if isinstance(value, str) and value.startswith("{") and "'value'" in value:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value
        return unwrap_value(parsed)
    return value


def canonical_title(value: Any) -> str:
    text = str(unwrap_value(value) or "")
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _stable_key(seed: int, split_name: str, paper_id: str) -> str:
    return hashlib.sha256(
        f"split-seed-{seed}{split_name}{paper_id}".encode("utf-8")
    ).hexdigest()


def _paper_asset_path(asset_root: Path, record: dict[str, Any]) -> Path | None:
    raw = str(record.get("MD_path") or "").strip()
    if not raw:
        return None
    base = asset_root / raw
    candidates = (
        base / "md_content_no_appendix.md",
        base / "md_content.md",
        base,
    )
    return next((path for path in candidates if path.is_file()), None)


def _content(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("content")
    return dict(value) if isinstance(value, dict) else {}


def _title(record: dict[str, Any]) -> str:
    value = unwrap_value(_content(record).get("title"))
    return str(value or "").strip()


def _abstract(record: dict[str, Any]) -> str:
    value = unwrap_value(_content(record).get("abstract"))
    return str(value or "").strip()


def _paperhash(record: dict[str, Any]) -> str:
    return str(unwrap_value(_content(record).get("paperhash")) or "").strip()


def load_coco_identity(coco_root: str | Path) -> dict[str, set[str]]:
    root = Path(coco_root).expanduser().resolve() / "benchmark"
    ids: set[str] = set()
    titles: set[str] = set()
    hashes: set[str] = set()
    for path in sorted(root.glob("benchmark_*.jsonl")):
        if path.name in {
            "benchmark_all_human_classify.jsonl",
            "benchmark_left_out_human.jsonl",
        }:
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                paper_id = str(value.get("id") or "").strip()
                title = canonical_title(_content(value).get("title"))
                paperhash = _paperhash(value)
                if paper_id:
                    ids.add(paper_id)
                if title:
                    titles.add(title)
                if paperhash:
                    hashes.add(paperhash)
    return {"ids": ids, "titles": titles, "paperhashes": hashes}


@dataclass(frozen=True)
class Candidate:
    paper_id: str
    title: str
    title_key: str
    abstract: str
    paperhash: str
    group: str
    md_path: str
    md_sha256: str
    source_line: int


def scan_candidates(
    train_clean_path: str | Path,
    paper_asset_root: str | Path,
    coco_identity: dict[str, set[str]],
) -> tuple[list[Candidate], Counter]:
    source = Path(train_clean_path).expanduser().resolve()
    asset_root = Path(paper_asset_root).expanduser().resolve()
    reasons: Counter = Counter()
    candidates: list[Candidate] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    seen_hashes: set[str] = set()
    seen_md_hashes: set[str] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            paper_id = str(record.get("id") or "").strip()
            title = _title(record)
            title_key = canonical_title(title)
            paperhash = _paperhash(record)
            md_path = _paper_asset_path(asset_root, record)
            checks = (
                (not paper_id, "missing_id"),
                (not title_key, "missing_title"),
                (md_path is None, "missing_frozen_paper"),
                (paper_id in coco_identity["ids"], "coco_exact_id"),
                (title_key in coco_identity["titles"], "coco_canonical_title"),
                (
                    bool(paperhash and paperhash in coco_identity["paperhashes"]),
                    "coco_paperhash",
                ),
                (paper_id in seen_ids, "duplicate_exact_id"),
                (title_key in seen_titles, "duplicate_canonical_title"),
                (bool(paperhash and paperhash in seen_hashes), "duplicate_paperhash"),
            )
            rejected = next((reason for matched, reason in checks if matched), None)
            if rejected:
                reasons[rejected] += 1
                continue
            assert md_path is not None
            md_hash = file_sha256(md_path)
            if md_hash in seen_md_hashes:
                reasons["duplicate_full_text_sha256"] += 1
                continue
            seen_ids.add(paper_id)
            seen_titles.add(title_key)
            seen_md_hashes.add(md_hash)
            if paperhash:
                seen_hashes.add(paperhash)
            candidates.append(
                Candidate(
                    paper_id=paper_id,
                    title=title,
                    title_key=title_key,
                    abstract=_abstract(record),
                    paperhash=paperhash,
                    group=str(record.get("conference") or "UNKNOWN").strip(),
                    md_path=str(md_path.resolve()),
                    md_sha256=md_hash,
                    source_line=line_number,
                )
            )
    return candidates, reasons


def select_balanced_splits(
    candidates: list[Candidate],
    *,
    split_sizes: dict[str, int] | None = None,
    seed: int = SPLIT_SEED,
) -> dict[str, list[Candidate]]:
    sizes = dict(split_sizes or SPLIT_SIZES)
    available = {candidate.paper_id: candidate for candidate in candidates}
    selected: dict[str, list[Candidate]] = {}
    for split_name, size in sizes.items():
        groups: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in available.values():
            groups[candidate.group].append(candidate)
        for group in groups:
            groups[group].sort(
                key=lambda item: _stable_key(seed, split_name, item.paper_id)
            )
        chosen: list[Candidate] = []
        group_order = sorted(groups)
        while len(chosen) < size:
            progressed = False
            for group in group_order:
                if groups[group] and len(chosen) < size:
                    chosen.append(groups[group].pop(0))
                    progressed = True
            if not progressed:
                raise ValueError(
                    f"Development corpus has {len(chosen)} usable records for {split_name}, requires {size}"
                )
        selected[split_name] = chosen
        for candidate in chosen:
            available.pop(candidate.paper_id, None)
    return selected


def _freeze_paper(candidate: Candidate, output_root: Path) -> dict[str, Any]:
    text = Path(candidate.md_path).read_text(encoding="utf-8")
    paper = Paper(
        paper_id=candidate.paper_id,
        title=candidate.title,
        abstract=candidate.abstract,
        text=text,
        source={
            "dataset": "OpenReview/train_clean",
            "source_line": candidate.source_line,
            "conference": candidate.group,
            "paperhash": candidate.paperhash,
            "md_source_path": candidate.md_path,
            "md_sha256": candidate.md_sha256,
        },
    )
    payload = paper.to_dict()
    payload["paper_asset_id"] = value_sha256(payload)
    path = output_root / "data" / "papers" / f"{candidate.paper_id}.json"
    atomic_write_json(path, payload)
    return {
        "paper_id": candidate.paper_id,
        "paper_asset_id": payload["paper_asset_id"],
        "path": str(path.resolve()),
        "group": candidate.group,
        "canonical_title": candidate.title_key,
        "paperhash": candidate.paperhash,
        "md_sha256": candidate.md_sha256,
    }


def prepare_data(
    *,
    train_clean_path: str | Path,
    paper_asset_root: str | Path,
    coco_root: str | Path,
    output_root: str | Path,
    split_sizes: dict[str, int] | None = None,
    seed: int = SPLIT_SEED,
) -> dict[str, Any]:
    output = Path(output_root).expanduser().resolve()
    coco_identity = load_coco_identity(coco_root)
    candidates, reasons = scan_candidates(
        train_clean_path,
        paper_asset_root,
        coco_identity,
    )
    splits = select_balanced_splits(candidates, split_sizes=split_sizes, seed=seed)
    split_payloads: dict[str, list[dict[str, Any]]] = {}
    used: set[str] = set()
    for split_name, values in splits.items():
        rows = [_freeze_paper(candidate, output) for candidate in values]
        ids = {row["paper_id"] for row in rows}
        if used & ids:
            raise RuntimeError(f"Development splits overlap: {split_name}")
        used.update(ids)
        split_payloads[split_name] = rows
        atomic_write_json(
            output / "data" / "splits" / f"{split_name}.json",
            {
                "split": split_name,
                "seed": seed,
                "count": len(rows),
                "records": rows,
                "split_sha256": value_sha256(rows),
            },
        )
    s2_queries = [
        {
            "paper_id": row["paper_id"],
            "canonical_query_title": next(
                candidate.title
                for candidate in candidates
                if candidate.paper_id == row["paper_id"]
            ),
        }
        for rows in split_payloads.values()
        for row in rows
    ]
    write_derived_jsonl(output / "data" / "s2_title_queries.jsonl", s2_queries)
    manifest = {
        "protocol": "research-v2-development-split-v1",
        "seed": seed,
        "source": {
            "train_clean_path": str(Path(train_clean_path).expanduser().resolve()),
            "train_clean_sha256": file_sha256(train_clean_path),
            "paper_asset_root": str(Path(paper_asset_root).expanduser().resolve()),
            "coco_root": str(Path(coco_root).expanduser().resolve()),
            "coco_id_count": len(coco_identity["ids"]),
            "coco_title_count": len(coco_identity["titles"]),
        },
        "usable_candidate_count": len(candidates),
        "rejection_counts": dict(sorted(reasons.items())),
        "splits": {
            name: {
                "count": len(rows),
                "paper_ids": [row["paper_id"] for row in rows],
                "split_sha256": value_sha256(rows),
            }
            for name, rows in split_payloads.items()
        },
    }
    manifest["manifest_sha256"] = value_sha256(manifest)
    atomic_write_json(output / "data" / "manifest.json", manifest)
    return manifest
