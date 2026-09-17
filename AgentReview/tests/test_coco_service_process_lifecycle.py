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
        (tmp_path / "judge.json").write_text(json.dumps({"process": identity}))
        status = services.manage({"state_root": str(tmp_path)}, "judge", "status")
        assert status["running"] is False
        assert not Path(f"/proc/{child.pid}").exists()
        assert status["record"]["process"] == identity
    finally:
        child.wait()


def test_zombie_with_different_start_identity_is_still_rejected(tmp_path):
    child, identity = exited_child()
    try:
        changed = {**identity, "start_time": str(int(identity["start_time"])+1)}
        (tmp_path / "judge.json").write_text(json.dumps({"process": changed}))
        with pytest.raises(RuntimeError, match="PID identity changed"):
            services.manage({"state_root": str(tmp_path)}, "judge", "status")
        assert Path(f"/proc/{child.pid}").exists()
    finally:
        child.wait()
