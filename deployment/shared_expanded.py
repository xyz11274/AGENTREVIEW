"""Larger development search after measured throughput and integrity gates."""
import argparse
import copy
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json, read_jsonl, write_immutable_jsonl, value_sha256
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding, bound, runtime_identity
from agentreview.research_v2.shared_evolution import SharedEvolution
from agentreview.research_v2.development_coco import validate_assessment_receipt
from shared_acceptance import identity, HISTORY, COCO

ROOT = REPO / "artifacts/shared-throughput-20260917/expanded"


def prepare(base_protocol):
    config = read_json(base_protocol)
    records = {name: read_json(bound(ref))["records"] for name, ref in config["splits"].items()}
    used = {row["paper_id"] for rows in records.values() for row in rows}
    old = {p.stem: read_json(p) for p in sorted(HISTORY.glob("*.json"))}
    benchmark = {}
    for path in sorted((COCO / "benchmark").glob("benchmark_*.jsonl")):
        if path.stem not in {"benchmark_all_human_classify", "benchmark_left_out_human"}:
            benchmark.update({row["id"]: row for row in read_jsonl(path) if row["id"] in old})
    human = {row["id"]: row for row in read_jsonl(COCO / "benchmark/benchmark_all_human_classify.jsonl") if row["id"] in old}
    selection = read_json(REPO / "artifacts/deployment/original-coco/references/benchmark_reviewer_selection.json")
    buckets = []
    rng = random.Random(20260917)
    for low, high in ((12000, 35000), (35000, 50000), (50000, 90000)):
        values = sorted(pid for pid, paper in old.items() if pid not in used and pid in human and pid in selection
                        and pid in benchmark and low <= len(paper["text"]) < high)
        rng.shuffle(values)
        buckets.append(values)
    additional = [buckets[i % 3].pop() for i in range(11)]
    offsets = {"build": additional[:4], "static": additional[4:7], "rotating": additional[7:]}
    for split in ("build", "static", "rotating"):
        existing = {row["paper_id"]: read_json(bound(row["assets"]["paper"])) for row in records[split]}
        ids = [*existing, *offsets[split]]
        refs = None
        if split != "build":
            # Existing exposed acceptance paper may not be in the old inventory.
            prior_refs = read_json(bound(records[split][0]["assets"]["coco_reference"]))
            old_benchmark = {row["id"]: row for ref in prior_refs["benchmark_files"] for row in read_jsonl(bound(ref))}
            old_human = {row["id"]: row for row in read_jsonl(bound(prior_refs["complete_human_classify_file"]))}
            local_selection = {**selection, **read_json(bound(prior_refs["reviewer_selection_file"]))}
            bench = write_immutable_jsonl(ROOT / split / "benchmark.jsonl", [(old_benchmark if pid in old_benchmark else benchmark)[pid] for pid in ids])
            hum = write_immutable_jsonl(ROOT / split / "human.jsonl", [(old_human if pid in old_human else human)[pid] for pid in ids])
            sel = atomic_write_json(ROOT / split / "selection.json", {pid: local_selection[pid] for pid in ids})
            refs = atomic_write_json(ROOT / split / "references.json", {"protocol": "research-v2-coco-reference-manifest-v1",
                "partition": split, "paper_ids": ids, "benchmark_files": [binding(bench)],
                "complete_human_classify_file": binding(hum), "reviewer_selection_file": binding(sel)})
        rows = []
        for pid in ids:
            paper = copy.deepcopy(existing[pid] if pid in existing else old[pid])
            paper.setdefault("source", {}).update(source_partition=split, purpose="expanded development; historically exposed")
            path = atomic_write_json(ROOT / split / "papers" / f"{pid}.json", paper)
            assets = {"paper": binding(path)}
            if refs:
                assets["coco_reference"] = binding(refs)
            rows.append({**identity(paper), "source_partition": split, "assets": assets})
        manifest = atomic_write_json(ROOT / split / "manifest.json", {"protocol": "shared-memory-split-v1", "split": split, "records": rows})
        config["splits"][split] = binding(manifest)
    audit = atomic_write_json(ROOT / "family-audit.json", {"status": "complete",
        "method": "Development partition checks: exact IDs, normalized titles and content hashes. Cross-publication family linkage remains incomplete.",
        "split_manifest_sha256": {split: ref["sha256"] for split, ref in config["splits"].items()}})
    config["deduplication"]["family_audit"] = binding(audit)
    config.update(experiment_id="shared-memory-expanded-development-20260917", status="pending",
        claim_scope="development-only; historical exposure and exhaustive family audit incomplete; sealed test remains closed")
    config["evolution"].update(attempts=6, rotating_sample_size=2)
    gate = atomic_write_json(ROOT / "data-isolation-gate.json", {"formal_search_eligible": False,
        "reasons": ["Historical exposure inventory explicitly incomplete", "Family identities use normalized titles, not exhaustive version/family linkage",
                    "Build/static/rotating intentionally reuse already exposed development papers"],
        "sealed_test_opened": False, "sealed_manifest": config["splits"]["sealed_test"],
        "sizes": {"build": 8, "static": 6, "rotating": 8},
        "additional_selection": "Fixed seed 20260917; whole-paper character strata [12000,35000), [35000,50000), [50000,90000); no paper text truncated"})
    config["data_isolation_gate"] = binding(gate)
    return atomic_write_json(ROOT / "protocol.pending.json", config)


def run(protocol_path, gate_path):
    from throughput_profile import install
    gate = read_json(gate_path)
    if gate.get("status") != "complete" or gate.get("runtime") != runtime_identity():
        raise ValueError("Expanded search requires completed throughput/integrity/stability gates for this runtime")
    for ref in gate["full_flow_assessments"]:
        validate_assessment_receipt(bound(ref))
    protocol = load_experiment_protocol(protocol_path)
    if protocol.document.get("throughput_gate") != binding(gate_path):
        raise ValueError("Expanded search protocol must bind the completed tuning gate")
    os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(ROOT / "telemetry")
    event, stop, thread = install(ROOT / "telemetry")
    started = time.time()
    host = LocalSharedBackend(protocol=protocol, output_root=ROOT / "run")
    search = SharedEvolution(protocol=protocol, output_root=host.root, backend=host)
    atomic_write_json(ROOT / "launched.json", {"started": started, "pid": os.getpid(), "protocol": binding(protocol_path),
        "gate": binding(gate_path), "formal": False, "sealed_test_opened": False})
    try:
        state = search.run()
        if state["status"] != "complete":
            raise ValueError("Expanded search did not complete")
        winner = state["winner"]["winner"]
        host.frozen_snapshots[winner["candidate_id"]] = winner["identity"]["snapshots"]
        recipe = {**state["recipes"][winner["key"]], "key": "champion-development-final"}
        result = host.assess(recipe, epoch=search.epoch)
        host.validate(result, recipe=recipe, epoch=search.epoch)
        atomic_write_json(ROOT / "development-final.json", {"recipe": recipe, "result": result,
            "frozen_champion": binding(host.root / "winner.json"), "formal": False})
        for key, assessed in state["results"].items():
            host.validate(assessed, recipe=state["recipes"][key], epoch=search.epoch)
        unique = {}
        for key in state["pool"]:
            unique.setdefault(state["recipes"][key]["candidate"]["program_sha256"], []).append(key)
        reviews = sum(len(state["recipes"][key]["paper_ids"]) for key in state["results"]) + len(recipe["paper_ids"])
        seconds = time.time()-started
        # Keep repaired/archived failures visible; never report success-only
        # checkpoint contents as a zero-failure experiment.
        failures = []
        for path in host.root.rglob("assessment.json"):
            value = read_json(path)
            if value.get("status") != "complete":
                failures.append({"path": str(path), "status": value.get("status"), "failure": value.get("failure")})
        failures.extend({"path": str(path), **read_json(path)} for path in host.root.rglob("pipeline.failure.json"))
        failed_cohorts = set()
        for failure in failures:
            folder = Path(failure["path"]).parent
            while folder != host.root and not (folder / "recipe.json").is_file():
                folder = folder.parent
            failed_cohorts.add(str(folder))
        complete_cohorts = [path for path in (host.root / "assessments").glob("e*/*/r*/coco/assessment.json")
                            if read_json(path).get("status") == "complete"]
        atomic_write_json(ROOT / "complete.json", {"status": "complete", "formal": False, "sealed_test_opened": False,
            "seconds": seconds, "valid_reviews": reviews, "valid_reviews_per_hour": reviews*3600/seconds,
            "failure_records": failures, "failed_cohort_attempts": sorted(failed_cohorts),
            "failure_rate": len(failed_cohorts)/(len(failed_cohorts)+len(complete_cohorts)),
            "failure_rate_definition": "Failed cohort attempts / (failed + completed cohort attempts), with nested paper failures counted once.",
            "duplicate_program_groups": [ids for ids in unique.values() if len(ids)>1],
            "duplicate_handling": "Existing ID-based admission and softmax parent weights preserved; duplicate proposals consume attempts"})
    except BaseException as exc:
        atomic_write_json(ROOT / "run-failure.json", {"status": "failed", "ended": time.time(),
            "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(),
            "scoring_failure_policy": "No zero score or admission for incomplete work; diagnose before resuming",
            "formal": False, "sealed_test_opened": False})
        raise
    finally:
        stop.set()
        thread.join(timeout=12)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--gate")
    args = parser.parse_args()
    print(prepare(args.protocol) if args.action == "prepare" else run(args.protocol, args.gate))
