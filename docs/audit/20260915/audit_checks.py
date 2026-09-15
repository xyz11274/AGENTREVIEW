"""Reproduce historical input, score, and program checks without model calls."""

import ast
import hashlib
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
WORKSPACE = REPO.parent
HISTORY = WORKSPACE / "AgentReview_0831/artifacts/research_v2"
sys.path.insert(0, str(REPO / "AgentReview"))

from agentreview.research_v2.models import Paper
from agentreview.research_v2.review_preparation import frozen_paper_context


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def nonplan_ast(entry):
    tree = ast.parse(Path(entry["program_path"]).read_text(encoding="utf-8"))
    return [
        ast.dump(node, include_attributes=False)
        for node in tree.body
        if not (isinstance(node, ast.FunctionDef) and node.name == "plan_review")
    ]


def main():
    report = {"scope": "Historical diagnosis; no generation or evaluator requests"}
    input_stats = {}
    for name, folder in (
        ("development", HISTORY / "data/papers"),
        ("coco1300", HISTORY / "data/coco_1300/papers"),
    ):
        papers = [read(p) for p in sorted(folder.glob("*.json"))]
        input_stats[name] = {
            "papers": len(papers),
            "empty_text": sum(not p["text"].strip() for p in papers),
            "source_text_over_60000_chars": sum(len(p["text"]) > 60000 for p in papers),
            "canonical_context_clipped": sum(
                "paper markdown clipped by canonical builder"
                in frozen_paper_context(Paper.from_dict(p), "QUAL")
                for p in papers
            ),
        }
        if name == "development":
            source_paths = [Path(p["source"]["md_source_path"]) for p in papers]
            input_stats[name]["source_files_present"] = sum(p.is_file() for p in source_paths)
            input_stats[name]["source_hash_matches"] = sum(
                p.is_file()
                and hashlib.sha256(p.read_bytes()).hexdigest() == d["source"]["md_sha256"]
                for p, d in zip(source_paths, papers)
            )
    native = HISTORY / "qwen38_27b_native_coco_common902_v1"
    native_stats = {"papers": 0, "source_present": 0, "same_source_text": 0, "agentreview_clipped": 0}
    for shard in sorted((native / "shards").glob("shard_*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            source = Path(record["MD_path"])
            if not source.is_absolute():
                source = WORKSPACE / "CoCoReviewBench" / source
            frozen = read(HISTORY / "data/coco_1300/papers" / f"{record['id']}.json")
            native_stats["papers"] += 1
            native_stats["source_present"] += source.is_file()
            native_stats["same_source_text"] += source.is_file() and source.read_text(encoding="utf-8") == frozen["text"]
            native_stats["agentreview_clipped"] += "paper markdown clipped by canonical builder" in frozen_paper_context(Paper.from_dict(frozen), "QUAL")
    native_stats["native_recorded_truncations"] = sum(
        bool(json.loads(line).get("truncation"))
        for shard in sorted((native / "formal").glob("shard_*.jsonl"))
        for line in shard.read_text(encoding="utf-8").splitlines()
    )
    input_stats["native_paired_input"] = native_stats
    report["inputs"] = input_stats
    report["evolution"] = {}
    for path in sorted((HISTORY / "evolution").glob("*/formal-evolution-v1/pool.json")):
        entries = read(path)["entries"]
        seeds = {e["seed_kind"]: e for e in entries if e["candidate_id"].startswith("seed-")}
        children = [e for e in entries if not e["candidate_id"].startswith("seed-")]
        report["evolution"][path.parts[-3]] = {
            "entries": len(entries),
            "source_sha_matches": sum(hashlib.sha256(Path(e["program_path"]).read_bytes()).hexdigest() == e["program_sha256"] for e in entries),
            "successful_children": len(children),
            "unchanged_nonplan_ast": sum(nonplan_ast(e) == nonplan_ast(seeds[e["seed_kind"]]) for e in children),
        }
    report["historical_table1"] = {
        p.parent.name: read(p)["table_1_summary"]
        for p in sorted((HISTORY / "preliminary_common902").glob("*/evaluation_common902.json"))
    }
    report["historical_table1"]["native"] = read(native / "formal/evaluation_primary_native_successful.json")["table_1_summary"]
    target = Path(__file__).with_suffix(".json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"inputs": report["inputs"], "evolution": report["evolution"], "output": str(target)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
