"""Explicit SSH transport for the original CoCo Split/Classify services."""
from pathlib import Path
import json
import os
import shlex
import subprocess
from urllib.parse import urlparse

from .io import value_sha256
from .local_service import listener_process


def ssh_command(connection):
    return ["/usr/bin/ssh", "-i", connection["identity_file"],
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]


def tunnel_command(service):
    connection = service["ssh"]
    local, remote = urlparse(service["base_url"]), urlparse(connection["base_url"])
    if local.hostname != "127.0.0.1" or remote.hostname != "127.0.0.1":
        raise ValueError("SSH CoCo forwarding must bind only loopback on both hosts")
    mapping = f"127.0.0.1:{local.port}:127.0.0.1:{remote.port}"
    return [*ssh_command(connection), "-N", "-T", "-o", "ExitOnForwardFailure=yes",
            "-L", mapping, connection["target"]]


def validate_remote_identity(service):
    connection = service["ssh"]
    local = service["model_identity"]
    root = Path(local["root"])
    expected = {str(Path(item["path"]).relative_to(root)): item["sha256"]
                for item in local["files"] if ".cache" not in Path(item["path"]).relative_to(root).parts}
    observed = {item["relative_path"]: item["sha256"] for item in connection["model_files"]}
    if not observed or expected != observed or len(observed) != len(connection["model_files"]):
        raise ValueError("Remote model bytes must match the complete frozen local model")
    if not Path(connection["model_root"]).is_absolute():
        raise ValueError("Remote model root must be absolute")
    tunnel_command(service)


def ssh_service_ready(service):
    validate_remote_identity(service)
    connection = service["ssh"]
    tunnel = listener_process(service["base_url"])
    if (tunnel["argv"] != tunnel_command(service) or tunnel["uid"] != os.getuid()
            or Path(f"/proc/{tunnel['pid']}/exe").resolve() != Path("/usr/bin/ssh").resolve()):
        raise ValueError("Local listener is not the configured owned SSH forward")
    # Send audited stdlib-only probe code over the authenticated connection. No
    # private key, API key, dataset or executable package is copied to the host.
    source = Path(__file__).with_name("local_service.py").read_text()
    source += '''
import sys
request = json.load(sys.stdin)
fingerprint = verify_model_inventory(request["model_identity"]["root"], request.pop("model_files"))
watcher = verify_remote_guard(request.pop("guard"), fingerprint)
result = local_service_ready(request)
result["verified_model_inventory"] = fingerprint
result["model_watcher"] = watcher
print(json.dumps(result))
'''
    request = {"base_url": connection["base_url"], "model": service["model"],
               "model_identity": {"root": connection["model_root"]},
               "model_files": connection["model_files"], "guard": connection["guard"]}
    command = [*ssh_command(connection), connection["target"],
               "/usr/bin/python3 -c " + shlex.quote(source)]
    result = subprocess.run(command, input=json.dumps(request), capture_output=True,
                            text=True, check=True, timeout=60)
    remote = json.loads(result.stdout)
    if remote["argv_sha256"] != connection["service_argv_sha256"]:
        raise ValueError("Remote service process arguments changed")
    tunnel["argv_sha256"] = value_sha256(tunnel.pop("argv"))
    return {**remote, "transport": "ssh", "ssh_target": connection["target"],
            "base_url": service["base_url"], "remote_base_url": connection["base_url"],
            "tunnel": tunnel}
