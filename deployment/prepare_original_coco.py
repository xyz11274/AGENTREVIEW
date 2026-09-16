"""Prepare archived reference assets and a separate development smoke deployment."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.development_coco import materialize_scoring_config
from agentreview.research_v2.io import atomic_write_json, file_sha256, write_immutable_jsonl
from agentreview.research_v2.models import Paper

ARCHIVE = "archive/coco-0915-0467b41"
SMOKE_ID = "-EHqoysUYLx"


def binding(path):
    return {"path": str(path), "sha256": file_sha256(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coco-root", type=Path, default=REPO.parent / "CoCoReviewBench")
    parser.add_argument("--output", type=Path, default=REPO / "artifacts/deployment/original-coco")
    parser.add_argument("--scoring-python", default="/data/xyj/envs/coco-original/bin/python")
    parser.add_argument("--serving-python", default="/data/xyj/envs/qwen38serve/bin/python")
    parser.add_argument("--judge-root", type=Path, default=Path("/data/xyj/models/Qwen3.8-27B"))
    parser.add_argument("--freeze-scoring", action="store_true")
    args = parser.parse_args()
    root, output = args.coco_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archived = subprocess.check_output(["git", "-C", str(root), "show", f"{ARCHIVE}:benchmark/benchmark_reviewer_selection.json"])
    selection = json.loads(archived)
    destination = root / "benchmark/benchmark_reviewer_selection.json"
    if destination.exists():
        if destination.read_bytes() != archived:
            raise ValueError("Existing reviewer selection differs from the archive; preserve it for audit")
    else:
        with destination.open("xb") as handle:
            handle.write(archived)
    refs = output / "references"
    refs.mkdir(exist_ok=True)
    frozen_selection = refs / "benchmark_reviewer_selection.json"
    if frozen_selection.exists():
        assert frozen_selection.read_bytes() == archived
    else:
        with frozen_selection.open("xb") as handle:
            handle.write(archived)
    # Validate archived choices against the actual original reference corpus.
    sys.path[:0] = [str(root), str(root / "src/evaluation")]
    import _4_evaluation as evaluation
    evaluation.benchmark_reviewer_selection_file = frozen_selection
    benchmarks = sorted((root / "benchmark").glob("benchmark_*_20*.jsonl"))
    choices = evaluation.left_out_one(benchmarks)
    if choices != selection:
        raise ValueError("Archived selection and benchmark identity sets differ")
    row = next(json.loads(line) for line in (root / "benchmark/benchmark_ICLR_2023.jsonl").read_text().splitlines()
               if json.loads(line)["id"] == SMOKE_ID)
    md_path = (root / row["MD_path"]).resolve()
    paper = Paper(SMOKE_ID, row["content"]["title"], row["content"]["abstract"],
                  md_path.read_text(), {"source_partition": "development", "md_source": binding(md_path),
                                        "historically_exposed": True, "purpose": "development acceptance only"})
    atomic_write_json(output / "paper.json", paper.to_dict())
    human = next(json.loads(line) for line in (root / "benchmark/benchmark_all_human_classify.jsonl").read_text().splitlines()
                 if json.loads(line)["id"] == SMOKE_ID)
    # Use a new immutable namespace; never regenerate reviewer choices per candidate.
    native_refs = output / "smoke-references"
    benchmark = write_immutable_jsonl(native_refs / "benchmark.jsonl", [row])
    human_path = write_immutable_jsonl(native_refs / "human.jsonl", [human])
    choice_path = atomic_write_json(native_refs / "selection.json", {SMOKE_ID: selection[SMOKE_ID]})
    atomic_write_json(native_refs / "references.json", {
        "protocol": "research-v2-coco-reference-manifest-v1", "partition": "development", "paper_ids": [SMOKE_ID],
        "benchmark_files": [binding(benchmark)], "complete_human_classify_file": binding(human_path),
        "reviewer_selection_file": binding(choice_path),
    })
    services = {
        "split": {"model": "Qwen3-8B-Split", "model_root": str(root / "model/split_model"), "port": 13099, "gpus": []},
        "classify": {"model": "Qwen3-8B-Classify", "model_root": str(root / "model/classify_model"), "port": 13098, "gpus": []},
        "judge": {"model": "qwen3.8-27b", "model_root": str(args.judge_root.resolve()), "port": 18001, "gpus": []},
    }
    for value in services.values():
        value["base_url"] = f"http://127.0.0.1:{value['port']}/v1"
    atomic_write_json(output / "services.pending.json", {"serving_python": args.serving_python,
        "state_root": str(output / "services"), "services": services})
    atomic_write_json(output / "reference-reuse.json", {
        "archive": ARCHIVE, "archive_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", ARCHIVE], text=True).strip(),
        "selection": binding(frozen_selection), "validated_paper_count": len(choices),
        "benchmark_sources": [binding(path) for path in benchmarks],
        "human_source": binding(root / "benchmark/benchmark_all_human_classify.jsonl"),
        "smoke_paper": binding(output / "paper.json"), "purpose": "independent development acceptance, not a formal experiment",
    })
    if args.freeze_scoring:
        bert = json.loads((output / "bert-model.json").read_text())
        config = materialize_scoring_config(coco_root=root, python_executable=args.scoring_python,
            output_path=output / "scoring.frozen.json", services=services,
            bert_score_model_root=bert["snapshot"], hf_home=bert["hf_home"], timeout_seconds=14400, max_stage_attempts=1)
        print("Frozen scoring identity:", config["config_id"])
    print("Prepared development assets:", output)


if __name__ == "__main__":
    main()
