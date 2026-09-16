"""Start/inspect/stop only this deployment's recorded local model processes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def process_identity(pid):
    root = Path(f"/proc/{pid}")
    stat = (root / "stat").read_text().rsplit(") ", 1)[1].split()
    return {"pid": pid, "uid": root.stat().st_uid, "start_time": stat[19],
            "argv": (root / "cmdline").read_bytes().decode().rstrip("\0").split("\0")}


def command(config, role):
    service = config["services"][role]
    gpus = service.get("gpus", [])
    if not gpus or any(type(g) is not int or g < 0 for g in gpus):
        raise ValueError(f"{role}: explicitly allocated GPU IDs are required")
    result = [config["serving_python"], "-m", "vllm.entrypoints.openai.api_server",
              "--model", service["model_root"], "--served-model-name", service["model"],
              "--host", "127.0.0.1", "--port", str(service["port"]),
              "--dtype", "bfloat16", "--tensor-parallel-size", str(len(gpus)),
              "--max-model-len", str({"split": 65536, "classify": 32768, "judge": 65536}[role]),
              "--gpu-memory-utilization", str(service.get("gpu_memory_utilization", 0.9))]
    if role == "split":
        # Current vLLM accepts the original YaRN setting via HF overrides.
        result += ["--hf-overrides", json.dumps({"rope_scaling": {
            "rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 32768}})]
    if role == "judge":
        result += ["--max-num-seqs", "8", "--reasoning-parser", "qwen3"]
    # Split/Classify retain think text in content for the upstream parsers.
    return result


def manage(config, role, action):
    root = Path(config["state_root"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = root / f"{role}.json"
    with (root / f"{role}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recorded = json.loads(state.read_text()) if state.exists() else None
        live = None
        if recorded:
            try:
                live = process_identity(recorded["process"]["pid"])
            except FileNotFoundError:
                pass
            if live and (live != recorded["process"] or live["uid"] != os.getuid()):
                raise RuntimeError("Recorded PID identity changed; refusing to signal it")
        if action == "status":
            return {"role": role, "running": live is not None, "record": recorded}
        if action == "stop":
            if live:
                os.kill(live["pid"], signal.SIGINT)
                return {"role": role, "signal": "SIGINT", "pid": live["pid"], "completion": "check status"}
            return {"role": role, "running": False}
        if live:
            raise RuntimeError(f"{role} already has a managed process")
        args = command(config, role)
        if action == "plan":
            return {"role": role, "argv": args, "gpus": config["services"][role]["gpus"]}
        service = config["services"][role]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", service["port"]))
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES=",".join(map(str, service["gpus"])),
                           NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
        with (root / f"{role}.log").open("ab") as log:
            child = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                     cwd=root, start_new_session=True)
        # Wait only for exec identity, never for model loading while holding the CLI.
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError(f"{role} exited during startup; inspect {root / (role + '.log')}")
            identity = process_identity(child.pid)
            if identity["argv"] == args:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("Could not establish child exec identity")
        record = {"role": role, "process": identity, "model_root": service["model_root"]}
        temporary = state.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2) + "\n")
        temporary.replace(state)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "start", "status", "stop"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--role", required=True, choices=["split", "classify", "judge"])
    args = parser.parse_args()
    result = manage(json.loads(Path(args.config).read_text()), args.role, args.action)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
