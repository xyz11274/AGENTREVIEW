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

from agentreview.corpus_semantic_audit import semantic_boundary_violations
from agentreview.review_corpus import canonical_json_bytes
from agentreview.review_corpus import CanonicalFullReviewRecord
from agentreview.review_corpus_builder import (
    ROLE_ORDER,
    CorpusPaper,
    build_corpus_reviewer_request,
)


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


def _local_prompt_tokens(tokenizer, messages):
    input_ids = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if isinstance(input_ids, Mapping):
        input_ids = input_ids["input_ids"]
    return len(input_ids)


def _diagnostic_constrained_smoke(endpoint, profile, tokenizer):
    expected = {
        "latex": r"\circledast",
        "quotes": 'a "quoted" value',
        "slash_backslash": "/ and \\",
        "newline": "line one\nline two",
        "crlf": "line one\r\nline two",
        "unicode": "中文 reviewer",
        "anchors": ["SRC-001", "SRC-002"],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Benchmark-independent constrained JSON transport smoke. "
                "Return only the requested object."
            ),
        },
        {
            "role": "user",
            "content": "Return this exact logical payload:\n"
            + json.dumps(expected, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(expected),
        "properties": {
            "latex": {"type": "string", "const": expected["latex"]},
            "quotes": {"type": "string", "const": expected["quotes"]},
            "slash_backslash": {
                "type": "string",
                "const": expected["slash_backslash"],
            },
            "newline": {"type": "string", "const": expected["newline"]},
            "crlf": {"type": "string", "const": expected["crlf"]},
            "unicode": {"type": "string", "const": expected["unicode"]},
            "anchors": {
                "type": "array",
                "const": expected["anchors"],
                "items": {"type": "string", "pattern": r"^SRC-\d{3}$"},
                "minItems": 2,
                "maxItems": 2,
            },
        },
    }
    body = {
        **profile["generation_semantics"]["request_json"],
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "corpus_special_character_smoke_v4",
                "strict": True,
                "schema": schema,
            },
        },
    }
    raw, response = _http_json(
        endpoint + "/chat/completions", body, timeout=600
    )
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
        actual = json.loads(content)
        prompt_tokens = response["usage"]["prompt_tokens"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Constrained special-character smoke returned invalid JSON") from exc
    if choice.get("finish_reason") != "stop" or actual != expected:
        raise RuntimeError(
            "Constrained special-character smoke changed logical bytes: "
            f"finish_reason={choice.get('finish_reason')!r}, actual={actual!r}"
        )
    local_tokens = _local_prompt_tokens(tokenizer, messages)
    if prompt_tokens != local_tokens:
        raise RuntimeError(
            "Strict token accounting mismatch in constrained smoke: "
            f"local={local_tokens}, provider={prompt_tokens!r}"
        )
    return {
        "status": "PASS",
        "logical_payload_sha256": hashlib.sha256(
            canonical_json_bytes(actual)
        ).hexdigest(),
        "raw_response_base64": base64.b64encode(raw).decode("ascii"),
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "local_prompt_tokens": local_tokens,
        "provider_prompt_tokens": prompt_tokens,
        "finish_reason": choice.get("finish_reason"),
    }


def _synthetic_paper():
    context = (
        "Context protocol: benchmark-independent-corpus-smoke-v1\n"
        "=== CANDIDATE-VISIBLE METADATA ===\n"
        "paper_id: SYNTH-CORPUS-V4\n"
        "title: Synthetic Structured Review Case\n"
        "venue: synthetic\n"
        "year: 2000\n"
        "reviewer_role: QUAL\n"
        "=== END CANDIDATE-VISIBLE METADATA ===\n\n"
        "=== BEGIN PAPER (MD) ===\n"
        "# METHOD\n"
        "The paper presents a deterministic method called \\circledast and claims a novel contribution.\n"
        "# EXPERIMENT\n"
        "The paper reports a benchmark improvement and describes it as state-of-the-art, using one synthetic run.\n"
        "Figure 1 is provided only as a link. Caption: Reported synthetic score. No image pixels are included.\n"
        "The text contains author names but supplies no venue policy, overlap evidence, compliance rule, human data, or material ethical-risk pathway.\n"
        "=== END PAPER (MD) ===\n\n"
        "=== SOURCE ANCHORS ===\n"
        "SRC-001 | page=1 | type=text | text=Method \\circledast with a quoted \"term\".\n"
        "SRC-002 | page=1 | type=image | text=Figure 1 caption only; no pixels.\n"
        "SRC-003 | page=1 | type=text | text=Reported benchmark and novelty claims.\n"
        "=== END SOURCE ANCHORS ==="
    )
    return CorpusPaper.from_context(
        paper_id="SYNTH-CORPUS-V4", canonical_paper_context=context
    )


def _role_semantic_smoke(endpoint, profile, tokenizer):
    paper = _synthetic_paper()
    generation_sha = profile["corpus_request_materialization_sha256"]
    outputs = []
    for role in ROLE_ORDER:
        request = build_corpus_reviewer_request(
            paper,
            role,
            generation_identity_sha256=generation_sha,
        )
        body = {
            **profile["generation_semantics"]["request_json"],
            "messages": [dict(item) for item in request.messages],
            "response_format": request.response_format,
        }
        raw, response = _http_json(
            endpoint + "/chat/completions", body, timeout=600
        )
        try:
            choice = response["choices"][0]
            content = choice["message"]["content"].encode("utf-8")
            provider_tokens = response["usage"]["prompt_tokens"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{role} semantic smoke response is malformed") from exc
        local_tokens = _local_prompt_tokens(tokenizer, request.messages)
        if provider_tokens != local_tokens:
            raise RuntimeError(
                f"{role} semantic smoke token accounting mismatch: "
                f"local={local_tokens}, provider={provider_tokens!r}"
            )
        if choice.get("finish_reason") != "stop":
            raise RuntimeError(
                f"{role} semantic smoke finish reason is {choice.get('finish_reason')!r}"
            )
        record = CanonicalFullReviewRecord.from_model_response(
            content,
            expected_role=role,
            expected_paper_id=paper.paper_id,
            paper_identity_sha256=paper.paper_identity_sha256,
            structural_context_sha256=paper.structural_context_sha256,
            reviewer_identity_sha256=profile["generation_semantics"][
                "model_artifact_identity_sha256"
            ],
            prompt_identity_sha256=request.prompt_identity_sha256,
            request_identity_sha256=request.request_identity_sha256,
            allowed_paper_anchor_ids=paper.allowed_paper_anchor_ids,
        )
        violations = semantic_boundary_violations(record)
        if violations:
            raise RuntimeError(
                f"{role} semantic boundary smoke failed: "
                + ",".join(item.code for item in violations)
            )
        outputs.append(
            {
                "role": role,
                "request_identity_sha256": request.request_identity_sha256,
                "canonical_payload_sha256": record.canonical_payload_sha256,
                "raw_response_base64": base64.b64encode(raw).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "local_prompt_tokens": local_tokens,
                "provider_prompt_tokens": provider_tokens,
                "finish_reason": choice.get("finish_reason"),
            }
        )
    return {"status": "PASS", "roles": outputs}


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
        or profile.get("profile_version") != "v4"
        or profile.get("tensor_parallel_size") != 4
        or profile.get("max_num_seqs") != 1
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
    constrained_smoke = _diagnostic_constrained_smoke(endpoint, profile, tokenizer)
    role_semantic_smoke = _role_semantic_smoke(endpoint, profile, tokenizer)

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
        "constrained_special_character_smoke": constrained_smoke,
        "role_semantic_smoke": role_semantic_smoke,
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
