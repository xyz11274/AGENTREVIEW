"""Resident, guarded FP32 Sentence Transformers service for physical GPUs 7,1.

The original adapter owns tokenization, pooling and normalization. This module
only dispatches its transformer layers and serializes transport requests.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def gpu_state():
    lines = subprocess.check_output(["nvidia-smi", "--query-gpu=index,uuid,memory.free,utilization.gpu",
                                    "--format=csv,noheader,nounits"], text=True, timeout=10).splitlines()
    return {int(v[0]): {"uuid": v[1].strip(), "free_mib": float(v[2]), "utilization": float(v[3])}
            for v in (line.split(",") for line in lines)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(config["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    from agentreview.research_v2.io import atomic_write_json, file_sha256, value_sha256
    from memory_evolution.embedding_adapter import Qwen3Embedding
    from huggingface_hub import snapshot_download
    import torch
    from accelerate import dispatch_model
    if os.getenv("CUDA_VISIBLE_DEVICES") != "7,1" or config["physical_gpus"] != [7, 1]:
        raise ValueError("Service requires the independently validated physical GPU order 7,1")
    if torch.cuda.device_count() != 2:
        raise ValueError("Expected exactly two visible GPUs")
    cards = gpu_state()
    if any(cards[g]["free_mib"] < config["initial_free_gib"]*1024 for g in (7, 1)):
        raise RuntimeError("Shared GPU initial headroom is below the validated safety margin")
    model_root = Path(snapshot_download(config["model"], revision=config["revision"], local_files_only=True))
    for path, expected in config["model_files_sha256"].items():
        if file_sha256(model_root / path) != expected:
            raise ValueError(f"Pinned embedding model/tokenizer changed: {path}")
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    for device in range(2):
        _, total = torch.cuda.mem_get_info(device)
        torch.cuda.set_per_process_memory_fraction(config["memory_cap_gib"]*2**30/total, device)
    adapter = Qwen3Embedding(config["model"], config["revision"], device="cpu", cache_enabled=False, batch_size=1)
    adapter._load()
    from sentence_transformers import SentenceTransformer

    class DispatchedSentenceTransformer(SentenceTransformer):
        def to(self, *args, **kwargs):
            if getattr(self, "_fixed_dispatch", False):
                if len(args) != 1 or kwargs or str(args[0]) != "cuda:0":
                    raise ValueError("FP32 embedding placement is fixed")
                return self
            return super().to(*args, **kwargs)

    model = adapter._model
    model.__class__ = DispatchedSentenceTransformer
    transformer = model[0].auto_model
    device_map = {"embed_tokens": 0, "rotary_emb": 0, "norm": 1,
                  **{f"layers.{i}": 0 if i < 16 else 1 for i in range(len(transformer.layers))}}
    model[0].auto_model = dispatch_model(transformer, device_map=device_map, main_device="cuda:0")
    model._fixed_dispatch = True
    adapter._device = "cuda:0"
    if {str(p.dtype) for p in model.parameters()} != {"torch.float32"} or adapter.attention_implementation != "sdpa":
        raise ValueError("Embedding precision/attention differs from validated FP32 semantics")
    identity = {**adapter.embedding_spec, "compute_dtype": "float32", "output_dtype": "float32",
        "tf32": False, "attention": "sdpa", "prompt_name": None, "prompt": None,
        "padding_side": "left", "post_normalization": "numpy-float32-l2", "batch_size": 1,
        "physical_gpus": [7, 1], "device_map": device_map, "memory_cap_gib_per_gpu": config["memory_cap_gib"],
        "gpu_uuids": {str(g): cards[g]["uuid"] for g in (7, 1)},
        "model_files_sha256": config["model_files_sha256"],
        "adapter_sha256": file_sha256(Path(__import__(Qwen3Embedding.__module__, fromlist=["x"]).__file__)),
        "runtime_versions": {p: importlib.metadata.version(p) for p in
                             ("torch", "transformers", "sentence-transformers", "accelerate")}}
    fingerprint = value_sha256(identity)
    atomic_write_json(root / "identity.json", {"identity": identity, "fingerprint": fingerprint})
    state = {"status": "ready", "fingerprint": fingerprint, "pid": os.getpid(), "started_at": time.time()}
    encode_lock, stopped, pressured = threading.Lock(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, value):
            rendered = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(rendered)))
            self.end_headers()
            self.wfile.write(rendered)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {**state, "status": "pressured" if pressured.is_set() else "ready"})
            elif self.path == "/identity":
                self.reply(200, {"identity": identity, "fingerprint": fingerprint})
            else:
                self.reply(404, {"error": "unknown endpoint"})

        def do_POST(self):
            self.connection.settimeout(10)
            if self.path != "/encode":
                return self.reply(404, {"error": "unknown endpoint"})
            if pressured.is_set() or not encode_lock.acquire(blocking=False):
                return self.reply(503, {"error": "GPU unavailable or encoder busy"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256*1024:
                    return self.reply(413, {"error": "request body exceeds finite limit"})
                data = json.loads(self.rfile.read(length))
                if data.get("fingerprint") != fingerprint:
                    return self.reply(409, {"error": "compute identity differs"})
                texts = data.get("texts")
                if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
                    return self.reply(422, {"error": "batch=1 string input required"})
                tokens = adapter.audit_token_lengths(texts)["max_token_count_before_truncation"]
                if tokens > config["max_tokens"]:
                    return self.reply(422, {"error": "input outside validated GPU envelope", "tokens": tokens})
                before = gpu_state()
                if any(before[g]["free_mib"] < config["remaining_free_gib"]*1024 for g in (7, 1)):
                    pressured.set()
                    return self.reply(503, {"error": "shared GPU headroom guard"})
                began = time.monotonic()
                vectors = adapter._embed_uncached_batch(texts)
                for d in range(2):
                    torch.cuda.synchronize(d)
                seconds = time.monotonic()-began
                with (root / "requests.jsonl").open("a") as stream:
                    stream.write(json.dumps({"at": time.time(), "text_sha256": hashlib.sha256(texts[0].encode()).hexdigest(),
                        "tokens": tokens, "seconds": seconds, "fingerprint": fingerprint})+"\n")
                self.reply(200, {"vectors": vectors, "fingerprint": fingerprint, "tokens": tokens, "seconds": seconds})
            except torch.cuda.OutOfMemoryError:
                pressured.set()
                self.reply(503, {"error": "GPU memory pressure; unloading own model"})
            except (ValueError, KeyError) as exc:
                self.reply(422, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                encode_lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
    server.daemon_threads = False

    def monitor():
        with (root / "gpu-monitor.jsonl").open("a") as stream:
            while not stopped.wait(1):
                try:
                    cards = gpu_state()
                    stream.write(json.dumps({"at": time.time(), "cards": {g: cards[g] for g in (7, 1)}})+"\n")
                    stream.flush()
                    if any(cards[g]["free_mib"] < config["remaining_free_gib"]*1024 for g in (7, 1)):
                        pressured.set()
                except Exception:
                    pressured.set()
                if pressured.is_set():
                    state["status"] = "unloaded-for-resource-pressure"
                    server.shutdown()
                    return

    atomic_write_json(root / "state.json", state, immutable=False)
    threading.Thread(target=monitor, daemon=True).start()
    try:
        print(json.dumps(state), flush=True)
        server.serve_forever(poll_interval=.2)
    finally:
        stopped.set()
        server.server_close()
        atomic_write_json(root / "state.json", {**state, "stopped_at": time.time()}, immutable=False)
        # Process exit releases only this service's GPU allocations.


if __name__ == "__main__":
    main()
