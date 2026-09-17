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


class _FakeModeLease:
    def __init__(self, _mode: str):
        self.state_identity = {
            "st_dev": 1,
            "st_ino": 2,
            "st_uid": os.getuid(),
            "mode": 0o600,
            "raw_state_sha256": "f" * 64,
            "reviewer_generation": 0,
        }

    def assert_state_identity(self, expected=None):
        return dict(expected or self.state_identity)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


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
parser.add_argument("--disable-custom-all-reduce", action="store_true")
parser.add_argument("--default-chat-template-kwargs")
args = parser.parse_args()

engine_script = "import time; time.sleep(3600)"
if args.tensor_parallel_size > 1:
    engine_script = (
        "import subprocess,time;"
        "subprocess.Popen([" + repr(sys.executable) + ",'-c',"
        "'import time; time.sleep(3600)']);"
        "time.sleep(3600)"
    )
engine = subprocess.Popen([
    "/bin/bash",
    "-c",
    "exec -a VLLM::EngineCore " + sys.executable
    + " -c " + repr(engine_script),
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
        if self.path == "/metrics":
            names = (
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
            lines = []
            for name in names:
                labels = f'engine="0",model_name="{args.served_model_name}"'
                if name == "request_success_total":
                    labels = (
                        'engine="0",finished_reason="stop",'
                        f'model_name="{args.served_model_name}"'
                    )
                lines.append(f"vllm:{name}{{{labels}}} 0.0")
            payload = ("\\n".join(lines) + "\\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
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
            "judge": self._service(
                gpu="2,3",
                port=_free_port(),
                name="qwen3.8-27b",
                max_model_len=65536,
                max_num_seqs=8,
                utilization=0.90,
                tensor_parallel_size=2,
                judge=True,
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
    print("2, GPU-test-2")
    print("3, GPU-test-3")
elif argument == "--query-compute-apps=pid,gpu_uuid":
    mappings = {
        SPLIT_PORT: "GPU-test-0",
        CLASSIFY_PORT: "GPU-test-1",
    }
    for port, uuid in mappings.items():
        path = root / f"engine-{port}.pid"
        if path.is_file():
            print(f"{path.read_text(encoding='ascii')}, {uuid}")
    judge_path = root / "engine-JUDGE_PORT.pid"
    if judge_path.is_file():
        engine_pid = int(judge_path.read_text(encoding="ascii"))
        print(f"{engine_pid}, GPU-test-2")
        for process in pathlib.Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == engine_pid:
                    print(f"{process.name}, GPU-test-3")
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                continue
else:
    raise SystemExit(2)
""".replace("SPLIT_PORT", str(self.services["split"]["port"]))
            .replace("CLASSIFY_PORT", str(self.services["classify"]["port"]))
            .replace("JUDGE_PORT", str(self.services["judge"]["port"])),
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
        tensor_parallel_size: int = 1,
        judge: bool = False,
    ) -> dict:
        model_root = self.coco_root / "model" / name
        model_root.mkdir(parents=True)
        return {
            "cuda_visible_devices": gpu,
            "python_executable": str(QWEN_PYTHON),
            "vllm_attention_backend": "" if judge else "FLASH_ATTN",
            "host": "127.0.0.1",
            "port": port,
            "served_model_name": name,
            "model_root": str(model_root),
            "max_model_len": max_model_len,
            "max_num_seqs": max_num_seqs,
            "tensor_parallel_size": tensor_parallel_size,
            "gpu_memory_utilization": utilization,
            "dtype": "bfloat16",
            "language_model_only": True,
            "reasoning_parser": "qwen3",
            "enable_prefix_caching": True,
            "enforce_eager": not judge,
            **(
                {
                    "disable_custom_all_reduce": True,
                    "default_chat_template_kwargs": {"enable_thinking": False},
                    "working_directory": str(self.coco_root),
                    "source_model": "Qwen/Qwen3.8-27B",
                    "revision": "1" * 40,
                    "tokenizer_sha256": "2" * 64,
                    "chat_template_sha256": "3" * 64,
                    "config_sha256": "4" * 64,
                    "tokenizer_config_sha256": "5" * 64,
                    "generation_config_sha256": "6" * 64,
                    "model_index_sha256": "7" * 64,
                    "weight_file_count": 18,
                    "weight_total_bytes": 1000,
                    "weights_tree_sha256": "8" * 64,
                    "mode_lease": runtime_module.qwen27b_mode_lease_identity(),
                }
                if judge
                else {}
            ),
        }

    def start(self) -> None:
        for service in self.services.values():
            environment = dict(os.environ)
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "CUDA_VISIBLE_DEVICES": service["cuda_visible_devices"],
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDAToolkit_ROOT": "/usr/local/cuda",
                    **self.extra_environment,
                }
            )
            if service["vllm_attention_backend"]:
                environment["VLLM_ATTENTION_BACKEND"] = service[
                    "vllm_attention_backend"
                ]
            else:
                environment.pop("VLLM_ATTENTION_BACKEND", None)
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
            ]
            if service["enforce_eager"]:
                command.append("--enforce-eager")
            if service.get("disable_custom_all_reduce"):
                command.append("--disable-custom-all-reduce")
            if "default_chat_template_kwargs" in service:
                command.extend(
                    [
                        "--default-chat-template-kwargs",
                        json.dumps(
                            service["default_chat_template_kwargs"],
                            separators=(",", ":"),
                        ),
                    ]
                )
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
    def setUp(self):
        model_fields = (
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
        self._patchers = (
            patch.object(runtime_module, "Qwen27BModeLease", _FakeModeLease),
            patch.object(
                runtime_module,
                "_judge_model_identity",
                side_effect=lambda service: {
                    name: service[name] for name in model_fields
                },
            ),
            patch.object(
                runtime_module,
                "validate_judge_reviewer_exclusivity",
                side_effect=lambda **kwargs: {
                    "blocked_agentreview_stages": sorted(
                        runtime_module._JUDGE_BLOCKED_AGENTREVIEW_STAGES
                    ),
                    "reviewer_evolution_processes_absent": True,
                    "established_judge_connections": 0,
                    "idle_connections_required": kwargs[
                        "require_idle_connections"
                    ],
                },
            ),
        )
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()

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
                    "gpu_indices": ["0"],
                    "compute_process_count": 1,
                    "all_compute_devices_registered": True,
                },
            )

            boolean_paths = (
                ("listener.exactly_one_process", "listener", "exactly_one_process"),
                ("engine.present", "engine", "present"),
                (
                    "engine.all_compute_devices_registered",
                    "engine",
                    "all_compute_devices_registered",
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
                        "incomplete|boolean flags (must all be true|differ)",
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
            tampered["evaluation_runtime"]["transport"] = {
                "HTTPS_PROXY": "http://127.0.0.1:7899"
            }
            body = {
                field: item
                for field, item in tampered.items()
                if field != "proof_id"
            }
            tampered["proof_id"] = value_sha256(body)
            with self.assertRaisesRegex(ValueError, "fields differ"):
                validate_coco_service_runtime_proof(
                    tampered,
                    scoring_spec_id=fixture.spec["spec_id"],
                    scoring_spec=fixture.spec,
                )

    def test_fresh_judge_metrics_are_sealed_and_nonzero_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            try:
                proof = runtime_module._judge_zero_generation_traffic(
                    fixture.services["judge"]
                )
            finally:
                fixture.stop()
            self.assertEqual(
                runtime_module.validate_zero_generation_traffic_proof(
                    fixture.spec,
                    proof,
                ),
                proof,
            )
            tampered = copy.deepcopy(proof)
            tampered["metrics"]["prompt_tokens_total"][0]["value"] = 1.0
            tampered["normalized_sha256"] = value_sha256(tampered["metrics"])
            with self.assertRaisesRegex(ValueError, "raw metrics differ"):
                runtime_module.validate_zero_generation_traffic_proof(
                    fixture.spec,
                    tampered,
                )

    def test_historical_restart_barrier_allows_active_judge_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            try:
                zero_traffic = runtime_module._judge_zero_generation_traffic(
                    fixture.services["judge"]
                )
            finally:
                fixture.stop()
            inputs = []
            for index in range(3):
                path = fixture.root / f"input-{index}.json"
                path.write_text("{}\n", encoding="utf-8")
                inputs.append(path)
            instance_body = {
                "boot_id": "11111111-1111-1111-1111-111111111111",
                "listener_pid": 101,
                "listener_start_identity": "listener-start",
                "engine_pid": 102,
                "engine_start_identity": "engine-start",
            }
            instance = {**instance_body, "instance_id": value_sha256(instance_body)}
            state = dict(_FakeModeLease("judge").state_identity)
            barrier = {
                "protocol": runtime_module.RESTART_BARRIER_PROTOCOL,
                "mode_lease": runtime_module.qwen27b_mode_lease_identity(),
                "bound_inputs": sorted(
                    (
                        {"path": str(path), "sha256": runtime_module.file_sha256(path)}
                        for path in inputs
                    ),
                    key=lambda item: item["path"],
                ),
                "minimum_start_delay_seconds": 2,
                "listener_started_after_inputs": True,
                "engine_started_after_inputs": True,
                "prefix_cache_cleared_by_process_restart": True,
                "reviewer_evolution_processes_absent": True,
                "zero_generation_traffic": zero_traffic,
                "mode_lease_state": state,
                "judge_instance": instance,
            }
            with (
                patch.object(
                    runtime_module,
                    "_current_judge_instance",
                    return_value=instance,
                ),
                patch.object(
                    runtime_module,
                    "validate_judge_reviewer_exclusivity",
                    return_value={
                        "reviewer_evolution_processes_absent": True,
                        "established_judge_connections": 5,
                    },
                ) as exclusivity,
            ):
                self.assertEqual(
                    runtime_module.validate_sealed_judge_restart_barrier(
                        fixture.spec,
                        sealed_barrier=barrier,
                        bound_input_paths=inputs,
                    ),
                    barrier,
                )
            self.assertFalse(
                exclusivity.call_args.kwargs["require_idle_connections"]
            )

    def test_scoring_runtime_allows_another_active_judge_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _RuntimeFixture(directory)
            fixture.start()
            try:
                sealed = validate_coco_service_runtime(
                    fixture.spec,
                    nvidia_smi_path=fixture.nvidia_smi,
                )
                instance = runtime_module._current_judge_instance(
                    fixture.services,
                    proc_root=Path("/proc"),
                )
                state = dict(_FakeModeLease("judge").state_identity)
                with patch.object(
                    runtime_module,
                    "validate_judge_reviewer_exclusivity",
                    side_effect=lambda **kwargs: {
                        "blocked_agentreview_stages": sorted(
                            runtime_module._JUDGE_BLOCKED_AGENTREVIEW_STAGES
                        ),
                        "reviewer_evolution_processes_absent": True,
                        "established_judge_connections": 5,
                        "idle_connections_required": kwargs[
                            "require_idle_connections"
                        ],
                    },
                ) as exclusivity:
                    current = runtime_module.validate_judge_scoring_runtime(
                        fixture.spec,
                        sealed_service_runtime=sealed,
                        expected_mode_lease_state=state,
                        expected_judge_instance=instance,
                        nvidia_smi_path=fixture.nvidia_smi,
                    )
            finally:
                fixture.stop()
            self.assertEqual(current["services"], sealed["services"])
            self.assertEqual(
                current["judge_exclusivity"]["established_judge_connections"],
                5,
            )
            self.assertTrue(
                all(
                    call.kwargs["require_idle_connections"] is False
                    for call in exclusivity.call_args_list
                )
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
