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
    max_model_len = service.get("max_model_len", {"split":65536,"classify":32768,"judge":65536}[role])
    if type(max_model_len) is not int or max_model_len <= 0:
        raise ValueError(f"{role}: max_model_len requires a positive integer")
    result = [config["serving_python"], "-m", "vllm.entrypoints.openai.api_server",
              "--model", service["model_root"], "--served-model-name", service["model"],
              "--host", "127.0.0.1", "--port", str(service["port"]),
              "--dtype", "bfloat16", "--tensor-parallel-size", str(len(gpus)),
              "--max-model-len", str(max_model_len),
              "--gpu-memory-utilization", str(service.get("gpu_memory_utilization", 0.9)),
              "--max-num-seqs", str(service.get("max_num_seqs", 8))]
    for name in ("max_num_batched_tokens",):
        if name in service:
            if type(service[name]) is not int or service[name] <= 0:
                raise ValueError(f"{role}: {name} requires a positive integer")
            result += ["--" + name.replace("_", "-"), str(service[name])]
    for name in ("enable_chunked_prefill", "enable_prefix_caching"):
        if name in service:
            if type(service[name]) is not bool:
                raise ValueError(f"{role}: {name} requires an explicit boolean")
            result.append("--" + ("" if service[name] else "no-") + name.replace("_", "-"))
    if role == "split":
        # Current vLLM accepts the original YaRN setting via HF overrides.
        result += ["--hf-overrides", json.dumps({"rope_scaling": {
            "rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 32768}})]
    if role == "judge":
        result += ["--reasoning-parser", "qwen3"]
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
                state_value = (Path(f"/proc/{live['pid']}") / "stat").read_text().rsplit(") ", 1)[1].split()[0]
                if state_value == "Z" and all(live[key] == recorded["process"][key] for key in ("pid", "uid", "start_time")) and live["uid"] == os.getuid():
                    # An owned, exited process has an empty cmdline. Its UID
                    # and start time still prove identity; do not confuse that
                    # terminal state with a recycled PID or signal it again.
                    try:
                        os.waitpid(live["pid"], os.WNOHANG)
                    except ChildProcessError:
                        pass
                    live = None
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
            # Match the HTTP server's bind semantics: a completed connection in
            # TIME_WAIT is not a live listener and must not block a safe restart.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", service["port"]))
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES=",".join(map(str, service["gpus"])),
                           NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
        # FlashInfer invokes tools such as ninja by name. Calling a venv's Python
        # does not activate that environment's executable search path.
        serving_bin = str(Path(config["serving_python"]).expanduser().absolute().parent)
        environment["PATH"] = serving_bin + os.pathsep + environment.get("PATH", os.defpath)
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
