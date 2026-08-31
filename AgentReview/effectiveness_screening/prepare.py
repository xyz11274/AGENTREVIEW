"""Prepare leakage-safe data for the frozen MemoryProgram effectiveness screen."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agentreview.paper_context import (
    CANONICAL_PAPER_CONTEXT_VERSION,
    DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    build_canonical_paper_context,
)
from agentreview.paper_review_player_md import format_refs
from agentreview.source_map import (
    build_source_anchors,
    extract_title_from_content_list,
    load_content_list,
)
from memory_evolution.agentreview_data import (
    _assign_work_families,
    _category_reference,
    _meta_reference,
)
from memory_evolution.assets import FOUR_ZONES, load_temporal_split_manifest
from memory_evolution.tasks import SPECIALIST_TASKS
from memory_evolution.toolkit import validate_frozen_s2_pool

from .activation import ActivationPending, require_activation

DESIGN_VERSION = "effectiveness-screening-v2-temporal"
STRATA = ("ICLR-accept", "ICLR-reject", "NeurIPS-accept", "NeurIPS-reject")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def category_reference_map(record: Mapping[str, Any], task_id: str) -> dict[str, str]:
    output: dict[str, str] = {}
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


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
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


def load_config(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if value.get("design_version") != DESIGN_VERSION:
        raise ValueError(f"Unsupported design version: {value.get('design_version')!r}")
    if value.get("metric_status") != "CoCo-style proxy screening":
        raise ValueError("The screening config must explicitly declare proxy status")
    value["_config_path"] = str(source)
    value["_config_sha256"] = sha256_file(source)
    value["_recorded_paths"] = {}
    external_roots = {}
    for name, recorded in dict(value.get("external_roots") or {}).items():
        candidate = Path(str(recorded)).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        external_roots[str(name)] = str(candidate.resolve())
    value["external_roots"] = external_roots
    for name in (
        "openreview_root",
        "evolution_data_root",
        "evolved_program_root",
        "output_root",
        "temporal_split_manifest",
        "frozen_s2_pool",
        "frozen_s2_pool_manifest",
    ):
        if not value.get(name):
            continue
        recorded = str(value.get(name) or "")
        value["_recorded_paths"][name] = recorded
        if recorded.startswith("external://"):
            root_name = recorded[len("external://") :].strip("/")
            if root_name not in external_roots:
                raise KeyError(f"External root is not configured: {root_name}")
            value[name] = external_roots[root_name]
            continue
        candidate = Path(recorded).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        value[name] = str(candidate.resolve())
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


def reviewer_overall_scores(record: Mapping[str, Any]) -> list[float]:
    scores: list[float] = []
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


def markdown_index(openreview_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
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
    markdown_by_id: Mapping[str, Path],
    allowed_paper_ids: set[str] | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if str(record.get("id") or "") not in set(allowed_paper_ids or set()):
        reasons.append("outside_frozen_temporal_development_zones")
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
    candidates: Sequence[dict[str, Any]],
    *,
    seed: int,
    per_stratum: int,
    controversial_total: int | None,
) -> list[dict[str, Any]]:
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_stratum[candidate["stratum"]].append(candidate)
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
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


def select_papers(config: dict[str, Any]) -> dict[str, Any]:
    openreview_root = Path(config["openreview_root"]).expanduser().resolve()
    evolution_data_root = Path(config["evolution_data_root"]).expanduser().resolve()
    output_root = Path(config["output_root"]).expanduser().resolve()
    require_activation(
        config,
        "selection",
        {
            "openreview_train": openreview_root / "train_clean.jsonl",
            "openreview_test": openreview_root / "test.jsonl",
            "temporal_split_manifest": config["temporal_split_manifest"],
            "evolution_data_root": {
                "path": evolution_data_root,
                "type": "directory",
            },
        },
    )
    if (output_root / "design_lock.json").exists():
        raise RuntimeError("Refusing to resample after the design lock was written")
    train_path = openreview_root / "train_clean.jsonl"
    test_path = openreview_root / "test.jsonl"
    train = list(iter_jsonl(train_path))
    test = list(iter_jsonl(test_path))
    _, temporal_zones = load_temporal_split_manifest(config["temporal_split_manifest"])
    temporal_entries = {
        str(value["paper_id"]): value
        for zone in (FOUR_ZONES[0], FOUR_ZONES[2])
        for value in temporal_zones[zone]
    }
    families = _assign_work_families(train + test)
    train_families = families[: len(train)]
    id_to_family = {
        str(record.get("id") or ""): family
        for record, family in zip(train + test, families)
    }
    evolution_ids = collect_evolution_paper_ids(evolution_data_root)
    evolution_families = {
        id_to_family[paper_id] for paper_id in evolution_ids if paper_id in id_to_family
    }
    md_index = markdown_index(openreview_root)
    rejection_counts: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    records_by_id: dict[str, dict[str, Any]] = {}
    for record, family in zip(train, train_families):
        eligible, reasons = _eligible(
            record,
            family,
            markdown_by_id=md_index,
            allowed_paper_ids=set(temporal_entries),
        )
        if not eligible:
            rejection_counts.update(reasons)
            continue
        paper_id = str(record.get("id") or "")
        family = str(temporal_entries[paper_id]["paper_family"])
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
            "temporal_zone": temporal_entries[paper_id]["zone"],
            "paper_review_cutoff": temporal_entries[paper_id]["paper_review_cutoff"],
        }
        candidates.append(candidate)
        records_by_id[paper_id] = record

    per_stratum = int(config["sampling"]["papers_per_stratum"])
    effectiveness = _sample_stratified(
        [value for value in candidates if value["temporal_zone"] == FOUR_ZONES[2]],
        seed=int(config["sampling"]["effectiveness_seed"]),
        per_stratum=per_stratum,
        controversial_total=int(config["sampling"]["controversial_total"]),
    )
    effectiveness_families = {value["work_family"] for value in effectiveness}
    memory_candidates = [
        value for value in candidates if value["temporal_zone"] == FOUR_ZONES[0]
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
            category_reference_by_tag.update(category_reference_map(record, task_id))
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
            "temporal_split_manifest": config["temporal_split_manifest"],
            "temporal_split_manifest_sha256": sha256_file(
                Path(config["temporal_split_manifest"])
            ),
            "evolution_data_root": str(evolution_data_root),
        },
        "counts": {
            "train_records": len(train),
            "test_records": len(test),
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
            "effectiveness_outside_C": sum(
                value["temporal_zone"] != FOUR_ZONES[2] for value in effectiveness
            ),
            "memory_outside_A": sum(
                value["temporal_zone"] != FOUR_ZONES[0] for value in memory
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


def audit_selected_s2_pool(
    config: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate the selected A/C subset against the global Frozen S2 v2 asset."""
    selected_by_id = {str(value["paper_id"]): value for value in selected}
    return validate_frozen_s2_pool(
        str(config["frozen_s2_pool"]),
        expected_cutoffs={
            paper_id: str(value["paper_review_cutoff"])
            for paper_id, value in selected_by_id.items()
        },
        required_ids=set(selected_by_id),
        expected_pool_version=str(config["frozen_s2_pool_version"]),
        expected_queries={
            paper_id: str(value["title"]) for paper_id, value in selected_by_id.items()
        },
    )


def build_bundles(config: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    related_path = Path(config["frozen_s2_pool"]).expanduser().resolve()
    require_activation(
        config,
        "bundle_generation",
        {
            "effectiveness_selection": output_root / "effectiveness_validation.jsonl",
            "memory_corpus_selection": output_root / "memory_corpus.jsonl",
            "frozen_s2_pool": related_path,
            "frozen_s2_pool_manifest": config["frozen_s2_pool_manifest"],
        },
    )
    if not related_path.is_file():
        raise FileNotFoundError(
            f"Configured Frozen S2 v2 asset is required: {related_path}"
        )
    selected_by_split = {
        split_name: list(iter_jsonl(output_root / filename))
        for split_name, filename in (
            ("effectiveness_validation", "effectiveness_validation.jsonl"),
            ("memory_corpus", "memory_corpus.jsonl"),
        )
    }
    selected_all = [
        value for split_values in selected_by_split.values() for value in split_values
    ]
    s2_audit = audit_selected_s2_pool(config, selected_all)
    if s2_audit.get("passed") is not True:
        raise RuntimeError(
            "Frozen S2 v2 selection audit failed: "
            + json.dumps(s2_audit, ensure_ascii=False, sort_keys=True)
        )
    related = {
        str(value["paper_id"]): value
        for value in iter_jsonl(related_path)
        if value.get("status") == "completed"
    }
    manifest_items = []
    for split_name, split_values in selected_by_split.items():
        for selected in split_values:
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
                "s2_usage": {
                    "available_as_frozen_capability": True,
                    "used": False,
                    "default_injection": False,
                },
                "paper_review_cutoff": selected["paper_review_cutoff"],
            }
            venue = str(selected.get("conference") or selected.get("venue") or "")
            year_match = re.search(r"(?:19|20)\d{2}", venue)
            canonical = build_canonical_paper_context(
                str(frozen_markdown),
                paper_id=paper_id,
                title=title,
                venue=venue,
                year=str(
                    selected.get("year") or (year_match.group(0) if year_match else "")
                ),
                reviewer_role="SPECIALIST",
                max_chars=int(
                    config.get("generation", {}).get(
                        "paper_context_max_chars",
                        DEFAULT_PAPER_CONTEXT_MAX_CHARS,
                    )
                ),
                internal_provenance={
                    "split": split_name,
                    "source_path": str(source),
                },
            )
            last_memory["source_anchors"] = canonical.source_anchors
            last_memory["candidate_visible_metadata"] = canonical.candidate_metadata
            last_memory["paper_context_version"] = canonical.version
            bundle = {
                "design_version": DESIGN_VERSION,
                "paper_id": paper_id,
                "work_family": selected["work_family"],
                "split": split_name,
                "paper_markdown_relative_path": "paper.md",
                "paper_markdown_sha256": sha256_file(frozen_markdown),
                "last_memory": last_memory,
                "extractor_content": canonical.text,
                "candidate_visible_metadata": canonical.candidate_metadata,
                "paper_context_version": CANONICAL_PAPER_CONTEXT_VERSION,
                "paper_review_cutoff": selected["paper_review_cutoff"],
                "temporal_zone": selected["temporal_zone"],
                "s2_default_injection": False,
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
        "frozen_s2_pool": str(related_path),
        "frozen_s2_pool_version": str(config["frozen_s2_pool_version"]),
        "frozen_s2_pool_sha256": sha256_file(related_path),
        "frozen_s2_selection_audit": s2_audit,
        "s2_default_injection": False,
        "paper_context_version": CANONICAL_PAPER_CONTEXT_VERSION,
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
    parser.add_argument("command", choices=("select", "build-bundles"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    try:
        if args.command == "select":
            result = select_papers(config)
        else:
            result = build_bundles(config)
    except ActivationPending as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
