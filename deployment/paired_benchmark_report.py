"""Paired Paper. deltas from validated final receipts, without score imputation."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound
from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.experiment_report import generate_experiment_report


def paired_summary(champion, control, families, *, seed, resamples):
    """Sample work families jointly, retaining paper weighting inside each draw."""
    import numpy as np
    if type(resamples) is not int or resamples < 1:
        raise ValueError("Bootstrap resamples must be a positive integer")
    if not champion or set(champion) != set(control) or set(champion) != set(families):
        raise ValueError("Paired comparison requires the same complete, nonempty paper cohort")
    if not all(type(v) in (float, int) and math.isfinite(v) for arm in (champion, control) for v in arm.values()):
        raise ValueError("Incomplete or nonfinite scores cannot be imputed")
    ids = list(champion)
    deltas = {pid: champion[pid] - control[pid] for pid in ids}
    grouped = {}
    for pid in ids:
        grouped.setdefault(families[pid], []).append(deltas[pid])
    sums = np.asarray([sum(x) for x in grouped.values()], dtype=float)
    sizes = np.asarray([len(x) for x in grouped.values()], dtype=float)
    rng = np.random.default_rng(seed)
    estimates = []
    for start in range(0, resamples, 256):
        draws = rng.integers(0, len(grouped), size=(min(256,resamples-start),len(grouped)))
        estimates.extend((sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)).tolist())
    return {"papers":len(ids), "families":len(grouped), "champion_mean":sum(champion.values())/len(ids),
        "control_mean":sum(control.values())/len(ids), "mean_gain":sum(deltas.values())/len(ids),
        "confidence_interval_95":np.quantile(estimates,[0.025,0.975]).tolist(),
        "wins":sum(v>1e-12 for v in deltas.values()),"ties":sum(abs(v)<=1e-12 for v in deltas.values()),
        "losses":sum(v < -1e-12 for v in deltas.values()),"per_paper_delta":deltas,
        "bootstrap_seed":seed,"bootstrap_resamples":resamples,"method":"paired family-cluster bootstrap"}


def report(root):
    root = Path(root)
    state = read_json(root / "final/final-test.json")
    protocol = read_json(root / "protocol.frozen.json")
    frozen = read_json(root / "search/winner.json")
    design = protocol["evaluation_design"]
    if state.get("status") != "complete" or state.get("freeze_id") != frozen["freeze_id"]:
        raise ValueError("Primary paired report requires a completed frozen final evaluation")
    if set(state["results"]) != set(design["arms"]):
        raise ValueError("Every prespecified comparison arm must complete")
    ids = read_json(bound(design["cohort_selection"]))["paper_ids"]
    rows = read_json(bound(protocol["splits"]["sealed_test"]))["records"]
    families = {row["paper_id"]:row["work_family"] for row in rows}
    annotations = (read_json(bound(design["historical_use_annotations"]))["records"]
        if design.get("historical_use_annotations") else [])
    prior_uses = {row["paper_id"]:row for row in annotations}
    scores = {}
    receipts = {}
    for key, result in state["results"].items():
        receipt = validate_assessment_receipt(bound(result["assessment"]), expected_paper_ids=ids,
            candidate_identity=result["identity"], expected_partition="sealed_test", expected_epoch=result["evaluation_epoch"])
        if receipt.get("review_target") != "final" or result.get("role_diagnostics"):
            raise ValueError("Only complete final reviews belong in this paired experiment")
        values = {row["paper_id"]: read_json(bound(row))["metrics"]["Paper."] for row in receipt["papers"]}
        if set(values) != set(ids):
            raise ValueError("Final score coverage must equal the frozen cohort")
        scores[key] = {pid:values[pid] for pid in ids}
        receipts[key] = result["assessment"]
    seeds = [key for key in design["arms"] if key.startswith("seed-")]
    best_seed = max(seeds, key=lambda k:frozen["assessments"][k]["static_score"])
    matched = "no-memory-" + frozen["winner"]["seed"]
    uncertainty = design["uncertainty"]
    comparisons = {key:paired_summary(scores["champion"],values,families,
        seed=uncertainty["seed"],resamples=uncertainty["resamples"]) for key,values in scores.items() if key!='champion'}
    output = root / "report"
    output.mkdir(exist_ok=True)
    path = output / "paired-paper-deltas.csv"
    columns = ["paper_id","work_family","documented_prior_development_use",*scores,*["champion_minus_"+k for k in comparisons]]
    with path.open("w",newline="",encoding="utf-8") as stream:
        writer = csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        for pid in ids:
            writer.writerow({"paper_id":pid,"work_family":families[pid],"documented_prior_development_use":pid in prior_uses,
                **{key:values[pid] for key,values in scores.items()},
                **{"champion_minus_"+key:value["per_paper_delta"][pid] for key,value in comparisons.items()}})
    valid = 0
    failed = []
    for parent in (root / "search", root / "final"):
        for path in parent.rglob("assessment.json"):
            receipt = read_json(path)
            if receipt.get("children"):
                continue
            if receipt.get("status") == "complete":
                validate_assessment_receipt(path)
                valid += receipt["complete_count"]
            else:
                failed.append({"path":str(path),"status":receipt.get("status"),"failure":receipt.get("failure")})
    # Generation can fail before any scoring receipt exists. Telemetry provides
    # whole-paper attempts; include explicit coverage limitations for serial arms.
    end_events = []
    for path in (root / "telemetry").glob("*events.jsonl"):
        with path.open() as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("stage") == "paper_full_flow" and row.get("event") == "end":
                    end_events.append(row)
    failures = sum(row.get("status") != "complete" for row in end_events)
    elapsed = time.time() - read_json(root / "launched.json")["started"]
    active_timing = root / "phase-timings.json"
    if active_timing.exists():
        elapsed = sum(read_json(active_timing).values())
    summary = {"protocol":"paired-paper-effect-report-v1","status":"complete","cohort_count":len(ids),
        "strictly_unseen_claim":False,"frozen_system":binding(root / "search/winner.json"),"receipts":receipts,
        "primary_control_keys":[best_seed,matched],"best_fixed_seed_selection":"static development Paper. only",
        "comparisons":{k:{f:v for f,v in x.items() if f!='per_paper_delta'} for k,x in comparisons.items()},
        "uncertainty_scope":uncertainty["scope"],"scores_source":"Fresh generation and original CoCo in this run",
        "documented_prior_use_count":len(prior_uses), "prior_uses_excluded":False,
        "diagnostic_case_evidence":{pid:{"prior_use":prior_uses.get(pid), "champion_score":scores["champion"][pid],
            "deltas":{key:comparison["per_paper_delta"][pid] for key,comparison in comparisons.items()},
            "receipt_sources":receipts} for pid in ids},
        "throughput":{"validated_complete_reviews":valid,"end_to_end_seconds_including_build":elapsed,
            "elapsed_scope":"sum of active search, development-final and final-evaluation phase durations" if active_timing.exists() else "wall time since launch",
            "valid_reviews_per_hour":valid*3600/elapsed,"failed_scoring_receipts":failed,
            "instrumented_paper_attempts":len(end_events),"instrumented_paper_failure_rate":failures/len(end_events) if end_events else None,
            "failure_rate_scope":"Instrumented whole-paper pipelines; serial Native failures remain in receipts and final state, never zero-imputed"}}
    atomic_write_json(output / "paired-summary.json", summary)
    generate_experiment_report(manifest_path=root / "final/report-input.json",output_dir=output / "coco-metrics",stage="final")
    return summary


if __name__ == "__main__":
    print(json.dumps(report(sys.argv[1]),ensure_ascii=False,indent=2))
