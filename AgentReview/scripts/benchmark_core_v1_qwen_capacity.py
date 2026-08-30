#!/usr/bin/env python3
"""Measure one externally launched TP4 max-num-seqs configuration.

Run this against three separately preflighted Qwen TP4 runtimes declared as
max-num-seqs 1, 2 and 4.  The script never launches, stops or reconfigures a
model service; this prevents a benchmark helper from mutating an active formal
runtime.  Its JSON output is consumed by the Core v1 capacity selector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")


def canonical_content_sha(content):
    raw = str(content or "").encode("utf-8")
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception:
        value = None
    if isinstance(value, (dict, list)):
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def gpu_free_mib():
    command = [
        "nvidia-smi",
        "--query-gpu=index,memory.free",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    values = {}
    for line in result.stdout.splitlines():
        index, free = [value.strip() for value in line.split(",", 1)]
        values[int(index)] = int(free)
    return tuple(values[index] for index in (0, 1, 2, 3))


def request(endpoint, model, payload, timeout):
    body = {
        "model": model,
        "messages": payload["messages"],
        "temperature": 0,
        "max_tokens": int(payload.get("max_tokens", 256)),
        "seed": int(payload.get("seed", 0)),
    }
    started = time.monotonic()
    wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=wire,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    elapsed = time.monotonic() - started
    value = json.loads(raw.decode("utf-8"))
    choice = value["choices"][0]
    content = choice["message"].get("content") or ""
    usage = value.get("usage") or {}
    return {
        "canonical_output_sha256": canonical_content_sha(content),
        "finish_reason": choice.get("finish_reason"),
        "protocol_disposition": "SUCCESS" if content else "EMPTY_RESPONSE",
        "latency_seconds": elapsed,
        "usage": usage,
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
    }


def percentile95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def load_workload(path):
    output = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                output.append(json.loads(line))
    if not output:
        raise ValueError("Captured benchmark-independent workload is empty")
    if {item.get("role") for item in output} != set(ROLES):
        raise ValueError("Capacity workload must cover all five roles")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--max-num-seqs", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--repetition", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    workload = load_workload(args.workload)
    free_after_load = gpu_free_mib()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.max_num_seqs) as executor:
        futures = [
            executor.submit(request, args.endpoint, args.model, item, args.timeout)
            for item in workload
        ]
        results = [future.result() for future in futures]
    elapsed = time.monotonic() - started
    free_after_burst = gpu_free_mib()
    output = {
        "max_num_seqs": args.max_num_seqs,
        "repetition": args.repetition,
        "canonical_output_sha256": [item["canonical_output_sha256"] for item in results],
        "finish_reasons": [item["finish_reason"] for item in results],
        "protocol_dispositions": [item["protocol_disposition"] for item in results],
        "elapsed_seconds": elapsed,
        "completed_requests": len(results),
        "p95_latency_seconds": percentile95([item["latency_seconds"] for item in results]),
        "tp_gpu_free_mib": list(free_after_burst),
        "tp_gpu_free_mib_after_load": list(free_after_load),
        "oom_count": 0,
        "schema_failure_count": sum(
            item["protocol_disposition"] == "EMPTY_RESPONSE" for item in results
        ),
        "protocol_failure_count": 0,
        "request_results": results,
        "workload_sha256": hashlib.sha256(Path(args.workload).read_bytes()).hexdigest(),
    }
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "max_num_seqs", "repetition", "elapsed_seconds", "p95_latency_seconds", "tp_gpu_free_mib"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
