"""Run and inspect one frozen pilot through explicit protocol and artifact paths."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.shared_experiment import (
    APISharedBackend, binding, bound, runtime_identity, run_shared_experiment, run_shared_final_test,
)
from evolution_round_report import report
from runtime_report import report as runtime_report
from agentreview.research_v2.execution_telemetry import span


def process_start_ticks(pid):
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class PilotRun:
    protocol_path: Path
    root: Path

    def __post_init__(self):
        object.__setattr__(self, 'protocol_path', Path(self.protocol_path).resolve())
        object.__setattr__(self, 'root', Path(self.root).resolve())

    def validate(self):
        protocol = load_experiment_protocol(self.protocol_path)
        implementation = protocol.document["implementation"]
        if implementation["runtime"] != runtime_identity():
            raise ValueError("Pilot runtime requires the implementation recorded by its protocol")
        for name in ("launcher", "reporter", "paired_reporter", "timing_reporter"):
            bound(implementation[name])
        recovery = protocol.document.get("recovery")
        if recovery:
            for name in ("evidence", "source_protocol", "search_checkpoint", "failure_policy"):
                bound(recovery[name])
        host = APISharedBackend(protocol=protocol, output_root=self.root / "preflight" / protocol.protocol_sha256)
        host.preflight()
        atomic_write_json(self.root / "protocol.frozen.json", read_json(self.protocol_path))
        return {"status": "validated", "counts": {split: len(rows) for split, rows in protocol.splits.items()},
                "protocol": binding(self.protocol_path), "runtime": "verified", "model_calls": 0}

    def check(self):
        value = self.validate()
        protocol = load_experiment_protocol(self.protocol_path)
        host = APISharedBackend(protocol=protocol, output_root=self.root / "preflight" / protocol.protocol_sha256)
        from agentreview.research_v2.llm import require_api_key
        from agentreview.research_v2.development_coco_worker import _runtime
        from agentreview.research_v2.config import ModelConfig
        require_api_key(host.model)
        require_api_key(ModelConfig(**host.scoring["services"]["judge"]))
        subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "--proc", "/proc",
                        "--dev", "/dev", "--", "/bin/true"], check=True, capture_output=True)
        services, blockers = {}, []
        for stage in ("split", "classify"):
            try:
                services[stage] = _runtime(host.scoring, stage)
            except (OSError, ValueError) as exc:
                blockers.append({"stage": stage, "error": str(exc)})
        value.update(status="blocked" if blockers else "ready", credential_configured=True,
                     sandbox="passed", services=services, blockers=blockers, checked_at=time.time())
        atomic_write_json(self.root / "readiness.json", value, immutable=False)
        return value

    def status(self):
        path = self.root / "pilot-state.json"
        state = read_json(path) if path.exists() else {"status": "prepared", "phase": "awaiting_start"}
        state["recorded_status"] = state["status"]
        if state["status"] == "running":
            active = process_start_ticks(state.get("pid"))
            if active is None or active != state.get("process_start_ticks"):
                state.update(status="interrupted", reason="Recorded worker process has exited")
        return {**state, "protocol_path": str(self.protocol_path), "output_root": str(self.root),
                "state_path": str(path), "log_command": "journalctl --user -u agentreview-0930-pilot.service -f",
                "timing_report_path": str(self.root / "report/流程耗时报告.md"),
                "timing_state_path": str(self.root / "report/runtime.json"),
                "report_path": str(self.root / "report/自进化逐轮报告.md")}

    def report(self):
        runtime_report(self.root, self.status())
        return report(self.root)

    def run(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "pilot.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._run_locked()

    def _run_locked(self):
        state = {"status": "running", "phase": "preflight", "pid": os.getpid(),
                 "process_start_ticks": process_start_ticks(os.getpid()),
                 "protocol": binding(self.protocol_path)}
        state_path = self.root / "pilot-state.json"
        timings_path = self.root / "phase-timings.json"
        timings = read_json(timings_path) if timings_path.exists() else {}
        stop = threading.Event()

        def save():
            state["updated_at"] = time.time()
            atomic_write_json(state_path, dict(state), immutable=False)

        def refresh_report(rounds=True):
            try:
                if rounds:
                    self.report()
                else:
                    runtime_report(self.root, dict(state))
            except Exception as exc:
                atomic_write_json(self.root / "report/monitor-error.json", {
                    "type": type(exc).__name__, "message": str(exc), "time": time.time()}, immutable=False)

        def monitor():
            previous = None
            while not stop.wait(20):
                save()
                checkpoint = self.root / "search/search.json"
                stamp = checkpoint.stat().st_mtime_ns if checkpoint.exists() else None
                refresh_report(rounds=stamp != previous)
                previous = stamp

        def stage(name, operation):
            state["phase"] = name
            save()
            started = time.monotonic()
            try:
                with span('pilot_' + name):
                    return operation()
            finally:
                timings[name + "_seconds"] = timings.get(name + "_seconds", 0) + time.monotonic() - started
                atomic_write_json(timings_path, timings, immutable=False)

        thread = threading.Thread(target=monitor, daemon=True)
        save()
        os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(self.root / "telemetry")
        thread.start()
        try:
            readiness = stage('preflight', self.check)
            if readiness["status"] == "blocked":
                state.update(status="blocked", reason="Service prerequisites are recorded in readiness.json")
                return state
            os.environ["HF_HUB_OFFLINE"] = "1"
            launched = self.root / "launched.json"
            if not launched.exists():
                atomic_write_json(launched, {"started": time.time(), "protocol": binding(self.protocol_path)})
            recovery = read_json(self.protocol_path).get("recovery")
            search = stage("search", lambda: run_shared_experiment(
                protocol_path=self.protocol_path, output_root=self.root / "search",
                repair_evidence=bound(recovery["evidence"]) if recovery else None))
            if search["status"] in {"complete", "ended"} and search.get("winner"):
                final = stage("final_evaluation", lambda: run_shared_final_test(
                    protocol_path=self.protocol_path, output_root=self.root / "final",
                    frozen_system_path=self.root / "search/winner.json"))
                if final["status"] == "complete":
                    stage("held_out_round_trajectory", lambda: evaluate_rounds(
                        load_experiment_protocol(self.protocol_path), search, final, root=self.root))
                    from paired_benchmark_report import report as paired_report
                    paired_report(self.root)
                    self.report()
                    state.update(status="complete", phase="reported", rounds_recorded=len(search["attempts"]))
                else:
                    state.update(status="paused", reason="Final scoring awaits complete receipts")
            else:
                state.update(status="paused", reason="Search awaits a validated frozen winner")
            return state
        except KeyboardInterrupt:
            state.update(status="stopped", reason="Operator requested shutdown")
            raise
        except Exception as exc:
            state.update(status="paused", error={"type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            stop.set()
            thread.join()
            state["finished_at"] = time.time()
            save()
            refresh_report()


def load_run(profile_path):
    profile_path = Path(profile_path).resolve()
    profile = read_json(profile_path)
    return PilotRun(profile_path.parent / profile["protocol"], profile_path.parent / profile["output_root"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "validate", "check", "run", "report"))
    parser.add_argument("--profile", type=Path, default=REPO / "config/active-pilot.json")
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    selected = load_run(args.profile)
    run = PilotRun(args.protocol or selected.protocol_path, args.output_root or selected.root)
    def shutdown(signum, frame):
        raise KeyboardInterrupt()
    if args.action == "run":
        signal.signal(signal.SIGTERM, shutdown)
    try:
        value = getattr(run, args.action)()
    except KeyboardInterrupt:
        value = run.status()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 2 if value.get("status") in {"blocked", "paused", "stopped"} else 0
def evaluate_rounds(protocol, search, final, *, root):
    """Held-out trajectory, evaluated only after search and Champion freeze."""
    frozen_path = root / "search/winner.json"
    frozen = read_json(frozen_path)
    path = root / "round-test/final-rounds.json"
    state = read_json(path) if path.exists() else {
        "status": "running", "freeze_id": frozen["freeze_id"], "recipes": {}, "results": {}}
    if state["freeze_id"] != frozen["freeze_id"] or final["freeze_id"] != frozen["freeze_id"]:
        raise ValueError("Round trajectory requires the same verified final freeze")
    host = APISharedBackend(protocol=protocol, output_root=root / "round-test")
    host.frozen_system = binding(frozen_path)
    for key, result in frozen["assessments"].items():
        recipe = frozen["recipes"][key]
        host.validate(result, recipe=recipe, epoch=result["evaluation_epoch"], generation_only=True)
        if recipe["memory_enabled"]:
            host.frozen_snapshots[recipe["candidate"]["candidate_id"]] = result["identity"]["snapshots"]
    ids = [row["paper_id"] for row in protocol.records("sealed_test")]
    for attempt in sorted(search["attempts"].values(), key=lambda row: row["ordinal"]):
        key = attempt["candidate_key"]
        if attempt["status"] != "complete":
            continue
        is_champion = key == frozen["winner"]["key"]
        recipe = (final["recipes"]["champion"] if is_champion else
                  {**frozen["recipes"][key], "split": "sealed_test", "paper_ids": ids})
        state["recipes"][key] = recipe
        epoch = frozen["assessments"][key]["evaluation_epoch"]
        if key not in state["results"]:
            # Reuse the identical frozen Champion's complete test receipt.
            state["results"][key] = (final["results"]["champion"] if is_champion
                                     else host.assess(recipe, epoch=epoch))
        host.validate(state["results"][key], recipe=recipe, epoch=epoch)
        atomic_write_json(path, state, immutable=False)
    state["status"] = "complete"
    atomic_write_json(path, state, immutable=False)


if __name__ == "__main__":
    raise SystemExit(main())
