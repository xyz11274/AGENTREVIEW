"""Deployment ownership and readiness checks require no GPUs or model requests."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time

import pytest


def load_tool(name):
    path = Path(__file__).resolve().parents[2] / "deployment" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(tmp_path):
    return {"state_root": str(tmp_path / "state"), "serving_python": sys.executable,
            "services": {role: {"model_root": str(tmp_path / role), "model": role,
                                "port": 19999, "gpus": [0]}
                         for role in ("split", "classify", "judge")}}


def test_launch_parameters_preserve_original_split_and_classify(tmp_path):
    tool = load_tool("coco_services")
    config = settings(tmp_path)
    split = tool.command(config, "split")
    assert split[split.index("--max-model-len") + 1] == "65536"
    assert json.loads(split[split.index("--hf-overrides") + 1])["rope_scaling"]["factor"] == 4.0
    assert "--reasoning-parser" not in split
    classify = tool.command(config, "classify")
    assert classify[classify.index("--max-model-len") + 1] == "32768"
    assert "--reasoning-parser" not in classify
    assert classify[classify.index("--max-num-seqs") + 1] == "8"
    config["services"]["judge"]["gpus"] = []
    with pytest.raises(ValueError, match="allocated GPU"):
        tool.command(config, "judge")


def test_managed_child_is_the_only_process_signaled(tmp_path, monkeypatch):
    tool = load_tool("coco_services")
    config = settings(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        config["services"]["judge"]["port"] = sock.getsockname()[1]
    monkeypatch.setattr(tool, "command", lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    child = tool.manage(config, "judge", "start")["process"]["pid"]
    try:
        assert tool.manage(config, "judge", "status")["running"]
        environment = dict(item.split(b"=", 1) for item in Path(f"/proc/{child}/environ").read_bytes().split(b"\0") if b"=" in item)
        assert environment[b"PATH"].decode().split(os.pathsep)[0] == str(Path(sys.executable).absolute().parent)
        signals = []
        real_kill = os.kill
        monkeypatch.setattr(tool.os, "kill", lambda pid, sig: (signals.append((pid, sig)), real_kill(pid, sig)))
        result = tool.manage(config, "judge", "stop")
        assert result["pid"] == child
        assert signals == [(child, signal.SIGINT)]
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(child, 0)


def test_reused_pid_is_never_signaled(tmp_path, monkeypatch):
    tool = load_tool("coco_services")
    config = settings(tmp_path)
    root = Path(config["state_root"])
    root.mkdir()
    recorded = {"pid": os.getpid(), "uid": os.getuid(), "start_time": "old", "argv": ["old"]}
    (root / "judge.json").write_text(json.dumps({"process": recorded}))
    monkeypatch.setattr(tool.os, "kill", lambda *args: pytest.fail("must not signal an unrelated process"))
    with pytest.raises(RuntimeError, match="identity changed"):
        tool.manage(config, "judge", "stop")


def test_unavailable_services_report_all_roles_without_generation(monkeypatch):
    tool = load_tool("validate_original_coco")
    def unavailable(service):
        raise ConnectionRefusedError("not deployed")
    monkeypatch.setattr(tool, "_service_ready", unavailable)
    report = tool.readiness({role: {} for role in ("split", "classify", "judge")})
    assert set(report) == {"split", "classify", "judge"}
    assert all(x["status"] == "unavailable" for x in report.values())
