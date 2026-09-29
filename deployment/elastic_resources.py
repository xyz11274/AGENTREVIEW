"""Demand-only, guardian-owned leases for original CoCo services on GPUs 4-6."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import fcntl
import itertools
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

from four_gpu_study import ServiceSwitcher, ModeCoordinator, mode_config
from coco_services import manage, process_identity
from agentreview.research_v2.io import atomic_write_json, read_json

BORROWABLE = (4, 5, 6)


def same_process(identity):
    try:
        current = process_identity(identity["pid"])
        state = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        return state != "Z" and all(current[k] == identity[k] for k in ("pid", "uid", "start_time"))
    except (FileNotFoundError, ProcessLookupError):
        return False


def gpu_snapshot():
    rows = subprocess.check_output(["nvidia-smi", "--query-gpu=index,uuid,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits"], text=True, timeout=10)
    result = {}
    for line in rows.splitlines():
        index, gpu_uuid, memory, utilization = [v.strip() for v in line.split(",")]
        if int(index) in BORROWABLE:
            result[int(index)] = {"uuid":gpu_uuid, "memory":int(memory), "utilization":int(utilization), "pids":[]}
    processes = subprocess.check_output(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid",
        "--format=csv,noheader,nounits"], text=True, timeout=10)
    for line in processes.splitlines():
        gpu_uuid, pid = [v.strip() for v in line.split(",")]
        for row in result.values():
            if gpu_uuid == row["uuid"]:
                row["pids"].append(int(pid))
    return result


def is_idle(row):
    return not row["pids"] and row["memory"] <= 256 and row["utilization"] == 0


def owned_tree(root, known):
    """Remember descendants before their parent exits; never infer ownership by name."""
    parents = {item["pid"] for item in known.values() if same_process(item)}
    if same_process(root):
        parents.add(root["pid"])
    entries = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(") ", 1)[1].split()
            if path.stat().st_uid == os.getuid() and stat[0] != "Z":
                entries[int(path.name)] = (int(stat[1]), int(stat[2]))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    while True:
        found = {pid for pid, (parent, group) in entries.items()
                 if parent in parents or (parents and group == root["pid"])}
        if found <= parents:
            break
        parents |= found
    for pid in parents:
        try:
            value = process_identity(pid)
            if value["uid"] == os.getuid() and (pid != root["pid"] or same_process(root)):
                if pid not in known:
                    known[pid] = value
        except (FileNotFoundError, ProcessLookupError):
            pass
    return [item for item in known.values() if same_process(item)]


def stop_owned(config, role, record, known):
    started = time.monotonic()
    manage(config, role, "stop")
    sent = set()
    while True:
        live = owned_tree(record["process"], known)
        if not live:
            return
        elapsed = time.monotonic() - started
        sig = signal.SIGKILL if elapsed > 60 else signal.SIGTERM if elapsed > 30 else None
        if sig:
            for identity in live:
                key = (identity["pid"], identity["start_time"], sig)
                if key not in sent and same_process(identity):
                    os.kill(identity["pid"], sig)
                    sent.add(key)
        if elapsed > 90:
            raise RuntimeError("Owned temporary service did not drain; lease cleanup requires diagnosis")
        time.sleep(0.5)


def guardian(directory, lock_fds):
    """Separate session survives controller death and releases only its own service."""
    directory = Path(directory)
    spec = read_json(directory / "lease.json")
    config, role = spec["service_config"], spec["role"]
    gpus = config["services"][role]["gpus"]
    if role not in ("split", "classify") or len(gpus) != 2 or not set(gpus) <= set(BORROWABLE):
        raise ValueError("Temporary services require exactly two of GPUs 4-6; GPU 7 is excluded")
    record, known, error, reason = None, {}, None, "release requested"
    requested = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_:requested.set())
    def status(value, **details):
        atomic_write_json(directory / "status.json", {"status":value,"time":time.time(),
            "guardian_pid":os.getpid(),"gpus":gpus, **details}, immutable=False)
    try:
        snapshot = gpu_snapshot()
        if not all(g in snapshot and is_idle(snapshot[g]) for g in gpus):
            raise RuntimeError("GPU changed occupancy after its lease was locked")
        if not same_process(spec["parent"]):
            return
        record = manage(config, role, "start")
        known[record["process"]["pid"]] = record["process"]
        status("loading", service=record)
        ready = False
        deadline = time.monotonic()+600
        probe = ServiceSwitcher(spec["base_config"])
        while same_process(spec["parent"]) and not requested.is_set() and not (directory / "release").exists():
            own = {item["pid"] for item in owned_tree(record["process"], known)}
            if not same_process(record["process"]):
                raise RuntimeError("Temporary API process exited unexpectedly")
            snapshot = gpu_snapshot()
            foreign = {pid for gpu in gpus for pid in snapshot[gpu]["pids"] if pid not in own}
            if foreign:
                atomic_write_json(directory / "revoke.json", {"reason":"another compute process appeared",
                    "foreign_pids":sorted(foreign),"time":time.time()}, immutable=False)
                # Finish the bounded in-flight stage jobs; the controller then drains.
            if not ready:
                try:
                    probe.ready(config, role)
                    ready = True
                    status("ready", service=record)
                except (OSError, ValueError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Temporary service readiness timed out")
            time.sleep(1)
        if not same_process(spec["parent"]):
            reason = "controller exited"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        status("releasing", reason=reason, error=error)
        try:
            if record:
                stop_owned(config, role, record, known)
            status("released", reason=reason, error=error,
                owned_processes_remaining=[i for i in known.values() if same_process(i)])
        except BaseException as exc:
            status("cleanup_failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            for fd in lock_fds:
                os.close(fd)


class BorrowPool:
    def __init__(self, config, base, emit):
        if config["borrowable_gpus"] != list(BORROWABLE) or config["excluded_gpus"] != [7]:
            raise ValueError("Authorization requires GPUs 4-6 only, with 7 permanently excluded")
        self.config, self.base, self.emit = config, base, emit
        self.root = Path(config["event_root"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_root = Path(config["gpu_lock_root"])
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self.idle_since = {}
        self.mutex, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()

    def observe(self, snapshot, now):
        with self.mutex:
            for gpu in BORROWABLE:
                if gpu in snapshot and is_idle(snapshot[gpu]):
                    self.idle_since.setdefault(gpu, now)
                else:
                    self.idle_since.pop(gpu, None)

    def monitor(self):
        while not self.stop.is_set():
            try:
                self.observe(gpu_snapshot(), time.monotonic())
            except Exception as exc:
                with self.mutex:
                    self.idle_since.clear()
                self.emit(event="borrow_observation_failed", error=str(exc))
            self.stop.wait(2)

    def acquire(self, role, jobs):
        if role not in ("split", "classify") or not jobs or (self.root / "borrow.pause").exists():
            return None
        with self.mutex:
            eligible = [g for g in BORROWABLE if g in self.idle_since
                and time.monotonic()-self.idle_since[g] >= self.config["idle_seconds"]]
        for pair in itertools.combinations(eligible, 2):
            locks = []
            child = None
            try:
                for gpu in pair:
                    lock = (self.lock_root / f"gpu-{gpu}.lock").open("a+b")
                    locks.append(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    snapshot = gpu_snapshot()
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    self.emit(event="borrow_unavailable", role=role, reason=str(exc))
                    return None
                if not all(g in snapshot and is_idle(snapshot[g]) for g in pair):
                    continue
                directory = self.root / "leases" / f"{time.time_ns()}-{role}-{uuid.uuid4().hex[:8]}"
                service, _ = mode_config(self.base, role)
                service["state_root"] = str(directory / "service")
                service["services"][role]["gpus"] = list(pair)
                atomic_write_json(directory / "lease.json", {"parent":process_identity(os.getpid()), "role":role,
                    "service_config":service,"base_config":self.base,"queued_jobs":jobs,"created":time.time()})
                with (directory / "guardian.log").open("ab") as log:
                    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "guard", str(directory),
                        "--lock-fds", *[str(lock.fileno()) for lock in locks]], pass_fds=tuple(lock.fileno() for lock in locks),
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                # The guardian retains these same flock descriptions until release.
                self.emit(event="borrow_loading", role=role, gpus=list(pair), jobs=jobs, lease=str(directory), guardian=child.pid)
                deadline = time.monotonic()+620
                while child.poll() is None and time.monotonic() < deadline:
                    path = directory / "status.json"
                    if path.exists():
                        status = read_json(path)
                        if status["status"] == "ready":
                            self.emit(event="borrow_ready", role=role, gpus=list(pair), lease=str(directory))
                            return directory, child
                        if status["status"] in ("released", "cleanup_failed"):
                            break
                    time.sleep(0.5)
                self.release((directory, child))
                self.emit(event="borrow_unavailable", role=role, lease=str(directory))
                return None
            except BlockingIOError:
                continue
            except BaseException:
                if child is not None:
                    self.release((directory, child))
                raise
            finally:
                for lock in locks:
                    lock.close()
        return None

    def release(self, lease):
        directory, child = lease
        (directory / "release").touch()
        child.wait(timeout=120)
        state = read_json(directory / "status.json")
        if state["status"] != "released" or state.get("owned_processes_remaining"):
            raise RuntimeError(f"Temporary GPU cleanup incomplete: {directory}")
        self.emit(event="borrow_released", lease=str(directory), gpus=state["gpus"], reason=state.get("reason"))

    def revoked(self, lease):
        return (self.root / "borrow.pause").exists() or (lease[0] / "revoke.json").exists()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=25)


class BatchResources:
    def __init__(self, config, base):
        self.config, self.base = config, base
        self.switcher = ServiceSwitcher(base)
        self.coordinator = ModeCoordinator(self.switcher, self.switcher.emit)
        self.pool = BorrowPool(config, base, self.switcher.emit)

    def reviewer(self):
        return self.coordinator.lease("reviewer")

    def execute(self, stage, jobs, original):
        mode = "judge" if stage == "evaluation" else stage
        results, remaining = [None]*len(jobs), []
        def invoke(index):
            try:
                return original(**jobs[index]), None
            except Exception as exc:
                return None, exc
        for index, job in enumerate(jobs):
            state = job["output"] / "stages" / stage / "state.json"
            if state.exists() and read_json(state)["status"] in ("complete", "failed"):
                results[index] = invoke(index)
            else:
                remaining.append(index)
        def check_errors():
            error = next((value[1] for value in results if value and value[1]), None)
            if error:
                raise error
        check_errors()
        lease = None
        if remaining and mode in ("split", "classify"):
            # An existing base service on the same port must be used as-is.
            if not manage(self.base["service_config"], mode, "status")["running"]:
                lease = self.pool.acquire(mode, len(remaining))
        def run_chunk(indices):
            with ThreadPoolExecutor(max_workers=self.config["stage_workers"]) as executor:
                values = list(executor.map(invoke, indices))
            for index, value in zip(indices, values):
                results[index] = value
            check_errors()
        try:
            if lease:
                while remaining and not self.pool.revoked(lease):
                    chunk, remaining = remaining[:self.config["stage_workers"]], remaining[self.config["stage_workers"]:]
                    run_chunk(chunk)
        finally:
            if lease:
                self.pool.release(lease)
        if remaining:
            with self.coordinator.lease(mode):
                for start in range(0, len(remaining), self.config["stage_workers"]):
                    run_chunk(remaining[start:start+self.config["stage_workers"]])
        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["guard"])
    parser.add_argument("directory")
    parser.add_argument("--lock-fds", nargs="+", type=int, required=True)
    args = parser.parse_args()
    guardian(args.directory, args.lock_fds)
