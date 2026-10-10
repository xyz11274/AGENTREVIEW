"""FP32 remote encoding with immutable per-text compute-lane provenance.

Vectors from CPU and GPU have distinct identities. An outage only routes *new*
texts to CPU; an existing vector is never recomputed under another identity.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import multiprocessing
import os
import threading
import time
from pathlib import Path

import httpx

from .io import atomic_write_json, read_json, value_sha256
from .execution_telemetry import span


def _cpu_worker(connection, model, revision):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        import torch
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        from memory_evolution.embedding_adapter import Qwen3Embedding
        adapter = Qwen3Embedding(model, revision, device="cpu", cache_enabled=False, batch_size=1)
        while True:
            text = connection.recv()
            if text is None:
                break
            adapter._load()
            if adapter.runtime_dtype != "float32" or adapter.attention_implementation != "sdpa":
                raise ValueError("CPU fallback must use original FP32 SDPA implementation")
            connection.send({"vector": adapter._embed_uncached_batch([text])[0]})
    except EOFError:
        pass
    except Exception as exc:
        connection.send({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()


class RemoteFP32Embedding:
    def __init__(self, *, model, revision, cache_path, service):
        from memory_evolution.embedding_adapter import Qwen3Embedding
        self.audit = Qwen3Embedding(model, revision, device="cpu", cache_enabled=False)
        self.model, self.revision = model, revision
        self.service = dict(service)
        self.gpu_identity = service["identity"]
        if (self.gpu_identity["model_id"] != model or self.gpu_identity["model_revision"] != revision
                or self.gpu_identity["compute_dtype"] != "float32"
                or self.gpu_identity["batch_size"] != 1):
            raise ValueError("Remote embedding identity must bind the original FP32 model")
        self.gpu_fingerprint = value_sha256(self.gpu_identity)
        self.cpu_identity = {**self.audit.embedding_spec, "compute_dtype": "float32",
            "output_dtype": "float32", "device": "cpu", "attention": "sdpa", "batch_size": 1,
            "runtime_versions": {p: importlib.metadata.version(p) for p in
                                 ("torch", "sentence-transformers", "transformers")}}
        self.cpu_fingerprint = value_sha256(self.cpu_identity)
        self.embedding_spec = {"policy": "immutable-fp32-lanes-v1", "gpu": self.gpu_identity,
            "cpu": self.cpu_identity, "gpu_max_tokens": int(service["max_tokens"]),
            "fallback": "new-text-only", "max_length": self.audit.max_length,
            "dimension": 4096, "prompt": None, "pooling": "last_token", "normalize": True}
        if not 0 < self.embedding_spec["gpu_max_tokens"] <= 1843:
            raise ValueError("GPU token ceiling exceeds the independently validated envelope")
        self.embedding_fingerprint = value_sha256(self.embedding_spec)
        # A new namespace cannot accidentally read the historical SQLite cache.
        self.root = Path(str(Path(cache_path).resolve()) + ".remote-fp32-v1")
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.root / "identity.json", self.embedding_spec)
        self.lock = threading.RLock()
        self.http = httpx.Client(base_url=service["url"].rstrip("/"), trust_env=False,
                                 timeout=float(service.get("timeout_seconds", 30)))
        self.cpu_process = self.cpu_connection = None
        self.stats = {"cache_hits": 0, "gpu_encodes": 0, "cpu_encodes": 0, "fallbacks": 0}

    @property
    def truncation_stats(self):
        return self.audit.truncation_stats

    def audit_token_lengths(self, texts):
        return self.audit.audit_token_lengths(texts)

    def health(self):
        response = self.http.get("/health", timeout=float(self.service.get("health_timeout_seconds", 2)))
        response.raise_for_status()
        data = response.json()
        if data["fingerprint"] != self.gpu_fingerprint:
            raise ValueError("Remote embedding service identity changed")
        return data

    def _cpu(self, text):
        if self.cpu_process is None or not self.cpu_process.is_alive():
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe()
            self.cpu_connection = parent
            self.cpu_process = ctx.Process(target=_cpu_worker, args=(child, self.model, self.revision), daemon=True)
            self.cpu_process.start()
            child.close()
        self.cpu_connection.send(text)
        if not self.cpu_connection.poll(float(self.service.get("cpu_timeout_seconds", 180))):
            self.cpu_process.terminate()
            self.cpu_process.join(timeout=5)
            raise TimeoutError("CPU FP32 fallback encoding exceeded its finite deadline")
        data = self.cpu_connection.recv()
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["vector"]

    def _gpu(self, text):
        data = self.health()
        if data["status"] != "ready":
            raise httpx.ConnectError("Remote GPU embedding is unavailable")
        response = self.http.post("/encode", json={"fingerprint": self.gpu_fingerprint, "texts": [text]})
        response.raise_for_status()
        data = response.json()
        if data["fingerprint"] != self.gpu_fingerprint:
            raise ValueError("Embedding response compute identity differs")
        if len(data["vectors"]) != 1:
            raise ValueError("Embedding response must contain exactly one vector")
        return data["vectors"][0]

    @staticmethod
    def _validate(vector):
        import numpy as np
        values = np.asarray(vector, dtype=np.float32)
        if values.shape != (4096,) or not np.isfinite(values).all():
            raise ValueError("Embedding returned an invalid FP32 vector")
        if abs(float(np.linalg.norm(values)) - 1) > 1e-5:
            raise ValueError("Embedding must preserve original normalization")
        return values.tolist(), hashlib.sha256(values.tobytes()).hexdigest()

    def __call__(self, texts):
        return self.embed(texts)

    def embed(self, texts):
        result = []
        # Cross-process exact-text serialization ensures one permanent lane per
        # text, including simultaneous first misses during memory construction.
        with self.lock, (self.root / "routes.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            for text in map(str, texts):
                key = hashlib.sha256(text.encode()).hexdigest()
                path = self.root / "routes" / f"{key}.json"
                if path.exists():
                    record = read_json(path)
                    lane = record["lane"]
                    expected = self.gpu_fingerprint if lane == "gpu" else self.cpu_fingerprint
                    vector, digest = self._validate(record["vector"])
                    if (lane not in {"gpu", "cpu"} or record["text_sha256"] != key
                            or record["lane_fingerprint"] != expected or record["vector_sha256"] != digest):
                        raise ValueError("Embedding cache identity or bytes changed; no fallback allowed")
                    self.stats["cache_hits"] += 1
                    result.append(vector)
                    continue
                tokens = self.audit_token_lengths([text])["max_token_count_before_truncation"]
                if tokens > self.audit.max_length:
                    raise ValueError("Complete embedding input exceeds the original token ceiling; never truncate")
                began, reason, lane = time.monotonic(), "within-tested-envelope", "gpu"
                with span("embedding_encode", token_count=tokens):
                    if tokens > self.embedding_spec["gpu_max_tokens"]:
                        lane, reason = "cpu", "outside-tested-gpu-envelope"
                        vector = self._cpu(text)
                    else:
                        try:
                            vector = self._gpu(text)
                        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {429, 502, 503, 504}:
                                raise
                            lane, reason = "cpu", f"{type(exc).__name__}: {exc}"
                            self.stats["fallbacks"] += 1
                            vector = self._cpu(text)
                    vector, digest = self._validate(vector)
                self.stats[lane + "_encodes"] += 1
                record = {"text_sha256": key, "token_count": tokens, "lane": lane,
                    "lane_fingerprint": self.gpu_fingerprint if lane == "gpu" else self.cpu_fingerprint,
                    "vector_sha256": digest, "vector": vector, "reason": reason,
                    "seconds": time.monotonic()-began}
                atomic_write_json(path, record)
                result.append(vector)
        return result

    def close(self):
        self.http.close()
        if self.cpu_process is not None:
            if self.cpu_process.is_alive():
                self.cpu_connection.send(None)
                self.cpu_process.join(timeout=5)
                if self.cpu_process.is_alive():
                    self.cpu_process.terminate()
                    self.cpu_process.join(timeout=5)
            self.cpu_connection.close()
