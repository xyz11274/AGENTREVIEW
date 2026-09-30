"""Process identity checks for the two unchanged local CoCo models."""
from pathlib import Path
import hashlib
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse

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


def value_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def listener_process(base_url):
    endpoint = urlparse(base_url)
    sockets = [item for item in _listening_sockets() if item[1] == endpoint.port]
    owners = _socket_owner_pids({item[2] for item in sockets})
    pids = {pid for values in owners.values() for pid in values}
    if len(sockets) != 1 or len(pids) != 1:
        raise ValueError("CoCo endpoint requires one identifiable listening process")
    pid = next(iter(pids))
    return {"pid": pid, "start_identity": _process_start_identity(pid, proc_root=Path("/proc")),
            "uid": Path(f"/proc/{pid}").stat().st_uid,
            "argv": _read_null_separated(Path(f"/proc/{pid}/cmdline"), label="CoCo service")}


def local_service_ready(service):
    """Verify a live loopback model against its process arguments, not its label alone."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(service["base_url"].rstrip("/") + "/models", timeout=15) as response:
        models = json.load(response)
    if service["model"] not in {item.get("id") for item in models.get("data", [])}:
        raise ValueError("CoCo service model name differs from its frozen configuration")
    process = listener_process(service["base_url"])
    args = process.pop("argv")
    def option(name):
        values = [args[index + 1] for index, value in enumerate(args[:-1]) if value == name]
        values += [value.split("=", 1)[1] for value in args if value.startswith(name + "=")]
        if len(values) != 1:
            raise ValueError(f"Scoring service requires one explicit {name} argument")
        return values[0]
    model_root = Path(option("--model")).expanduser().resolve()
    if (model_root != Path(service["model_identity"]["root"])
            or option("--served-model-name") != service["model"]):
        raise ValueError("Live scoring process differs from its frozen model identity")
    return {**process, "argv_sha256": value_digest(args), "model_root": str(model_root),
            "model": service["model"], "base_url": service["base_url"],
            "max_model_len": int(option("--max-model-len"))}


def verify_model_inventory(root, files):
    """Check the namespace and change timestamps of previously byte-hashed remote files."""
    root = Path(root).resolve(strict=True)
    actual = {str(path.relative_to(root)) for path in root.rglob("*")
              if path.is_file() and ".cache" not in path.relative_to(root).parts}
    if actual != {row["relative_path"] for row in files}:
        raise ValueError("Remote model file inventory changed")
    for row in files:
        path = root / row["relative_path"]
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Remote model file escapes the verified model root")
        st = path.stat()
        observed = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    "ctime_ns": st.st_ctime_ns, "inode": st.st_ino}
        if observed != {key: row[key] for key in observed}:
            raise ValueError(f"Remote model changed after SHA256 verification: {row['relative_path']}")
    return value_digest(files)


def verify_remote_guard(guard, inventory_digest):
    for row in guard["source_files"]:
        if hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Remote watcher implementation changed")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(15)
        client.connect(guard["socket"])
        pid, uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        args = _read_null_separated(Path(f"/proc/{pid}/cmdline"), label="remote model watcher")
        if uid != os.getuid() or value_digest(args) != guard["argv_sha256"]:
            raise ValueError("Remote model watcher process differs from its frozen identity")
        with client.makefile("rwb") as stream:
            stream.write(json.dumps({"role": guard["role"]}).encode() + b"\n")
            stream.flush()
            result = json.loads(stream.readline())
    if result != {"status": "verified", "inventory_sha256": inventory_digest}:
        raise ValueError("Remote model watcher rejected the current model inventory")
    return {"pid": pid, "start_identity": _process_start_identity(pid, proc_root=Path("/proc"))}
