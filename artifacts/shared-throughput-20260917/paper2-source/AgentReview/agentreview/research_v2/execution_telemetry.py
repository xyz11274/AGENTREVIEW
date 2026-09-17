"""Optional stage timing shared by orchestrators and isolated CoCo workers."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import threading
import time


def emit(**value):
    root = os.environ.get("AGENTREVIEW_TELEMETRY_ROOT")
    if not root:
        return
    path = Path(root) / "worker-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps({"time": time.time(), "pid": os.getpid(),
                     "thread": threading.get_ident(), **value}, ensure_ascii=False) + "\n")


@contextmanager
def span(stage, **tags):
    start = time.monotonic()
    identity = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    emit(event="start", stage=stage, span=identity, **tags)
    try:
        yield
    except BaseException as exc:
        emit(event="end", stage=stage, span=identity, seconds=time.monotonic()-start,
             status="failed", error=str(exc), **tags)
        raise
    else:
        emit(event="end", stage=stage, span=identity, seconds=time.monotonic()-start,
             status="complete", **tags)
