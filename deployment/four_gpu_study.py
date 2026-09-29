"""User-authorized service rotation around the unchanged frozen experiment.

Hold a mode for a complete generation client or complete original CoCo stage.
This is deployment scheduling, not a replacement for scoring or request retries.
"""
from contextlib import contextmanager
from collections import deque
from pathlib import Path
import argparse
import copy
import fcntl
import functools
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound
from coco_services import manage, command

ROOT = REPO / "artifacts/coco-paired-3search-20260917"
DEPLOY = ROOT / "deployment-minimum-cards"
CONFIG = DEPLOY / "services-four-cards.json"
MANIFEST = DEPLOY / "deployment.frozen.json"
MODES = ("reviewer", "split", "classify", "judge")


def mode_config(config, mode):
    if mode not in MODES or config["allowed_gpus"] != [0, 1, 2, 3]:
        raise ValueError("This deployment is restricted to GPUs 0-3 and four explicit modes")
    value = copy.deepcopy(config["service_config"])
    role = "judge" if mode == "reviewer" else mode
    service = value["services"][role]
    if mode == "reviewer":
        service["max_model_len"] = 131072
    if not set(service["gpus"]) <= set(config["allowed_gpus"]):
        raise ValueError("Model service cannot use a GPU outside the allocation")
    return value, role


class ModeCoordinator:
    """Fair mode leases: same-mode work may overlap, changing modes must drain."""
    def __init__(self, switch, emit=lambda **event: None):
        self.switch, self.emit = switch, emit
        self.condition = threading.Condition()
        self.queue = deque()
        self.mode, self.active, self.failure = None, 0, None

    @contextmanager
    def lease(self, mode):
        if mode not in MODES:
            raise ValueError("Unknown service mode")
        ticket = object()
        started = time.monotonic()
        with self.condition:
            self.queue.append((ticket, mode))
            try:
                while True:
                    if self.failure is not None:
                        raise RuntimeError("Service rotation failed; diagnosis required") from self.failure
                    earlier = []
                    for item, waiting_mode in self.queue:
                        if item is ticket:
                            break
                        earlier.append(waiting_mode)
                    if (not earlier or all(x == mode for x in earlier)) and (self.active == 0 or self.mode == mode):
                        break
                    self.condition.wait()
                if self.mode != mode:
                    assert self.active == 0
                    try:
                        self.switch(mode)
                    except BaseException as exc:
                        self.failure = exc
                        self.condition.notify_all()
                        raise
                    self.mode = mode
                self.queue.remove((ticket, mode))
                self.active += 1
                self.emit(event="mode_acquired", mode=mode, active=self.active,
                          wait_seconds=time.monotonic()-started, waiting=[m for _, m in self.queue])
                self.condition.notify_all()
            except BaseException:
                if (ticket, mode) in self.queue:
                    self.queue.remove((ticket, mode))
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.emit(event="mode_released", mode=mode, active=self.active,
                          waiting=[m for _, m in self.queue])
                self.condition.notify_all()


class ServiceSwitcher:
    def __init__(self, config):
        self.config = config
        self.root = Path(config["event_root"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def emit(self, **event):
        value = {"time":time.time(), "pid":os.getpid(), **event}
        with self.lock:
            with (self.root / "events.jsonl").open("a") as handle:
                handle.write(json.dumps(value, ensure_ascii=False)+"\n")
            atomic_write_json(self.root / "state.json", value, immutable=False)

    def gpu_compute_processes(self):
        ids = subprocess.check_output(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"], text=True)
        allowed = {line.split(",")[1].strip() for line in ids.splitlines()
                   if int(line.split(",")[0]) in self.config["allowed_gpus"]}
        values = subprocess.check_output(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"], text=True)
        return [line for line in values.splitlines() if line.split(",")[0].strip() in allowed]

    def ready(self, config, role):
        service = config["services"][role]
        with self.opener.open(service["base_url"].rstrip("/")+"/models", timeout=5) as response:
            rows = json.load(response)["data"]
        matches = [row for row in rows if row["id"] == service["model"]]
        if len(matches) != 1 or matches[0]["max_model_len"] != service["max_model_len"]:
            raise ValueError("Live model name or context differs from the requested mode")

    def __call__(self, mode):
        config, role = mode_config(self.config, mode)
        started = time.monotonic()
        self.emit(event="switch_start", mode=mode, allowed_gpus=self.config["allowed_gpus"])
        # Only records in this deployment's owned service directory can be stopped.
        statuses = {name:manage(config, name, "status") for name in ("judge","split","classify")}
        live = [name for name, status in statuses.items() if status["running"]]
        if live == [role] and statuses[role]["record"]["process"]["argv"] == command(config, role):
            self.ready(config, role)
            self.emit(event="mode_ready", mode=mode, adopted=True, seconds=time.monotonic()-started)
            return
        for name in live:
            manage(config, name, "stop")
        deadline = time.monotonic()+180
        while any(manage(config, name, "status")["running"] for name in live) or self.gpu_compute_processes():
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned service did not release GPUs 0-3; no replacement started")
            time.sleep(1)
        record = manage(config, role, "start")
        self.emit(event="model_loading", mode=mode, service_pid=record["process"]["pid"], gpus=config["services"][role]["gpus"])
        deadline = time.monotonic()+600
        while True:
            if not manage(config, role, "status")["running"]:
                raise RuntimeError(f"{mode} model exited during loading")
            try:
                self.ready(config, role)
                break
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{mode} failed to become ready within its deployment timeout")
                time.sleep(1)
        self.emit(event="mode_ready", mode=mode, adopted=False, seconds=time.monotonic()-started,
                  context=config["services"][role]["max_model_len"], service_pid=record["process"]["pid"])


def install(coordinator):
    from agentreview.research_v2 import shared_experiment as shared, development_coco as coco
    original_client, original_stage = shared.LocalSharedBackend.client, coco._run_stage
    @contextmanager
    @functools.wraps(original_client)
    def client(self, directory):
        # Covers tokenization, full paper generation and durable response writes.
        with coordinator.lease("reviewer"):
            with original_client(self, directory) as instance:
                yield instance
    @functools.wraps(original_stage)
    def stage(**kwargs):
        mode = "judge" if kwargs["stage"] == "evaluation" else kwargs["stage"]
        # Acquired BEFORE stage retry reservation or its worker timeout starts.
        # The original worker validates the same real service PID before/after.
        with coordinator.lease(mode):
            return original_stage(**kwargs)
    shared.LocalSharedBackend.client, coco._run_stage = client, stage
    return original_client, original_stage


def prepare():
    base = read_json(REPO / "artifacts/deployment/original-coco/services.fullscale-frozen-20260917.json")
    base["state_root"] = str(DEPLOY / "services-four-cards")
    for name in ("split","classify"):
        base["services"][name]["gpus"] = [0,1]
    base["services"]["judge"].update(gpus=[0,1,2,3], max_model_len=65536)
    config = {"protocol":"four-gpu-stage-rotation-v1", "allowed_gpus":[0,1,2,3],
        "service_config":base, "event_root":str(DEPLOY / "rotation"),
        "reviewer_context":131072, "judge_context":65536,
        "precision":"original bfloat16 weights and KV; no offload or quantization",
        "scheduling":"drain complete generation clients and complete original CoCo stages; fair shared-mode leases",
        "original_experiment":binding(ROOT / "study.frozen.json"),
        "authorization":"User selected four GPUs, stage model rotation, then explicitly corrected allocation to 0-3"}
    atomic_write_json(CONFIG, config)
    print(CONFIG)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare","check-modes","run"))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
        return
    config = read_json(CONFIG)
    switcher = ServiceSwitcher(config)
    with (DEPLOY / "rotation.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "check-modes":
            results = []
            for mode in ("split","classify","judge","reviewer"):
                switcher(mode)
                config_value, role = mode_config(config, mode)
                from agentreview.research_v2.development_coco_worker import _service_ready
                original = read_json(bound(read_json(ROOT / "protocol.pending.json")["scoring_config"]))
                checked = _service_ready(original["services"][role])
                assert checked["max_model_len"] == config_value["services"][role]["max_model_len"]
                results.append({"mode":mode,"verified_service":checked})
            atomic_write_json(DEPLOY / "mode-readiness.json", {"status":"passed","results":results,
                "scope":"Real deployment lifecycle and original worker service identity/context checks; no new pilot or model-quality experiment"})
            return
        manifest = read_json(MANIFEST)
        for name in ("adapter","service_manager","configuration","validation","mode_readiness","original_study"):
            bound(manifest[name])
        if manifest["adapter"] != binding(__file__) or manifest["configuration"] != binding(CONFIG):
            raise ValueError("Stage rotation requires its exact frozen deployment appendix")
        coordinator = ModeCoordinator(switcher, switcher.emit)
        install(coordinator)
        import three_search_study
        three_search_study.run()


if __name__ == "__main__":
    main()
