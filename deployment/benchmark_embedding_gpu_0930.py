"""Isolated, read-only-source Sentence Transformers embedding benchmark.

This does not configure production or run any review/search/CoCo request.
FP32 model sharding uses a private Accelerate installation; ST tokenization,
forward, pooling and encode remain the installed implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
MORPH = {"QUAL": ("MET", "EXP", "STA", "REP", "CMP"),
         "CLAR": ("WRT", "NOT", "FIG"), "SIGN": ("BRD", "DOM", "SOT", "IMP"),
         "ORIG": ("PROB", "MTH", "COM", "ANL", "EXP", "NEG"),
         "POL": ("ANO", "DAT", "ETH", "IMP", "PLG")}


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def prepare(source: Path, output: Path) -> None:
    import numpy as np
    from memory_evolution.embedding_adapter import Qwen3Embedding
    from huggingface_hub import snapshot_download

    # Never open an old experiment with a writable SQLite connection.
    database = source / "embedding.sqlite3"
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    rows = {r[0]: r[1:] for r in connection.execute(
        "SELECT text_sha256, vector_float32, vector_sha256, dimension, "
        "semantics_version FROM embeddings")}
    connection.close()
    protocol = json.loads((source / "protocol.frozen.json").read_text())
    config = protocol["embedding"]
    snapshots = {}
    for role in ROLES:
        for p in sorted(source.rglob(role + ".json")):
            d = json.loads(p.read_text())
            if "snapshot" in d:
                snapshots[d["snapshot_id"]] = (p, d)
    texts, cases, sources = {}, [], {}

    def register(text, kind, provenance):
        key = sha(text.encode())
        if key not in rows:
            raise ValueError(f"No original CPU vector for {kind}: {key}")
        blob, vector_sha, dimension, semantics = rows[key]
        if sha(blob) != vector_sha or dimension != 4096:
            raise ValueError(f"Invalid original vector: {key}")
        if key not in texts:
            texts[key] = {"sha256": key, "text": text, "kind": kind,
                          "sources": [], "cpu_vector_sha256": vector_sha,
                          "original_semantics_version": semantics}
        texts[key]["sources"].append(provenance)
        return key

    for arm in ("seed-vector_search", "champion"):
        for role in ROLES:
            pattern = f"final/frozen-test-generation/{arm}/reviews/*/*/{role}.json"
            for p in sorted(source.glob(pattern)):
                d = json.loads(p.read_text())
                m = d["retrieval_provenance"]["memory"]
                snapshot_path, snapshot = snapshots[m["snapshot_id"]]
                sources[str(p)] = sha(p.read_bytes())
                sources[str(snapshot_path)] = sha(snapshot_path.read_bytes())
                query = m["query"]["query_text"]
                if arm == "champion":
                    # Exact candidate-0002 probe; matching the historic cache hash
                    # validates that this is the actual text, not a new query.
                    probe = f"{role} reviewer dimension for the current paper."
                    if query.strip():
                        probe += " " + query.strip()[:1200]
                    probe += " Focus subcategories: " + ", ".join(MORPH[role]) + "."
                else:
                    probe = query
                query_key = register(probe, "query", str(p))
                records = []
                for c in snapshot["snapshot"]["collections"].values():
                    for record in c["records"]:
                        key = register(record["text"], "memory", str(snapshot_path))
                        if record["embedding_sha256"] != rows[key][1]:
                            raise ValueError("Snapshot and original vector SHA differ")
                        records.append({"id": record["id"], "sha256": key,
                                        "metadata": record["metadata"]})
                expected = next(log["payload"] for log in m["logs"]
                                if log["event"] == "retrieval_hits")
                cases.append({"arm": arm, "role": role, "paper_id": d["paper_id"],
                              "snapshot_id": m["snapshot_id"],
                              "program_sha256": m["program_sha256"],
                              "query_sha256": query_key, "records": records,
                              "historic_selected_ids": [v["id"] for v in expected]})
    if len(cases) != 30:
        raise ValueError(f"Expected 2 arms x 5 roles x 3 papers, got {len(cases)}")
    adapter = Qwen3Embedding(config["model"], config["revision"], device="cpu",
                             cache_enabled=False)
    adapter.audit_token_lengths([v["text"] for v in texts.values()])
    for value in texts.values():
        value["tokens"] = len(adapter._tokenizer(value["text"],
            add_special_tokens=True, truncation=False)["input_ids"])
        if value["tokens"] > adapter.max_length:
            raise ValueError("Input over production token budget")
    values = sorted(texts.values(), key=lambda v: v["sha256"])
    np.save(output / "cpu-historic-vectors.npy", np.stack([
        np.frombuffer(rows[v["sha256"]][0], dtype="<f4") for v in values]))
    write(output / "texts.json", values)
    write(output / "retrieval-cases.json", cases)
    memory = sorted((i for i, v in enumerate(values) if v["kind"] == "memory"),
                    key=lambda i: values[i]["tokens"])
    queries = sorted((i for i, v in enumerate(values) if v["kind"] == "query"),
                     key=lambda i: values[i]["tokens"])
    # Stratified actual texts, including the longest record. Same subset for
    # CPU and GPU latency measurements; full old vectors for retrieval reference.
    picks = list(dict.fromkeys([memory[round(j * (len(memory)-1)/19)] for j in range(20)]
                             + [queries[round(j * (len(queries)-1)/7)] for j in range(8)]))
    model_root = Path(snapshot_download(config["model"], revision=config["revision"],
                                       local_files_only=True))
    model_files = {}
    for p in model_root.rglob("*"):
        if p.is_file() and (p.suffix == ".json" or p.suffix == ".safetensors"):
            # Hash weights as well as tokenizer/config: exact loaded model identity.
            digest = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            model_files[str(p.relative_to(model_root))] = digest.hexdigest()
    write(output / "inputs.frozen.json", {
        "source": str(source), "source_database_sha256": sha(database.read_bytes()),
        "source_files": sources, "embedding": config, "model_root": str(model_root),
        "model_files_sha256": model_files, "adapter_spec": adapter.embedding_spec,
        "performance_indices": picks, "texts_count": len(values),
        "token_summary": {"min": min(v["tokens"] for v in values),
                          "max": max(v["tokens"] for v in values),
                          "total": sum(v["tokens"] for v in values)},
        "retrieval_cases": len(cases), "baseline_reference": "historic CPU FP32 cache",
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                                              text=True).strip(),
        "script_sha256": sha(Path(__file__).read_bytes()),
        "budget": {"wall_seconds": 3600, "review_api_calls": 0},
        "frozen_at": time.time(),
    })
    print(json.dumps({"prepared": str(output), "texts": len(values),
                      "cases": len(cases), "timing_texts": len(picks)}), flush=True)


def sync(torch):
    for i in range(torch.cuda.device_count()):
        torch.cuda.synchronize(i)


def worker(output: Path, variant: str) -> None:
    import numpy as np
    import torch
    from memory_evolution.embedding_adapter import Qwen3Embedding, embedding_fingerprint

    frozen = json.loads((output / "inputs.frozen.json").read_text())
    if time.time() - frozen["frozen_at"] >= frozen["budget"]["wall_seconds"]:
        raise TimeoutError("Standalone benchmark wall budget exhausted")
    directory = output / variant
    directory.mkdir(exist_ok=False)
    values = json.loads((output / "texts.json").read_text())
    texts = [v["text"] for v in values]
    indices = frozen["performance_indices"]
    dtype_name = "bfloat16" if variant == "gpu-bf16" else "float32"
    is_gpu = variant != "cpu-fp32"
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    if is_gpu:
        expected_devices = 2 if variant == "gpu-fp32" else 1
        if torch.cuda.device_count() != expected_devices:
            raise ValueError(f"{variant} requires exactly {expected_devices} visible GPUs")
        for i in range(expected_devices):
            free, total = torch.cuda.mem_get_info(i)
            if free < 23 * 1024**3:
                raise RuntimeError(f"GPU {i} is no longer idle; refusing shared allocation")
    adapter = Qwen3Embedding(frozen["embedding"]["model"],
        frozen["embedding"]["revision"], device="cpu" if variant != "gpu-bf16" else "cuda:0",
        cache_enabled=False, batch_size=1)
    start = time.perf_counter()
    adapter._load()
    device_map = None
    if variant == "gpu-fp32":
        from accelerate import dispatch_model
        from sentence_transformers import SentenceTransformer

        class DispatchedSentenceTransformer(SentenceTransformer):
            """Keep ST.encode from moving a correctly dispatched model to one GPU."""
            def to(self, *args, **kwargs):
                if getattr(self, "_benchmark_dispatched", False):
                    requested = args[0] if args else kwargs.get("device")
                    if str(requested) != "cuda:0" or len(args) != 1 or kwargs:
                        raise ValueError("Dispatched benchmark model device/dtype is fixed")
                    return self
                return super().to(*args, **kwargs)

        model = adapter._model
        model.__class__ = DispatchedSentenceTransformer
        transformer = model[0].auto_model
        # Qwen3Model: put embedding and first 16 decoder layers on GPU 0;
        # remaining 20 layers and final norm on GPU 1. No CPU/disk offload.
        device_map = {"embed_tokens": 0, "rotary_emb": 0, "norm": 1}
        device_map.update({f"layers.{i}": 0 if i < 16 else 1
                           for i in range(len(transformer.layers))})
        model[0].auto_model = dispatch_model(transformer, device_map=device_map,
                                             main_device="cuda:0")
        model._benchmark_dispatched = True
        adapter._device = "cuda:0"
    if is_gpu:
        sync(torch)
    load_seconds = time.perf_counter() - start
    parameters = list(adapter._model.parameters())
    observed_dtypes = sorted({str(p.dtype) for p in parameters})
    if observed_dtypes != ["torch." + dtype_name]:
        raise ValueError(f"Unexpected loaded precision: {observed_dtypes}")
    identity = {**adapter.embedding_spec,
        "compute_dtype": dtype_name, "output_dtype": "float32",
        "attention_implementation": adapter.attention_implementation,
        "device_topology": "cpu" if not is_gpu else (
            "two-GPU-layer-sharding" if device_map else "single-GPU"),
        "device_map": device_map, "tf32": False,
        "prompt_name": None, "prompt": None, "padding_side": "left",
        "post_normalization": "numpy-float32-l2",
        "libraries": {p: importlib.metadata.version(p) for p in
            ("torch", "transformers", "sentence-transformers")},
    }
    fingerprint = embedding_fingerprint(identity)
    write(directory / "identity.json", {"specification": identity,
        "fingerprint": fingerprint, "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
        "sys_executable": sys.executable, "cwd": os.getcwd(),
        "adapter_file": str(sys.modules[Qwen3Embedding.__module__].__file__),
        "load_seconds": load_seconds, "parameter_count": sum(p.numel() for p in parameters),
        "parameter_bytes": sum(p.numel() * p.element_size() for p in parameters)})
    # The benchmark has no writable production cache. Each variant's vectors and
    # complete precision-aware fingerprint are kept in its own directory.
    warmup = min(indices, key=lambda i: values[i]["tokens"])
    adapter._embed_uncached_batch([texts[warmup]])
    if is_gpu:
        sync(torch)
        for i in range(torch.cuda.device_count()):
            torch.cuda.reset_peak_memory_stats(i)
    measurements = []
    batches = (1, 4, 8) if is_gpu else (1,)
    reference_subset = None
    for batch_size in batches:
        adapter.batch_size = batch_size
        runs = []
        vectors = None
        repetitions = 2 if is_gpu else 1
        for rep in range(repetitions):
            began = time.perf_counter()
            chunks, observations = [], []
            for first in range(0, len(indices), batch_size):
                selected = indices[first:first+batch_size]
                if is_gpu:
                    sync(torch)
                t = time.perf_counter()
                encoded = adapter._embed_uncached_batch([texts[i] for i in selected])
                if is_gpu:
                    sync(torch)
                elapsed = time.perf_counter() - t
                chunks.extend(encoded)
                observations.append({"indices": selected, "seconds": elapsed,
                    "tokens": sum(values[i]["tokens"] for i in selected),
                    "max_tokens": max(values[i]["tokens"] for i in selected)})
                print(json.dumps({"variant": variant, "batch": batch_size,
                    "rep": rep, "first": first, "seconds": round(elapsed, 4)}), flush=True)
                if time.time() - frozen["frozen_at"] > frozen["budget"]["wall_seconds"]:
                    raise TimeoutError("Benchmark wall budget exhausted")
            vectors = np.asarray(chunks, dtype=np.float32)
            runs.append({"seconds": time.perf_counter() - began, "batches": observations})
        np.save(directory / f"subset-b{batch_size}.npy", vectors)
        if reference_subset is None:
            reference_subset = vectors
        measurements.append({"batch_size": batch_size, "runs": runs,
            "texts": len(indices), "tokens": sum(values[i]["tokens"] for i in indices),
            "batch_vs_b1_cosine_min": float(np.min(np.sum(vectors * reference_subset, axis=1)))})
        write(directory / "measurements.json", measurements)
    if is_gpu:
        # Full real collections plus actual queries for retrieval consistency.
        adapter.batch_size = 8
        began = time.perf_counter()
        chunks, observations = [], []
        # Length buckets for the separate offline corpus test, not query semantics.
        order = sorted(range(len(texts)), key=lambda i: values[i]["tokens"])
        vectors = np.empty((len(texts), 4096), dtype=np.float32)
        for first in range(0, len(order), 8):
            selected = order[first:first+8]
            sync(torch)
            t = time.perf_counter()
            vectors[selected] = adapter._embed_uncached_batch([texts[i] for i in selected])
            sync(torch)
            observations.append({"indices": selected, "seconds": time.perf_counter() - t})
            print(json.dumps({"variant": variant, "full_corpus": first,
                              "total": len(texts)}), flush=True)
        sync(torch)
        np.save(directory / "full-vectors.npy", vectors)
        write(directory / "full-corpus.json", {"texts": len(texts),
            "seconds": time.perf_counter() - began, "batch_size": 8,
            "length_buckets": True, "batches": observations})
        write(directory / "gpu-memory.json", [{"logical_gpu": i,
            "allocated_bytes": torch.cuda.memory_allocated(i),
            "reserved_bytes": torch.cuda.memory_reserved(i),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(i),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(i)}
            for i in range(torch.cuda.device_count())])
    write(directory / "complete.json", {"status": "complete", "fingerprint": fingerprint})


def rank(case, values, vectors):
    import numpy as np
    index = {v["sha256"]: i for i, v in enumerate(values)}
    query = vectors[index[case["query_sha256"]]].astype(np.float64)
    query /= np.linalg.norm(query)
    scored = []
    for record in case["records"]:
        doc = vectors[index[record["sha256"]]].astype(np.float64)
        score = float(doc @ query / np.linalg.norm(doc))
        scored.append({**record, "score": score})
    scored.sort(key=lambda v: v["score"], reverse=True)
    if case["arm"] == "champion":
        seen, candidates = set(), []
        for record in scored[:48]:
            if record["metadata"].get("role") != case["role"]:
                continue
            body = values[index[record["sha256"]]]["text"].strip()
            key = body[:160]
            if key in seen:
                continue
            seen.add(key)
            preferred = record["metadata"].get("subcategory") in MORPH[case["role"]]
            candidates.append((preferred, record))
        candidates.sort(key=lambda v: (-int(v[0]), -v[1]["score"]))
        selected = [r["id"] for _, r in candidates[:6]]
    else:
        selected = [r["id"] for r in scored[:20]
                    if r["metadata"].get("role") == case["role"]][:5]
    return scored, selected


def compare(output: Path) -> None:
    import numpy as np
    values = json.loads((output / "texts.json").read_text())
    cases = json.loads((output / "retrieval-cases.json").read_text())
    frozen = json.loads((output / "inputs.frozen.json").read_text())
    baseline = np.load(output / "cpu-historic-vectors.npy").astype(np.float64)
    baseline /= np.linalg.norm(baseline, axis=1, keepdims=True)
    result = {}
    for variant in ("cpu-fp32", "gpu-fp32", "gpu-bf16"):
        directory = output / variant
        if not (directory / "complete.json").exists():
            result[variant] = {"status": "incomplete"}
            continue
        indices = frozen["performance_indices"]
        subset = np.load(directory / "subset-b1.npy").astype(np.float64)
        subset /= np.linalg.norm(subset, axis=1, keepdims=True)
        cosine = np.sum(subset * baseline[indices], axis=1)
        row = {"status": "complete", "fresh_b1_vs_historic": {
            "cosine_min": float(cosine.min()), "cosine_mean": float(cosine.mean()),
            "max_abs_difference": float(np.max(np.abs(subset - baseline[indices])))}}
        if variant != "cpu-fp32":
            vectors = np.load(directory / "full-vectors.npy").astype(np.float64)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            cosine = np.sum(vectors * baseline, axis=1)
            row["full_vector_comparison"] = {"cosine_min": float(cosine.min()),
                "cosine_mean": float(cosine.mean()),
                "max_abs_difference": float(np.max(np.abs(vectors - baseline)))}
            details = []
            for case in cases:
                ref, expected = rank(case, values, baseline)
                observed, selected = rank(case, values, vectors)
                if expected != case["historic_selected_ids"]:
                    raise ValueError(f"CPU replay differs from actual retrieval: {case['role']} {case['arm']}")
                entry = {"arm": case["arm"], "role": case["role"], "paper": case["paper_id"],
                    "historic_replay_exact": True, "selected_exact_order": expected == selected,
                    "selected_set_overlap": len(set(expected) & set(selected)) / len(expected),
                    "expected_selected": expected, "observed_selected": selected}
                for k in (1, 5, 6, 20, 48):
                    a, b = [r["id"] for r in ref[:k]], [r["id"] for r in observed[:k]]
                    entry[f"top{k}_exact_order"] = a == b
                    entry[f"top{k}_set_overlap"] = len(set(a) & set(b)) / len(a)
                details.append(entry)
            row["retrieval"] = {"cases": len(details),
                "historic_replay_exact_cases": sum(v["historic_replay_exact"] for v in details),
                "selected_exact_order_cases": sum(v["selected_exact_order"] for v in details),
                "selected_mean_set_overlap": float(np.mean([v["selected_set_overlap"] for v in details])),
                **{f"top{k}_exact_order_cases": sum(v[f"top{k}_exact_order"] for v in details)
                   for k in (1, 5, 6, 20, 48)},
                **{f"top{k}_mean_set_overlap": float(np.mean([v[f"top{k}_set_overlap"] for v in details]))
                   for k in (1, 5, 6, 20, 48)}}
            write(directory / "retrieval-details.json", details)
        result[variant] = row
    # Hash all historical inputs again after testing, without touching them.
    changed = [path for path, expected in frozen["source_files"].items()
               if sha(Path(path).read_bytes()) != expected]
    database = Path(frozen["source"]) / "embedding.sqlite3"
    if sha(database.read_bytes()) != frozen["source_database_sha256"]:
        changed.append(str(database))
    result["historical_source_verification"] = {"changed": changed,
                                                "files": len(frozen["source_files"]) + 1}
    if changed:
        raise ValueError("Historical source changed during isolated benchmark")
    write(output / "comparison.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "worker", "compare"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=REPO / "artifacts/smallflow-0930-20261009-v2")
    parser.add_argument("--variant", choices=("cpu-fp32", "gpu-fp32", "gpu-bf16"))
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action == "prepare":
        output.mkdir(parents=True, exist_ok=True)
        if (output / "inputs.frozen.json").exists():
            raise FileExistsError("Use a new benchmark identity; frozen inputs already exist")
        prepare(args.source.resolve(), output)
    elif args.action == "worker":
        if not args.variant:
            parser.error("worker requires --variant")
        worker(output, args.variant)
    else:
        compare(output)


if __name__ == "__main__":
    main()
