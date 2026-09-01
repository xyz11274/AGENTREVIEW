"""Prove that the two local CoCo vLLM services match the scoring spec.

The proof intentionally omits process IDs, start times, response creation times,
and the ambient environment.  Those values are either nondeterministic or can
contain secrets.  Process IDs and start times are still checked internally to
detect listener replacement while validation is in progress.
"""

from __future__ import annotations

import ipaddress
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

from .coco_scoring_spec import FORMAL_EVALUATION_PROXY_URL
from .io import value_sha256


PROTOCOL = "research-v2-coco-service-runtime-proof-v1"
SERVICE_ROLES = ("split", "classify")
ENTRYPOINT = "vllm.entrypoints.openai.api_server"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
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
}
_BOOLEAN_OPTIONS = {
    "--language-model-only": "language_model_only",
    "--enable-prefix-caching": "enable_prefix_caching",
    "--enforce-eager": "enforce_eager",
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
    "transport",
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
    transport = _require_exact_fields(
        runtime.get("transport"),
        {"HTTP_PROXY", "HTTPS_PROXY"},
        label=f"{label} transport",
    )
    if (
        transport.get("HTTP_PROXY") != FORMAL_EVALUATION_PROXY_URL
        or transport.get("HTTPS_PROXY") != FORMAL_EVALUATION_PROXY_URL
    ):
        raise ValueError(f"{label} transport differs")
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
        raise ValueError("CoCo split and classify services require distinct ports")
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
    parsed: dict[str, Any] = {}
    index = 3
    while index < len(argv):
        option = argv[index]
        if option in _BOOLEAN_OPTIONS:
            name = _BOOLEAN_OPTIONS[option]
            if name in parsed:
                raise ValueError(f"CoCo {role} service repeats {option}")
            parsed[name] = True
            index += 1
            continue
        if option not in _VALUE_OPTIONS or index + 1 >= len(argv):
            raise ValueError(f"CoCo {role} service has an unregistered argument: {option}")
        name = _VALUE_OPTIONS[option]
        if name in parsed:
            raise ValueError(f"CoCo {role} service repeats {option}")
        raw = argv[index + 1]
        try:
            if name in _INTEGER_ARGUMENTS:
                parsed[name] = int(raw)
            elif name in _FLOAT_ARGUMENTS:
                parsed[name] = float(raw)
            else:
                parsed[name] = raw
        except ValueError as exc:
            raise ValueError(f"CoCo {role} service has an invalid {option} value") from exc
        index += 2
    expected_names = set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values())
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
    for name in ("CUDA_VISIBLE_DEVICES", "VLLM_ATTENTION_BACKEND"):
        if len(values.get(name, [])) != 1:
            raise ValueError(f"CoCo {role} service requires exactly one {name}")
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
    return {
        "CUDA_VISIBLE_DEVICES": values["CUDA_VISIBLE_DEVICES"][0],
        "VLLM_ATTENTION_BACKEND": values["VLLM_ATTENTION_BACKEND"][0],
        "PYTHONHOME_unset": True,
        "PYTHONPATH_unset": True,
        "CUDA_DEVICE_ORDER_unset": True,
        "CUDA_HOME": values["CUDA_HOME"][0],
        "CUDAToolkit_ROOT": values["CUDAToolkit_ROOT"][0],
        "unregistered_runtime_controls_absent": True,
    }


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
) -> dict[str, str]:
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
    assignments: dict[str, str] = {}
    for role in SERVICE_ROLES:
        gpu_index = str(service_specs[role]["cuda_visible_devices"])
        if not re.fullmatch(r"[0-9]+", gpu_index) or gpu_index not in gpu_uuids:
            raise ValueError(f"CoCo {role} CUDA_VISIBLE_DEVICES is not one GPU index")
        observed = pid_uuids.get(engine_pids[role], [])
        if observed != [gpu_uuids[gpu_index]]:
            raise ValueError(
                f"CoCo {role} EngineCore is not confined to its registered GPU"
            )
        assignments[role] = gpu_index
    if len(set(assignments.values())) != len(SERVICE_ROLES):
        raise ValueError("CoCo split and classify EngineCore GPUs must be distinct")
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
    if working_directory != repository_root:
        raise ValueError(f"CoCo {role} working directory differs from the CoCo repository")

    parsed = _parse_arguments(argv, role=role)
    expected_arguments = {
        name: service[name]
        for name in set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values())
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
        or environment["VLLM_ATTENTION_BACKEND"]
        != service["vllm_attention_backend"]
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
    if engine_executable != resolved_python or engine_working_directory != repository_root:
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


def validate_coco_service_runtime(
    scoring_spec: dict[str, Any],
    *,
    proc_root: str | Path = "/proc",
    nvidia_smi_path: str | Path = "/usr/bin/nvidia-smi",
) -> dict[str, Any]:
    """Return a deterministic proof for the exact two registered services."""
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
            _SERVICE_FIELDS,
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
        for flag in (
            "language_model_only",
            "enable_prefix_caching",
            "enforce_eager",
        )
    ):
        raise ValueError("CoCo scoring services require all registered boolean flags")

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
    )
    for role in SERVICE_ROLES:
        proof_services[role]["engine"].update(
            {
                "gpu_index": gpu_assignments[role],
                "exactly_one_compute_device": True,
            }
        )
    if len(resolved_pythons) != 1:
        raise ValueError("CoCo split and classify use different Python environments")
    observed_software = _software_versions(next(iter(resolved_pythons)))
    if observed_software != expected_software:
        raise ValueError("CoCo service software versions differ from the scoring spec")
    after_lease = _instance_lease(service_specs, proc_root=root)
    if after_lease != before_lease:
        raise RuntimeError(
            "CoCo service listener or EngineCore changed during validation"
        )

    body = {
        "protocol": PROTOCOL,
        "scoring_spec_id": spec_id,
        "software": dict(observed_software),
        "evaluation_runtime": dict(evaluation_runtime),
        "services": proof_services,
    }
    return {**body, "proof_id": value_sha256(body)}


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
    services = _require_exact_fields(
        proof.get("services"),
        set(SERVICE_ROLES),
        label="CoCo service runtime proof services",
    )
    for role in SERVICE_ROLES:
        role_proof = _require_exact_fields(
            services.get(role),
            {"listener", "process", "engine", "endpoint"},
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
                "gpu_index",
                "exactly_one_compute_device",
            },
            label=f"CoCo {role} EngineCore proof",
        )
        if (
            engine.get("present") is not True
            or engine.get("exactly_one_compute_device") is not True
        ):
            raise ValueError(f"CoCo {role} EngineCore proof is incomplete")
        arguments = _require_exact_fields(
            process.get("arguments"),
            set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values()),
            label=f"CoCo {role} argument proof",
        )
        if any(arguments[name] is not True for name in _BOOLEAN_OPTIONS.values()):
            raise ValueError(
                f"CoCo {role} argument proof boolean flags must all be true"
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
        )
        if any(
            not isinstance(arguments[name], str) or not arguments[name]
            for name in text_arguments
        ):
            raise ValueError(
                f"CoCo {role} argument proof text values must be non-empty strings"
            )
        environment = _require_exact_fields(
            process.get("environment"),
            {
                "CUDA_VISIBLE_DEVICES",
                "VLLM_ATTENTION_BACKEND",
                "PYTHONHOME_unset",
                "PYTHONPATH_unset",
                "CUDA_DEVICE_ORDER_unset",
                "CUDA_HOME",
                "CUDAToolkit_ROOT",
                "unregistered_runtime_controls_absent",
            },
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
        _require_exact_fields(
            role_proof.get("endpoint"),
            {"health", "models"},
            label=f"CoCo {role} endpoint proof",
        )
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
    argument_names = set(_VALUE_OPTIONS.values()) | set(_BOOLEAN_OPTIONS.values())
    for role in SERVICE_ROLES:
        service = _require_exact_fields(
            services.get(role),
            _SERVICE_FIELDS,
            label=f"CoCo scoring {role} service",
        )
        role_proof = _require_object(
            proof_services.get(role), label=f"CoCo {role} service runtime proof"
        )
        expected_python = str(Path(service["python_executable"]).resolve())
        expected_arguments = {name: service[name] for name in argument_names}
        expected_environment = {
            "CUDA_VISIBLE_DEVICES": service["cuda_visible_devices"],
            "VLLM_ATTENTION_BACKEND": service["vllm_attention_backend"],
            "PYTHONHOME_unset": True,
            "PYTHONPATH_unset": True,
            "CUDA_DEVICE_ORDER_unset": True,
            "CUDA_HOME": "/usr/local/cuda",
            "CUDAToolkit_ROOT": "/usr/local/cuda",
            "unregistered_runtime_controls_absent": True,
        }
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
            "working_directory": repository_root,
            "entrypoint": ENTRYPOINT,
            "arguments": expected_arguments,
            "environment": expected_environment,
        }:
            raise ValueError(f"CoCo {role} process proof differs from spec")
        if role_proof.get("engine") != {
            "present": True,
            "resolved_python_executable": expected_python,
            "working_directory": repository_root,
            "gpu_index": service["cuda_visible_devices"],
            "exactly_one_compute_device": True,
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


__all__ = [
    "PROTOCOL",
    "validate_coco_service_runtime",
    "validate_coco_service_runtime_proof",
]
