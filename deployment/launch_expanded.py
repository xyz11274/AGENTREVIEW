"""Restore-ready -> freeze verified configuration -> launch development search."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/shared-throughput-20260917"
PYTHON = "/data/xyj/envs/coco-original/bin/python"


def worker(transition):
    status_path = ROOT / "expanded/startup-status.json"
    def status(name, **values):
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"status": name, "time": time.time(), **values}, indent=2)+"\n")
        tmp.replace(status_path)
    try:
        status("waiting_for_verified_service_restore", transition=str(transition))
        deadline = time.monotonic()+900
        while not transition.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Verified service restore timed out")
            time.sleep(2)
        if json.loads(transition.read_text())["status"] != "ready":
            raise RuntimeError("Service restore failed; search was not launched")
        status("validating_and_freezing_gate")
        subprocess.run([PYTHON, str(REPO / "deployment/freeze_throughput_gate.py"), "--services",
            str(REPO / "artifacts/deployment/original-coco/services.bounded-20260916.json"),
            "--diagnostic", "judge-reference-r1", "--full-flow", str(ROOT / "paper2")], check=True, cwd=REPO)
        status("launching_development_search", formal=False, sealed_test_opened=False)
        os.execv(PYTHON, [PYTHON, "-u", str(REPO / "deployment/shared_expanded.py"), "run",
            "--protocol", str(ROOT / "expanded/protocol.frozen.json"),
            "--gate", str(ROOT / "throughput-gate.frozen.json")])
    except BaseException as exc:
        status("failed_before_search_launch", error_type=type(exc).__name__, error=str(exc))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-transition", required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    transition = Path(args.after_transition).resolve()
    if args.worker:
        return worker(transition)
    scoring = json.loads((REPO / "artifacts/deployment/original-coco/scoring.frozen.json").read_text())
    env = dict(os.environ, AGENTREVIEW_QWEN27B_MODE_LEASE_PATH=scoring["lease_path"],
        PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    cmd = [PYTHON, "-u", str(Path(__file__).resolve()), "--worker", "--after-transition", str(transition)]
    with (ROOT / "expanded.log").open("xb") as log:
        child = subprocess.Popen(cmd, env=env, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (ROOT / "expanded-launch.json").write_text(json.dumps({"pid": child.pid, "command": cmd}, indent=2)+"\n")
    print(child.pid)


if __name__ == "__main__":
    main()
