"""Evidence-based use inventory for a paired rerun; never edits old artifacts."""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
BASE = REPO.parent
DATA = REPO / "data/original"
ROOT = REPO / "artifacts/paired-benchmark-20260917"
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.data import canonical_title, unwrap_value
from agentreview.research_v2.experiment_protocol import normalized_content_sha256
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256


def main():
    sources = {}
    uses = collections.defaultdict(list)
    papers = {}
    metadata = collections.defaultdict(dict)
    errors = []

    def binding(path):
        path = Path(path).resolve()
        key = str(path)
        if key not in sources:
            sources[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"path": key, "sha256": sources[key]}

    def add(pid, purpose, path, locator, **extra):
        pid = str(pid).split("::")[0]
        if not pid:
            raise ValueError("Empty use identity")
        record = {"purpose": purpose, "evidence": binding(path), "locator": locator, **extra}
        if record not in uses[pid]:
            uses[pid].append(record)

    names = {"build": "canonical_review_corpus", "static": "evolution_static", "rotating": "evolution_rotating_pool"}
    original = {}
    for split, name in names.items():
        path = DATA / "splits" / f"{name}.json"
        original[split] = binding(path)
        rows = read_json(path)["records"]
        for i, row in enumerate(rows):
            pid = row["paper_id"]
            papers[pid] = read_json(DATA / "papers" / f"{pid}.json")
            metadata[pid]["paperhash"] = row.get("paperhash")
            add(pid, f"original_{split}_reserved_development", path, f"records[{i}]")
    test_path = DATA / "coco_1300/manifest.json"
    original["test_basis"] = binding(test_path)
    test = read_json(test_path)["records"]
    ordered_test = [row["paper_id"] for row in test]
    assert len(ordered_test) == len(set(ordered_test)) == 1300
    for pid in ordered_test:
        papers[pid] = read_json(DATA / "coco_1300/papers" / f"{pid}.json")

    # This is an explicitly reserved development cohort, even where the paused
    # run has not yet evaluated every rotating item. Do not assert it did.
    small_ids = set()
    for split in names:
        path = REPO / "artifacts/shared-throughput-20260917/expanded" / split / "manifest.json"
        for i, row in enumerate(read_json(path)["records"]):
            pid = row["paper_id"]
            small_ids.add(pid)
            papers.setdefault(pid, read_json(row["assets"]["paper"]["path"]))
            add(pid, f"current_small_run_{split}_development", path, f"records[{i}]",
                evidence_scope="Development assignment; execution must be read from separate receipts, not inferred from this manifest")

    # Explicit root-cause diagnostic corpus used to change Meta's generation
    # contract, as recorded in SYSTEM_REASSESSMENT section 3. Successful scored
    # papers and merely viewed historical score tables are not excluded for that.
    diagnostic = REPO / "docs/audit/20260915/classify_failure_audit.json"
    diagnostic_ids = set()
    for arm, result in read_json(diagnostic)["arms"].items():
        for i, pid in enumerate(result["failed_paper_ids"]):
            diagnostic_ids.add(pid)
            add(pid, "system_modification_classify_failure_diagnostic", diagnostic,
                f"arms.{arm}.failed_paper_ids[{i}]",
                decision_evidence=binding(REPO / "docs/audit/20260915/SYSTEM_REASSESSMENT.md"),
                decision_locator="section 3 and 3.2: segmentation root cause and Meta generation contract")

    # Historical data files alone prove preparation, not consumption. Only
    # committed memory checkpoints or generated search-run candidate reviews
    # establish actual use here. Keep old uses visible despite fresh reset now.
    historical_ids = set()
    scanned_runs = []
    roots = [BASE / n for n in ("AgentReview_0811", "AgentReview_0811_new", "AgentReview_0827",
             "AgentReview_0901_parallel", "recovered_formal_qual_20260827",
             "backup_formal_qual_seed0_20260825_20260827_214826") if (BASE / n).exists()]
    files = subprocess.check_output(["rg", "--files", "-uuu", "-g", "*.json", "-g", "candidate_reviews.jsonl", *map(str, roots)], text=True).splitlines()
    for name in sorted(files):
        path = Path(name)
        if path.name == "candidate_reviews.jsonl":
            # A search controller or a formal search configuration is required;
            # final-only benchmark evaluation is not treated as search feedback.
            if not (path.parent / "config.json").is_file():
                continue
            config = read_json(path.parent / "config.json")
            if "iterations" not in config or "episode_budget" not in config:
                continue
            binding(path.parent / "config.json")
            scanned_runs.append(str(path.parent))
            with path.open() as stream:
                for line, text in enumerate(stream, 1):
                    if not text.strip():
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        errors.append({"path": str(path), "line": line, "reason": "incomplete historical JSONL"})
                        continue
                    pid = row.get("paper_id")
                    if pid:
                        historical_ids.add(pid)
                        add(pid, "historical_search_candidate_evaluation", path, f"line:{line}")
        elif "checkpoints" in path.parts and path.stat().st_size > 0:
            try:
                row = read_json(path)
            except (ValueError, OSError):
                errors.append({"path": str(path), "reason": "unreadable historical checkpoint"})
                continue
            if not isinstance(row, dict) or row.get("checkpoint_version") != "historical-replay-checkpoint-v1":
                continue
            if not isinstance(row.get("memory_snapshot"), dict):
                continue
            for i, pid in enumerate(row.get("completed_paper_ids", [])):
                historical_ids.add(str(pid).split("::")[0])
                add(pid, "historical_committed_memory_build", path, f"completed_paper_ids[{i}]")

    # Metadata-only inventory. No human reference text is sent to generation.
    benchmark_ids = set()
    coco = BASE / "CoCoReviewBench/benchmark"
    for path in sorted(coco.glob("benchmark_*.jsonl")):
        if path.name in {"benchmark_all_human_classify.jsonl", "benchmark_left_out_human.jsonl"}:
            continue
        binding(path)
        with path.open() as stream:
            for text in stream:
                row = json.loads(text)
                pid = row["id"]
                benchmark_ids.add(pid)
                content = row.get("content", {})
                metadata[pid].update(title=str(unwrap_value(content.get("title", ""))),
                    paperhash=str(unwrap_value(content.get("paperhash", ""))),
                    forum=row.get("forum"), original=row.get("original"))

    # Reuse and verify the pre-existing version-family inventory, then union it
    # with canonical title, full-content and paperhash matches for original275.
    family_root = BASE / "AgentReview_0811_new/AgentReview/configs/coco_temporal_split_v2"
    family_manifest = read_json(family_root / "manifest.json")
    family_file = family_root / family_manifest["families"]["path"]
    assert binding(family_file)["sha256"] == family_manifest["families"]["sha256"]
    binding(family_root / "manifest.json")
    parents = {pid: pid for pid in set(metadata) | set(papers) | set(uses)}

    def find(pid):
        parents.setdefault(pid, pid)
        if parents[pid] != pid:
            parents[pid] = find(parents[pid])
        return parents[pid]

    links = []

    def union(a, b, reason):
        if find(a) != find(b):
            parents[find(b)] = find(a)
            links.append({"paper_ids": [a, b], "reason": reason})

    with family_file.open() as stream:
        for text in stream:
            row = json.loads(text)
            ids = [p["paper_id"] for p in row["source_provenance"]]
            for pid in ids[1:]:
                union(ids[0], pid, "frozen_coco_paper_family")
    seen = {}
    for pid in sorted(parents):
        paper = papers.get(pid, {})
        meta = metadata[pid]
        title = paper.get("title", meta.get("title", ""))
        keys = [("canonical_title", canonical_title(title)), ("paperhash", meta.get("paperhash"))]
        if paper.get("text"):
            keys.append(("normalized_full_text", normalized_content_sha256(paper["text"])))
        for key in keys:
            if not key[1]:
                continue
            if key in seen:
                union(pid, seen[key], key[0])
            seen[key] = pid
        for k in ("forum", "original"):
            if isinstance(meta.get(k), str) and meta[k] in parents:
                union(pid, meta[k], k)
    families = collections.defaultdict(list)
    for pid in parents:
        families[find(pid)].append(pid)
    family_uses = collections.defaultdict(list)
    for pid in uses:
        family_uses[find(pid)].append(pid)
    excluded = [pid for pid in ordered_test if family_uses[find(pid)]]
    retained = [pid for pid in ordered_test if not family_uses[find(pid)]]
    def identity(pid):
        paper = papers[pid]
        versions = sorted(families[find(pid)])
        return {"paper_id": pid, "title": paper["title"],
                "content_sha256": hashlib.sha256(paper["text"].encode()).hexdigest(),
                "normalized_content_sha256": normalized_content_sha256(paper["text"]),
                "work_family": "paired-family-" + value_sha256(versions), "version_ids": versions}

    # Review-reference availability is a distinct prerequisite, not leakage.
    human_path = coco / "benchmark_all_human_classify.jsonl"
    binding(human_path)
    with human_path.open() as stream:
        human_ids = {json.loads(text)["id"] for text in stream if text.strip()}
    development_refs = {}
    for split, ref in original.items():
        if split not in {"static", "rotating"}:
            continue
        ids = [row["paper_id"] for row in read_json(ref["path"])["records"]]
        development_refs[split] = {"required": len(ids), "benchmark_rows_present": len(set(ids) & benchmark_ids),
            "classified_human_rows_present": len(set(ids) & human_ids),
            "missing_ids": [pid for pid in ids if pid not in benchmark_ids or pid not in human_ids]}
    # Original documents and bodies remain untouched. New manifests retain their
    # original order and provenance rather than resampling convenient survivors.
    ROOT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ROOT / "audit/use-inventory.json", {"protocol": "paired-benchmark-actual-use-inventory-v1",
        "records": [{"paper_id": pid, "evidence": uses[pid]} for pid in sorted(uses)],
        "errors": errors, "scanned_historical_search_runs": sorted(set(scanned_runs))}, immutable=False)
    atomic_write_json(ROOT / "audit/exclusions.json", {"records": [
        {**identity(pid), "direct_use": uses.get(pid, []), "linked_development_ids": sorted(family_uses[find(pid)])}
        for pid in excluded]}, immutable=False)
    atomic_write_json(ROOT / "audit/families.json", {"links": links, "families": [sorted(v) for v in families.values() if len(v)>1],
        "method": "Verified frozen CoCo family inventory plus canonical title, paperhash, explicit forum/original links and normalized full text",
        "limitations": "Undocumented renaming/revision without an identity link can remain unknown; no claim of strictly unseen test"}, immutable=False)
    atomic_write_json(ROOT / "audit/identities.json", {"records": [identity(pid) for pid in papers]}, immutable=False)
    atomic_write_json(ROOT / "final-cohort.selection.json", {"protocol": "paired-existing-benchmark-cohort-v1",
        "original_manifest": original["test_basis"], "paper_ids": retained,
        "excluded_paper_ids": excluded, "count": len(retained), "resampled": False,
        "claim_scope": "Paired comparison on an existing benchmark; not a strictly unseen blind test"}, immutable=False)
    stats = {"original": original, "sizes": {"build":125,"static":25,"rotating":125,"test_basis":1300,"final":len(retained)},
        "current_small_development_in_test": sorted(small_ids & set(ordered_test)),
        "diagnostic_development_in_test": len(diagnostic_ids & set(ordered_test)),
        "historical_actual_development_in_test": len(historical_ids & set(ordered_test)),
        "excluded_unique": len(excluded), "family_only_exclusions": [pid for pid in excluded if pid not in uses],
        "development_reference_availability": development_refs,
        "historical_unknowns": ["Only locally retained committed memory checkpoints and actual search candidate reviews prove older consumption; absent records are unknown.",
            "Prepared datasets, historical evaluation alone, viewed score summaries and mechanical text-integrity checks do not alone prove selection/reflection use.",
            "Classify-failure IDs were used in documented root-cause analysis motivating the Meta change; successes were not excluded just for being evaluated."],
        "rules": {"no_old_memory_or_candidates":True,"no_historical_scores_as_controls":True,"original_manifests_unchanged":True},
        "sources": [{"path":p,"sha256":h} for p,h in sorted(sources.items())], "errors":errors}
    atomic_write_json(ROOT / "audit/summary.json", stats, immutable=False)
    print(json.dumps({k:v for k,v in stats.items() if k in {"sizes","excluded_unique","diagnostic_development_in_test","historical_actual_development_in_test","family_only_exclusions"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
