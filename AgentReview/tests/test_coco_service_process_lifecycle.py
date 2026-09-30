"""Real exited-child coverage for ownership-safe model service restarts."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

spec = importlib.util.spec_from_file_location("coco_services_lifecycle",
    Path(__file__).resolve().parents[2] / "deployment/coco_services.py")
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


def exited_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.15)"])
    identity = services.process_identity(child.pid)
    deadline = time.monotonic()+5
    while Path(f"/proc/{child.pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z":
        if time.monotonic() > deadline:
            child.kill()
            child.wait()
            raise AssertionError("Test child did not exit")
        time.sleep(.01)
    return child, identity


def test_owned_zombie_is_exited_and_reaped(tmp_path):
    child, identity = exited_child()
    try:
        (tmp_path / "classify.json").write_text(json.dumps({"process": identity}))
        status = services.manage({"state_root": str(tmp_path)}, "classify", "status")
        assert status["running"] is False
        assert not Path(f"/proc/{child.pid}").exists()
        assert status["record"]["process"] == identity
    finally:
        child.wait()


def test_zombie_with_different_start_identity_is_still_rejected(tmp_path):
    child, identity = exited_child()
    try:
        changed = {**identity, "start_time": str(int(identity["start_time"])+1)}
        (tmp_path / "classify.json").write_text(json.dumps({"process": changed}))
        with pytest.raises(RuntimeError, match="PID identity changed"):
            services.manage({"state_root": str(tmp_path)}, "classify", "status")
        assert Path(f"/proc/{child.pid}").exists()
    finally:
        child.wait()


def test_explicit_context_capacity_is_not_silently_ignored():
    config = {"serving_python":sys.executable,"services":{
        "classify":{"gpus":[0,1,2,3],"model_root":"/models/classify","model":"classify","port":18001,
                 "max_model_len":131072}}}
    argv = services.command(config,"classify")
    assert argv[argv.index("--max-model-len")+1] == "131072"
    for invalid in (0,-1,True,"131072"):
        config["services"]["classify"]["max_model_len"] = invalid
        with pytest.raises(ValueError, match="max_model_len"):
            services.command(config,"classify")
