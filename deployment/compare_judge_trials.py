"""Compare all completed fixed-input diagnostics; preserve every observation."""
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.development_coco import validate_assessment_receipt, validate_stage_integrity
from agentreview.research_v2.io import file_sha256

ROOT = REPO / "artifacts/shared-throughput-20260917"


def read(path):
    return json.loads(Path(path).read_text())


def payloads(root):
    # Counter rather than set: duplicate requests must not disappear.
    from collections import Counter
    return Counter(hashlib.sha256(json.dumps(read(path)["payload"], sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        for path in Path(root).glob("cache/judge/wire/*/attempt-1.request.json"))


def compare():
    assessment = Path(read(ROOT / "baseline/result.json")["assessment"]["path"])
    baseline = validate_assessment_receipt(assessment)
    rows = {row["paper_id"]: row["metrics"] for row in baseline["rows"]}
    baseline_payloads = payloads(assessment.parent)
    if sum(baseline_payloads.values()) != 16:
        raise ValueError("Unexpected baseline Judge task inventory")
    reference_seconds = read(ROOT / "judge-reference-r1/status.json")["seconds"]
    policy = read(ROOT / "stability-policy.v2.json")
    trials = []
    for status_path in sorted(ROOT.glob("judge-*/status.json")):
        status = read(status_path)
        trial = {"name": status_path.parent.name, "status": status["status"],
                 "status_artifact": str(status_path), "valid_full_flow_reviews": 0}
        if status["status"] == "complete":
            result_path = Path(status["result"]["path"])
            if file_sha256(result_path) != status["result"]["sha256"]:
                raise ValueError("Changed diagnostic result")
            result = read(result_path)
            validate_stage_integrity({**baseline["stage_integrity"], "evaluation": result["integrity"]}, baseline["paper_ids"])
            if payloads(status_path.parent) != baseline_payloads:
                raise ValueError("Judge request payloads differ from the fixed baseline")
            metrics = {pid: result["papers"][pid]["table_1_summary"]["absolute_scores"] for pid in baseline["paper_ids"]}
            deltas = {pid: {key: metrics[pid][key]-value for key, value in row.items()} for pid, row in rows.items()}
            paper_deltas = [delta["Paper."] for delta in deltas.values()]
            mean_delta = sum(paper_deltas)/len(paper_deltas)
            passed = max(map(abs, paper_deltas)) <= policy["paper_per_item_max_absolute_delta"]+1e-9 and abs(mean_delta) <= policy["paper_cohort_mean_max_absolute_delta"]+1e-9
            if status_path.parent.name.startswith("judge-prefix4096"):
                control = read(ROOT / "judge-batch4096/status.json")
                control_deltas = {pid: status["paper_scores"][pid]-control["paper_scores"][pid] for pid in baseline["paper_ids"]}
                control_passed = max(map(abs, control_deltas.values())) <= policy["paper_per_item_max_absolute_delta"]+1e-9 and abs(sum(control_deltas.values())/len(control_deltas)) <= policy["paper_cohort_mean_max_absolute_delta"]+1e-9
                passed = passed and control_passed
                trial.update(parent_control="judge-batch4096", parent_control_paper_deltas=control_deltas,
                             parent_control_gate="passed" if control_passed else "rejected")
            trial.update(seconds=status["seconds"], speed_ratio_to_reference=reference_seconds/status["seconds"],
                paper_scores=status["paper_scores"], paper_deltas=paper_deltas, cohort_mean_delta=mean_delta,
                primary_gate="passed" if passed else "rejected", integrity="complete", identical_payloads=16,
                all_metric_deltas=deltas,
                aggregate_metric_deltas={key: sum(delta[key] for delta in deltas.values())/len(deltas) for key in rows[baseline["paper_ids"][0]]},
                result_sha256=status["result"]["sha256"])
        trials.append(trial)
    output = {"scope": "Fixed-input original Judge diagnostics, not new complete reviews or MSTAR candidates",
              "baseline_paper_scores": {pid: row["Paper."] for pid, row in rows.items()},
              "policy": {"path": str(ROOT / "stability-policy.v2.json"), "sha256": file_sha256(ROOT / "stability-policy.v2.json")},
              "trials": trials}
    (ROOT / "judge-comparison.json").write_text(json.dumps(output, indent=2)+"\n")
    for trial in trials:
        print(json.dumps({key: value for key, value in trial.items() if key not in {"all_metric_deltas", "aggregate_metric_deltas", "status_artifact", "result_sha256"}}))


if __name__ == "__main__":
    compare()
