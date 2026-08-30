#!/usr/bin/env python3
"""Validate the fresh Corpus TP4 service before any A request is sent."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Mapping

from agentreview.review_corpus import canonical_json_bytes


def _http_json(url: str, body=None, timeout=30):
    data = None if body is None else canonical_json_bytes(body)
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
    return raw, json.loads(raw.decode("utf-8", errors="strict"))


def _descendants(root_pid: int):
    parents = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text().split()
            parents[int(path.name)] = int(fields[3])
        except (OSError, ValueError, IndexError):
            continue
    allowed = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in allowed and pid not in allowed:
                allowed.add(pid)
                changed = True
    return allowed


def _status_field(pid: int, name: str):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"Missing {name} for PID {pid}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-cpus", default="0-35,72-107")
    parser.add_argument("--expected-memory-node", type=int, default=0)
    parser.add_argument("--min-free-mib", type=int, default=768)
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if (
        profile.get("profile_name") != "CORPUS_BUILD_RUNTIME_PROFILE"
        or profile.get("tensor_parallel_size") != 4
        or profile.get("max_num_seqs") != 1
        or profile.get("corpus_build_max_model_len") != 32768
    ):
        raise RuntimeError("Corpus runtime profile is not the frozen bootstrap profile")
    command = Path(f"/proc/{args.server_pid}/cmdline").read_bytes().split(b"\0")
    command_text = [item.decode("utf-8", errors="strict") for item in command if item]
    required_args = {
        "--tensor-parallel-size": "4",
        "--dtype": "bfloat16",
        "--max-model-len": str(profile["corpus_build_max_model_len"]),
        "--max-num-seqs": "1",
        "--gpu-memory-utilization": profile["gpu_memory_utilization"],
        "--reasoning-parser": profile["reasoning_parser"],
    }
    for flag, expected in required_args.items():
        try:
            actual = command_text[command_text.index(flag) + 1]
        except (ValueError, IndexError) as exc:
            raise RuntimeError(f"Corpus Qwen command lacks {flag}") from exc
        if actual != expected:
            raise RuntimeError(f"Corpus Qwen {flag} mismatch: {actual} != {expected}")
    for flag in (
        "--language-model-only",
        "--enable-prefix-caching",
        "--disable-custom-all-reduce",
    ):
        if flag not in command_text:
            raise RuntimeError(f"Corpus Qwen command lacks {flag}")

    cpus = _status_field(args.server_pid, "Cpus_allowed_list")
    mems = _status_field(args.server_pid, "Mems_allowed_list")
    numa_policies = {
        line.split()[1]
        for line in Path(f"/proc/{args.server_pid}/numa_maps").read_text().splitlines()
        if len(line.split()) >= 2
    }
    expected_policy = f"bind:{args.expected_memory_node}"
    # Some CUDA/vLLM mappings explicitly use MPOL_LOCAL.  With the process CPU
    # affinity restricted entirely to NUMA0, LOCAL is still node-0-local and is
    # compatible with the frozen NUMA0 policy.  Any interleave, preferred, or
    # other-node bind remains a hard failure.
    allowed_memory_policies = {expected_policy, "local"}
    if cpus != args.expected_cpus or not numa_policies <= allowed_memory_policies:
        raise RuntimeError(
            "NUMA affinity mismatch: "
            f"cpus={cpus!r}, allowed_memory_nodes={mems!r}, "
            f"memory_policies={sorted(numa_policies)!r}"
        )

    endpoint = args.endpoint.rstrip("/")
    models_raw, models = _http_json(endpoint + "/models")
    served_ids = [item.get("id") for item in models.get("data", [])]
    expected_model = profile["generation_semantics"]["served_model_name"]
    if served_ids != [expected_model]:
        raise RuntimeError(f"Served model identity mismatch: {served_ids}")

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_path, local_files_only=True, trust_remote_code=False
    )
    messages = [
        {
            "role": "system",
            "content": "Benchmark-independent Corpus token-accounting preflight.",
        },
        {"role": "user", "content": "Return the single word READY."},
    ]
    input_ids = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if isinstance(input_ids, Mapping):
        input_ids = input_ids["input_ids"]
    local_prompt_tokens = len(input_ids)
    smoke_request = {
        **profile["generation_semantics"]["request_json"],
        "messages": messages,
        "max_tokens": 8,
    }
    response_raw, response = _http_json(
        endpoint + "/chat/completions", smoke_request, timeout=600
    )
    provider_prompt_tokens = response.get("usage", {}).get("prompt_tokens")
    if provider_prompt_tokens != local_prompt_tokens:
        raise RuntimeError(
            "Strict token accounting mismatch: "
            f"local={local_prompt_tokens}, provider={provider_prompt_tokens!r}"
        )

    gpu_rows = []
    query = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    )
    for line in query.splitlines():
        index, uuid, total, used, free, utilization = [
            item.strip() for item in line.split(",")
        ]
        gpu_rows.append(
            {
                "index": int(index),
                "uuid": uuid,
                "memory_total_mib": int(total),
                "memory_used_mib": int(used),
                "memory_free_mib": int(free),
                "utilization_percent": int(utilization),
            }
        )
    if any(row["memory_free_mib"] < args.min_free_mib for row in gpu_rows[:4]):
        raise RuntimeError(f"Corpus TP4 free-memory gate failed: {gpu_rows[:4]}")
    if any(row["memory_used_mib"] > 32 for row in gpu_rows[4:]):
        raise RuntimeError(f"Unauthorized compute/resource use on GPU4-7: {gpu_rows[4:]}")

    allowed_pids = _descendants(args.server_pid)
    compute_processes = []
    try:
        applications = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,gpu_uuid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
    except subprocess.CalledProcessError:
        applications = ""
    for line in applications.splitlines():
        if not line.strip():
            continue
        pid, uuid, name, used = [item.strip() for item in line.split(",", 3)]
        item = {"pid": int(pid), "gpu_uuid": uuid, "process_name": name, "used_mib": used}
        compute_processes.append(item)
        if item["pid"] not in allowed_pids:
            raise RuntimeError(f"GPU compute process is outside Corpus allowlist: {item}")

    payload = {
        "schema_version": "core-v1-corpus-service-preflight-v1",
        "status": "PASS",
        "corpus_build_runtime_profile_sha256": profile[
            "corpus_build_runtime_profile_sha256"
        ],
        "server_pid": args.server_pid,
        "command": command_text,
        "cpu_affinity": cpus,
        "memory_nodes_allowed_by_kernel": mems,
        "memory_policy": expected_policy,
        "observed_memory_policies": sorted(numa_policies),
        "served_model_ids": served_ids,
        "models_response_sha256": hashlib.sha256(models_raw).hexdigest(),
        "token_accounting": {
            "local_prompt_tokens": local_prompt_tokens,
            "provider_prompt_tokens": provider_prompt_tokens,
            "response_raw_base64": base64.b64encode(response_raw).decode("ascii"),
            "response_raw_sha256": hashlib.sha256(response_raw).hexdigest(),
        },
        "gpu_state": gpu_rows,
        "allowed_process_ids": sorted(allowed_pids),
        "gpu_compute_processes": compute_processes,
        "runtime_packages": {
            name: importlib.metadata.version(name)
            for name in ("vllm", "torch", "transformers")
        },
    }
    payload["preflight_sha256"] = hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".tmp.{os.getpid()}")
    with temporary.open("wb") as handle:
        handle.write(canonical_json_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output)
    print(json.dumps({"status": "PASS", "preflight_sha256": payload["preflight_sha256"]}))


if __name__ == "__main__":
    main()
