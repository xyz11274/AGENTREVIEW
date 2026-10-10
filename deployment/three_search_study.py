"""Run all prespecified independent searches before opening any sealed test."""
import argparse
import fcntl
import os
from pathlib import Path
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import read_json, atomic_write_json, value_sha256
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.shared_experiment import (
    bound, binding, runtime_identity, generation_identity, APISharedBackend,
    run_shared_experiment, run_shared_final_test, validate_study_freeze,
)
from agentreview.research_v2.transport_observer import process_identity
from evolution_round_report import report
from paired_benchmark_report import report as paired_report
from runtime_report import report as timing_report
from runtime_0930_progress import process_start_ticks, effective_status
from agentreview.research_v2.runtime_limits import initialize_run_budget, validate_run_budget


def validate(study_path):
    study = read_json(study_path)
    validate_run_budget(study.get('runtime_budget'))
    for ref in study["implementation"].values():
        bound(ref)
    acceptance = read_json(bound(study["runtime_acceptance"]))
    if acceptance.get("status") != "complete" or acceptance.get("runtime") != runtime_identity():
        raise ValueError("Formal study requires complete real acceptance of this exact runtime")
    bound(acceptance["checkpoint"])
    protocols = []
    for member in study["searches"]:
        protocol = load_experiment_protocol(bound(member["protocol"]))
        if protocol.document["implementation"]["runtime"] != runtime_identity():
            raise ValueError("Formal runtime differs from frozen code")
        if protocol.document["evolution"]["random_seed"] != member["seed"]:
            raise ValueError("Study random seed differs from member protocol")
        if protocols and protocol.document["splits"] != protocols[0].document["splits"]:
            raise ValueError("Independent searches require the same frozen data partition")
        APISharedBackend(protocol=protocol, output_root=Path(member["run_root"]) / "preflight").preflight()
        protocols.append(protocol)
    assert [p.document["evolution"]["random_seed"] for p in protocols] == [0, 1000, 2000]
    return study, protocols


def run(study_path):
    study, protocols = validate(study_path)
    root = Path(study["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    budget_path = initialize_run_budget(study.get('runtime_budget'), root)
    os.environ['AGENTREVIEW_CALL_BUDGET'] = str(budget_path)
    os.environ['AGENTREVIEW_REQUEST_WALL_SECONDS'] = str(study['runtime_budget']['request_wall_seconds'])
    atomic_write_json(root / "process-identity.json", process_identity(), immutable=False)
    path = root / "study-state.json"
    state = {"status": "running", "pid": os.getpid(), "process_start_ticks": process_start_ticks(os.getpid()),
             "study": binding(study_path), "stage": "search", "started_at": time.time(), 'call_budget': str(budget_path)}
    stop = threading.Event()
    def save():
        state["updated_at"] = time.time()
        atomic_write_json(path, dict(state), immutable=False)
    def monitor():
        while not stop.wait(20):
            save()
            for member in study["searches"]:
                run_root = Path(member["run_root"])
                if (run_root / "search/search.json").exists():
                    try:
                        report(run_root)
                        timing_report(run_root, {**state, 'telemetry_root': str(run_root / 'telemetry')})
                    except Exception as exc:
                        atomic_write_json(run_root / "report/monitor-error.json", {"error": str(exc)}, immutable=False)
    with (root / "study.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save()
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            systems = []
            for member, protocol in zip(study["searches"], protocols):
                state.update(stage="search", seed=member["seed"])
                save()
                run_root = Path(member["run_root"])
                os.environ['AGENTREVIEW_TELEMETRY_ROOT'] = str(run_root / 'telemetry')
                search = run_shared_experiment(protocol_path=protocol.path, output_root=run_root / "search")
                report(run_root)
                if search["status"] not in {"complete", "ended"} or not search.get("winner"):
                    state.update(status="paused", reason="Search requires a valid terminal freeze")
                    return state
                systems.append({"seed": member["seed"], "protocol": member["protocol"],
                                "frozen_system": binding(run_root / "search/winner.json")})
            gate = {"protocol": "three-search-freeze-v1", "status": "all_frozen", "random_seeds": [0, 1000, 2000],
                    "generation_identity": generation_identity(), "systems": systems}
            gate_path = Path(protocols[0].document["study"]["freeze_manifest"])
            atomic_write_json(gate_path, gate)
            for protocol in protocols:
                validate_study_freeze(protocol)
            for member, protocol in zip(study["searches"], protocols):
                state.update(stage="final_test", seed=member["seed"])
                save()
                run_root = Path(member["run_root"])
                os.environ['AGENTREVIEW_TELEMETRY_ROOT'] = str(run_root / 'telemetry')
                result = run_shared_final_test(protocol_path=protocol.path, output_root=run_root / "final",
                                              frozen_system_path=run_root / "search/winner.json")
                if result["status"] != "complete":
                    state.update(status="paused", reason="Final evaluation requires complete receipts")
                    return state
                paired_report(run_root)
                report(run_root)
            state.update(status="complete", stage="reported")
            return state
        except BaseException as exc:
            state.update(status="paused", error={"type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            stop.set()
            thread.join()
            save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "run", "status"))
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "run":
        result = run(args.study.resolve())
        print(result)
        sys.exit(0 if result["status"] == "complete" else 2)
    elif args.action == "validate":
        study, protocols = validate(args.study.resolve())
        print({"status": "validated", "seeds": [m["seed"] for m in study["searches"]]})
    else:
        study = read_json(args.study)
        print(effective_status(read_json(Path(study["output_root"]) / "study-state.json")))
