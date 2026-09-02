"""Prove that all local CoCo vLLM services match the scoring spec.

The proof intentionally omits process IDs, start times, response creation times,
and the ambient environment.  Those values are either nondeterministic or can
contain secrets.  Process IDs and start times are still checked internally to
detect listener replacement while validation is in progress.
"""

from __future__ import annotations

import ipaddress
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .coco_scoring_spec import judge_weight_identity
from .io import file_sha256, value_sha256
from .judge_lease import identity as qwen27b_mode_lease_identity
from .judge_lease import Qwen27BModeLease


PROTOCOL = "research-v2-coco-service-runtime-proof-v2"
RESTART_BARRIER_PROTOCOL = "research-v2-coco-judge-restart-barrier-v2"
SERVICE_ROLES = ("split", "classify", "judge")
SINGLE_GPU_ROLES = ("split", "classify")
ENTRYPOINT = "vllm.entrypoints.openai.api_server"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ZERO_TRAFFIC_METRICS = (
    "num_requests_running",
    "num_requests_waiting",
    "prompt_tokens_total",
    "generation_tokens_total",
    "request_success_total",
    "prefix_cache_queries_total",
    "request_prompt_tokens_count",
    "request_generation_tokens_count",
    "e2e_request_latency_seconds_count",
)
_VALUE_OPTIONS = {
    "--model": "model_root",
    "--served-model-name": "served_model_name",
    "--host": "host",
    "--port": "port",
    "--tensor-parallel-size": "tensor_parallel_size",
    "--max-model-len": "max_model_len",
    "--max-num-seqs": "max_num_seqs",
    "--gpu-memory-utilization": "gpu_memory_utilization",
    "--dtype": "dtype",
    "--reasoning-parser": "reasoning_parser",
    "--default-chat-template-kwargs": "default_chat_template_kwargs",
}
_BOOLEAN_OPTIONS = {
    "--language-model-only": "language_model_only",
    "--enable-prefix-caching": "enable_prefix_caching",
    "--enforce-eager": "enforce_eager",
    "--disable-custom-all-reduce": "disable_custom_all_reduce",
}
_INTEGER_ARGUMENTS = {
    "port",
    "tensor_parallel_size",
    "max_model_len",
    "max_num_seqs",
}
_FLOAT_ARGUMENTS = {"gpu_memory_utilization"}
_REGISTERED_RUNTIME_ENVIRONMENT = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "CUDA_HOME",
        "CUDAToolkit_ROOT",
        "VLLM_ATTENTION_BACKEND",
    }
)
_RUNTIME_CONTROL_PREFIXES = (
    "CUDA_",
    "NVIDIA_",
    "LD_",
    "NCCL_",
    "TORCH_",
    "TORCHINDUCTOR_",
    "TORCHDYNAMO_",
    "PYTORCH_",
    "CUBLAS_",
    "CUDNN_",
    "FLASH_ATTN_",
    "FLASH_ATTENTION_",
    "FLASHINFER_",
    "MKL_",
    "OMP_",
    "OPENBLAS_",
    "TENSORRT_",
    "TRITON_",
    "VLLM_",
    "XFORMERS_",
    "XLA_",
)
_SERVICE_FIELDS = {
    "cuda_visible_devices",
    "python_executable",
    "vllm_attention_backend",
    "host",
    "port",
    "served_model_name",
    "model_root",
    "max_model_len",
    "max_num_seqs",
    "tensor_parallel_size",
    "gpu_memory_utilization",
    "dtype",
    "language_model_only",
    "reasoning_parser",
    "enable_prefix_caching",
    "enforce_eager",
}
_JUDGE_SERVICE_FIELDS = _SERVICE_FIELDS | {
    "disable_custom_all_reduce",
    "default_chat_template_kwargs",
    "working_directory",
    "source_model",
    "revision",
    "tokenizer_sha256",
    "chat_template_sha256",
    "config_sha256",
    "tokenizer_config_sha256",
    "generation_config_sha256",
    "model_index_sha256",
    "weight_file_count",
    "weight_total_bytes",
    "weights_tree_sha256",
    "mode_lease",
}
_EVALUATION_RUNTIME_FIELDS = {
    "python_executable",
    "python_version",
    "sys_prefix",
    "user_site_enabled",
    "isolated_mode",
    "no_user_site",
    "dont_write_bytecode",
    "ignore_environment",
    "packages",
    "prefix_tree",
}
_EVALUATION_PACKAGES = {
    "bert-score",
    "nltk",
    "numpy",
    "openai",
    "requests",
    "rouge-score",
    "torch",
    "tqdm",
    "transformers",
}
_PREFIX_TREE_FIELDS = {
    "root",
    "file_count",
    "total_bytes",
    "symlink_count",
    "pyc_file_count",
    "tree_sha256",
}
_JUDGE_BLOCKED_AGENTREVIEW_STAGES = frozenset(
    {
        "build-memory",
        "review",
        "internal-eval",
        "evaluate-corpus",
        "evolve-step",
        "run-evolution",
        "formal-evolution",
        "generate-coco-reviews",
        "bootstrap-corpus",
    }
)


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_exact_fields(
    value: Any,
    fields: set[str],
    *,
    label: str,
) -> dict[str, Any]:
    result = _require_object(value, label=label)
    if set(result) != fields:
        raise ValueError(f"{label} fields differ")
    return result


def _validate_evaluation_runtime(value: Any, *, label: str) -> dict[str, Any]:
    runtime = _require_exact_fields(
        value,
        _EVALUATION_RUNTIME_FIELDS,
        label=label,
    )
    if any(
        not isinstance(runtime.get(name), str) or not runtime[name]
        for name in ("python_executable", "python_version", "sys_prefix")
    ):
        raise ValueError(f"{label} text identity is invalid")
    for name in ("python_executable", "sys_prefix"):
        path = Path(runtime[name])
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label} {name} is not canonical")
    if (
        runtime.get("user_site_enabled") is not False
        or runtime.get("isolated_mode") is not False
        or runtime.get("no_user_site") is not True
        or runtime.get("dont_write_bytecode") is not True
        or runtime.get("ignore_environment") is not False
    ):
        raise ValueError(f"{label} isolation flags differ")
    packages = _require_exact_fields(
        runtime.get("packages"),
        _EVALUATION_PACKAGES,
        label=f"{label} packages",
    )
    if any(not isinstance(version, str) or not version for version in packages.values()):
        raise ValueError(f"{label} package versions are invalid")
    prefix_tree = _require_exact_fields(
        runtime.get("prefix_tree"),
        _PREFIX_TREE_FIELDS,
        label=f"{label} prefix tree",
    )
    if (
        not isinstance(prefix_tree.get("root"), str)
        or not prefix_tree["root"]
        or prefix_tree["root"] != runtime["sys_prefix"]
        or not isinstance(prefix_tree.get("tree_sha256"), str)
        or not _SHA256_PATTERN.fullmatch(prefix_tree["tree_sha256"])
    ):
        raise ValueError(f"{label} prefix tree identity differs")
    for name in ("file_count", "total_bytes", "symlink_count", "pyc_file_count"):
        if type(prefix_tree.get(name)) is not int or prefix_tree[name] < 0:
            raise ValueError(f"{label} prefix tree counts are invalid")
    if (
        prefix_tree["file_count"] < 1
        or prefix_tree["pyc_file_count"] > prefix_tree["file_count"]
    ):
        raise ValueError(f"{label} prefix tree counts are invalid")
    return runtime


def _decode_address(encoded: str, *, family: int) -> str:
    raw = bytes.fromhex(encoded)
    if family == socket.AF_INET:
        return socket.inet_ntop(family, raw[::-1])
    if family == socket.AF_INET6:
        if len(raw) != 16:
            raise ValueError("Invalid IPv6 listener address in /proc")
        raw = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
        return socket.inet_ntop(family, raw)
    raise ValueError("Unsupported listener address family")


def _listening_sockets(
    *,
    proc_root: Path = Path("/proc"),
) -> list[tuple[str, int, str]]:
    """Return ``(host, port, inode)`` for every readable TCP listener."""
    listeners: list[tuple[str, int, str]] = []
    for relative, family in (("net/tcp", socket.AF_INET), ("net/tcp6", socket.AF_INET6)):
        source = proc_root / relative
        try:
            lines = source.read_text(encoding="ascii").splitlines()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(f"CoCo service listener table is unreadable: {source}") from exc
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                encoded_host, encoded_port = fields[1].split(":", 1)
                host = _decode_address(encoded_host, family=family)
                port = int(encoded_port, 16)
            except (ValueError, OSError) as exc:
                raise RuntimeError(
                    f"CoCo service listener table contains an invalid row: {source}"
                ) from exc
            listeners.append((host, port, fields[9]))
    return listeners


def _established_connection_count(
    port: int,
    *,
    proc_root: Path,
) -> int:
    count = 0
    for relative in ("net/tcp", "net/tcp6"):
        source = proc_root / relative
        try:
            lines = source.read_text(encoding="ascii").splitlines()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(
                f"CoCo judge connection table is unreadable: {source}"
            ) from exc
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "01":
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
                remote_port = int(fields[2].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError) as exc:
                raise RuntimeError(
                    f"CoCo judge connection table contains an invalid row: {source}"
                ) from exc
            if port in {local_port, remote_port}:
                count += 1
    # Each loopback TCP connection appears once for the client and once for the
    # server in the system-wide table.
    if count % 2 != 0:
        raise RuntimeError("CoCo judge loopback connection inventory is incomplete")
    return count // 2


def validate_judge_reviewer_exclusivity(
    *,
    proc_root: str | Path = "/proc",
    judge_port: int = 18001,
    require_idle_connections: bool,
) -> dict[str, Any]:
    """Reject Reviewer/Evolution overlap with the human-reference judge."""
    root = Path(proc_root).expanduser().resolve()
    active_stages: set[str] = set()
    try:
        processes = list(root.iterdir())
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("CoCo process table is unreadable") from exc
    for process in processes:
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            argv = _read_null_separated(
                process / "cmdline",
                label="AgentReview judge-exclusivity command line",
            )
        except RuntimeError:
            continue
        if "agentreview.research_v2" not in argv:
            continue
        active_stages.update(_JUDGE_BLOCKED_AGENTREVIEW_STAGES.intersection(argv))
    if active_stages:
        raise RuntimeError(
            "CoCo judge cannot overlap Reviewer/Evolution stages: "
            f"{sorted(active_stages)}"
        )
    established = _established_connection_count(judge_port, proc_root=root)
    if require_idle_connections and established != 0:
        raise RuntimeError(
            "CoCo pre-score seal requires an idle judge endpoint with no "
            "established clients"
        )
    return {
        "blocked_agentreview_stages": sorted(_JUDGE_BLOCKED_AGENTREVIEW_STAGES),
        "reviewer_evolution_processes_absent": True,
        "established_judge_connections": established,
        "idle_connections_required": require_idle_connections,
    }


def _boot_time_epoch_ns(proc_root: Path) -> int:
    try:
        lines = (proc_root / "stat").read_text(encoding="ascii").splitlines()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("Linux boot-time identity is unreadable") from exc
    values = [line.split()[1] for line in lines if line.startswith("btime ")]
    if len(values) != 1:
        raise RuntimeError("Linux boot-time identity is malformed")
    try:
        return int(values[0]) * 1_000_000_000
    except ValueError as exc:
        raise RuntimeError("Linux boot-time identity is malformed") from exc


def _process_start_epoch_ns(pid: int, *, proc_root: Path) -> int:
    try:
        ticks = int(_process_start_identity(pid, proc_root=proc_root))
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError) as exc:
        raise RuntimeError("CoCo service process start time is unreadable") from exc
    if ticks < 0 or ticks_per_second < 1:
        raise RuntimeError("CoCo service process start time is invalid")
    return _boot_time_epoch_ns(proc_root) + (
        ticks * 1_000_000_000 // ticks_per_second
    )


def _boot_id(proc_root: Path) -> str:
    try:
        value = (proc_root / "sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("Linux boot ID is unreadable") from exc
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        raise RuntimeError("Linux boot ID is malformed")
    return value


def _current_judge_instance(
    service_specs: dict[str, dict[str, Any]],
    *,
    proc_root: Path,
) -> dict[str, Any]:
    listeners = _listener_processes(service_specs, proc_root=proc_root)
    listener_pid = listeners["judge"]
    engine_pid = _engine_process(listener_pid, proc_root=proc_root)
    body = {
        "boot_id": _boot_id(proc_root),
        "listener_pid": listener_pid,
        "listener_start_identity": _process_start_identity(
            listener_pid, proc_root=proc_root
        ),
        "engine_pid": engine_pid,
        "engine_start_identity": _process_start_identity(
            engine_pid, proc_root=proc_root
        ),
    }
    return {**body, "instance_id": value_sha256(body)}


def validate_judge_restart_barrier(
    scoring_spec: dict[str, Any],
    *,
    bound_input_paths: list[str | Path],
    proc_root: str | Path = "/proc",
    minimum_start_delay_seconds: int = 2,
) -> dict[str, Any]:
    """Prove that a fresh 27B process was started after sealed Reviewer outputs."""
    if type(minimum_start_delay_seconds) is not int or minimum_start_delay_seconds < 1:
        raise ValueError("CoCo judge restart delay must be a positive integer")
    spec = _require_object(scoring_spec, label="CoCo scoring spec")
    services = _require_exact_fields(
        spec.get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    service_specs = {
        role: _require_exact_fields(
            services.get(role),
            _JUDGE_SERVICE_FIELDS if role == "judge" else _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        for role in SERVICE_ROLES
    }
    root = Path(proc_root).expanduser().resolve()
    bindings: list[dict[str, str]] = []
    latest_mtime_ns = 0
    seen: set[Path] = set()
    for value in bound_input_paths:
        path = Path(value).expanduser()
        if (
            not path.is_absolute()
            or path.parent.resolve() != path.parent
            or path.is_symlink()
            or not path.is_file()
            or path in seen
        ):
            raise ValueError("CoCo judge restart input binding is invalid")
        seen.add(path)
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise RuntimeError("CoCo judge restart input changed during validation")
        latest_mtime_ns = max(latest_mtime_ns, before.st_mtime_ns)
        bindings.append({"path": str(path), "sha256": digest})
    if len(bindings) < 3:
        raise ValueError("CoCo judge restart requires scoring spec and both arm inputs")
    bindings.sort(key=lambda item: item["path"])
    with Qwen27BModeLease("judge") as mode_lease:
        validate_judge_reviewer_exclusivity(
            proc_root=root,
            judge_port=int(service_specs["judge"]["port"]),
            require_idle_connections=True,
        )
        judge_instance = _current_judge_instance(
            service_specs,
            proc_root=root,
        )
        listener_pid = int(judge_instance["listener_pid"])
        engine_pid = int(judge_instance["engine_pid"])
        threshold_ns = latest_mtime_ns + minimum_start_delay_seconds * 1_000_000_000
        if (
            _process_start_epoch_ns(listener_pid, proc_root=root) <= threshold_ns
            or _process_start_epoch_ns(engine_pid, proc_root=root) <= threshold_ns
        ):
            raise RuntimeError(
                "CoCo judge must be restarted after both sealed candidate inputs "
                "and the scoring spec"
            )
        zero_generation_traffic = _judge_zero_generation_traffic(
            service_specs["judge"]
        )
        if (
            _current_judge_instance(service_specs, proc_root=root)
            != judge_instance
        ):
            raise RuntimeError(
                "CoCo judge instance changed during zero-traffic validation"
            )
        mode_lease_state = mode_lease.assert_state_identity()
    return {
        "protocol": RESTART_BARRIER_PROTOCOL,
        "mode_lease": qwen27b_mode_lease_identity(),
        "bound_inputs": bindings,
        "minimum_start_delay_seconds": minimum_start_delay_seconds,
        "listener_started_after_inputs": True,
        "engine_started_after_inputs": True,
        "prefix_cache_cleared_by_process_restart": True,
        "reviewer_evolution_processes_absent": True,
        "zero_generation_traffic": zero_generation_traffic,
        "mode_lease_state": mode_lease_state,
        "judge_instance": judge_instance,
    }


def validate_sealed_judge_restart_barrier(
    scoring_spec: dict[str, Any],
    *,
    sealed_barrier: dict[str, Any],
    bound_input_paths: list[str | Path],
    proc_root: str | Path = "/proc",
) -> dict[str, Any]:
    """Validate a historical zero-traffic snapshot against the same live instance."""

    spec = _require_object(scoring_spec, label="CoCo scoring spec")
    services = _require_exact_fields(
        spec.get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    service_specs = {
        role: _require_exact_fields(
            services.get(role),
            _JUDGE_SERVICE_FIELDS if role == "judge" else _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        for role in SERVICE_ROLES
    }
    barrier = _require_exact_fields(
        sealed_barrier,
        {
            "protocol",
            "mode_lease",
            "bound_inputs",
            "minimum_start_delay_seconds",
            "listener_started_after_inputs",
            "engine_started_after_inputs",
            "prefix_cache_cleared_by_process_restart",
            "reviewer_evolution_processes_absent",
            "zero_generation_traffic",
            "mode_lease_state",
            "judge_instance",
        },
        label="sealed CoCo judge restart barrier",
    )
    if (
        barrier.get("protocol") != RESTART_BARRIER_PROTOCOL
        or barrier.get("mode_lease") != qwen27b_mode_lease_identity()
        or barrier.get("minimum_start_delay_seconds") != 2
        or any(
            barrier.get(name) is not True
            for name in (
                "listener_started_after_inputs",
                "engine_started_after_inputs",
                "prefix_cache_cleared_by_process_restart",
                "reviewer_evolution_processes_absent",
            )
        )
    ):
        raise ValueError("Sealed CoCo judge restart barrier differs")
    _validate_zero_generation_traffic(
        barrier.get("zero_generation_traffic"),
        service=service_specs["judge"],
    )
    expected_bindings = sorted(
        (
            {"path": str(Path(path).expanduser().resolve()), "sha256": file_sha256(path)}
            for path in bound_input_paths
        ),
        key=lambda item: item["path"],
    )
    if barrier.get("bound_inputs") != expected_bindings:
        raise RuntimeError("Sealed CoCo judge restart inputs changed")
    expected_state = _require_object(
        barrier.get("mode_lease_state"),
        label="sealed CoCo mode lease state",
    )
    expected_instance = _require_object(
        barrier.get("judge_instance"),
        label="sealed CoCo judge instance",
    )
    root = Path(proc_root).expanduser().resolve()
    with Qwen27BModeLease("judge") as mode_lease:
        mode_lease.assert_state_identity(expected_state)
        validate_judge_reviewer_exclusivity(
            proc_root=root,
            judge_port=int(service_specs["judge"]["port"]),
            require_idle_connections=False,
        )
        if _current_judge_instance(service_specs, proc_root=root) != expected_instance:
            raise RuntimeError(
                "Live CoCo judge instance differs from its restart barrier"
            )
        mode_lease.assert_state_identity(expected_state)
    return dict(barrier)


def _socket_owner_pids(
    inodes: set[str],
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, set[int]]:
    owners = {inode: set() for inode in inodes}
    if not inodes:
        return owners
    try:
        processes = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("CoCo process table is unreadable") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        fd_root = process / "fd"
        try:
            descriptors = list(fd_root.iterdir())
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            inode = target[8:-1]
            if inode in owners:
                owners[inode].add(int(process.name))
    return owners


def _listener_processes(
    service_specs: dict[str, dict[str, Any]],
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, int]:
    ports = {int(service_specs[role]["port"]) for role in SERVICE_ROLES}
    if len(ports) != len(SERVICE_ROLES):
        raise ValueError("CoCo split, classify, and judge services require distinct ports")
    relevant = [item for item in _listening_sockets(proc_root=proc_root) if item[1] in ports]
    inodes = {item[2] for item in relevant}
    owners = _socket_owner_pids(inodes, proc_root=proc_root)
    result: dict[str, int] = {}
    for role in SERVICE_ROLES:
        expected_host = str(service_specs[role]["host"])
        expected_port = int(service_specs[role]["port"])
        matches = [item for item in relevant if item[1] == expected_port]
        listener_pids = {
            pid for _, _, inode in matches for pid in owners.get(inode, set())
        }
        if len(matches) != 1 or len(listener_pids) != 1:
            raise RuntimeError(
                f"CoCo {role} port {expected_port} must have exactly one listener process"
            )
        observed_host = matches[0][0]
        if ipaddress.ip_address(observed_host) != ipaddress.ip_address(expected_host):
            raise ValueError(
                f"CoCo {role} listener host differs: expected={expected_host}, "
                f"observed={observed_host}"
            )
        result[role] = next(iter(listener_pids))
    if len(set(result.values())) != len(SERVICE_ROLES):
        raise ValueError("CoCo split and classify must use distinct listener processes")
    return result


def _read_null_separated(path: Path, *, label: str) -> list[str]:
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}") from exc
    try:
        return [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} is not UTF-8") from exc


def _process_start_identity(pid: int, *, proc_root: Path) -> str:
    path = proc_root / str(pid) / "stat"
    try:
        value = path.read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"CoCo service process identity is unreadable: {pid}") from exc
    closing = value.rfind(")")
    fields = value[closing + 2 :].split() if closing >= 0 else []
    if len(fields) <= 19:
        raise RuntimeError(f"CoCo service process identity is malformed: {pid}")
    return fields[19]


def _process_parent_pid(pid: int, *, proc_root: Path) -> int:
    path = proc_root / str(pid) / "stat"
    try:
        value = path.read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"CoCo service process ancestry is unreadable: {pid}") from exc
    closing = value.rfind(")")
    fields = value[closing + 2 :].split() if closing >= 0 else []
    if len(fields) <= 1:
        raise RuntimeError(f"CoCo service process ancestry is malformed: {pid}")
    try:
        return int(fields[1])
    except ValueError as exc:
        raise RuntimeError(f"CoCo service process ancestry is malformed: {pid}") from exc


def _engine_process(
    listener_pid: int,
    *,
    proc_root: Path,
) -> int:
    matches: list[int] = []
    try:
        processes = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("CoCo process table is unreadable") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        pid = int(process.name)
        try:
            if _process_parent_pid(pid, proc_root=proc_root) != listener_pid:
                continue
            argv = _read_null_separated(
                process / "cmdline",
                label="CoCo EngineCore command line",
            )
        except RuntimeError:
            continue
        if argv and argv[0].strip() == "VLLM::EngineCore":
            matches.append(pid)
    if len(matches) != 1:
        raise RuntimeError(
            "CoCo service requires exactly one direct VLLM::EngineCore process"
        )
    return matches[0]


def _instance_lease(
    service_specs: dict[str, dict[str, Any]],
    *,
    proc_root: Path,
) -> dict[str, dict[str, int | str]]:
    listeners = _listener_processes(service_specs, proc_root=proc_root)
    lease: dict[str, dict[str, int | str]] = {}
    for role in SERVICE_ROLES:
        listener_pid = listeners[role]
        listener_start = _process_start_identity(
            listener_pid,
            proc_root=proc_root,
        )
        engine_pid = _engine_process(listener_pid, proc_root=proc_root)
        engine_start = _process_start_identity(engine_pid, proc_root=proc_root)
        lease[role] = {
            "listener_pid": listener_pid,
            "listener_start_identity": listener_start,
            "engine_pid": engine_pid,
            "engine_start_identity": engine_start,
        }
    return lease


def _parse_arguments(argv: list[str], *, role: str) -> dict[str, Any]:
    if len(argv) < 3 or argv[1:3] != ["-m", ENTRYPOINT]:
        raise ValueError(f"CoCo {role} service entrypoint differs")
    if role not in SERVICE_ROLES:
        raise ValueError(f"Unknown CoCo service role: {role}")
    allowed_value_options = {
        option: name
        for option, name in _VALUE_OPTIONS.items()
        if role == "judge" or name != "default_chat_template_kwargs"
    }
    allowed_boolean_options = {
        option: name
        for option, name in _BOOLEAN_OPTIONS.items()
        if role == "judge" or name != "disable_custom_all_reduce"
    }
    parsed: dict[str, Any] = (
        {"enforce_eager": False, "disable_custom_all_reduce": False}
        if role == "judge"
        else {}
    )
    index = 3
    while index < len(argv):
        option = argv[index]
        if option in allowed_boolean_options:
            name = allowed_boolean_options[option]
            if parsed.get(name) is True:
                raise ValueError(f"CoCo {role} service repeats {option}")
            parsed[name] = True
            index += 1
            continue
        if option not in allowed_value_options or index + 1 >= len(argv):
            raise ValueError(f"CoCo {role} service has an unregistered argument: {option}")
        name = allowed_value_options[option]
        if name in parsed:
            raise ValueError(f"CoCo {role} service repeats {option}")
        raw = argv[index + 1]
        try:
            if name in _INTEGER_ARGUMENTS:
                parsed[name] = int(raw)
            elif name in _FLOAT_ARGUMENTS:
                parsed[name] = float(raw)
            elif name == "default_chat_template_kwargs":
                parsed[name] = json.loads(raw)
                if not isinstance(parsed[name], dict):
                    raise ValueError
            else:
                parsed[name] = raw
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"CoCo {role} service has an invalid {option} value") from exc
        index += 2
    expected_names = set(allowed_value_options.values()) | set(
        allowed_boolean_options.values()
    )
    if set(parsed) != expected_names:
        raise ValueError(f"CoCo {role} service arguments are incomplete")
    return parsed


def _relevant_environment(pid: int, *, proc_root: Path, role: str) -> dict[str, Any]:
    entries = _read_null_separated(
        proc_root / str(pid) / "environ",
        label=f"CoCo {role} service environment",
    )
    values: dict[str, list[str]] = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if separator:
            values.setdefault(name, []).append(value)
    if len(values.get("CUDA_VISIBLE_DEVICES", [])) != 1:
        raise ValueError(
            f"CoCo {role} service requires exactly one CUDA_VISIBLE_DEVICES"
        )
    if role == "judge":
        if "VLLM_ATTENTION_BACKEND" in values:
            raise ValueError(
                "CoCo judge service requires VLLM_ATTENTION_BACKEND to be unset"
            )
    elif len(values.get("VLLM_ATTENTION_BACKEND", [])) != 1:
        raise ValueError(
            f"CoCo {role} service requires exactly one VLLM_ATTENTION_BACKEND"
        )
    unregistered_controls = sorted(
        name
        for name in values
        if name not in _REGISTERED_RUNTIME_ENVIRONMENT
        and any(name.startswith(prefix) for prefix in _RUNTIME_CONTROL_PREFIXES)
    )
    if unregistered_controls:
        raise ValueError(
            f"CoCo {role} service has unregistered runtime environment controls: "
            f"{unregistered_controls}"
        )
    if any(name in values for name in ("PYTHONHOME", "PYTHONPATH")):
        raise ValueError(f"CoCo {role} service Python import path is not isolated")
    for name in ("CUDA_HOME", "CUDAToolkit_ROOT"):
        if values.get(name) != ["/usr/local/cuda"]:
            raise ValueError(f"CoCo {role} service requires {name}=/usr/local/cuda")
    result = {
        "CUDA_VISIBLE_DEVICES": values["CUDA_VISIBLE_DEVICES"][0],
        "PYTHONHOME_unset": True,
        "PYTHONPATH_unset": True,
        "CUDA_DEVICE_ORDER_unset": True,
        "CUDA_HOME": values["CUDA_HOME"][0],
        "CUDAToolkit_ROOT": values["CUDAToolkit_ROOT"][0],
        "unregistered_runtime_controls_absent": True,
    }
    if role == "judge":
        result["VLLM_ATTENTION_BACKEND_unset"] = True
    else:
        result["VLLM_ATTENTION_BACKEND"] = values[
            "VLLM_ATTENTION_BACKEND"
        ][0]
    return result


def _endpoint_json(url: str, *, label: str) -> tuple[int, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=10) as response:
            status = int(response.status)
            raw = response.read(1_048_577)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"CoCo {label} endpoint is unavailable: {url}") from exc
    if status != 200:
        raise RuntimeError(f"CoCo {label} endpoint returned HTTP {status}")
    if len(raw) > 1_048_576:
        raise RuntimeError(f"CoCo {label} endpoint response is too large")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CoCo {label} endpoint did not return JSON") from exc
    return status, value


def _prometheus_labels(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    labels: dict[str, str] = {}
    for item in raw.split(","):
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)="([^",]*)"', item)
        if match is None or match.group(1) in labels:
            raise ValueError("CoCo judge metrics labels are invalid")
        labels[match.group(1)] = match.group(2)
    return labels


def _judge_zero_generation_traffic(service: dict[str, Any]) -> dict[str, Any]:
    """Bind selected fresh-process metrics proving no generation reached Judge."""

    endpoint = f"http://{service['host']}:{service['port']}/metrics"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            urllib.request.Request(endpoint, method="GET"),
            timeout=10,
        ) as response:
            status = int(response.status)
            raw = response.read(8_388_609)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("CoCo judge metrics endpoint is unavailable") from exc
    if status != 200 or len(raw) > 8_388_608:
        raise RuntimeError("CoCo judge metrics endpoint response is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CoCo judge metrics endpoint is not UTF-8") from exc

    expected_model = str(service["served_model_name"])
    observed: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _ZERO_TRAFFIC_METRICS
    }
    selected_raw: list[str] = []
    sample_pattern = re.compile(
        r"^vllm:([A-Za-z0-9_]+)(?:\{([^}]*)\})?\s+([^\s]+)$"
    )
    for line in text.splitlines():
        match = sample_pattern.fullmatch(line.strip())
        if match is None or match.group(1) not in observed:
            continue
        labels = _prometheus_labels(match.group(2) or "")
        if labels.get("model_name") != expected_model:
            continue
        expected_labels = {"engine", "model_name"} | (
            {"finished_reason"}
            if match.group(1) == "request_success_total"
            else set()
        )
        if set(labels) != expected_labels or labels.get("engine") != "0":
            raise ValueError("CoCo judge metrics series labels differ")
        try:
            sample = float(match.group(3))
        except ValueError as exc:
            raise ValueError("CoCo judge metrics sample is invalid") from exc
        if not math.isfinite(sample) or sample < 0:
            raise ValueError("CoCo judge metrics sample is invalid")
        normalized = {
            "labels": {name: labels[name] for name in sorted(labels)},
            "value": sample,
        }
        if normalized in observed[match.group(1)]:
            raise ValueError("CoCo judge metrics series is duplicated")
        observed[match.group(1)].append(normalized)
        selected_raw.append(line.strip())

    for name, series in observed.items():
        series.sort(key=value_sha256)
        if not series or sum(float(item["value"]) for item in series) != 0.0:
            raise RuntimeError(
                f"CoCo judge fresh-process metric is not zero: vllm:{name}"
            )
    normalized_sha256 = value_sha256(observed)
    selected_bytes = (
        "\n".join(sorted(selected_raw)) + "\n"
    ).encode("utf-8")
    return {
        "endpoint": endpoint,
        "model": expected_model,
        "metrics": observed,
        "raw_selected_samples": sorted(selected_raw),
        "raw_selected_sha256": hashlib.sha256(selected_bytes).hexdigest(),
        "normalized_sha256": normalized_sha256,
        "all_generation_traffic_zero": True,
    }


def _validate_zero_generation_traffic(
    value: Any,
    *,
    service: dict[str, Any],
) -> dict[str, Any]:
    proof = _require_exact_fields(
        value,
        {
            "endpoint",
            "model",
            "metrics",
            "raw_selected_samples",
            "raw_selected_sha256",
            "normalized_sha256",
            "all_generation_traffic_zero",
        },
        label="CoCo judge zero-generation-traffic proof",
    )
    metrics = _require_exact_fields(
        proof.get("metrics"),
        set(_ZERO_TRAFFIC_METRICS),
        label="CoCo judge zero-generation-traffic metrics",
    )
    raw_samples = proof.get("raw_selected_samples")
    if (
        not isinstance(raw_samples, list)
        or not raw_samples
        or raw_samples != sorted(raw_samples)
        or any(not isinstance(line, str) or not line for line in raw_samples)
    ):
        raise ValueError("CoCo judge zero-generation-traffic raw samples differ")
    raw_sha256 = hashlib.sha256(
        ("\n".join(raw_samples) + "\n").encode("utf-8")
    ).hexdigest()
    raw_observed: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _ZERO_TRAFFIC_METRICS
    }
    raw_pattern = re.compile(
        r"^vllm:([A-Za-z0-9_]+)(?:\{([^}]*)\})?\s+([^\s]+)$"
    )
    for line in raw_samples:
        match = raw_pattern.fullmatch(line)
        if match is None or match.group(1) not in raw_observed:
            raise ValueError("CoCo judge zero-generation-traffic raw sample differs")
        labels = _prometheus_labels(match.group(2) or "")
        try:
            sample_value = float(match.group(3))
        except ValueError as exc:
            raise ValueError(
                "CoCo judge zero-generation-traffic raw sample differs"
            ) from exc
        raw_observed[match.group(1)].append(
            {
                "labels": {name: labels[name] for name in sorted(labels)},
                "value": sample_value,
            }
        )
    for series in raw_observed.values():
        series.sort(key=value_sha256)
    if raw_observed != metrics:
        raise ValueError("CoCo judge zero-generation-traffic raw metrics differ")
    if (
        proof.get("endpoint")
        != f"http://{service['host']}:{service['port']}/metrics"
        or proof.get("model") != service["served_model_name"]
        or proof.get("all_generation_traffic_zero") is not True
        or proof.get("normalized_sha256") != value_sha256(metrics)
        or proof.get("raw_selected_sha256") != raw_sha256
    ):
        raise ValueError("CoCo judge zero-generation-traffic proof differs")
    for name, series in metrics.items():
        if not isinstance(series, list) or not series:
            raise ValueError("CoCo judge zero-generation-traffic series differs")
        expected_labels = {"engine", "model_name"} | (
            {"finished_reason"} if name == "request_success_total" else set()
        )
        seen: set[str] = set()
        for sample in series:
            item = _require_exact_fields(
                sample,
                {"labels", "value"},
                label=f"CoCo judge metric vllm:{name}",
            )
            labels = _require_exact_fields(
                item.get("labels"),
                expected_labels,
                label=f"CoCo judge metric vllm:{name} labels",
            )
            if (
                labels.get("engine") != "0"
                or labels.get("model_name") != service["served_model_name"]
                or isinstance(item.get("value"), bool)
                or not isinstance(item.get("value"), (int, float))
                or float(item["value"]) != 0.0
            ):
                raise ValueError("CoCo judge zero-generation-traffic sample differs")
            sample_id = value_sha256(item)
            if sample_id in seen:
                raise ValueError("CoCo judge zero-generation-traffic sample is duplicated")
            seen.add(sample_id)
    return dict(proof)


def validate_zero_generation_traffic_proof(
    scoring_spec: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    """Validate one sealed zero-traffic snapshot without re-reading live counters."""

    services = _require_exact_fields(
        _require_object(scoring_spec, label="CoCo scoring spec").get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    judge = _require_exact_fields(
        services.get("judge"),
        _JUDGE_SERVICE_FIELDS,
        label="CoCo scoring judge service",
    )
    return _validate_zero_generation_traffic(value, service=judge)


def _endpoint_proof(service: dict[str, Any], *, role: str) -> dict[str, Any]:
    base_url = f"http://{service['host']}:{service['port']}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            urllib.request.Request(f"{base_url}/health", method="GET"),
            timeout=10,
        ) as response:
            health_status = int(response.status)
            response.read(1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"CoCo {role} health endpoint is unavailable") from exc
    if health_status != 200:
        raise RuntimeError(
            f"CoCo {role} health endpoint returned HTTP {health_status}"
        )
    models_status, value = _endpoint_json(
        f"{base_url}/v1/models",
        label=f"{role} models",
    )
    models = _require_object(value, label=f"CoCo {role} models response")
    data = models.get("data")
    if models.get("object") != "list" or not isinstance(data, list) or len(data) != 1:
        raise ValueError(f"CoCo {role} models response must expose exactly one model")
    model = _require_object(data[0], label=f"CoCo {role} served model")
    observed_root = Path(str(model.get("root") or "")).expanduser().resolve()
    expected_root = Path(service["model_root"]).expanduser().resolve()
    if (
        model.get("id") != service["served_model_name"]
        or model.get("object") != "model"
        or model.get("owned_by") != "vllm"
        or model.get("parent") is not None
        or observed_root != expected_root
        or model.get("max_model_len") != service["max_model_len"]
    ):
        raise ValueError(f"CoCo {role} served model identity differs")
    return {
        "health": {"status": health_status},
        "models": {
            "status": models_status,
            "object": "list",
            "data": [
                {
                    "id": model["id"],
                    "object": "model",
                    "owned_by": "vllm",
                    "root": str(observed_root),
                    "parent": None,
                    "max_model_len": model["max_model_len"],
                }
            ],
        },
    }


def _judge_model_identity(service: dict[str, Any]) -> dict[str, Any]:
    """Verify immutable source/tokenizer/template identity under the served root."""
    model_root = Path(str(service.get("model_root") or "")).expanduser()
    if (
        not model_root.is_absolute()
        or model_root.is_symlink()
        or model_root.resolve() != model_root
        or not model_root.is_dir()
    ):
        raise ValueError("CoCo judge model root is not a canonical directory")
    metadata_path = model_root / ".hfd" / "repo_metadata.json"
    tokenizer_path = model_root / "tokenizer.json"
    chat_template_path = model_root / "chat_template.jinja"
    for path, label in (
        (metadata_path, "source metadata"),
        (tokenizer_path, "tokenizer"),
        (chat_template_path, "chat template"),
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"CoCo judge {label} file identity is invalid")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CoCo judge source metadata is unreadable") from exc
    if not isinstance(metadata, dict):
        raise ValueError("CoCo judge source metadata is invalid")
    observed = {
        "source_model": metadata.get("id"),
        "revision": metadata.get("sha"),
        "tokenizer_sha256": file_sha256(tokenizer_path),
        "chat_template_sha256": file_sha256(chat_template_path),
        **judge_weight_identity(),
    }
    expected = {
        name: service.get(name)
        for name in (
            "source_model",
            "revision",
            "tokenizer_sha256",
            "chat_template_sha256",
            "config_sha256",
            "tokenizer_config_sha256",
            "generation_config_sha256",
            "model_index_sha256",
            "weight_file_count",
            "weight_total_bytes",
            "weights_tree_sha256",
        )
    }
    if observed != expected:
        raise ValueError("CoCo judge model source identity differs")
    return dict(observed)


def _software_versions(python_executable: Path) -> dict[str, str]:
    script = (
        "import json, transformers, vllm; "
        "print(json.dumps({'transformers': transformers.__version__, "
        "'vllm': vllm.__version__}, sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-I", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PYTHONNOUSERSITE": "1"},
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("CoCo service software versions are unreadable") from exc
    if result.returncode != 0:
        raise RuntimeError("CoCo service software versions are unreadable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CoCo service software version output is invalid") from exc
    return dict(
        _require_exact_fields(
            value,
            {"vllm", "transformers"},
            label="CoCo service software versions",
        )
    )


def _nvidia_smi_rows(
    executable: Path,
    query: str,
) -> list[list[str]]:
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError(f"nvidia-smi is unavailable: {executable}")
    try:
        result = subprocess.run(
            [
                str(executable),
                f"--query-{query}",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin"},
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("CoCo GPU process identity is unreadable") from exc
    if result.returncode != 0:
        raise RuntimeError("CoCo GPU process identity is unreadable")
    return [
        [field.strip() for field in line.split(",")]
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _engine_gpu_assignments(
    *,
    engine_pids: dict[str, int],
    service_specs: dict[str, dict[str, Any]],
    nvidia_smi_path: Path,
    proc_root: Path,
) -> dict[str, dict[str, Any]]:
    gpu_rows = _nvidia_smi_rows(nvidia_smi_path, "gpu=index,uuid")
    gpu_uuids: dict[str, str] = {}
    for row in gpu_rows:
        if len(row) != 2 or not row[0] or not row[1] or row[0] in gpu_uuids:
            raise RuntimeError("CoCo GPU inventory is invalid")
        gpu_uuids[row[0]] = row[1]
    compute_rows = _nvidia_smi_rows(
        nvidia_smi_path,
        "compute-apps=pid,gpu_uuid",
    )
    pid_uuids: dict[int, list[str]] = {}
    for row in compute_rows:
        if len(row) != 2:
            raise RuntimeError("CoCo GPU process inventory is invalid")
        try:
            pid = int(row[0])
        except ValueError as exc:
            raise RuntimeError("CoCo GPU process inventory is invalid") from exc
        pid_uuids.setdefault(pid, []).append(row[1])
    parent_by_pid: dict[int, int] = {}
    try:
        processes = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError("CoCo process table is unreadable") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        pid = int(process.name)
        try:
            parent_by_pid[pid] = _process_parent_pid(pid, proc_root=proc_root)
        except RuntimeError:
            continue

    assignments: dict[str, dict[str, Any]] = {}
    assigned_indices: set[str] = set()
    for role in SERVICE_ROLES:
        raw_indices = str(service_specs[role]["cuda_visible_devices"])
        gpu_indices = raw_indices.split(",")
        if (
            not gpu_indices
            or any(not re.fullmatch(r"[0-9]+", item) for item in gpu_indices)
            or len(gpu_indices) != len(set(gpu_indices))
            or any(item not in gpu_uuids for item in gpu_indices)
        ):
            raise ValueError(
                f"CoCo {role} CUDA_VISIBLE_DEVICES is not a canonical GPU list"
            )
        if len(gpu_indices) != int(service_specs[role]["tensor_parallel_size"]):
            raise ValueError(
                f"CoCo {role} GPU count differs from tensor_parallel_size"
            )
        if assigned_indices.intersection(gpu_indices):
            raise ValueError("CoCo service GPU assignments must be disjoint")
        assigned_indices.update(gpu_indices)

        subtree = {engine_pids[role]}
        changed = True
        while changed:
            changed = False
            for pid, parent in parent_by_pid.items():
                if parent in subtree and pid not in subtree:
                    subtree.add(pid)
                    changed = True
        compute_pids = sorted(pid for pid in subtree if pid in pid_uuids)
        observed = sorted(
            uuid for pid in compute_pids for uuid in pid_uuids.get(pid, [])
        )
        expected = sorted(gpu_uuids[index] for index in gpu_indices)
        if observed != expected:
            raise ValueError(
                f"CoCo {role} engine subtree is not confined to its registered GPUs"
            )
        assignments[role] = {
            "gpu_indices": gpu_indices,
            "compute_process_count": len(compute_pids),
            "all_compute_devices_registered": True,
        }
    return assignments


def _validate_process(
    *,
    role: str,
    pid: int,
    service: dict[str, Any],
    repository_root: Path,
    proc_root: Path,
) -> dict[str, Any]:
    process_root = proc_root / str(pid)
    start_identity = _process_start_identity(pid, proc_root=proc_root)
    argv = _read_null_separated(
        process_root / "cmdline", label=f"CoCo {role} service command line"
    )
    if not argv:
        raise RuntimeError(f"CoCo {role} service command line is empty")
    registered_python = Path(service["python_executable"]).expanduser()
    try:
        resolved_python = registered_python.resolve(strict=True)
        process_executable = (process_root / "exe").resolve(strict=True)
        working_directory = (process_root / "cwd").resolve(strict=True)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"CoCo {role} process filesystem identity is unreadable") from exc
    if process_executable != resolved_python or Path(argv[0]).resolve() != resolved_python:
        raise ValueError(f"CoCo {role} Python executable differs")
    expected_working_directory = (
        Path(service["working_directory"]).expanduser().resolve()
        if role == "judge"
        else repository_root
    )
    if working_directory != expected_working_directory:
        raise ValueError(f"CoCo {role} working directory differs")

    parsed = _parse_arguments(argv, role=role)
    expected_arguments = {
        name: service[name]
        for name in (
            (
                set(_VALUE_OPTIONS.values())
                | set(_BOOLEAN_OPTIONS.values())
            )
            if role == "judge"
            else (
                (set(_VALUE_OPTIONS.values()) - {"default_chat_template_kwargs"})
                | (set(_BOOLEAN_OPTIONS.values()) - {"disable_custom_all_reduce"})
            )
        )
    }
    if parsed != expected_arguments:
        differences = sorted(
            name
            for name in expected_arguments
            if parsed.get(name) != expected_arguments[name]
        )
        raise ValueError(
            f"CoCo {role} service arguments differ from the scoring spec: {differences}"
        )
    environment = _relevant_environment(
        pid, proc_root=proc_root, role=role
    )
    if (
        environment["CUDA_VISIBLE_DEVICES"] != service["cuda_visible_devices"]
        or (
            role != "judge"
            and environment["VLLM_ATTENTION_BACKEND"]
            != service["vllm_attention_backend"]
        )
    ):
        raise ValueError(f"CoCo {role} service environment differs")
    engine_pid = _engine_process(pid, proc_root=proc_root)
    engine_start_identity = _process_start_identity(engine_pid, proc_root=proc_root)
    try:
        engine_executable = (proc_root / str(engine_pid) / "exe").resolve(strict=True)
        engine_working_directory = (
            proc_root / str(engine_pid) / "cwd"
        ).resolve(strict=True)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"CoCo {role} EngineCore identity is unreadable") from exc
    if (
        engine_executable != resolved_python
        or engine_working_directory != expected_working_directory
    ):
        raise ValueError(f"CoCo {role} EngineCore filesystem identity differs")
    if _process_start_identity(pid, proc_root=proc_root) != start_identity:
        raise RuntimeError(f"CoCo {role} service process changed during validation")
    if (
        _engine_process(pid, proc_root=proc_root) != engine_pid
        or _process_start_identity(engine_pid, proc_root=proc_root)
        != engine_start_identity
    ):
        raise RuntimeError(f"CoCo {role} EngineCore changed during validation")
    return {
        "listener": {
            "host": service["host"],
            "port": service["port"],
            "exactly_one_process": True,
        },
        "process": {
            "python_executable": str(registered_python),
            "resolved_python_executable": str(resolved_python),
            "working_directory": str(working_directory),
            "entrypoint": ENTRYPOINT,
            "arguments": parsed,
            "environment": environment,
        },
        "engine": {
            "present": True,
            "resolved_python_executable": str(engine_executable),
            "working_directory": str(engine_working_directory),
        },
    }


def _validate_coco_service_runtime_under_lease(
    scoring_spec: dict[str, Any],
    *,
    proc_root: str | Path = "/proc",
    nvidia_smi_path: str | Path = "/usr/bin/nvidia-smi",
    require_idle_judge_connections: bool = True,
) -> dict[str, Any]:
    """Return a deterministic proof for every registered scoring service."""
    spec = _require_object(scoring_spec, label="CoCo scoring spec")
    spec_id = spec.get("spec_id")
    if not isinstance(spec_id, str) or not _SHA256_PATTERN.fullmatch(spec_id):
        raise ValueError("CoCo scoring spec_id is invalid")
    repository = _require_exact_fields(
        spec.get("repository"),
        {"root", "git_commit", "clean"},
        label="CoCo scoring repository",
    )
    repository_root = Path(str(repository.get("root") or "")).expanduser().resolve()
    if str(repository_root) != repository.get("root") or not repository_root.is_dir():
        raise ValueError("CoCo scoring repository root is invalid")
    services = _require_exact_fields(
        spec.get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    expected_software = _require_exact_fields(
        services.get("software"),
        {"vllm", "transformers"},
        label="CoCo scoring service software",
    )
    service_specs = {
        role: _require_exact_fields(
            services.get(role),
            _JUDGE_SERVICE_FIELDS if role == "judge" else _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        for role in SERVICE_ROLES
    }
    evaluation_runtime = _validate_evaluation_runtime(
        services.get("evaluation"),
        label="CoCo scoring evaluation runtime",
    )
    root = Path(proc_root).expanduser().resolve()
    before_lease = _instance_lease(service_specs, proc_root=root)
    before_pids = {
        role: int(before_lease[role]["listener_pid"])
        for role in SERVICE_ROLES
    }
    if any(
        service_specs[role][flag] is not True
        for role in SERVICE_ROLES
        for flag in ("language_model_only", "enable_prefix_caching")
    ):
        raise ValueError("CoCo scoring services require registered boolean flags")
    if any(service_specs[role]["enforce_eager"] is not True for role in SINGLE_GPU_ROLES):
        raise ValueError("CoCo split and classify require enforce_eager")
    if (
        service_specs["judge"]["enforce_eager"] is not False
        or service_specs["judge"]["disable_custom_all_reduce"] is not True
        or service_specs["judge"]["default_chat_template_kwargs"]
        != {"enable_thinking": False}
        or service_specs["judge"].get("mode_lease")
        != qwen27b_mode_lease_identity()
    ):
        raise ValueError("CoCo judge boolean or chat-template identity differs")

    proof_services: dict[str, Any] = {}
    resolved_pythons: set[Path] = set()
    for role in SERVICE_ROLES:
        service = service_specs[role]
        process = _validate_process(
            role=role,
            pid=before_pids[role],
            service=service,
            repository_root=repository_root,
            proc_root=root,
        )
        process["endpoint"] = _endpoint_proof(service, role=role)
        if role == "judge":
            process["model_identity"] = _judge_model_identity(service)
        proof_services[role] = process
        resolved_pythons.add(
            Path(process["process"]["resolved_python_executable"])
        )
    engine_pids = {
        role: int(before_lease[role]["engine_pid"])
        for role in SERVICE_ROLES
    }
    gpu_assignments = _engine_gpu_assignments(
        engine_pids=engine_pids,
        service_specs=service_specs,
        nvidia_smi_path=Path(nvidia_smi_path).expanduser().resolve(),
        proc_root=root,
    )
    for role in SERVICE_ROLES:
        proof_services[role]["engine"].update(gpu_assignments[role])
    if len(resolved_pythons) != 1:
        raise ValueError("CoCo services use different Python environments")
    observed_software = _software_versions(next(iter(resolved_pythons)))
    if observed_software != expected_software:
        raise ValueError("CoCo service software versions differ from the scoring spec")
    after_lease = _instance_lease(service_specs, proc_root=root)
    if after_lease != before_lease:
        raise RuntimeError(
            "CoCo service listener or EngineCore changed during validation"
        )
    judge_exclusivity = validate_judge_reviewer_exclusivity(
        proc_root=root,
        judge_port=int(service_specs["judge"]["port"]),
        require_idle_connections=require_idle_judge_connections,
    )

    body = {
        "protocol": PROTOCOL,
        "scoring_spec_id": spec_id,
        "software": dict(observed_software),
        "evaluation_runtime": dict(evaluation_runtime),
        "judge_exclusivity": judge_exclusivity,
        "services": proof_services,
    }
    return {**body, "proof_id": value_sha256(body)}


def validate_coco_service_runtime(
    scoring_spec: dict[str, Any],
    *,
    proc_root: str | Path = "/proc",
    nvidia_smi_path: str | Path = "/usr/bin/nvidia-smi",
    require_idle_judge_connections: bool = True,
) -> dict[str, Any]:
    """Prove all services while holding the shared Judge side of the mode lease."""
    with Qwen27BModeLease("judge"):
        if not isinstance(require_idle_judge_connections, bool):
            raise TypeError("require_idle_judge_connections must be a boolean")
        return _validate_coco_service_runtime_under_lease(
            scoring_spec,
            proc_root=proc_root,
            nvidia_smi_path=nvidia_smi_path,
            require_idle_judge_connections=require_idle_judge_connections,
        )


def validate_judge_scoring_runtime(
    scoring_spec: dict[str, Any],
    *,
    sealed_service_runtime: dict[str, Any],
    expected_mode_lease_state: dict[str, Any],
    expected_judge_instance: dict[str, Any],
    proc_root: str | Path = "/proc",
    nvidia_smi_path: str | Path = "/usr/bin/nvidia-smi",
) -> dict[str, Any]:
    """Re-prove live scoring identity under SH and match the sealed instance."""
    spec = _require_object(scoring_spec, label="CoCo scoring spec")
    services = _require_exact_fields(
        spec.get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    service_specs = {
        role: _require_exact_fields(
            services.get(role),
            _JUDGE_SERVICE_FIELDS if role == "judge" else _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        for role in SERVICE_ROLES
    }
    root = Path(proc_root).expanduser().resolve()
    with Qwen27BModeLease("judge") as mode_lease:
        if mode_lease.state_identity != expected_mode_lease_state:
            raise RuntimeError(
                "CoCo scoring mode-lease state differs from the pre-score seal"
            )
        current_instance = _current_judge_instance(
            service_specs,
            proc_root=root,
        )
        if current_instance != expected_judge_instance:
            raise RuntimeError(
                "CoCo scoring judge instance differs from the pre-score seal"
            )
        current = _validate_coco_service_runtime_under_lease(
            spec,
            proc_root=root,
            nvidia_smi_path=nvidia_smi_path,
            require_idle_judge_connections=False,
        )
        if (
            _current_judge_instance(service_specs, proc_root=root)
            != expected_judge_instance
        ):
            raise RuntimeError(
                "CoCo scoring judge instance changed during runtime validation"
            )
        mode_lease.assert_state_identity(expected_mode_lease_state)
    sealed = validate_coco_service_runtime_proof(
        sealed_service_runtime,
        scoring_spec_id=str(spec.get("spec_id") or ""),
        scoring_spec=spec,
    )
    for name in (
        "protocol",
        "scoring_spec_id",
        "software",
        "evaluation_runtime",
        "services",
    ):
        if current.get(name) != sealed.get(name):
            raise RuntimeError(
                "Live CoCo scoring service identity differs from its sealed proof"
            )
    return current


def validate_coco_service_runtime_proof(
    value: Any,
    *,
    scoring_spec_id: str,
    scoring_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the deterministic envelope before accepting a stored proof."""
    proof = _require_exact_fields(
        value,
        {
            "protocol",
            "scoring_spec_id",
            "software",
            "evaluation_runtime",
            "judge_exclusivity",
            "services",
            "proof_id",
        },
        label="CoCo service runtime proof",
    )
    body = {name: item for name, item in proof.items() if name != "proof_id"}
    if (
        proof.get("protocol") != PROTOCOL
        or proof.get("scoring_spec_id") != scoring_spec_id
        or proof.get("proof_id") != value_sha256(body)
    ):
        raise ValueError("CoCo service runtime proof identity differs")
    _require_exact_fields(
        proof.get("software"),
        {"vllm", "transformers"},
        label="CoCo service runtime proof software",
    )
    _validate_evaluation_runtime(
        proof.get("evaluation_runtime"),
        label="CoCo service runtime proof evaluation runtime",
    )
    exclusivity = _require_exact_fields(
        proof.get("judge_exclusivity"),
        {
            "blocked_agentreview_stages",
            "reviewer_evolution_processes_absent",
            "established_judge_connections",
            "idle_connections_required",
        },
        label="CoCo judge exclusivity proof",
    )
    if (
        exclusivity.get("blocked_agentreview_stages")
        != sorted(_JUDGE_BLOCKED_AGENTREVIEW_STAGES)
        or exclusivity.get("reviewer_evolution_processes_absent") is not True
        or type(exclusivity.get("established_judge_connections")) is not int
        or exclusivity["established_judge_connections"] < 0
        or not isinstance(exclusivity.get("idle_connections_required"), bool)
        or (
            exclusivity["idle_connections_required"]
            and exclusivity["established_judge_connections"] != 0
        )
    ):
        raise ValueError("CoCo judge exclusivity proof differs")
    services = _require_exact_fields(
        proof.get("services"),
        set(SERVICE_ROLES),
        label="CoCo service runtime proof services",
    )
    for role in SERVICE_ROLES:
        role_proof = _require_exact_fields(
            services.get(role),
            {"listener", "process", "engine", "endpoint"}
            | ({"model_identity"} if role == "judge" else set()),
            label=f"CoCo {role} service runtime proof",
        )
        listener = _require_exact_fields(
            role_proof.get("listener"),
            {"host", "port", "exactly_one_process"},
            label=f"CoCo {role} listener proof",
        )
        if listener.get("exactly_one_process") is not True:
            raise ValueError(f"CoCo {role} listener proof is incomplete")
        process = _require_exact_fields(
            role_proof.get("process"),
            {
                "python_executable",
                "resolved_python_executable",
                "working_directory",
                "entrypoint",
                "arguments",
                "environment",
            },
            label=f"CoCo {role} process proof",
        )
        if process.get("entrypoint") != ENTRYPOINT:
            raise ValueError(f"CoCo {role} process proof entrypoint differs")
        engine = _require_exact_fields(
            role_proof.get("engine"),
            {
                "present",
                "resolved_python_executable",
                "working_directory",
                "gpu_indices",
                "compute_process_count",
                "all_compute_devices_registered",
            },
            label=f"CoCo {role} EngineCore proof",
        )
        if (
            engine.get("present") is not True
            or engine.get("all_compute_devices_registered") is not True
            or type(engine.get("compute_process_count")) is not int
            or engine["compute_process_count"] < 1
            or not isinstance(engine.get("gpu_indices"), list)
            or not engine["gpu_indices"]
            or any(
                not isinstance(index, str) or not re.fullmatch(r"[0-9]+", index)
                for index in engine["gpu_indices"]
            )
        ):
            raise ValueError(f"CoCo {role} EngineCore proof is incomplete")
        arguments = _require_exact_fields(
            process.get("arguments"),
            (
                set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values())
                if role == "judge"
                else (
                    (set(_VALUE_OPTIONS.values()) - {"default_chat_template_kwargs"})
                    | (
                        set(_BOOLEAN_OPTIONS.values())
                        - {"disable_custom_all_reduce"}
                    )
                )
            ),
            label=f"CoCo {role} argument proof",
        )
        expected_boolean_arguments = (
            {
                "language_model_only": True,
                "enable_prefix_caching": True,
                "enforce_eager": False,
                "disable_custom_all_reduce": True,
            }
            if role == "judge"
            else {
                "language_model_only": True,
                "enable_prefix_caching": True,
                "enforce_eager": True,
            }
        )
        if any(
            arguments.get(name) is not expected
            for name, expected in expected_boolean_arguments.items()
        ):
            raise ValueError(
                f"CoCo {role} argument proof boolean flags differ"
            )
        if any(
            type(arguments[name]) is not int or arguments[name] < 1
            for name in _INTEGER_ARGUMENTS
        ):
            raise ValueError(
                f"CoCo {role} argument proof integer values must be positive integers"
            )
        if any(
            isinstance(arguments[name], bool)
            or not isinstance(arguments[name], (int, float))
            or not math.isfinite(float(arguments[name]))
            for name in _FLOAT_ARGUMENTS
        ):
            raise ValueError(
                f"CoCo {role} argument proof float values must be finite numbers"
            )
        text_arguments = (
            set(_VALUE_OPTIONS.values())
            - _INTEGER_ARGUMENTS
            - _FLOAT_ARGUMENTS
            - {"default_chat_template_kwargs"}
        )
        if any(
            not isinstance(arguments[name], str) or not arguments[name]
            for name in text_arguments
        ):
            raise ValueError(
                f"CoCo {role} argument proof text values must be non-empty strings"
            )
        if role == "judge" and arguments.get("default_chat_template_kwargs") != {
            "enable_thinking": False
        }:
            raise ValueError(
                "CoCo judge argument proof chat-template kwargs differ"
            )
        environment = _require_exact_fields(
            process.get("environment"),
            ({
                "CUDA_VISIBLE_DEVICES",
                "PYTHONHOME_unset",
                "PYTHONPATH_unset",
                "CUDA_DEVICE_ORDER_unset",
                "CUDA_HOME",
                "CUDAToolkit_ROOT",
                "unregistered_runtime_controls_absent",
            } | (
                {"VLLM_ATTENTION_BACKEND_unset"}
                if role == "judge"
                else {"VLLM_ATTENTION_BACKEND"}
            )),
            label=f"CoCo {role} environment proof",
        )
        environment_flags = (
            "PYTHONHOME_unset",
            "PYTHONPATH_unset",
            "CUDA_DEVICE_ORDER_unset",
            "unregistered_runtime_controls_absent",
        )
        if any(environment[name] is not True for name in environment_flags):
            raise ValueError(
                f"CoCo {role} environment proof boolean flags must all be true"
            )
        if role == "judge" and environment["VLLM_ATTENTION_BACKEND_unset"] is not True:
            raise ValueError("CoCo judge attention backend proof differs")
        _require_exact_fields(
            role_proof.get("endpoint"),
            {"health", "models"},
            label=f"CoCo {role} endpoint proof",
        )
        if role == "judge":
            model_identity = _require_exact_fields(
                role_proof.get("model_identity"),
                {
                    "source_model",
                    "revision",
                    "tokenizer_sha256",
                    "chat_template_sha256",
                    "config_sha256",
                    "tokenizer_config_sha256",
                    "generation_config_sha256",
                    "model_index_sha256",
                    "weight_file_count",
                    "weight_total_bytes",
                    "weights_tree_sha256",
                },
                label="CoCo judge model identity proof",
            )
            hash_fields = {
                "tokenizer_sha256",
                "chat_template_sha256",
                "config_sha256",
                "tokenizer_config_sha256",
                "generation_config_sha256",
                "model_index_sha256",
                "weights_tree_sha256",
            }
            if (
                any(
                    not isinstance(model_identity.get(name), str)
                    or re.fullmatch(r"[0-9a-f]{64}", model_identity[name]) is None
                    for name in hash_fields
                )
                or any(
                    not isinstance(model_identity.get(name), str)
                    or not model_identity[name]
                    for name in ("source_model", "revision")
                )
                or type(model_identity.get("weight_file_count")) is not int
                or model_identity["weight_file_count"] < 1
                or type(model_identity.get("weight_total_bytes")) is not int
                or model_identity["weight_total_bytes"] < 1
            ):
                raise ValueError("CoCo judge model identity proof differs")
    if scoring_spec is not None:
        _validate_proof_against_spec(proof, scoring_spec=scoring_spec)
    return proof


def _validate_proof_against_spec(
    proof: dict[str, Any],
    *,
    scoring_spec: dict[str, Any],
) -> None:
    spec = _require_object(scoring_spec, label="CoCo scoring spec")
    if spec.get("spec_id") != proof.get("scoring_spec_id"):
        raise ValueError("CoCo service runtime proof scoring spec differs")
    repository = _require_object(
        spec.get("repository"), label="CoCo scoring repository"
    )
    repository_root = str(Path(str(repository.get("root") or "")).resolve())
    services = _require_exact_fields(
        spec.get("services"),
        {"software", "evaluation", *SERVICE_ROLES},
        label="CoCo scoring services",
    )
    if proof.get("software") != services.get("software"):
        raise ValueError("CoCo service runtime proof software differs from spec")
    expected_evaluation_runtime = _validate_evaluation_runtime(
        services.get("evaluation"),
        label="CoCo scoring evaluation runtime",
    )
    if proof.get("evaluation_runtime") != expected_evaluation_runtime:
        raise ValueError("CoCo service runtime proof evaluation runtime differs from spec")
    proof_services = _require_object(
        proof.get("services"), label="CoCo service runtime proof services"
    )
    for role in SERVICE_ROLES:
        service = _require_exact_fields(
            services.get(role),
            _JUDGE_SERVICE_FIELDS if role == "judge" else _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        role_proof = _require_object(
            proof_services.get(role), label=f"CoCo {role} service runtime proof"
        )
        expected_python = str(Path(service["python_executable"]).resolve())
        argument_names = (
            set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values())
            if role == "judge"
            else (
                (set(_VALUE_OPTIONS.values()) - {"default_chat_template_kwargs"})
                | (set(_BOOLEAN_OPTIONS.values()) - {"disable_custom_all_reduce"})
            )
        )
        expected_arguments = {name: service[name] for name in argument_names}
        expected_environment = {
            "CUDA_VISIBLE_DEVICES": service["cuda_visible_devices"],
            "PYTHONHOME_unset": True,
            "PYTHONPATH_unset": True,
            "CUDA_DEVICE_ORDER_unset": True,
            "CUDA_HOME": "/usr/local/cuda",
            "CUDAToolkit_ROOT": "/usr/local/cuda",
            "unregistered_runtime_controls_absent": True,
        }
        if role == "judge":
            expected_environment["VLLM_ATTENTION_BACKEND_unset"] = True
        else:
            expected_environment["VLLM_ATTENTION_BACKEND"] = service[
                "vllm_attention_backend"
            ]
        if role_proof.get("listener") != {
            "host": service["host"],
            "port": service["port"],
            "exactly_one_process": True,
        }:
            raise ValueError(f"CoCo {role} listener proof differs from spec")
        process = _require_object(
            role_proof.get("process"), label=f"CoCo {role} process proof"
        )
        if process != {
            "python_executable": service["python_executable"],
            "resolved_python_executable": expected_python,
            "working_directory": (
                service["working_directory"]
                if role == "judge"
                else repository_root
            ),
            "entrypoint": ENTRYPOINT,
            "arguments": expected_arguments,
            "environment": expected_environment,
        }:
            raise ValueError(f"CoCo {role} process proof differs from spec")
        if role_proof.get("engine") != {
            "present": True,
            "resolved_python_executable": expected_python,
            "working_directory": (
                service["working_directory"]
                if role == "judge"
                else repository_root
            ),
            "gpu_indices": service["cuda_visible_devices"].split(","),
            "compute_process_count": service["tensor_parallel_size"],
            "all_compute_devices_registered": True,
        }:
            raise ValueError(f"CoCo {role} EngineCore proof differs from spec")
        endpoint = _require_object(
            role_proof.get("endpoint"), label=f"CoCo {role} endpoint proof"
        )
        if endpoint.get("health") != {"status": 200} or endpoint.get("models") != {
            "status": 200,
            "object": "list",
            "data": [
                {
                    "id": service["served_model_name"],
                    "object": "model",
                    "owned_by": "vllm",
                    "root": str(Path(service["model_root"]).resolve()),
                    "parent": None,
                    "max_model_len": service["max_model_len"],
                }
            ],
        }:
            raise ValueError(f"CoCo {role} endpoint proof differs from spec")
        if role == "judge" and role_proof.get("model_identity") != {
            name: service[name]
            for name in (
                "source_model",
                "revision",
                "tokenizer_sha256",
                "chat_template_sha256",
                "config_sha256",
                "tokenizer_config_sha256",
                "generation_config_sha256",
                "model_index_sha256",
                "weight_file_count",
                "weight_total_bytes",
                "weights_tree_sha256",
            )
        }:
            raise ValueError("CoCo judge model identity proof differs from spec")


__all__ = [
    "PROTOCOL",
    "RESTART_BARRIER_PROTOCOL",
    "validate_judge_reviewer_exclusivity",
    "validate_judge_restart_barrier",
    "validate_sealed_judge_restart_barrier",
    "validate_zero_generation_traffic_proof",
    "validate_judge_scoring_runtime",
    "validate_coco_service_runtime",
    "validate_coco_service_runtime_proof",
]
