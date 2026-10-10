"""Opt-in cross-process request admission, shared by generation and CoCo."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
import os
from pathlib import Path
import time
import hashlib

from .io import atomic_write_json, file_sha256, read_json
from .execution_telemetry import emit


def profile():
    path = os.environ.get("AGENTREVIEW_EXECUTION_PROFILE")
    if not path:
        return None
    if file_sha256(path) != os.environ.get("AGENTREVIEW_EXECUTION_PROFILE_SHA256"):
        raise ValueError("Execution profile differs from its frozen binding")
    value = read_json(path)
    if (value.get("protocol") != "shared-api-execution-v2"
            or type(value.get("paper_workers")) is not int or value["paper_workers"] <= 0
            or not value.get("limits")
            or any(type(n) is not int or n <= 0 for n in value["limits"].values())):
        raise ValueError("Execution profile requires positive concurrency values")
    return value


class Slot:
    def __init__(self, root, port, limit):
        self.root = Path(root) / hashlib.sha256(str(port).encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        self.port, self.limit, self.fd = port, limit, None
        self.started = time.monotonic()
        # Capacity changes are a separate, idle-service operation. Concurrent
        # clients cannot silently choose incompatible capacities.
        with (self.root / "config.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            atomic_write_json(self.root / "capacity.json", {"limit": limit})

    def try_acquire(self):
        for number in range(self.limit):
            fd = os.open(self.root / f"slot-{number}.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            self.fd = fd
            emit(event="admitted", stage="request_admission", port=self.port,
                 wait_seconds=time.monotonic()-self.started, slot=number)
            return True
        return False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        while not self.try_acquire():
            time.sleep(.01)
        return self

    def __exit__(self, *args):
        self.close()


def slot_for(base_url):
    config = profile()
    if config is None:
        return None
    endpoint = base_url.rstrip("/")
    return Slot(config["admission_root"], endpoint, config["limits"][endpoint])



@contextmanager
def request_slot(base_url):
    slot = slot_for(base_url)
    if slot is None:
        yield
    else:
        with slot:
            yield


@asynccontextmanager
async def async_request_slot(base_url):
    slot = slot_for(base_url)
    try:
        if slot is not None:
            while not slot.try_acquire():
                await asyncio.sleep(.01)
        yield
    finally:
        if slot is not None:
            slot.close()
