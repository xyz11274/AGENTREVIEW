"""Read-only health probe for the active Research Core v2 experiment."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROBE_PROTOCOL = "research-v2-hourly-probe-v1"
FORMAL_RUN_ID = "formal-evolution-v1"
COCO_S2_TOTAL = 1300
ROLES = ("QUAL", "CLAR", "ORIG", "SIGN", "POL")

FORMAL_TOKENS = (
    "-m agentreview.research_v2",
    "formal-evolution",
    "--action run-all-roles",
    f"--run-id {FORMAL_RUN_ID}",
)
S2_TOKENS = (
    "-m agentreview.research_v2",
    "build-s2",
    "--dataset coco",
    "--min-request-interval-seconds 1.2",
    "--retry-until-success",
)
QWEN_TOKENS = (
    "vllm.entrypoints.openai.api_server",
    "--port 18001",
    "--tensor-parallel-size 2",
    "--max-model-len 65536",
    "--max-num-seqs 8",
)
ENDPOINTS = {
    "qwen_27b": (18001, "qwen3.8-27b"),
    "coco_classify": (13098, "Qwen3-8B-Classify"),
    "coco_split_reserve": (13099, "Qwen3-8B-Split"),
}
EXACT_FAILURE_PATTERNS = (
    "embedding identity differs from snapshot",
    "requires embedding_identity",
    "without an embedding client",
    "snapshot identity mismatch",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(command: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _processes() -> list[dict[str, Any]]:
    result = _run(["ps", "-eo", "pid=,etimes=,args="], timeout=10.0)
    if result.returncode != 0:
        return []
    values = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        values.append(
            {
                "pid": int(fields[0]),
                "elapsed_seconds": int(fields[1]),
                "command": fields[2],
            }
        )
    return values


def _matches(processes: list[dict[str, Any]], tokens: tuple[str, ...]):
    return [
        value
        for value in processes
        if all(token in str(value.get("command") or "") for token in tokens)
    ]


def _process_view(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "pid": value["pid"],
            "elapsed_seconds": value["elapsed_seconds"],
            "command": value["command"],
        }
        for value in processes
    ]


def _lock_owners() -> list[dict[str, Any]]:
    try:
        result = _run(["lslocks", "-n", "-o", "PID,PATH"], timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    owners = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        path = fields[1]
        if "formal-evolution-runner" in path or ".s2-build" in path:
            owners.append({"pid": int(fields[0]), "path": path})
    return owners


def _http_get(url: str, *, timeout: float = 5.0) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return 200 <= response.status < 300, response.read().decode(
                "utf-8", errors="replace"
            )
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _metric(text: str, name: str) -> float | None:
    pattern = re.compile(
        rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([-+0-9.eE]+)\s*$",
        flags=re.MULTILINE,
    )
    values = [float(match.group(1)) for match in pattern.finditer(text)]
    return sum(values) if values else None


def _endpoint_snapshot(name: str, port: int, expected_model: str) -> dict[str, Any]:
    base = f"http://127.0.0.1:{port}"
    health_ok, health_body = _http_get(f"{base}/health")
    models_ok, models_body = _http_get(f"{base}/v1/models")
    model_ids: list[str] = []
    if models_ok:
        try:
            payload = json.loads(models_body)
            model_ids = [
                str(value.get("id") or "")
                for value in payload.get("data", [])
                if isinstance(value, dict)
            ]
        except (AttributeError, json.JSONDecodeError):
            models_ok = False
    snapshot: dict[str, Any] = {
        "name": name,
        "port": port,
        "healthy": health_ok and models_ok and expected_model in model_ids,
        "health_ok": health_ok,
        "models_ok": models_ok,
        "expected_model": expected_model,
        "model_ids": model_ids,
    }
    if name == "qwen_27b":
        metrics_ok, metrics_body = _http_get(f"{base}/metrics")
        snapshot["metrics_ok"] = metrics_ok
        snapshot["metrics"] = (
            {
                "running": _metric(metrics_body, "vllm:num_requests_running"),
                "waiting": _metric(metrics_body, "vllm:num_requests_waiting"),
                "kv_cache_usage": _metric(
                    metrics_body, "vllm:kv_cache_usage_perc"
                ),
                "success_total": _metric(
                    metrics_body, "vllm:request_success_total"
                ),
            }
            if metrics_ok
            else {}
        )
    if not health_ok:
        snapshot["health_error"] = health_body[:500]
    if not models_ok:
        snapshot["models_error"] = models_body[:500]
    return snapshot


def _endpoints() -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as executor:
        futures = {
            name: executor.submit(_endpoint_snapshot, name, port, model)
            for name, (port, model) in ENDPOINTS.items()
        }
        return {name: future.result() for name, future in futures.items()}


def _gpu_snapshot() -> list[dict[str, int]]:
    try:
        result = _run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    values = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 5 or not all(field.isdigit() for field in fields):
            continue
        values.append(
            {
                "index": int(fields[0]),
                "memory_used_mib": int(fields[1]),
                "memory_total_mib": int(fields[2]),
                "utilization_percent": int(fields[3]),
                "temperature_c": int(fields[4]),
            }
        )
    return values


def _log_snapshot(path: Path, previous_size: int) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "size": 0}
    stat = path.stat()
    start = previous_size if 0 <= previous_size <= stat.st_size else 0
    with path.open("rb") as handle:
        handle.seek(start)
        new_text = handle.read().decode("utf-8", errors="replace")
    latest_event = None
    with path.open("rb") as handle:
        handle.seek(max(0, stat.st_size - 262_144))
        tail = handle.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            latest_event = value
            break
    lowered = new_text.casefold()
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_epoch": stat.st_mtime,
        "age_seconds": max(0.0, time.time() - stat.st_mtime),
        "new_exact_failures": [
            pattern for pattern in EXACT_FAILURE_PATTERNS if pattern in lowered
        ],
        "new_traceback": "traceback (most recent call last)" in lowered,
        "latest_event": latest_event,
    }


def _count_json_files(path: Path) -> int:
    return sum(1 for value in path.rglob("*.json") if value.is_file()) if path.is_dir() else 0


def _formal_progress(artifact_root: Path) -> dict[str, Any]:
    run_summary_path = artifact_root / "runs" / FORMAL_RUN_ID / "formal-evolution.json"
    summary = _read_json(run_summary_path)
    role_states = {}
    for role in ROLES:
        root = artifact_root / "evolution" / role / FORMAL_RUN_ID
        coordinator = _read_json(root / "coordinator.json")
        role_states[role] = {
            "pool": (root / "pool.json").is_file(),
            "coordinator_status": (
                None if coordinator is None else coordinator.get("status")
            ),
            "winner": (root / "winner.json").is_file(),
            "attempt_files": len(list(root.glob("attempt-*.json"))),
        }
    review_root = artifact_root / "reviews"
    evaluation_root = artifact_root / "evaluations"
    memory_root = artifact_root / "memories"
    review_files = sum(
        _count_json_files(path)
        for path in review_root.glob(f"{FORMAL_RUN_ID}-*")
        if path.is_dir()
    )
    evaluation_files = sum(
        _count_json_files(path)
        for path in evaluation_root.glob(f"{FORMAL_RUN_ID}-*")
        if path.is_dir()
    )
    memory_files = sum(
        _count_json_files(path)
        for path in memory_root.glob(f"*/{FORMAL_RUN_ID}-*")
        if path.is_dir()
    )
    progress = {
        "summary_path": str(run_summary_path),
        "summary_status": None if summary is None else summary.get("status"),
        "summary_roles": (
            [] if summary is None else sorted(dict(summary.get("roles") or {}))
        ),
        "role_states": role_states,
        "review_json_files": review_files,
        "evaluation_json_files": evaluation_files,
        "memory_json_files": memory_files,
    }
    progress["signature"] = json.dumps(
        {
            "summary_status": progress["summary_status"],
            "summary_roles": progress["summary_roles"],
            "role_states": role_states,
            "review_json_files": review_files,
            "evaluation_json_files": evaluation_files,
            "memory_json_files": memory_files,
        },
        sort_keys=True,
    )
    return progress


def _s2_progress(artifact_root: Path) -> dict[str, Any]:
    final_path = artifact_root / "s2" / "frozen_s2_coco1300_v1.jsonl"
    parts_root = final_path.with_name(f"{final_path.name}.parts")
    part_count = len(list(parts_root.glob("*.json"))) if parts_root.is_dir() else 0
    final_lines = 0
    if final_path.is_file():
        with final_path.open("rb") as handle:
            final_lines = sum(1 for line in handle if line.strip())
    return {
        "parts_path": str(parts_root),
        "part_count": part_count,
        "total": COCO_S2_TOTAL,
        "final_path": str(final_path),
        "final_exists": final_path.is_file(),
        "final_lines": final_lines,
        "complete": final_path.is_file() and final_lines == COCO_S2_TOTAL,
    }


def _disk_snapshot(repo_root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(repo_root)
    return {
        "path": str(repo_root),
        "total_gib": round(usage.total / (1024**3), 2),
        "used_gib": round(usage.used / (1024**3), 2),
        "free_gib": round(usage.free / (1024**3), 2),
        "used_percent": round(100.0 * usage.used / usage.total, 2),
    }


def _git_snapshot(repo_root: Path) -> dict[str, Any]:
    head = _run(["git", "rev-parse", "HEAD"], timeout=10.0)
    status = _run(["git", "status", "--short"], timeout=10.0)
    changed = [line for line in status.stdout.splitlines() if line.strip()]
    return {
        "head": head.stdout.strip() if head.returncode == 0 else "",
        "dirty": bool(changed),
        "changed": changed,
    }


def _stagnant_count(
    previous: dict[str, Any] | None,
    *,
    key: str,
    current_signature: Any,
    previous_signature: Any,
    active: bool,
) -> int:
    if not previous or not active or current_signature != previous_signature:
        return 0
    elapsed = time.time() - float(previous.get("checked_epoch") or 0.0)
    if elapsed < 1800.0:
        return int(previous.get("stagnation", {}).get(key) or 0)
    return int(previous.get("stagnation", {}).get(key) or 0) + 1


def _issue(issues: list[dict[str, str]], severity: str, code: str, message: str):
    issues.append({"severity": severity, "code": code, "message": message})


def collect_probe(repo_root: Path, previous: dict[str, Any] | None) -> dict[str, Any]:
    checked_epoch = time.time()
    artifact_root = repo_root / "artifacts" / "research_v2"
    processes = _processes()
    formal_processes = _matches(processes, FORMAL_TOKENS)
    all_s2_processes = _matches(
        processes, ("-m agentreview.research_v2", "build-s2")
    )
    coco_s2_processes = _matches(processes, S2_TOKENS)
    qwen_processes = _matches(processes, QWEN_TOKENS)
    formal_progress = _formal_progress(artifact_root)
    s2_progress = _s2_progress(artifact_root)
    formal_active = formal_progress["summary_status"] != "complete"
    s2_active = not s2_progress["complete"]
    previous_logs = dict((previous or {}).get("logs") or {})
    formal_log = _log_snapshot(
        artifact_root / "runtime" / "formal_evolution_v1" / "stderr.log",
        int(dict(previous_logs.get("formal") or {}).get("size") or 0),
    )
    s2_log = _log_snapshot(
        artifact_root / "runtime" / "s2_coco1300" / "build.log",
        int(dict(previous_logs.get("s2") or {}).get("size") or 0),
    )
    endpoints = _endpoints()
    previous_progress = dict((previous or {}).get("progress") or {})
    formal_stagnant = _stagnant_count(
        previous,
        key="formal",
        current_signature=formal_progress["signature"],
        previous_signature=dict(previous_progress.get("formal") or {}).get(
            "signature"
        ),
        active=formal_active,
    )
    s2_parts_stagnant = _stagnant_count(
        previous,
        key="s2_parts",
        current_signature=s2_progress["part_count"],
        previous_signature=dict(previous_progress.get("s2") or {}).get("part_count"),
        active=s2_active,
    )
    s2_log_stagnant = _stagnant_count(
        previous,
        key="s2_log",
        current_signature=s2_log.get("size"),
        previous_signature=dict(previous_logs.get("s2") or {}).get("size"),
        active=s2_active,
    )
    issues: list[dict[str, str]] = []

    if formal_progress["summary_status"] == "interrupted":
        _issue(issues, "critical", "formal_interrupted", "Formal summary is interrupted")
    if formal_active and len(formal_processes) != 1:
        _issue(
            issues,
            "critical",
            "formal_process_count",
            f"Expected one active Formal runner, found {len(formal_processes)}",
        )
    if len(formal_processes) > 1:
        _issue(issues, "critical", "duplicate_formal", "Duplicate Formal runners detected")
    if formal_stagnant == 1:
        _issue(issues, "warning", "formal_no_hourly_progress", "Formal artifacts did not advance in one probe interval")
    if formal_stagnant >= 2:
        _issue(issues, "critical", "formal_stalled", "Formal artifacts did not advance for at least two probe intervals")

    if len(all_s2_processes) > 1:
        _issue(issues, "critical", "parallel_s2", "More than one S2 builder process is active")
    if s2_active and len(coco_s2_processes) != 1:
        _issue(
            issues,
            "critical",
            "s2_process_count",
            f"Expected one serial CoCo S2 builder, found {len(coco_s2_processes)}",
        )
    if s2_progress["part_count"] > COCO_S2_TOTAL:
        _issue(issues, "critical", "s2_too_many_parts", "CoCo S2 parts exceed the frozen target")
    if s2_progress["final_exists"] and not s2_progress["complete"]:
        _issue(issues, "critical", "s2_final_count", "CoCo S2 final JSONL does not contain exactly 1300 records")
    if s2_parts_stagnant == 1 and s2_log_stagnant == 0:
        _issue(issues, "warning", "s2_upstream_degraded", "S2 retry log is active but no new part completed in one interval")
    if s2_log_stagnant >= 1:
        _issue(issues, "critical", "s2_stalled", "S2 log and parts did not advance in a full probe interval")
    if s2_parts_stagnant >= 2:
        _issue(issues, "critical", "s2_paper_stalled", "S2 completed no new part for at least two probe intervals")

    if formal_log.get("new_exact_failures"):
        _issue(issues, "critical", "formal_identity_failure", "Formal log contains a new exact artifact or embedding identity failure")
    if formal_log.get("new_traceback"):
        _issue(issues, "warning", "formal_traceback", "Formal log contains a new traceback; process and summary require diagnosis")

    qwen = endpoints["qwen_27b"]
    if formal_active and len(qwen_processes) != 1:
        _issue(issues, "critical", "qwen_process_count", f"Expected one frozen 27B service, found {len(qwen_processes)}")
    if not qwen["healthy"]:
        _issue(issues, "warning", "qwen_endpoint", "Frozen 27B endpoint identity or health check failed")
    running = dict(qwen.get("metrics") or {}).get("running")
    if running is not None and running > 8:
        _issue(issues, "critical", "qwen_concurrency", f"27B reports {running:g} running requests; frozen maximum is 8")
    for name in ("coco_classify", "coco_split_reserve"):
        if not endpoints[name]["healthy"]:
            _issue(issues, "warning", f"{name}_endpoint", f"Reserved endpoint {name} is unavailable or has the wrong model identity")

    disk = _disk_snapshot(repo_root)
    if disk["free_gib"] < 50.0:
        _issue(issues, "critical", "disk_critical", f"Only {disk['free_gib']} GiB remains on the artifact filesystem")
    elif disk["free_gib"] < 200.0:
        _issue(issues, "warning", "disk_low", f"Only {disk['free_gib']} GiB remains on the artifact filesystem")

    severities = {value["severity"] for value in issues}
    transition_ready = formal_progress["summary_status"] == "complete" and s2_progress["complete"]
    if "critical" in severities:
        status = "action_required"
    elif "warning" in severities:
        status = "degraded"
    elif transition_ready:
        status = "transition_ready"
    else:
        status = "healthy"
    return {
        "protocol": PROBE_PROTOCOL,
        "checked_at": _utc_now(),
        "checked_epoch": checked_epoch,
        "status": status,
        "transition_ready": transition_ready,
        "issues": issues,
        "stagnation": {
            "formal": formal_stagnant,
            "s2_parts": s2_parts_stagnant,
            "s2_log": s2_log_stagnant,
        },
        "processes": {
            "formal": _process_view(formal_processes),
            "s2_all": _process_view(all_s2_processes),
            "s2_coco_exact": _process_view(coco_s2_processes),
            "qwen_27b": _process_view(qwen_processes),
        },
        "locks": _lock_owners(),
        "endpoints": endpoints,
        "gpus": _gpu_snapshot(),
        "disk": disk,
        "git": _git_snapshot(repo_root),
        "progress": {"formal": formal_progress, "s2": s2_progress},
        "logs": {"formal": formal_log, "s2": s2_log},
    }


def run_once(repo_root: Path, state_root: Path) -> tuple[int, dict[str, Any]]:
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "probe.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another Research v2 probe is already running") from exc
        previous = _read_json(state_root / "latest.json")
        result = collect_probe(repo_root, previous)
        _atomic_json(state_root / "latest.json", result)
        _append_jsonl(state_root / "probes.jsonl", result)
        action = {
            "protocol": PROBE_PROTOCOL,
            "checked_at": result["checked_at"],
            "required": result["status"] in {"action_required", "transition_ready"},
            "status": result["status"],
            "issues": result["issues"],
        }
        _atomic_json(state_root / "action.json", action)
    return (2 if result["status"] == "action_required" else 0), result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default_repo = Path(__file__).resolve().parents[3]
    parser.add_argument("--repo-root", type=Path, default=default_repo)
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument(
        "--loop-seconds",
        type=float,
        default=0.0,
        help="Run repeatedly at this fixed start-to-start interval; 0 runs once",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    state_root = (
        args.state_root.expanduser().resolve()
        if args.state_root is not None
        else repo_root / "artifacts" / "research_v2" / "runtime" / "hourly_probe"
    )
    interval = max(0.0, float(args.loop_seconds))
    last_code = 0
    while True:
        started = time.monotonic()
        try:
            last_code, result = run_once(repo_root, state_root)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "protocol": PROBE_PROTOCOL,
                        "checked_at": _utc_now(),
                        "status": "probe_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if interval <= 0.0:
                return 3
        else:
            print(
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None),
                flush=True,
            )
            if interval <= 0.0:
                return last_code
        time.sleep(max(1.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
