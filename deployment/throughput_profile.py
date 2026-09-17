"""Measure real development work; no synthetic progress or cached-score counting."""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import urllib.request


def install(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    def event(value):
        value = {"time": time.time(), "pid": os.getpid(), "thread": threading.get_ident(), **value}
        with lock, (root / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
    def wrap(owner, name, label=None):
        original = getattr(owner, name)
        @functools.wraps(original)
        def measured(*args, **kwargs):
            tags = {key: str(kwargs[key]) for key in ("role", "paper_path", "stage", "component", "request_path") if key in kwargs}
            if label == "embedding_load_if_needed":
                tags["actual_load"] = args[0]._model is None
            if name in {"embed", "_embed_uncached_batch"}:
                texts = args[1]
                tags.update(texts=len(texts), unique_texts=len(set(texts)))
                if name == "_embed_uncached_batch":
                    tags["text_sha256"] = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
            span = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
            stage = label or name
            event({"event": "start", "span": span, "stage": stage, **tags})
            start = time.monotonic()
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:
                event({"event": "end", "span": span, "stage": stage, "seconds": time.monotonic()-start, "status": "failed", "error": str(exc), **tags})
                raise
            event({"event": "end", "span": span, "stage": stage, "seconds": time.monotonic()-start, "status": "complete", **tags})
            return result
        setattr(owner, name, measured)
    from agentreview.research_v2 import shared_experiment as shared, development_coco as coco
    from agentreview.research_v2.llm import OpenAICompletionClient
    from memory_evolution.embedding_adapter import CachedDenseEmbedding, Qwen3Embedding
    for name in ("_model_identity", "_validate_config", "_run_stage"):
        wrap(coco, name)
    for name in ("run_specialist_review", "generate_final_review", "build_memory_snapshot"):
        wrap(shared, name)
    for name in ("preflight", "assess", "_candidate_check", "_bootstrap"):
        wrap(shared.LocalSharedBackend, name)
    wrap(OpenAICompletionClient, "complete", "generation_request")
    wrap(CachedDenseEmbedding, "embed", "embedding_cache_lookup_and_encode")
    wrap(Qwen3Embedding, "_load", "embedding_load_if_needed")
    wrap(Qwen3Embedding, "_embed_uncached_batch", "embedding_uncached_encode")
    stop = threading.Event()
    def monitor():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        names = ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc", "num_preemptions_total", "prefix_cache_queries_total", "prefix_cache_hits_total", "request_success_total", "prompt_tokens_total", "generation_tokens_total")
        while not stop.is_set():
            sample = {"time": time.time(), "services": {}}
            for port in (18001, 13099, 13098):
                try:
                    with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=3) as response:
                        lines = response.read().decode().splitlines()
                    sample["services"][str(port)] = [line for line in lines if any(line.startswith("vllm:"+name+"{") or line.startswith("vllm:"+name+" ") for name in names)]
                except Exception as exc:
                    sample["services"][str(port)] = {"error": str(exc)}
            with (root / "metrics.jsonl").open("a") as handle:
                handle.write(json.dumps(sample) + "\n")
            stop.wait(1)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    return event, stop, thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--pilot", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source) / "AgentReview"))
    from agentreview.research_v2.io import atomic_write_json, read_json
    from agentreview.research_v2.experiment_protocol import load_experiment_protocol
    from agentreview.research_v2.shared_experiment import LocalSharedBackend
    from agentreview.research_v2.development_coco import validate_assessment_receipt
    root = Path(args.root)
    os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(root)
    event, stop, thread = install(root)
    started = time.time()
    status = {"status": "running", "started": started, "pid": os.getpid(), "formal": False}
    atomic_write_json(root / "status.json", status, immutable=False)
    try:
        host = LocalSharedBackend(protocol=load_experiment_protocol(args.protocol), output_root=root / "run")
        pilot = read_json(args.pilot)
        champion = pilot["winner"]["winner"]
        host.frozen_snapshots[champion["candidate_id"]] = champion["identity"]["snapshots"]
        host.preflight()
        candidate = host.seed("vector_search")
        recipe = {"key": "throughput-champion", "candidate": candidate, "kind": "seed", "memory_enabled": True,
                  "split": "static", "paper_ids": [row["paper_id"] for row in host.protocol.records("static")][:2]}
        atomic_write_json(root / "recipe.json", recipe)
        result = host.assess(recipe, epoch=0)
        host.validate(result, recipe=recipe, epoch=0)
        receipt = validate_assessment_receipt(result["assessment"]["path"], expected_paper_ids=recipe["paper_ids"], candidate_identity=result["identity"])
        atomic_write_json(root / "result.json", result)
        seconds = time.time()-started
        status.update(status="complete", ended=time.time(), seconds=seconds, valid_reviews=len(receipt["rows"]),
                      valid_reviews_per_hour=len(receipt["rows"])*3600/seconds, failed_reviews=0, failure_rate=0,
                      paper_scores={row["paper_id"]: row["metrics"]["Paper."] for row in receipt["rows"]})
    except BaseException as exc:
        status.update(status="failed", ended=time.time(), error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        atomic_write_json(root / "status.json", status, immutable=False)
        event({"event": "run_finished", **status})
        stop.set()
        thread.join(timeout=12)
    print(json.dumps(status), flush=True)


if __name__ == "__main__":
    main()
