"""Freeze measured configuration only after receipt and fixed-input gates pass."""
import argparse
import json
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound, runtime_identity
from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.development_coco_worker import _service_ready
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from coco_services import command, manage
from compare_judge_trials import compare

ROOT = REPO / "artifacts/shared-throughput-20260917"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--services", required=True)
    parser.add_argument("--diagnostic", required=True)
    parser.add_argument("--full-flow", required=True)
    args = parser.parse_args()
    compare()
    comparison = read_json(ROOT / "judge-comparison.json")
    trial = next(row for row in comparison["trials"] if row["name"] == args.diagnostic)
    if trial.get("primary_gate") != "passed" or trial.get("integrity") != "complete":
        raise ValueError("Selected configuration has not passed fixed-input Paper. and completeness gates")
    config = read_json(args.services)
    original = read_json(REPO / "artifacts/deployment/original-coco/services.bounded-20260916.json")
    is_original = config["services"] == original["services"]
    if not is_original and trial["speed_ratio_to_reference"] < 1.03:
        raise ValueError("Changed service configuration did not demonstrate the planned speed gain")
    full_root = Path(args.full_flow).resolve()
    full_status = read_json(full_root / "status.json")
    if full_status.get("status") != "complete" or full_status.get("valid_reviews") != 2 or full_status.get("failed_reviews") != 0:
        raise ValueError("A complete fresh two-paper full-flow measurement is required")
    baseline_status = read_json(ROOT / "baseline/status.json")
    if full_status["valid_reviews_per_hour"] / baseline_status["valid_reviews_per_hour"] < 1.03:
        raise ValueError("Full-flow speed gate did not pass")
    full = read_json(full_root / "result.json")
    receipt = validate_assessment_receipt(bound(full["assessment"]))
    scoring = read_json(bound(receipt["inputs"]["scoring_config"]))
    if receipt["mean_score"] != full["static_score"]:
        raise ValueError("Full-flow result and complete receipt disagree")
    # The benchmark intentionally ran from an immutable source archive. Verify
    # every live runtime file against that archive before binding live paths.
    equivalence = []
    for ref in runtime_identity():
        path = bound(ref)
        archived = ROOT / "paper2-source" / path.relative_to(REPO)
        frozen = binding(archived)
        if frozen["sha256"] != ref["sha256"]:
            raise ValueError(f"Live runtime differs from measured source: {path}")
        equivalence.append({"live": ref, "measured": frozen})
    pilot = read_json(ROOT / "pilot-audit.json")
    for ref in pilot["historical_bindings"]:
        bound(ref)
    live_services = {}
    for role, service in config["services"].items():
        live = manage(config, role, "status")
        if not live["running"]:
            raise ValueError(f"Selected service is not running: {role}")
        expected = command(config, role)
        # Process identity may use an exec-resolved interpreter path. All
        # remaining arguments must exactly match the selected frozen config.
        if live["record"]["process"]["argv"][1:] != expected[1:]:
            raise ValueError(f"Running service differs from selected config: {role}")
        live_services[role] = _service_ready(scoring["services"][role])
    # The diagnostic captures the same command hash as the selected live Judge.
    judged = read_json(ROOT / args.diagnostic / "result.json")["service_runtime"]
    if judged["argv_sha256"] != live_services["judge"]["argv_sha256"]:
        raise ValueError("Selected Judge configuration differs from its measured diagnostic")
    for row in receipt["rows"]:
        evaluation = read_json(bound(row["raw"]))
        if evaluation["service_runtime"]["argv_sha256"] != live_services["judge"]["argv_sha256"]:
            raise ValueError("Full-flow Judge configuration differs from the selected service")
    gate = {"protocol": "shared-throughput-gate-v1", "status": "complete", "created": time.time(),
        "runtime": runtime_identity(), "measured_source_equivalence": equivalence,
        "full_flow_assessments": [full["assessment"]], "full_flow_status": binding(full_root / "status.json"),
        "primary_stability_policy": binding(ROOT / "stability-policy.v2.json"),
        "selected_diagnostic": binding(ROOT / args.diagnostic / "result.json"),
        "all_diagnostics": binding(ROOT / "judge-comparison.json"),
        "services_config": binding(args.services), "service_runtime": live_services,
        "execution_profile": binding(ROOT / "execution-paper2.json"),
        "historical_pilot_preserved": True, "formal_search_eligible": False, "sealed_test_opened": False,
        "limitations": ["Two exposed development papers; warm embedding cache; small-sample measured throughput, not a generalization claim",
                        "Paper. numerical stability primary; all required category values complete and numeric drifts reported",
                        "Diagnostic replays do not count as new full-flow reviews or candidate admission"]}
    gate_path = atomic_write_json(ROOT / "throughput-gate.frozen.json", gate)
    protocol = read_json(ROOT / "expanded/protocol.pending.json")
    protocol.update(status="frozen", throughput_gate=binding(gate_path), services_config=binding(args.services))
    protocol["model"]["max_num_seqs"] = config["services"]["judge"]["max_num_seqs"]
    protocol["launch_policy"] = {"reason": "Validated measured two-paper pipeline and Paper. stability gate; development only; stop on unresolved failure"}
    protocol_path = atomic_write_json(ROOT / "expanded/protocol.frozen.json", protocol)
    load_experiment_protocol(protocol_path)
    print(json.dumps({"gate": str(gate_path), "protocol": str(protocol_path), "formal": False}))


if __name__ == "__main__":
    main()
