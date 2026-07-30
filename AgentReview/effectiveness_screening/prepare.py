"""Prepare leakage-safe data for the frozen MemoryProgram effectiveness screen."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from agentreview.paper_review_player_md import format_refs
from agentreview.source_map import (
    build_source_anchors,
    extract_title_from_content_list,
    format_source_anchors,
    load_content_list,
)
from memory_evolution.agentreview_data import (
    _assign_work_families,
    _category_reference,
    _meta_reference,
)
from memory_evolution.tasks import SPECIALIST_TASKS


DESIGN_VERSION = "effectiveness-screening-v1"
STRATA = ("ICLR-accept", "ICLR-reject", "NeurIPS-accept", "NeurIPS-reject")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_value(value: Any) -> str:
    while isinstance(value, dict) and "value" in value:
        value = value["value"]
    return str(value or "").strip()


def category_reference_map(
    record: Mapping[str, Any], task_id: str
) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for category, value in sorted(
        (record.get("opinions_md_by_category_dict") or {}).items()
    ):
        if not str(category).startswith(f"{task_id}-"):
            continue
        text = ""
        if isinstance(value, list):
            text = next(
                (
                    str(item).strip()
                    for item in reversed(value)
                    if isinstance(item, str) and item.strip()
                ),
                "",
            )
        elif isinstance(value, str):
            text = value.strip()
        if text:
            output[str(category)] = text
    return output


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"Expected object at {path}:{line_number}")
            yield value


def write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def semantic_scholar_query_candidates(title: str) -> List[str]:
    """Return deterministic title queries, ordered from specific to broader."""
    candidates = [title.strip()]
    if ":" in title:
        descriptive_title = title.split(":", 1)[1].strip()
        if descriptive_title:
            candidates.append(descriptive_title)
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def semantic_scholar_search(
    requests_module: Any,
    *,
    query: str,
    limit: int,
    api_key: str,
    min_interval_seconds: float,
    max_attempts: int,
    last_request_at: float | None,
) -> tuple[List[Dict[str, Any]], float]:
    """Search S2 with 1-RPS pacing and bounded retry for transient failures."""
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(1, max_attempts + 1):
        if last_request_at is not None:
            elapsed = time.monotonic() - last_request_at
            if elapsed < min_interval_seconds:
                time.sleep(min_interval_seconds - elapsed)
        try:
            response = requests_module.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": limit,
                    "minCitationCount": 0,
                    "fields": (
                        "title,year,abstract,authors.name,citationCount,url,"
                        "externalIds"
                    ),
                },
                headers={"x-api-key": api_key},
                timeout=60,
            )
            last_request_at = time.monotonic()
        except requests_module.RequestException as exc:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Semantic Scholar request failed after {max_attempts} "
                    f"attempts for query {query!r}"
                ) from exc
            delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
            print(
                f"S2 network error; retrying in {delay:.0f}s "
                f"({attempt}/{max_attempts})",
                flush=True,
            )
            time.sleep(delay)
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Semantic Scholar returned invalid JSON for query {query!r}"
                ) from exc
            return list(payload.get("data") or []), last_request_at
        if response.status_code not in retryable_statuses:
            raise RuntimeError(
                f"Semantic Scholar retrieval failed for query {query!r}: "
                f"HTTP {response.status_code}. Check S2_API_KEY and request."
            )
        if attempt == max_attempts:
            raise RuntimeError(
                f"Semantic Scholar retrieval failed after {max_attempts} "
                f"attempts for query {query!r}: HTTP {response.status_code}."
            )
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            delay = float(retry_after)
        except ValueError:
            delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
        delay = max(min_interval_seconds, min(delay, 300.0))
        print(
            f"S2 HTTP {response.status_code}; retrying in {delay:.0f}s "
            f"({attempt}/{max_attempts})",
            flush=True,
        )
        time.sleep(delay)
    raise AssertionError("unreachable")


def load_config(path: Path) -> Dict[str, Any]:
    source = path.expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if value.get("design_version") != DESIGN_VERSION:
        raise ValueError(
            f"Unsupported design version: {value.get('design_version')!r}"
        )
    if value.get("metric_status") != "CoCo-style proxy screening":
        raise ValueError("The screening config must explicitly declare proxy status")
    value["_config_path"] = str(source)
    value["_config_sha256"] = sha256_file(source)
    lock_path = Path(value["output_root"]).expanduser().resolve() / "design_lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        expected = str(lock.get("config_sha256") or "")
        if value["_config_sha256"] != expected:
            raise RuntimeError(
                "Frozen effectiveness config checksum changed: "
                f"expected={expected}, actual={value['_config_sha256']}"
            )
    return value


def collect_evolution_paper_ids(data_root: Path) -> set[str]:
    paper_ids: set[str] = set()
    for path in sorted(data_root.rglob("*.jsonl")):
        for value in iter_jsonl(path):
            metadata = value.get("metadata") or {}
            candidates = (
                value.get("id"),
                value.get("paper_id"),
                metadata.get("paper_id") if isinstance(metadata, dict) else None,
            )
            for candidate in candidates:
                if candidate:
                    paper_ids.add(str(candidate).split("::", 1)[0])
    return paper_ids


def decision_stratum(record: Mapping[str, Any]) -> str | None:
    conference = str(record.get("conference") or "")
    if conference.startswith("ICLR "):
        venue = "ICLR"
    elif conference.startswith("NeurIPS "):
        venue = "NeurIPS"
    else:
        return None
    decision = str(record.get("decision") or "").strip().lower()
    if decision.startswith("accept"):
        outcome = "accept"
    elif decision == "reject":
        outcome = "reject"
    else:
        return None
    return f"{venue}-{outcome}"


def has_author_reply(record: Mapping[str, Any]) -> bool:
    for group in record.get("reviews") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if (
                isinstance(item, (list, tuple))
                and item
                and str(item[0]).strip().lower().startswith("author")
            ):
                return True
    return False


def reviewer_overall_scores(record: Mapping[str, Any]) -> List[float]:
    scores: List[float] = []
    for group in record.get("reviews") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            role, payload = item[0], item[1]
            if not str(role).lower().startswith("reviewer") or not isinstance(
                payload, dict
            ):
                continue
            score_map = payload.get("scores") or {}
            if not isinstance(score_map, dict):
                continue
            for key in ("Overall", "overall", "Rating", "rating"):
                value = score_map.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    scores.append(float(value))
                    break
    return scores


def markdown_index(openreview_root: Path) -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    for path in sorted(openreview_root.rglob("md_content_no_appendix.md")):
        sample_dir = path.parent.name
        if "_" in sample_dir:
            index.setdefault(sample_dir.split("_", 1)[1], path.resolve())
    for path in sorted(openreview_root.rglob("md_content.md")):
        sample_dir = path.parent.name
        if "_" in sample_dir:
            index.setdefault(sample_dir.split("_", 1)[1], path.resolve())
    return index


def _eligible(
    record: Mapping[str, Any],
    family: str,
    *,
    test_families: set[str],
    evolution_families: set[str],
    markdown_by_id: Mapping[str, Path],
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    if family in test_families:
        reasons.append("official_test_family")
    if family in evolution_families:
        reasons.append("mstar_seen_family")
    if decision_stratum(record) not in STRATA:
        reasons.append("unsupported_decision_or_venue")
    if str(record.get("id") or "") not in markdown_by_id:
        reasons.append("missing_markdown")
    if not has_author_reply(record):
        reasons.append("missing_author_reply")
    if not record.get("metareview"):
        reasons.append("missing_meta_review")
    by_category = record.get("opinions_md_by_category_dict") or {}
    missing_tasks = [
        task_id
        for task_id in SPECIALIST_TASKS
        if not any(
            str(category).startswith(f"{task_id}-") and bool(value)
            for category, value in by_category.items()
        )
    ]
    if missing_tasks:
        reasons.append("missing_specialist_reference:" + ",".join(missing_tasks))
    return not reasons, reasons


def _sample_stratified(
    candidates: Sequence[Dict[str, Any]],
    *,
    seed: int,
    per_stratum: int,
    controversial_total: int | None,
) -> List[Dict[str, Any]]:
    by_stratum: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_stratum[candidate["stratum"]].append(candidate)
    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    controversial_allocation = {}
    if controversial_total is not None:
        base, remainder = divmod(controversial_total, len(STRATA))
        rotated = list(STRATA)
        rng.shuffle(rotated)
        controversial_allocation = {
            stratum: base + (1 if stratum in rotated[:remainder] else 0)
            for stratum in STRATA
        }
    for stratum in STRATA:
        values = sorted(by_stratum[stratum], key=lambda item: item["paper_id"])
        rng.shuffle(values)
        if controversial_total is None:
            chosen = values[:per_stratum]
        else:
            needed_high = controversial_allocation[stratum]
            high = [value for value in values if value["score_gap_ge_4"]]
            ordinary = [value for value in values if not value["score_gap_ge_4"]]
            chosen = high[:needed_high] + ordinary[: per_stratum - needed_high]
        if len(chosen) != per_stratum:
            raise RuntimeError(
                f"Not enough eligible papers in {stratum}: "
                f"needed={per_stratum}, selected={len(chosen)}"
            )
        selected.extend(chosen)
    rng.shuffle(selected)
    return selected


def select_papers(config: Dict[str, Any]) -> Dict[str, Any]:
    openreview_root = Path(config["openreview_root"]).expanduser().resolve()
    evolution_data_root = Path(config["evolution_data_root"]).expanduser().resolve()
    output_root = Path(config["output_root"]).expanduser().resolve()
    if (output_root / "design_lock.json").exists():
        raise RuntimeError("Refusing to resample after the design lock was written")
    train_path = openreview_root / "train_clean.jsonl"
    test_path = openreview_root / "test.jsonl"
    train = list(iter_jsonl(train_path))
    test = list(iter_jsonl(test_path))
    families = _assign_work_families(train + test)
    train_families = families[: len(train)]
    test_families = set(families[len(train) :])
    id_to_family = {
        str(record.get("id") or ""): family
        for record, family in zip(train + test, families)
    }
    evolution_ids = collect_evolution_paper_ids(evolution_data_root)
    evolution_families = {
        id_to_family[paper_id]
        for paper_id in evolution_ids
        if paper_id in id_to_family
    }
    md_index = markdown_index(openreview_root)
    rejection_counts: Counter[str] = Counter()
    candidates: List[Dict[str, Any]] = []
    records_by_id: Dict[str, Dict[str, Any]] = {}
    for record, family in zip(train, train_families):
        eligible, reasons = _eligible(
            record,
            family,
            test_families=test_families,
            evolution_families=evolution_families,
            markdown_by_id=md_index,
        )
        if not eligible:
            rejection_counts.update(reasons)
            continue
        paper_id = str(record.get("id") or "")
        scores = reviewer_overall_scores(record)
        gap = max(scores) - min(scores) if len(scores) >= 2 else None
        candidate = {
            "paper_id": paper_id,
            "work_family": family,
            "title": text_value((record.get("content") or {}).get("title")),
            "conference": str(record.get("conference") or ""),
            "decision": str(record.get("decision") or ""),
            "stratum": decision_stratum(record),
            "reviewer_overall_scores": scores,
            "reviewer_score_gap": gap,
            "score_gap_ge_4": gap is not None and gap >= 4.0,
            "markdown_source": str(md_index[paper_id]),
            "markdown_sha256": sha256_file(md_index[paper_id]),
        }
        candidates.append(candidate)
        records_by_id[paper_id] = record

    per_stratum = int(config["sampling"]["papers_per_stratum"])
    effectiveness = _sample_stratified(
        candidates,
        seed=int(config["sampling"]["effectiveness_seed"]),
        per_stratum=per_stratum,
        controversial_total=int(config["sampling"]["controversial_total"]),
    )
    effectiveness_families = {value["work_family"] for value in effectiveness}
    memory_candidates = [
        value
        for value in candidates
        if value["work_family"] not in effectiveness_families
    ]
    memory = _sample_stratified(
        memory_candidates,
        seed=int(config["sampling"]["memory_seed"]),
        per_stratum=per_stratum,
        controversial_total=None,
    )
    memory_families = {value["work_family"] for value in memory}
    if effectiveness_families & memory_families:
        raise AssertionError("Effectiveness and memory corpus families overlap")

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_root / "effectiveness_validation.jsonl", effectiveness)
    write_jsonl(output_root / "memory_corpus.jsonl", memory)

    proxy_references = []
    for selected in effectiveness:
        record = records_by_id[selected["paper_id"]]
        category_references = {}
        category_reference_by_tag = {}
        reference_categories = {}
        for task_id in SPECIALIST_TASKS:
            reference, categories = _category_reference(record, task_id)
            category_references[task_id] = reference
            category_reference_by_tag.update(
                category_reference_map(record, task_id)
            )
            reference_categories[task_id] = categories
        proxy_references.append(
            {
                "paper_id": selected["paper_id"],
                "work_family": selected["work_family"],
                "metric_status": "CoCo-style proxy; not official CoCoReviewBench",
                "category_references": category_references,
                "category_reference_by_tag": category_reference_by_tag,
                "reference_categories": reference_categories,
                "meta_reference": _meta_reference(record),
            }
        )
    reference_path = output_root / "evaluation_references_proxy.jsonl"
    write_jsonl(reference_path, proxy_references)

    audit = {
        "design_version": DESIGN_VERSION,
        "metric_status": config["metric_status"],
        "source": {
            "train_clean": str(train_path),
            "train_clean_sha256": sha256_file(train_path),
            "sealed_test": str(test_path),
            "sealed_test_sha256": sha256_file(test_path),
            "evolution_data_root": str(evolution_data_root),
        },
        "counts": {
            "train_records": len(train),
            "test_records": len(test),
            "test_families": len(test_families),
            "mstar_seen_paper_ids": len(evolution_ids),
            "mstar_seen_families": len(evolution_families),
            "eligible_records": len(candidates),
            "effectiveness_papers": len(effectiveness),
            "memory_corpus_papers": len(memory),
            "effectiveness_controversial": sum(
                bool(value["score_gap_ge_4"]) for value in effectiveness
            ),
        },
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "invariants": {
            "effectiveness_vs_test_family_overlap": len(
                effectiveness_families & test_families
            ),
            "memory_vs_test_family_overlap": len(memory_families & test_families),
            "effectiveness_vs_mstar_family_overlap": len(
                effectiveness_families & evolution_families
            ),
            "memory_vs_mstar_family_overlap": len(
                memory_families & evolution_families
            ),
            "effectiveness_vs_memory_family_overlap": len(
                effectiveness_families & memory_families
            ),
        },
        "selection_sha256": {
            "effectiveness_validation": canonical_json_sha256(effectiveness),
            "memory_corpus": canonical_json_sha256(memory),
            "evaluation_references_proxy": sha256_file(reference_path),
        },
    }
    if any(audit["invariants"].values()):
        raise AssertionError(f"Leakage invariant failed: {audit['invariants']}")
    audit_path = output_root / "selection_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return audit


def retrieve_related_work(config: Dict[str, Any]) -> Dict[str, Any]:
    import requests

    output_root = Path(config["output_root"]).expanduser().resolve()
    destination = output_root / "frozen_related_work.jsonl"
    existing = {
        str(value["paper_id"]): value
        for value in iter_jsonl(destination)
    } if destination.is_file() else {}
    selected = list(iter_jsonl(output_root / "effectiveness_validation.jsonl"))
    selected += list(iter_jsonl(output_root / "memory_corpus.jsonl"))
    api_key = os.environ.get("S2_API_KEY", "")
    min_interval_seconds = float(os.environ.get("S2_MIN_INTERVAL_SECONDS", "1.1"))
    max_attempts = int(os.environ.get("S2_MAX_ATTEMPTS", "6"))
    if min_interval_seconds < 1.0:
        raise ValueError("S2_MIN_INTERVAL_SECONDS must be at least 1.0")
    if max_attempts < 1:
        raise ValueError("S2_MAX_ATTEMPTS must be positive")
    last_request_at = None
    for index, value in enumerate(selected, start=1):
        paper_id = str(value["paper_id"])
        if paper_id in existing and existing[paper_id].get("status") == "ok":
            continue
        title = str(value["title"])
        queries = semantic_scholar_query_candidates(title)
        papers: List[Dict[str, Any]] = []
        resolved_query = None
        for query in queries:
            papers, last_request_at = semantic_scholar_search(
                requests,
                query=query,
                limit=int(config["paper_context"]["max_retrieved_refs"]),
                api_key=api_key,
                min_interval_seconds=min_interval_seconds,
                max_attempts=max_attempts,
                last_request_at=last_request_at,
            )
            if papers:
                resolved_query = query
                break
        existing[paper_id] = {
            "paper_id": paper_id,
            "query": title,
            "queries_attempted": (
                queries
                if not papers
                else queries[: queries.index(resolved_query) + 1]
            ),
            "resolved_query": resolved_query,
            "retrieved_papers": papers,
            "empty_result_confirmed": not papers,
            "retrieval_policy": "s2-search-paced-v2",
            "status": "ok",
        }
        write_jsonl(destination, [existing[key] for key in sorted(existing)])
        result_note = f"{len(papers)} papers" if papers else "confirmed empty"
        print(
            f"[{index}/{len(selected)}] froze related work for "
            f"{paper_id} ({result_note})",
            flush=True,
        )
    return {
        "path": str(destination),
        "sha256": sha256_file(destination),
        "paper_count": len(existing),
    }


def build_bundles(config: Dict[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    related_path = output_root / "frozen_related_work.jsonl"
    if not related_path.is_file():
        raise FileNotFoundError(
            "Run retrieve-related before building frozen paper-context bundles"
        )
    related = {
        str(value["paper_id"]): value
        for value in iter_jsonl(related_path)
        if value.get("status") == "ok"
    }
    manifest_items = []
    for split_name, filename in (
        ("effectiveness_validation", "effectiveness_validation.jsonl"),
        ("memory_corpus", "memory_corpus.jsonl"),
    ):
        for selected in iter_jsonl(output_root / filename):
            paper_id = str(selected["paper_id"])
            if paper_id not in related:
                raise ValueError(f"Frozen related-work cache missing {paper_id}")
            source = Path(selected["markdown_source"]).resolve()
            if sha256_file(source) != selected["markdown_sha256"]:
                raise RuntimeError(f"Markdown source changed for {paper_id}")
            paper_dir = output_root / "paper_context" / split_name / paper_id
            paper_dir.mkdir(parents=True, exist_ok=True)
            frozen_markdown = paper_dir / "paper.md"
            shutil.copyfile(source, frozen_markdown)
            content_list = load_content_list(str(frozen_markdown))
            anchors = build_source_anchors(content_list)
            title = extract_title_from_content_list(content_list) or selected["title"]
            retrieved_papers = related[paper_id].get("retrieved_papers") or []
            last_memory = {
                "paper_md_path": str(frozen_markdown),
                "paper_title": title,
                "semantic_scholar_query": title,
                "semantic_scholar_queries": [title],
                "keyword_model_name": None,
                "retrieved_papers": retrieved_papers,
                "references_text": format_refs(retrieved_papers),
                "source_anchors": anchors,
                "frozen_context": True,
            }
            extractor_content = (
                "Here is the paper content in Markdown:\n\n"
                "=== BEGIN PAPER (MD) ===\n"
                + frozen_markdown.read_text(encoding="utf-8", errors="ignore")
                + "\n=== END PAPER (MD) ===\n\n"
                "=== SOURCE ANCHORS ===\n"
                + format_source_anchors(anchors)
                + "\n=== END SOURCE ANCHORS ===\n\n"
                "=== SEMANTIC SCHOLAR REFERENCES ===\n"
                + last_memory["references_text"]
                + "\n=== END REFERENCES ==="
            )
            bundle = {
                "design_version": DESIGN_VERSION,
                "paper_id": paper_id,
                "work_family": selected["work_family"],
                "split": split_name,
                "paper_markdown_relative_path": "paper.md",
                "paper_markdown_sha256": sha256_file(frozen_markdown),
                "last_memory": last_memory,
                "extractor_content": extractor_content,
                "contains_human_review_data": False,
            }
            bundle_path = paper_dir / "bundle.json"
            bundle_path.write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            manifest_items.append(
                {
                    "paper_id": paper_id,
                    "split": split_name,
                    "work_family": selected["work_family"],
                    "bundle_path": str(bundle_path),
                    "bundle_sha256": sha256_file(bundle_path),
                    "paper_markdown_sha256": sha256_file(frozen_markdown),
                }
            )
    manifest = {
        "design_version": DESIGN_VERSION,
        "contains_human_review_data": False,
        "frozen_related_work_sha256": sha256_file(related_path),
        "paper_count": len(manifest_items),
        "items": manifest_items,
    }
    path = output_root / "paper_context_manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("select", "retrieve-related", "build-bundles")
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    if args.command == "select":
        result = select_papers(config)
    elif args.command == "retrieve-related":
        result = retrieve_related_work(config)
    else:
        result = build_bundles(config)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
