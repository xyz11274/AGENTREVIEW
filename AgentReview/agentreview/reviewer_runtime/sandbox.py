"""Process-level capability sandbox for executable MemoryPrograms."""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, field, is_dataclass, make_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class SandboxError(RuntimeError):
    pass


_BWRAP_USABLE: bool | None = None
_REVIEW_PLAN_PROTOCOL = "evolvable-review-plan-v1"
_REVIEW_PLAN_FIELDS = {
    "protocol",
    "s2_disposition",
    "selected_s2_ids",
    "decision_label",
    "usage_instruction",
    "context_order",
    "review_strategy",
}
_S2_DISPOSITIONS = {"not_consulted", "consulted_no_use", "use"}
_CONTEXT_NAMES = {"memory", "s2", "strategy"}
_REVIEW_PLAN_TEXT_LIMITS = {
    "decision_label": 128,
    "usage_instruction": 1500,
    "review_strategy": 2000,
}


def _bubblewrap_usable(binary: str | None) -> bool:
    global _BWRAP_USABLE
    if _BWRAP_USABLE is not None:
        return _BWRAP_USABLE
    if not binary:
        _BWRAP_USABLE = False
        return False
    try:
        probe = subprocess.run(
            [
                binary,
                "--unshare-all",
                "--ro-bind",
                "/",
                "/",
                "--",
                "/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        _BWRAP_USABLE = probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _BWRAP_USABLE = False
    return _BWRAP_USABLE


def _value_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Expected dataclass or dict, got {type(value).__name__}")


def _validated_review_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SandboxError("plan_review() must return a dict")
    fields = set(value)
    if fields != _REVIEW_PLAN_FIELDS:
        missing = sorted(_REVIEW_PLAN_FIELDS - fields)
        extra = sorted(fields - _REVIEW_PLAN_FIELDS)
        raise SandboxError(
            f"plan_review() fields mismatch: missing={missing}, extra={extra}"
        )
    if value.get("protocol") != _REVIEW_PLAN_PROTOCOL:
        raise SandboxError(f"plan_review() protocol must be {_REVIEW_PLAN_PROTOCOL!r}")
    disposition = value.get("s2_disposition")
    if disposition not in _S2_DISPOSITIONS:
        raise SandboxError(f"Invalid s2_disposition: {disposition!r}")
    selected_ids = value.get("selected_s2_ids")
    if not isinstance(selected_ids, list) or any(
        not isinstance(item, str) or not item or len(item) > 256
        for item in selected_ids
    ):
        raise SandboxError(
            "selected_s2_ids must be a list of bounded non-empty strings"
        )
    if len(selected_ids) != len(set(selected_ids)):
        raise SandboxError("selected_s2_ids must not contain duplicates")
    if len(selected_ids) > 5:
        raise SandboxError("selected_s2_ids must contain at most five IDs")
    if disposition == "use" and not selected_ids:
        raise SandboxError("s2_disposition='use' requires selected_s2_ids")
    if disposition != "use" and selected_ids:
        raise SandboxError(
            f"s2_disposition={disposition!r} requires empty selected_s2_ids"
        )
    for field_name, maximum in _REVIEW_PLAN_TEXT_LIMITS.items():
        text = value.get(field_name)
        if not isinstance(text, str):
            raise SandboxError(f"{field_name} must be a string")
        if len(text) > maximum:
            raise SandboxError(f"{field_name} exceeds {maximum} characters")
    context_order = value.get("context_order")
    if (
        not isinstance(context_order, list)
        or len(context_order) != len(_CONTEXT_NAMES)
        or any(not isinstance(item, str) for item in context_order)
        or set(context_order) != _CONTEXT_NAMES
    ):
        raise SandboxError(
            "context_order must be a permutation of memory, s2, and strategy"
        )
    return dict(value)


class SandboxSession:
    """Keep one candidate KB alive while all capabilities stay in the parent."""

    def __init__(
        self,
        program_path: str,
        toolkit,
        timeout_seconds: float = 60.0,
        memory_mb: int = 1024,
        cpu_seconds: int = 60,
        require_bubblewrap: bool = True,
    ):
        self.program_path = Path(program_path).expanduser().resolve()
        self.toolkit = toolkit
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.memory_mb = memory_mb
        self.cpu_seconds = cpu_seconds
        self.require_bubblewrap = require_bubblewrap
        self._command_id = 0
        self._lock = threading.Lock()
        self._tempdir: tempfile.TemporaryDirectory | None = None
        self._process: subprocess.Popen | None = None
        self.logs = []
        try:
            self._start()
        except BaseException:
            self.close(force=True)
            raise

    def _start(self):
        if not self.program_path.is_file():
            raise FileNotFoundError(f"MemoryProgram not found: {self.program_path}")
        bwrap = shutil.which("bwrap")
        bwrap_enabled = _bubblewrap_usable(bwrap)
        if self.require_bubblewrap and not bwrap_enabled:
            raise SandboxError(
                "bubblewrap was required but this host does not permit its namespaces"
            )

        self._tempdir = tempfile.TemporaryDirectory(prefix="mstar_sandbox_")
        temp_root = Path(self._tempdir.name)
        candidate = temp_root / "program.py"
        candidate.write_bytes(self.program_path.read_bytes())
        worker = Path(__file__).resolve().with_name("sandbox_worker.py")
        python_executable = Path(sys.executable).resolve()
        python_root = python_executable.parents[1]
        sandbox_python = f"/runtime/bin/{python_executable.name}"

        if bwrap_enabled:
            command = [
                bwrap,
                "--die-with-parent",
                "--unshare-all",
                "--new-session",
                "--clearenv",
                "--ro-bind",
                "/usr",
                "/usr",
                "--ro-bind",
                "/lib",
                "/lib",
                "--ro-bind",
                "/lib64",
                "/lib64",
                "--ro-bind",
                str(python_root),
                "/runtime",
                "--dir",
                "/sandbox",
                "--dir",
                "/candidate",
                "--ro-bind",
                str(worker),
                "/sandbox/worker.py",
                "--ro-bind",
                str(candidate),
                "/candidate/program.py",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/work",
                "--chdir",
                "/work",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PYTHONHASHSEED",
                "0",
                sandbox_python,
                "-I",
                "-S",
                "/sandbox/worker.py",
                "/candidate/program.py",
                str(self.memory_mb),
                str(self.cpu_seconds),
            ]
        else:
            command = [
                sys.executable,
                "-I",
                "-S",
                str(worker),
                str(candidate),
                str(self.memory_mb),
                str(self.cpu_seconds),
            ]

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={},
        )
        first = self._read_line()
        if first.get("type") != "ready":
            self.close(force=True)
            raise SandboxError(
                first.get("error") or "candidate sandbox failed to start"
            )
        self.execution_identity = first.get("identity", {})
        import hashlib
        if self.execution_identity.get("program_sha256") != hashlib.sha256(candidate.read_bytes()).hexdigest():
            self.close(force=True)
            raise SandboxError("Sandbox loaded program differs from the supplied candidate")
        from agentreview.research_v2.execution_telemetry import emit
        emit(event="identity", stage="candidate_sandbox", source_path=str(self.program_path),
             host_executable=sys.executable, child_pid=self._process.pid, **self.execution_identity)

    def _read_line(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise SandboxError("sandbox process is not available")
        line_holder = []
        error_holder = []

        def read():
            try:
                line_holder.append(self._process.stdout.readline())
            except BaseException as exc:
                error_holder.append(exc)

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(self.timeout_seconds)
        if thread.is_alive():
            self.close(force=True)
            raise TimeoutError(
                f"MemoryProgram sandbox exceeded {self.timeout_seconds:.1f}s"
            )
        if error_holder:
            raise SandboxError(str(error_holder[0]))
        line = line_holder[0] if line_holder else ""
        if not line:
            stderr = ""
            if self._process.stderr is not None:
                stderr = self._process.stderr.read()[-4000:]
            raise SandboxError(
                f"MemoryProgram sandbox exited unexpectedly: {stderr.strip()}"
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise SandboxError(
                f"Invalid sandbox protocol output: {line[:500]}"
            ) from exc

    def _send(self, value: dict[str, Any]):
        if self._process is None or self._process.stdin is None:
            raise SandboxError("sandbox process is not available")
        self._process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _handle_tool_call(self, message: dict[str, Any]):
        call_id = message.get("id")
        name = message.get("name")
        args = list(message.get("args") or [])
        kwargs = dict(message.get("kwargs") or {})
        try:
            self.toolkit.require_capability(str(name))
            if name == "llm_completion":
                value = self.toolkit.llm_completion(*args, **kwargs)
            elif name == "embed":
                value = self.toolkit.embed(*args, **kwargs)
            elif name == "retrieve_current_s2":
                value = self.toolkit.retrieve_current_s2(*args, **kwargs)
            elif name in {"count_tokens", "memory_token_limit", "select_memory_entries"}:
                value = getattr(self.toolkit, name)(*args, **kwargs)
            elif name == "log":
                self.logs.append(
                    {"event": args[0], "payload": args[1] if len(args) > 1 else None}
                )
                value = None
            else:
                raise SandboxError(f"Candidate requested unknown capability: {name}")
            response = {
                "type": "tool_result",
                "id": call_id,
                "ok": True,
                "value": value,
            }
        except BaseException as exc:
            response = {
                "type": "tool_result",
                "id": call_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            # A candidate may catch the worker-side exception. Fail at the host
            # boundary as well, and poison this session so no fallback snapshot
            # or subsequent operation can publish state after a tool failure.
            self._tool_failure = SandboxError(response["error"])
            if getattr(exc, "failure_category", None):
                self._tool_failure.failure_category = exc.failure_category
            self._send(response)
            raise self._tool_failure from exc
        self._send(response)

    def _command(self, name: str, **payload):
        with self._lock:
            if getattr(self, "_tool_failure", None) is not None:
                raise self._tool_failure
            self._command_id += 1
            command_id = self._command_id
            self._send(
                {
                    "type": "command",
                    "id": command_id,
                    "command": name,
                    **payload,
                }
            )
            while True:
                message = self._read_line()
                if message.get("type") == "tool_call":
                    self._handle_tool_call(message)
                    continue
                if message.get("type") == "fatal":
                    raise SandboxError(
                        message.get("error") or "candidate sandbox failed"
                    )
                if message.get("type") != "result" or message.get("id") != command_id:
                    raise SandboxError(f"Unexpected sandbox message: {message}")
                if not message.get("ok"):
                    raise SandboxError(message.get("error") or f"{name} failed")
                return message.get("value")

    def schemas(self):
        return self._command("schemas")

    def snapshot(self) -> dict[str, Any]:
        value = self._command("snapshot")
        if not isinstance(value, dict):
            raise SandboxError("MemoryProgram snapshot must be a JSON object")
        return value

    def restore(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            raise TypeError("MemoryProgram snapshot must be a dict")
        self._command("restore", snapshot=snapshot)

    def program_interface(self):
        schema = self.schemas()

        def build(name, values):
            definitions = []
            type_map = {
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "list": list,
                "List": list,
                "dict": dict,
                "Dict": dict,
            }
            for value in values:
                type_name = str(value.get("type"))
                annotation = type_map.get(type_name)
                if annotation is None:
                    lowered = type_name.lower()
                    annotation = next(
                        (
                            mapped
                            for marker, mapped in (
                                ("list", list),
                                ("dict", dict),
                                ("bool", bool),
                                ("int", int),
                                ("float", float),
                            )
                            if marker in lowered
                        ),
                        str,
                    )
                if value.get("has_factory") and isinstance(
                    value.get("default"), (list, dict)
                ):
                    default_type = type(value["default"])
                    definitions.append(
                        (
                            str(value["name"]),
                            annotation,
                            field(default_factory=default_type),
                        )
                    )
                elif value.get("has_default"):
                    definitions.append(
                        (
                            str(value["name"]),
                            annotation,
                            field(default=value.get("default")),
                        )
                    )
                else:
                    definitions.append((str(value["name"]), annotation))
            return make_dataclass(name, definitions)

        instructions = schema["instructions"]
        return SimpleNamespace(
            KnowledgeItem=build("KnowledgeItem", schema["knowledge_item"]),
            Query=build("Query", schema["query"]),
            KnowledgeBase=object,
            INSTRUCTION_KNOWLEDGE_ITEM=str(instructions["knowledge_item"]),
            INSTRUCTION_QUERY=str(instructions["query"]),
            INSTRUCTION_RESPONSE=str(instructions["response"]),
            ALWAYS_ON_KNOWLEDGE=str(instructions["always_on"]),
        )

    def write(self, item, raw_text: str):
        return self._command("write", item=_value_dict(item), raw_text=str(raw_text))

    def read(self, query) -> str:
        value = self._command("read", query=_value_dict(query))
        if not isinstance(value, str):
            raise SandboxError("KnowledgeBase.read() returned a non-string")
        return value

    def plan_review(self, query, memory_context: str) -> dict[str, Any]:
        query_values = _value_dict(query)
        value = self._command(
            "plan_review",
            query={
                "query_text": str(query_values.get("query_text") or ""),
                "reviewer_dimension": str(
                    query_values.get("reviewer_dimension") or ""
                ),
            },
            memory_context=str(memory_context),
        )
        return _validated_review_plan(value)

    def close(self, force: bool = False):
        process = self._process
        if process is not None and process.poll() is None:
            if not force:
                try:
                    self._command("close")
                except BaseException:
                    force = True
            if force and process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        self._process = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(force=exc is not None)
