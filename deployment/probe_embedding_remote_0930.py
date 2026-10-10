"""Bounded FP32 feasibility probe on explicitly shared remote GPUs.

Uses copied, hash-verified 0930 adapters and existing benchmark inputs. Never
writes an experiment cache or changes a model service. Batch size stays one.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def gpu_snapshot():
    lines = subprocess.check_output([
        "nvidia-smi", "--query-gpu=index,uuid,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits"], text=True, timeout=10).splitlines()
    cards = {}
    for line in lines:
        index, uuid, total, used, free, utilization = [v.strip() for v in line.split(",")]
        cards[int(index)] = {"uuid": uuid, "total_mib": float(total),
            "used_mib": float(used), "free_mib": float(free), "utilization": float(utilization)}
    apps = subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
        "--format=csv,noheader,nounits"], text=True, timeout=10).splitlines()
    own = {}
    for line in apps:
        uuid, pid, memory = [v.strip() for v in line.split(",")]
        if int(pid) == os.getpid():
            own[uuid] = float(memory)
    return {"time": time.time(), "pid": os.getpid(), "cards": cards,
            "own_process_gpu_memory_mib": own}


def run(root):
    manifest = json.loads((root / "remote-test.frozen.json").read_text())
    result = root / "result"
    result.mkdir(exist_ok=False)
    began = time.monotonic()
    deadline = began + manifest["wall_budget_seconds"]
    phase = "source_verification"
    stop, abort = threading.Event(), threading.Event()
    monitor = None
    adapter = model = transformer = None
    torch = None
    state = {"status": "running", "phase": phase, "pid": os.getpid(),
             "started_at": time.time(), "sys_executable": sys.executable,
             "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES")}
    write(root / "state.json", state)

    def stage(name, **extra):
        nonlocal phase
        phase = name
        state.update(phase=name, updated_at=time.time(), elapsed_seconds=time.monotonic()-began,
                     **extra)
        write(root / "state.json", state)
        print(json.dumps({"phase": name, **extra}), flush=True)

    def guard():
        if time.monotonic() > deadline:
            raise TimeoutError("Remote feasibility wall budget exhausted")
        if abort.is_set():
            raise RuntimeError("GPU remaining headroom crossed guard or monitoring failed")

    def monitor_cards():
        with (result / "gpu-monitor.jsonl").open("a") as stream:
            while not stop.is_set():
                try:
                    snapshot = gpu_snapshot()
                    selected = {g: snapshot["cards"][g] for g in manifest["physical_gpus"]}
                    snapshot["cards"] = selected
                    stream.write(json.dumps(snapshot) + "\n")
                    stream.flush()
                    if any(c["free_mib"] < manifest["minimum_remaining_free_gib"] * 1024
                           for c in selected.values()):
                        abort.set()
                except Exception as exc:
                    stream.write(json.dumps({"time": time.time(), "monitor_error":
                        f"{type(exc).__name__}: {exc}"}) + "\n")
                    stream.flush()
                    abort.set()
                stop.wait(1.0)

    try:
        for p, expected in manifest["source_files"].items():
            if digest(root / "bundle" / p) != expected:
                raise ValueError(f"Copied source hash differs: {p}")
        for p, expected in manifest["inputs_files"].items():
            if digest(root / "inputs" / p) != expected:
                raise ValueError(f"Copied input hash differs: {p}")
        if os.getenv("CUDA_VISIBLE_DEVICES") != ",".join(map(str, manifest["physical_gpus"])):
            raise ValueError("Physical GPU assignment differs from frozen manifest")
        for p in [root / "bundle/AgentReview", root / "bundle/deployment", root / "bundle/vendor"]:
            sys.path.insert(0, str(p))
        import numpy as np
        import torch as torch_module
        torch = torch_module
        from memory_evolution.embedding_adapter import Qwen3Embedding, embedding_fingerprint
        from benchmark_embedding_gpu_0930 import rank
        from huggingface_hub import snapshot_download
        from accelerate import dispatch_model
        frozen = json.loads((root / "inputs/inputs.frozen.json").read_text())
        stage("model_hash_verification")
        model_root = Path(snapshot_download(frozen["embedding"]["model"],
            revision=frozen["embedding"]["revision"], local_files_only=True))
        for p, expected in frozen["model_files_sha256"].items():
            if digest(model_root / p) != expected:
                raise ValueError(f"Remote model/tokenizer hash differs: {p}")
            guard()
        write(result / "source-verification.json", {"all_source_and_input_hashes_match": True,
            "all_model_and_tokenizer_hashes_match": True, "model_root": str(model_root),
            "adapter_file": str(sys.modules[Qwen3Embedding.__module__].__file__),
            "torch_file": torch.__file__, "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "libraries": {p: importlib.metadata.version(p) for p in
                ("transformers", "sentence-transformers", "accelerate")}})
        before = gpu_snapshot()
        write(result / "gpu-before.json", before)
        if any(before["cards"][g]["free_mib"] < manifest["minimum_free_gib_before_load"]*1024
               for g in manifest["physical_gpus"]):
            raise RuntimeError("Shared GPUs no longer have required initial headroom")
        if torch.cuda.device_count() != 2:
            raise RuntimeError("Expected exactly two visible GPUs")
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.set_float32_matmul_precision("highest")
        for device in range(2):
            _, total = torch.cuda.mem_get_info(device)
            torch.cuda.set_per_process_memory_fraction(
                manifest["memory_cap_gib_per_gpu"] * 2**30 / total, device)
        monitor = threading.Thread(target=monitor_cards, daemon=True)
        monitor.start()
        stage("model_loading")
        loading = time.monotonic()
        adapter = Qwen3Embedding(frozen["embedding"]["model"], frozen["embedding"]["revision"],
                                 device="cpu", cache_enabled=False, batch_size=1)
        adapter._load()
        from sentence_transformers import SentenceTransformer

        class DispatchedSentenceTransformer(SentenceTransformer):
            def to(self, *args, **kwargs):
                if getattr(self, "_remote_probe_dispatched", False):
                    requested = args[0] if args else kwargs.get("device")
                    if len(args) != 1 or kwargs or str(requested) != "cuda:0":
                        raise ValueError("Dispatched FP32 probe placement is fixed")
                    return self
                return super().to(*args, **kwargs)

        model = adapter._model
        model.__class__ = DispatchedSentenceTransformer
        transformer = model[0].auto_model
        device_map = {"embed_tokens": 0, "rotary_emb": 0, "norm": 1,
                      **{f"layers.{i}": 0 if i < 16 else 1
                         for i in range(len(transformer.layers))}}
        model[0].auto_model = dispatch_model(transformer, device_map=device_map,
                                              main_device="cuda:0")
        model._remote_probe_dispatched = True
        adapter._device = "cuda:0"
        for device in range(2):
            torch.cuda.synchronize(device)
        if {str(p.dtype) for p in model.parameters()} != {"torch.float32"}:
            raise ValueError("Actual model precision is not exclusively FP32")
        if adapter.attention_implementation != "sdpa":
            raise ValueError("Attention implementation differs from local FP32 reference")
        identity = {**adapter.embedding_spec, "compute_dtype": "float32",
            "output_dtype": "float32", "tf32": False, "attention": "sdpa",
            "prompt_name": None, "prompt": None, "padding_side": "left",
            "post_normalization": "numpy-float32-l2", "batch_size": 1,
            "physical_gpus": manifest["physical_gpus"], "device_map": device_map,
            "memory_cap_gib_per_gpu": manifest["memory_cap_gib_per_gpu"],
            "runtime_versions": {p: importlib.metadata.version(p) for p in
                ("torch", "transformers", "sentence-transformers", "accelerate")}}
        write(result / "identity.json", {"specification": identity,
            "fingerprint": embedding_fingerprint(identity),
            "load_seconds": time.monotonic()-loading,
            "parameter_bytes": sum(p.numel()*p.element_size() for p in model.parameters())})
        values = json.loads((root / "inputs/texts.json").read_text())
        baseline = np.load(root / "inputs/cpu-historic-vectors.npy").astype(np.float64)
        baseline /= np.linalg.norm(baseline, axis=1, keepdims=True)
        vectors = np.empty((len(values), 4096), dtype=np.float32)
        observations = []

        def encode(index):
            guard()
            for device in range(2):
                torch.cuda.synchronize(device)
            t = time.monotonic()
            encoded = np.asarray(adapter._embed_uncached_batch([values[index]["text"]])[0],
                                 dtype=np.float32)
            for device in range(2):
                torch.cuda.synchronize(device)
            guard()
            return encoded, time.monotonic()-t

        encode(min(range(len(values)), key=lambda i: values[i]["tokens"]))
        for device in range(2):
            torch.cuda.reset_peak_memory_stats(device)
        stage("same_28_texts_timing")
        timing = []
        for rep in range(2):
            rows = []
            start = time.monotonic()
            for index in frozen["performance_indices"]:
                _, seconds = encode(index)
                rows.append({"index": index, "tokens": values[index]["tokens"],
                             "kind": values[index]["kind"], "seconds": seconds})
            timing.append({"seconds": time.monotonic()-start, "rows": rows})
        write(result / "same-28-timing.json", timing)
        stage("full_376_text_encoding")
        start = time.monotonic()
        for ordinal, index in enumerate(sorted(range(len(values)), key=lambda i: values[i]["tokens"])):
            vectors[index], seconds = encode(index)
            observations.append({"index": index, "tokens": values[index]["tokens"],
                                 "kind": values[index]["kind"], "seconds": seconds})
            if ordinal % 25 == 0:
                stage("full_376_text_encoding", completed_texts=ordinal+1, total_texts=len(values))
        np.save(result / "vectors.npy", vectors)
        write(result / "full-corpus.json", {"seconds": time.monotonic()-start,
            "texts": len(values), "batch_size": 1, "observations": observations})
        observed_vectors = vectors.astype(np.float64)
        observed_vectors /= np.linalg.norm(observed_vectors, axis=1, keepdims=True)
        cosine = np.sum(baseline * observed_vectors, axis=1)
        cases = json.loads((root / "inputs/retrieval-cases.json").read_text())
        retrieval = []
        for case in cases:
            reference, expected = rank(case, values, baseline)
            observed, selected = rank(case, values, observed_vectors)
            if expected != case["historic_selected_ids"]:
                raise ValueError("CPU retrieval reference differs from the historic receipt")
            entry = {"arm": case["arm"], "role": case["role"], "paper_id": case["paper_id"],
                "selected_exact_order": expected == selected, "expected": expected,
                "observed": selected}
            for k in (1, 5, 6, 20, 48):
                a = [r["id"] for r in reference[:k]]
                b = [r["id"] for r in observed[:k]]
                entry[f"top{k}_exact_order"] = a == b
            retrieval.append(entry)
        write(result / "retrieval-details.json", retrieval)
        consistency = {"vector_cosine_min": float(cosine.min()),
            "vector_cosine_mean": float(cosine.mean()),
            "max_abs_vector_difference": float(np.max(np.abs(baseline-observed_vectors))),
            "cases": len(cases),
            "selected_exact_order_cases": sum(v["selected_exact_order"] for v in retrieval),
            **{f"top{k}_exact_order_cases": sum(v[f"top{k}_exact_order"] for v in retrieval)
               for k in (1, 5, 6, 20, 48)}}
        write(result / "consistency.json", consistency)
        if any(not v["selected_exact_order"] or
               not all(v[f"top{k}_exact_order"] for k in (1, 5, 6, 20, 48)) for v in retrieval):
            raise ValueError("Remote FP32 retrieval failed the exact-order acceptance")
        stage("resident_observation", completed_texts=len(values))
        keep_until = time.monotonic() + manifest["resident_observation_seconds"]
        query_indices = [i for i, value in enumerate(values) if value["kind"] == "query"]
        largest = max(range(len(values)), key=lambda i: values[i]["tokens"])
        repeated = []
        turn = 0
        while time.monotonic() < keep_until:
            index = largest if turn % 6 == 0 else query_indices[turn % len(query_indices)]
            vec, seconds = encode(index)
            v = vec.astype(np.float64)
            v /= np.linalg.norm(v)
            repeated.append({"index": index, "seconds": seconds,
                "cosine_vs_first_encoding": float(v @ observed_vectors[index])})
            write(result / "resident-observation.json", {"calls": repeated,
                "elapsed_seconds": manifest["resident_observation_seconds"] -
                    max(0, keep_until-time.monotonic())})
            turn += 1
            stage("resident_observation", repeated_calls=turn)
            stop.wait(min(5., max(0., keep_until-time.monotonic())))
        write(result / "gpu-memory.json", [{"logical_gpu": i,
            "physical_gpu": manifest["physical_gpus"][i],
            "allocated_bytes": torch.cuda.memory_allocated(i),
            "reserved_bytes": torch.cuda.memory_reserved(i),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(i),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(i)} for i in range(2)])
        write(result / "complete.json", {"status": "complete", "consistency": consistency,
            "elapsed_seconds": time.monotonic()-began, "resident_observation_seconds": 180,
            "review_api_calls": 0})
        stage("complete")
        state.update(status="complete")
        write(root / "state.json", state)
    except BaseException as exc:
        error = {"status": "failed", "phase": phase, "exception_type": type(exc).__name__,
            "error": str(exc), "traceback": traceback.format_exc(),
            "elapsed_seconds": time.monotonic()-began}
        write(result / "failure.json", error)
        state.update(error)
        write(root / "state.json", state)
        raise
    finally:
        stop.set()
        if monitor is not None:
            monitor.join(timeout=12)
        if adapter is not None:
            adapter._model = None
        model = transformer = adapter = None
        gc.collect()
        if torch is not None and torch.cuda.is_initialized():
            for device in range(torch.cuda.device_count()):
                with torch.cuda.device(device):
                    torch.cuda.empty_cache()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    run(args.root.resolve())
