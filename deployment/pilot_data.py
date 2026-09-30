"""Build the reproducible 4/3/5/3 pilot dataset from the development corpus."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.experiment_protocol import _identity_keys
from agentreview.research_v2.shared_experiment import binding, bound

SPLIT_SEED = 20260929
COUNTS = {"build": 4, "static": 3, "rotating": 5, "sealed_test": 3}

def stratified(rows, count, label):
    """Reproducible venue/year-balanced ordering, independent of any scores."""
    def rank(value):
        return hashlib.sha256(f"{SPLIT_SEED}:{label}:{value}".encode()).hexdigest()
    groups = {}
    for row in rows:
        groups.setdefault(row["venue_year"], []).append(row)
    groups = {key: sorted(group, key=lambda row: rank(row["paper_id"]))
              for key, group in groups.items()}
    ordered = []
    while len(ordered) < count:
        for key in sorted(groups, key=rank):
            if groups[key]:
                ordered.append(groups[key].pop(0))
                if len(ordered) == count:
                    return ordered
        if not any(groups.values()):
            raise ValueError("Insufficient unique papers for the pilot")

def subset_jsonl(source, target, ids):
    rows = {str(row["id"]): row for row in map(json.loads, source.read_text().splitlines())}
    text = "".join(json.dumps(rows[pid], ensure_ascii=False) + "\n" for pid in ids)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text() != text:
        raise ValueError(f"Existing pilot input changed: {target}")
    target.write_text(text, encoding="utf-8")
    return binding(target)

def prepare(*, source_root, data_root):
    original = Path(source_root).resolve()
    data_root = Path(data_root).resolve()
    sources = {split: read_json(original / split / "manifest.json")["records"]
               for split in COUNTS}
    rotating_and_test = stratified(sources["rotating"], 8, "rotating_and_test")
    chosen = {"build": stratified(sources["build"], 4, "build"),
              "static": stratified(sources["static"], 3, "static"),
              "rotating": rotating_and_test[:5], "sealed_test": rotating_and_test[5:]}
    original_test_keys = {key for row in sources["sealed_test"] for key in _identity_keys(row)}
    pilot_keys = {key for rows in chosen.values() for row in rows for key in _identity_keys(row)}
    if pilot_keys & original_test_keys:
        raise ValueError("Pilot must not use any original sealed-test identity or family")
    split_bindings = {}
    for split, rows in chosen.items():
        origin = "rotating" if split == "sealed_test" else split
        directory = data_root / split
        ids = [row["paper_id"] for row in rows]
        reference = None
        if split != "build":
            reference = copy.deepcopy(read_json(original / origin / "scoring-only/references.json"))
            assets = directory / "scoring-only"
            reference.update(partition=split, paper_ids=ids)
            reference["benchmark_files"] = [subset_jsonl(
                bound(reference["benchmark_files"][0]), assets / "benchmark.jsonl", ids)]
            reference["complete_human_classify_file"] = subset_jsonl(
                bound(reference["complete_human_classify_file"]), assets / "human.jsonl", ids)
            selected = read_json(bound(reference["reviewer_selection_file"]))
            atomic_write_json(assets / "selection.json", {pid: selected[pid] for pid in ids})
            reference["reviewer_selection_file"] = binding(assets / "selection.json")
            atomic_write_json(assets / "references.json", reference)
            reference = binding(assets / "references.json")
        records = []
        for original_row in rows:
            row = copy.deepcopy(original_row)
            paper = read_json(bound(row["assets"]["paper"]))
            paper["source"].update(source_partition=split, pilot_origin_partition=origin)
            path = directory / "papers" / f"{row['paper_id']}.json"
            atomic_write_json(path, paper)
            row.update(source_partition=split, pilot_origin_partition=origin)
            row["assets"] = {"paper": binding(path)}
            if reference:
                row["assets"]["coco_reference"] = reference
            records.append(row)
        atomic_write_json(directory / "manifest.json", {
            "protocol": "shared-memory-split-v1", "split": split, "records": records})
        split_bindings[split] = binding(directory / "manifest.json")
    atomic_write_json(data_root / "family-audit.json", {
        "status": "complete", "method": "Seeded venue/year-balanced selection; full identity/family/content disjointness verified by protocol loader",
        "split_manifest_sha256": {key: value["sha256"] for key, value in split_bindings.items()},
        "original_sealed_test_identity_overlap": 0})
    atomic_write_json(data_root / "cohort.json", {"paper_ids": [r["paper_id"] for r in chosen["sealed_test"]],
        "purpose": "Pilot held-out cohort drawn from original rotating development, never the 1300-paper test",
        "strictly_unseen_claim": False})
    atomic_write_json(data_root / "allocation.json", {
        "split_seed": SPLIT_SEED, "counts": COUNTS,
        "selection": "Venue/year-balanced, SHA256 order; no score or text-length filtering",
        "sources": {split: binding(original / split / "manifest.json") for split in COUNTS},
        "paper_ids": {split: [row["paper_id"] for row in rows] for split, rows in chosen.items()},
        "original_sealed_test_identity_overlap": 0,
        "history": "Development assignment in earlier versions is retained in the historical inventory; pilot is not a historically unseen test"})
    return {"splits": split_bindings, "family_audit": binding(data_root / "family-audit.json"),
            "cohort": binding(data_root / "cohort.json"), "counts": COUNTS}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(source_root=args.source_root, data_root=args.data_root), indent=2))
