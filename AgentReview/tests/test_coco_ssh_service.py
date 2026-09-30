import hashlib
import os
from pathlib import Path

import pytest

from agentreview.research_v2 import ssh_service
from agentreview.research_v2.local_service import verify_model_inventory
from agentreview.research_v2.model_watch import ModelWatch
from agentreview.research_v2.ssh_model_guard import ModelGuard


def inventory(path):
    st = path.stat()
    return {"relative_path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns, "inode": st.st_ino}


def service():
    return {"base_url": "http://127.0.0.1:13099/v1", "model": "split",
            "model_identity": {"root": "/models/split", "files": [{"path": "/models/split/config.json", "sha256": "abc"}]},
            "ssh": {"base_url": "http://127.0.0.1:13099/v1", "model_root": "/remote/split",
                    "model_files": [{"relative_path": "config.json", "sha256": "abc"}],
                    "identity_file": "/private/key", "target": "user@host"}}


def test_remote_model_requires_exact_complete_content_identity():
    value = service()
    ssh_service.validate_remote_identity(value)
    value["ssh"]["model_files"][0]["sha256"] = "changed"
    with pytest.raises(ValueError, match="bytes must match"):
        ssh_service.validate_remote_identity(value)
    value = service()
    value["ssh"]["model_files"].append(dict(value["ssh"]["model_files"][0]))
    with pytest.raises(ValueError, match="bytes must match"):
        ssh_service.validate_remote_identity(value)


def test_remote_file_rewrite_cannot_hide_behind_restored_mtime(tmp_path):
    path = tmp_path / "weights.bin"
    path.write_bytes(b"same size")
    files = [inventory(path)]
    before = path.stat()
    guard = ModelGuard({"split": {"root": str(tmp_path), "files": files}}, ModelWatch)
    verify_model_inventory(tmp_path, files)
    path.write_bytes(b"evil size")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="modified after byte verification"):
        guard.verify("split")
    with pytest.raises(ValueError, match="modified after byte verification"):
        guard.verify("split")
    guard.watches["split"].close()


def test_remote_unexpected_file_or_symlink_is_rejected(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    path = root / "weights.bin"
    path.write_bytes(b"weights")
    files = [inventory(path)]
    (root / "unexpected.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory changed"):
        verify_model_inventory(root, files)
    (root / "unexpected.json").unlink()
    outside = tmp_path / "outside.bin"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        verify_model_inventory(root, [inventory(path)])


def test_tunnel_pins_loopback_identity_and_destination():
    value = service()
    command = ssh_service.tunnel_command(value)
    assert command[-1] == "user@host"
    assert command[command.index("-L") + 1] == "127.0.0.1:13099:127.0.0.1:13099"
    assert "StrictHostKeyChecking=yes" in command
    assert "ExitOnForwardFailure=yes" in command
    value["base_url"] = "http://0.0.0.0:13099/v1"
    with pytest.raises(ValueError, match="loopback"):
        ssh_service.tunnel_command(value)


def test_wrong_listener_cannot_pass_as_ssh_tunnel(monkeypatch):
    monkeypatch.setattr(ssh_service, "listener_process", lambda url: {
        "pid": os.getpid(), "uid": os.getuid(), "argv": ["ssh", "other@host"]})
    with pytest.raises(ValueError, match="configured owned SSH forward"):
        ssh_service.ssh_service_ready(service())


def test_worker_remote_dispatch_works_without_package_main(monkeypatch):
    from agentreview.research_v2.development_coco_worker import _service_ready
    monkeypatch.setattr(ssh_service, "ssh_service_ready", lambda value: {"model": value["model"]})
    assert _service_ready(service()) == {"model": "split"}
