"""Apply one owned service configuration only after all three queues drain."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request

from coco_services import manage, process_identity


def process_alive(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    if state == "Z":
        # Startup failures are our children; reap them rather than waiting for
        # /proc to disappear while the parent itself is keeping the zombie.
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--role", default="judge", choices=("judge", "classify", "split"))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(args.record)
    if root.exists():
        raise ValueError("Service transition records are immutable; use a fresh path")
    root.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic()+180
    idle_checks = 0
    while idle_checks < 2:
        occupied = []
        for service in config["services"].values():
            try:
                with opener.open(f"http://127.0.0.1:{service['port']}/metrics", timeout=5) as response:
                    lines = response.read().decode().splitlines()
                occupied.extend(line for line in lines if line.startswith(("vllm:num_requests_running{", "vllm:num_requests_waiting{")) and float(line.rsplit(" ",1)[1]) != 0)
            except Exception:
                # The target may already be stopped after a rejected startup.
                if service != config["services"][args.role]:
                    raise
        idle_checks = idle_checks+1 if not occupied else 0
        if time.monotonic() > deadline:
            raise RuntimeError("Live requests remain; refusing to restart the owned service")
        if idle_checks < 2:
            time.sleep(5)
    before = manage(config, args.role, "status")
    result = {"config": str(Path(args.config).resolve()), "before": before, "started": time.time(), "role": args.role}
    try:
        manage(config, args.role, "stop")
        if before.get("running"):
            old_pid = before["record"]["process"]["pid"]
            deadline = time.monotonic()+120
            while process_alive(old_pid):
                if time.monotonic() > deadline:
                    raise RuntimeError("Owned service has not exited; inspect before escalating signals")
                time.sleep(1)
        result["after"] = manage(config, args.role, "start")
        print(json.dumps({"event": "loading", "pid": result["after"]["process"]["pid"]}), flush=True)
        service = config["services"][args.role]
        deadline = time.monotonic()+900
        while True:
            if not process_alive(result['after']['process']['pid']):
                raise RuntimeError("Trial service exited during loading; preserve logs and restore verified config")
            try:
                with opener.open(service["base_url"]+"/models", timeout=3) as response:
                    models = json.load(response)
                if service["model"] in {item["id"] for item in models.get("data",[])}:
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Service readiness timed out")
            time.sleep(3)
        if process_identity(result["after"]["process"]["pid"]) != result["after"]["process"]:
            raise ValueError("Trial service process identity changed")
        result.update(status="ready", ended=time.time())
    except BaseException as exc:
        result.update(status="failed", ended=time.time(), error=str(exc))
        raise
    finally:
        root.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"status": result["status"], "seconds": result["ended"]-result["started"]}), flush=True)


if __name__ == "__main__":
    main()
