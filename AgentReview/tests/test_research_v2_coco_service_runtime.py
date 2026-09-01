import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import coco_service_runtime as runtime_module
from agentreview.research_v2.coco_service_runtime import (
    validate_coco_service_runtime,
    validate_coco_service_runtime_proof,
)
from agentreview.research_v2.io import value_sha256


QWEN_PYTHON = Path("/data0/xyj/miniconda3/envs/qwen38serve/bin/python")


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class _RuntimeFixture:
    def __init__(self, directory: str):
        self.root = Path(directory).resolve()
        self.coco_root = self.root / "coco"
        self.coco_root.mkdir()
        self.processes: list[subprocess.Popen] = []
        self.extra_environment: dict[str, str] = {}
        module = self.coco_root / "vllm/entrypoints/openai/api_server.py"
        module.parent.mkdir(parents=True)
        for package in (
            self.coco_root / "vllm/__init__.py",
            self.coco_root / "vllm/entrypoints/__init__.py",
            self.coco_root / "vllm/entrypoints/openai/__init__.py",
        ):
            package.write_text("", encoding="utf-8")
        module.write_text(
            """
import argparse
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--served-model-name", required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--tensor-parallel-size", required=True, type=int)
parser.add_argument("--max-model-len", required=True, type=int)
parser.add_argument("--max-num-seqs", required=True, type=int)
parser.add_argument("--gpu-memory-utilization", required=True, type=float)
parser.add_argument("--dtype", required=True)
parser.add_argument("--language-model-only", action="store_true")
parser.add_argument("--reasoning-parser", required=True)
parser.add_argument("--enable-prefix-caching", action="store_true")
parser.add_argument("--enforce-eager", action="store_true")
args = parser.parse_args()

engine = subprocess.Popen([
    "/bin/bash",
    "-c",
    "exec -a VLLM::EngineCore " + sys.executable
    + " -c 'import time; time.sleep(3600)'",
])
with open(f"engine-{args.port}.pid", "w", encoding="ascii") as handle:
    handle.write(str(engine.pid))
for _ in range(100):
    try:
        with open(f"/proc/{engine.pid}/cmdline", "rb") as handle:
            if handle.read().split(b"\\0", 1)[0].strip() == b"VLLM::EngineCore":
                break
    except FileNotFoundError:
        pass
    time.sleep(0.01)
else:
    raise RuntimeError("fake EngineCore did not start")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        if self.path == "/v1/models":
            value = {
                "object": "list",
                "data": [{
                    "id": args.served_model_name,
                    "object": "model",
                    "owned_by": "vllm",
                    "root": args.model,
                    "parent": None,
                    "max_model_len": args.max_model_len,
                }],
            }
            payload = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return

ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
""".lstrip(),
            encoding="utf-8",
        )
        self.services = {
            "split": self._service(
                gpu="0",
                port=_free_port(),
                name="Qwen3-8B-Split",
                max_model_len=8192,
                max_num_seqs=8,
                utilization=0.40,
            ),
            "classify": self._service(
                gpu="1",
                port=_free_port(),
                name="Qwen3-8B-Classify",
                max_model_len=32768,
                max_num_seqs=16,
                utilization=0.90,
            ),
        }
        self.nvidia_smi = self.root / "nvidia-smi"
        self.nvidia_smi.write_text(
            """#!/usr/bin/python3
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent / "coco"
argument = sys.argv[1]
if argument == "--query-gpu=index,uuid":
    print("0, GPU-test-0")
    print("1, GPU-test-1")
elif argument == "--query-compute-apps=pid,gpu_uuid":
    mappings = {
        SPLIT_PORT: "GPU-test-0",
        CLASSIFY_PORT: "GPU-test-1",
    }
    for port, uuid in mappings.items():
        path = root / f"engine-{port}.pid"
        if path.is_file():
            print(f"{path.read_text(encoding='ascii')}, {uuid}")
else:
    raise SystemExit(2)
""".replace("SPLIT_PORT", str(self.services["split"]["port"]))
            .replace("CLASSIFY_PORT", str(self.services["classify"]["port"])),
            encoding="utf-8",
        )
        self.nvidia_smi.chmod(0o755)
        self.spec = {
            "spec_id": "a" * 64,
            "repository": {
                "root": str(self.coco_root),
                "git_commit": "b" * 40,
                "clean": True,
            },
            "services": {
                "software": {"vllm": "0.20.1", "transformers": "5.8.1"},
                "evaluation": {
                    "python_executable": str(Path(sys.executable).resolve()),
                    "python_version": "3.12.13",
                    "sys_prefix": str(Path(sys.prefix).resolve()),
                    "user_site_enabled": False,
                    "isolated_mode": False,
                    "no_user_site": True,
                    "dont_write_bytecode": True,
                    "ignore_environment": False,
                    "packages": {
                        "bert-score": "0.3.13",
                        "nltk": "3.9.3",
                        "numpy": "2.2.6",
                        "openai": "2.6.0",
                        "requests": "2.32.5",
                        "rouge-score": "0.1.2",
                        "torch": "2.8.0",
                        "tqdm": "4.67.1",
                        "transformers": "4.57.1",
                    },
                    "prefix_tree": {
                        "root": str(Path(sys.prefix).resolve()),
                        "file_count": 10,
                        "total_bytes": 100,
                        "symlink_count": 1,
                        "pyc_file_count": 2,
                        "tree_sha256": "c" * 64,
                    },
                    "transport": {
                        "HTTP_PROXY": "http://127.0.0.1:7898",
                        "HTTPS_PROXY": "http://127.0.0.1:7898",
                    },
                },
                **self.services,
            },
        }

    def _service(
        self,
        *,
        gpu: str,
        port: int,
        name: str,
        max_model_len: int,
        max_num_seqs: int,
        utilization: float,
    ) -> dict:
        model_root = self.coco_root / "model" / name
        model_root.mkdir(parents=True)
        return {
            "cuda_visible_devices": gpu,
            "python_executable": str(QWEN_PYTHON),
            "vllm_attention_backend": "FLASH_ATTN",
            "host": "127.0.0.1",
            "port": port,
            "served_model_name": name,
            "model_root": str(model_root),
            "max_model_len": max_model_len,
            "max_num_seqs": max_num_seqs,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": utilization,
            "dtype": "bfloat16",
            "language_model_only": True,
            "reasoning_parser": "qwen3",
            "enable_prefix_caching": True,
            "enforce_eager": True,
        }

    def start(self) -> None:
        for service in self.services.values():
            environment = dict(os.environ)
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "CUDA_VISIBLE_DEVICES": service["cuda_visible_devices"],
                    "VLLM_ATTENTION_BACKEND": service["vllm_attention_backend"],
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDAToolkit_ROOT": "/usr/local/cuda",
                    **self.extra_environment,
                }
            )
            command = [
                str(QWEN_PYTHON),
                "-m",
                "vllm.entrypoints.openai.api_server",
                "--model",
                service["model_root"],
                "--served-model-name",
                service["served_model_name"],
                "--host",
                service["host"],
                "--port",
                str(service["port"]),
                "--tensor-parallel-size",
                str(service["tensor_parallel_size"]),
                "--max-model-len",
                str(service["max_model_len"]),
                "--max-num-seqs",
                str(service["max_num_seqs"]),
                "--gpu-memory-utilization",
                str(service["gpu_memory_utilization"]),
                "--dtype",
                service["dtype"],
                "--language-model-only",
                "--reasoning-parser",
                service["reasoning_parser"],
                "--enable-prefix-caching",
                "--enforce-eager",
            ]
            self.processes.append(
                subprocess.Popen(
                    command,
                    cwd=self.coco_root,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            )
        deadline = time.monotonic() + 10
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for service in self.services.values():
            url = f"http://127.0.0.1:{service['port']}/health"
            while True:
                try:
                    with opener.open(url, timeout=0.2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        details = []
                        for process in self.processes:
                            if process.poll() is not None and process.stderr is not None:
                                details.append(process.stderr.read().decode("utf-8"))
                        raise RuntimeError(
                            f"fake CoCo service did not start: {details}"
                        ) from None
                    time.sleep(0.05)

    def stop(self) -> None:
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for service in self.services.values():
            engine_path = self.coco_root / f"engine-{service['port']}.pid"
            if engine_path.is_file():
                try:
                    os.kill(int(engine_path.read_text(encoding="ascii")), 15)
                except (ProcessLookupError, ValueError):
                    pass


@unittest.skipUnless(QWEN_PYTHON.is_file(), "registered CoCo Python is unavailable")
class CocoServiceRuntimeTest(unittest.TestCase):
    def test_exact_services_produce_pid_free_deterministic_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            try:
                first = validate_coco_service_runtime(
                    fixture.spec, nvidia_smi_path=fixture.nvidia_smi
                )
                second = validate_coco_service_runtime(
                    fixture.spec, nvidia_smi_path=fixture.nvidia_smi
                )
            finally:
                fixture.stop()
            self.assertEqual(first, second)
            self.assertEqual(
                validate_coco_service_runtime_proof(
                    first,
                    scoring_spec_id=fixture.spec["spec_id"],
                    scoring_spec=fixture.spec,
                ),
                first,
            )
            rendered = json.dumps(first, sort_keys=True).lower()
            self.assertNotIn("pid", rendered)
            self.assertNotIn("timestamp", rendered)
            self.assertNotIn("api_key", rendered)
            self.assertEqual(
                first["evaluation_runtime"],
                fixture.spec["services"]["evaluation"],
            )
            self.assertEqual(first["services"]["split"]["listener"]["port"], fixture.services["split"]["port"])
            self.assertEqual(
                first["services"]["split"]["engine"],
                {
                    "present": True,
                    "resolved_python_executable": str(QWEN_PYTHON.resolve()),
                    "working_directory": str(fixture.coco_root),
                    "gpu_index": "0",
                    "exactly_one_compute_device": True,
                },
            )

            boolean_paths = (
                ("listener.exactly_one_process", "listener", "exactly_one_process"),
                ("engine.present", "engine", "present"),
                (
                    "engine.exactly_one_compute_device",
                    "engine",
                    "exactly_one_compute_device",
                ),
                (
                    "arguments.language_model_only",
                    "process",
                    "arguments",
                    "language_model_only",
                ),
                (
                    "arguments.enable_prefix_caching",
                    "process",
                    "arguments",
                    "enable_prefix_caching",
                ),
                (
                    "arguments.enforce_eager",
                    "process",
                    "arguments",
                    "enforce_eager",
                ),
                (
                    "environment.PYTHONHOME_unset",
                    "process",
                    "environment",
                    "PYTHONHOME_unset",
                ),
                (
                    "environment.PYTHONPATH_unset",
                    "process",
                    "environment",
                    "PYTHONPATH_unset",
                ),
                (
                    "environment.CUDA_DEVICE_ORDER_unset",
                    "process",
                    "environment",
                    "CUDA_DEVICE_ORDER_unset",
                ),
                (
                    "environment.unregistered_runtime_controls_absent",
                    "process",
                    "environment",
                    "unregistered_runtime_controls_absent",
                ),
            )
            for item in boolean_paths:
                label, *path = item
                with self.subTest(integer_boolean=label):
                    tampered = copy.deepcopy(first)
                    target = tampered["services"]["split"]
                    for name in path[:-1]:
                        target = target[name]
                    target[path[-1]] = 1
                    body = {
                        name: value
                        for name, value in tampered.items()
                        if name != "proof_id"
                    }
                    tampered["proof_id"] = value_sha256(body)
                    with self.assertRaisesRegex(
                        ValueError,
                        "incomplete|boolean flags must all be true",
                    ):
                        validate_coco_service_runtime_proof(
                            tampered,
                            scoring_spec_id=fixture.spec["spec_id"],
                            scoring_spec=fixture.spec,
                        )

            tampered = copy.deepcopy(first)
            tampered["services"]["split"]["process"]["arguments"][
                "tensor_parallel_size"
            ] = True
            body = {
                name: value
                for name, value in tampered.items()
                if name != "proof_id"
            }
            tampered["proof_id"] = value_sha256(body)
            with self.assertRaisesRegex(
                ValueError,
                "integer values must be positive integers",
            ):
                validate_coco_service_runtime_proof(
                    tampered,
                    scoring_spec_id=fixture.spec["spec_id"],
                    scoring_spec=fixture.spec,
                )

            for name, replacement in (
                ("user_site_enabled", 0),
                ("isolated_mode", 1),
                ("no_user_site", 1),
                ("dont_write_bytecode", 1),
                ("ignore_environment", 0),
            ):
                with self.subTest(evaluation_runtime_boolean=name):
                    tampered = copy.deepcopy(first)
                    tampered["evaluation_runtime"][name] = replacement
                    body = {
                        field: item
                        for field, item in tampered.items()
                        if field != "proof_id"
                    }
                    tampered["proof_id"] = value_sha256(body)
                    with self.assertRaisesRegex(ValueError, "isolation flags differ"):
                        validate_coco_service_runtime_proof(
                            tampered,
                            scoring_spec_id=fixture.spec["spec_id"],
                            scoring_spec=fixture.spec,
                        )

            tampered = copy.deepcopy(first)
            tampered["evaluation_runtime"]["prefix_tree"]["file_count"] = True
            body = {
                field: item
                for field, item in tampered.items()
                if field != "proof_id"
            }
            tampered["proof_id"] = value_sha256(body)
            with self.assertRaisesRegex(ValueError, "prefix tree counts are invalid"):
                validate_coco_service_runtime_proof(
                    tampered,
                    scoring_spec_id=fixture.spec["spec_id"],
                    scoring_spec=fixture.spec,
                )

            tampered = copy.deepcopy(first)
            tampered["evaluation_runtime"]["transport"]["HTTPS_PROXY"] = (
                "http://127.0.0.1:7899"
            )
            body = {
                field: item
                for field, item in tampered.items()
                if field != "proof_id"
            }
            tampered["proof_id"] = value_sha256(body)
            with self.assertRaisesRegex(ValueError, "transport differs"):
                validate_coco_service_runtime_proof(
                    tampered,
                    scoring_spec_id=fixture.spec["spec_id"],
                    scoring_spec=fixture.spec,
                )

    def test_official_split_spec_rejects_reserve_8192_and_point_40(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            official = copy.deepcopy(fixture.spec)
            official["services"]["split"]["max_model_len"] = 40960
            official["services"]["split"]["gpu_memory_utilization"] = 0.90
            try:
                with self.assertRaisesRegex(ValueError, "service arguments differ"):
                    validate_coco_service_runtime(
                        official, nvidia_smi_path=fixture.nvidia_smi
                    )
            finally:
                fixture.stop()

    def test_tampered_proof_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            try:
                proof = validate_coco_service_runtime(
                    fixture.spec, nvidia_smi_path=fixture.nvidia_smi
                )
            finally:
                fixture.stop()
            proof["services"]["split"]["listener"]["port"] += 1
            with self.assertRaisesRegex(ValueError, "proof identity differs"):
                validate_coco_service_runtime_proof(
                    proof,
                    scoring_spec_id=fixture.spec["spec_id"],
                )

    def test_unregistered_vllm_environment_control_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.extra_environment["VLLM_USE_V1"] = "0"
            fixture.start()
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    "unregistered runtime environment controls",
                ):
                    validate_coco_service_runtime(
                        fixture.spec,
                        nvidia_smi_path=fixture.nvidia_smi,
                    )
            finally:
                fixture.stop()

    def test_runtime_environment_rejects_unregistered_compute_and_loader_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            process_root = proc_root / "123"
            process_root.mkdir()
            registered = {
                "CUDA_VISIBLE_DEVICES": "0",
                "VLLM_ATTENTION_BACKEND": "FLASH_ATTN",
                "CUDA_HOME": "/usr/local/cuda",
                "CUDAToolkit_ROOT": "/usr/local/cuda",
                "HOME": "/tmp/formal-service",
                "PATH": "/usr/bin:/bin",
            }

            def write_environment(extra: dict[str, str]) -> None:
                values = {**registered, **extra}
                (process_root / "environ").write_bytes(
                    b"\0".join(
                        f"{name}={value}".encode("utf-8")
                        for name, value in values.items()
                    )
                    + b"\0"
                )

            write_environment({})
            proof = runtime_module._relevant_environment(
                123,
                proc_root=proc_root,
                role="split",
            )
            self.assertEqual(proof["CUDA_VISIBLE_DEVICES"], "0")
            self.assertEqual(proof["CUDA_HOME"], "/usr/local/cuda")
            self.assertEqual(proof["CUDAToolkit_ROOT"], "/usr/local/cuda")
            self.assertTrue(proof["unregistered_runtime_controls_absent"])

            for name, value in (
                ("CUDA_LAUNCH_BLOCKING", "1"),
                ("NVIDIA_TF32_OVERRIDE", "0"),
                ("LD_PRELOAD", "/tmp/injected.so"),
                ("LD_AUDIT", "/tmp/audit.so"),
                ("FLASH_ATTN_DETERMINISTIC", "1"),
                ("FLASH_ATTENTION_DETERMINISTIC", "1"),
                ("TORCHINDUCTOR_CACHE_DIR", "/tmp/torchinductor"),
                ("TORCHDYNAMO_DISABLE", "1"),
                ("OMP_NUM_THREADS", "1"),
                ("MKL_NUM_THREADS", "1"),
                ("OPENBLAS_NUM_THREADS", "1"),
                ("XLA_FLAGS", "--xla_cpu_use_thunk_runtime=false"),
                ("TENSORRT_LOGGER_LEVEL", "ERROR"),
            ):
                with self.subTest(name=name):
                    write_environment({name: value})
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"unregistered runtime environment controls:.*{name}",
                    ):
                        runtime_module._relevant_environment(
                            123,
                            proc_root=proc_root,
                            role="split",
                        )

            for name in ("PYTHONHOME", "PYTHONPATH"):
                with self.subTest(name=name):
                    write_environment({name: ""})
                    with self.assertRaisesRegex(
                        ValueError,
                        "Python import path is not isolated",
                    ):
                        runtime_module._relevant_environment(
                            123,
                            proc_root=proc_root,
                            role="split",
                        )

    def test_single_proof_rejects_engine_replacement_toctou(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            original = runtime_module._instance_lease
            calls = 0

            def observed_lease(*args, **kwargs):
                nonlocal calls
                calls += 1
                lease = original(*args, **kwargs)
                if calls == 2:
                    lease["split"]["engine_start_identity"] = "replaced"
                return lease

            try:
                with (
                    patch.object(
                        runtime_module,
                        "_instance_lease",
                        side_effect=observed_lease,
                    ),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "listener or EngineCore changed",
                    ),
                ):
                    validate_coco_service_runtime(
                        fixture.spec,
                        nvidia_smi_path=fixture.nvidia_smi,
                    )
            finally:
                fixture.stop()


if __name__ == "__main__":
    unittest.main()
