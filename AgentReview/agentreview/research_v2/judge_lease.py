"""Cross-process mode lease for the shared Reviewer/Judge 27B endpoint."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Literal


PROTOCOL = "research-v2-qwen27b-mode-lease-v1"
LEASE_PATH = Path(
    "/data0/xyj/shengao/AgentReview_0915/artifacts/research_v2/runtime/"
    "qwen38_27b_mode.lock"
)
_STATE_LOCK = threading.RLock()
_STATE_MODE: Literal["reviewer", "judge"] | None = None
_STATE_DESCRIPTOR: int | None = None
_STATE_REFERENCES = 0
_STATE_GENERATION: int | None = None
_STATE_OWNER_PID = os.getpid()


def _reset_after_fork() -> None:
    """Discard the child's inherited open-file-description without unlocking it."""

    global _STATE_DESCRIPTOR, _STATE_GENERATION, _STATE_LOCK, _STATE_MODE
    global _STATE_OWNER_PID, _STATE_REFERENCES
    descriptor = _STATE_DESCRIPTOR
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError:
            pass
    _STATE_LOCK = threading.RLock()
    _STATE_MODE = None
    _STATE_DESCRIPTOR = None
    _STATE_REFERENCES = 0
    _STATE_GENERATION = None
    _STATE_OWNER_PID = os.getpid()


os.register_at_fork(after_in_child=_reset_after_fork)


def identity() -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "path": str(LEASE_PATH),
        "judge_lock": "shared",
        "reviewer_lock": "exclusive",
        "nonblocking": True,
    }


class Qwen27BModeLease:
    """Hold one non-blocking flock until explicitly closed or process exit."""

    def __init__(self, mode: Literal["reviewer", "judge"]):
        if mode not in {"reviewer", "judge"}:
            raise ValueError("Qwen27B mode lease requires reviewer or judge mode")
        self.mode = mode
        self._active = False
        self._owner_pid = os.getpid()
        self.generation = -1
        self.state_identity: dict[str, int | str] = {}
        self._acquire()

    @staticmethod
    def _read_state(descriptor: int) -> tuple[int, bytes] | None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            raise ValueError("Qwen27B mode lease state is too large")
        if not raw:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Qwen27B mode lease state is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"protocol", "reviewer_generation"}
            or value.get("protocol") != PROTOCOL
            or type(value.get("reviewer_generation")) is not int
            or value["reviewer_generation"] < 0
        ):
            raise ValueError("Qwen27B mode lease state is invalid")
        canonical = (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if raw != canonical:
            raise ValueError("Qwen27B mode lease state is not canonical")
        return value["reviewer_generation"], raw

    @staticmethod
    def _write_generation(descriptor: int, generation: int) -> None:
        raw = (
            json.dumps(
                {"protocol": PROTOCOL, "reviewer_generation": generation},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        os.ftruncate(descriptor, 0)
        written = 0
        while written < len(raw):
            count = os.pwrite(descriptor, raw[written:], written)
            if count < 1:
                raise RuntimeError("Qwen27B mode lease state write failed")
            written += count
        os.fsync(descriptor)

    @staticmethod
    def _state_identity(
        descriptor: int,
        generation: int,
    ) -> dict[str, int | str]:
        state = Qwen27BModeLease._read_state(descriptor)
        if state is None or state[0] != generation:
            raise RuntimeError("Qwen27B mode lease state changed while locked")
        descriptor_stat = os.fstat(descriptor)
        path_stat = LEASE_PATH.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or descriptor_stat.st_nlink != 1
            or (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise RuntimeError("Qwen27B mode lease inode changed while locked")
        return {
            "st_dev": descriptor_stat.st_dev,
            "st_ino": descriptor_stat.st_ino,
            "st_uid": descriptor_stat.st_uid,
            "mode": stat.S_IMODE(descriptor_stat.st_mode),
            "raw_state_sha256": hashlib.sha256(state[1]).hexdigest(),
            "reviewer_generation": generation,
        }

    def _acquire(self) -> None:
        global _STATE_DESCRIPTOR, _STATE_GENERATION, _STATE_MODE
        global _STATE_OWNER_PID, _STATE_REFERENCES
        with _STATE_LOCK:
            if _STATE_OWNER_PID != os.getpid():
                raise RuntimeError("Qwen27B mode lease process state is stale after fork")
            if _STATE_DESCRIPTOR is not None:
                if _STATE_MODE != self.mode:
                    raise RuntimeError(
                        "Qwen27B process cannot mix Reviewer and Judge mode leases"
                    )
                self.generation = int(_STATE_GENERATION)
                self.state_identity = self._state_identity(
                    _STATE_DESCRIPTOR,
                    self.generation,
                )
                _STATE_REFERENCES += 1
                self._active = True
                return
            LEASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LEASE_PATH.parent.resolve() != LEASE_PATH.parent:
                raise ValueError("Qwen27B mode lease parent must not use a symbolic link")
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(LEASE_PATH, flags, 0o600)
            try:
                descriptor_stat = os.fstat(descriptor)
                path_stat = LEASE_PATH.lstat()
                if (
                    not stat.S_ISREG(descriptor_stat.st_mode)
                    or descriptor_stat.st_nlink != 1
                    or stat.S_IMODE(descriptor_stat.st_mode) != 0o600
                    or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                    != (path_stat.st_dev, path_stat.st_ino)
                ):
                    raise ValueError("Qwen27B mode lease file identity is invalid")
                if self.mode == "reviewer":
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    state = self._read_state(descriptor)
                    generation = 0 if state is None else state[0]
                    if state is None:
                        self._write_generation(descriptor, generation)
                else:
                    generation = self._acquire_initialized_judge_lock(descriptor)
                if self.mode == "reviewer":
                    generation += 1
                    self._write_generation(descriptor, generation)
            except BlockingIOError as exc:
                os.close(descriptor)
                opposite = (
                    "Judge" if self.mode == "reviewer" else "Reviewer/Evolution"
                )
                raise RuntimeError(
                    f"Qwen27B {self.mode} mode conflicts with an active {opposite} lease"
                ) from exc
            except BaseException:
                os.close(descriptor)
                raise
            _STATE_DESCRIPTOR = descriptor
            _STATE_GENERATION = generation
            _STATE_MODE = self.mode
            _STATE_REFERENCES = 1
            _STATE_OWNER_PID = os.getpid()
            self._active = True
            self.generation = generation
            self.state_identity = self._state_identity(descriptor, generation)

    @classmethod
    def _acquire_initialized_judge_lock(cls, descriptor: int) -> int:
        """Acquire SH, serializing only the one-time empty-file initialization."""

        for attempt in range(101):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                # Waiting is limited to an empty state file being initialized by
                # another first Judge. An initialized conflicting Reviewer stays
                # strictly non-blocking and fails immediately.
                initializing = os.fstat(descriptor).st_size == 0
                if not initializing:
                    try:
                        observed = cls._read_state(descriptor)
                        initializing = observed is None or observed[0] == 0
                    except ValueError:
                        # A concurrent ftruncate/pwrite can be observed only
                        # during the initial generation-zero write.
                        initializing = True
                if not initializing or attempt == 100:
                    raise
                time.sleep(0.01)
                continue
            state = cls._read_state(descriptor)
            if state is not None:
                return state[0]
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if attempt == 100:
                    raise
                time.sleep(0.01)
                continue
            state = cls._read_state(descriptor)
            generation = 0 if state is None else state[0]
            if state is None:
                cls._write_generation(descriptor, generation)
            # Linux flock converts the exclusive lock on this open file
            # description directly to shared without an unlocked interval.
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return generation
        raise RuntimeError("Qwen27B mode lease initialization did not converge")

    def close(self) -> None:
        global _STATE_DESCRIPTOR, _STATE_GENERATION, _STATE_MODE, _STATE_REFERENCES
        if self._owner_pid != os.getpid():
            self._active = False
            return
        with _STATE_LOCK:
            if not self._active:
                return
            self._active = False
            _STATE_REFERENCES -= 1
            if _STATE_REFERENCES > 0:
                return
            descriptor = _STATE_DESCRIPTOR
            _STATE_DESCRIPTOR = None
            _STATE_GENERATION = None
            _STATE_MODE = None
            _STATE_REFERENCES = 0
            if descriptor is not None:
                validation_error: BaseException | None = None
                try:
                    current = self._state_identity(descriptor, self.generation)
                    if current != self.state_identity:
                        raise RuntimeError(
                            "Qwen27B mode lease identity changed before release"
                        )
                except BaseException as exc:
                    validation_error = exc
                finally:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
                if validation_error is not None:
                    raise validation_error

    def assert_state_identity(
        self,
        expected: dict[str, int | str] | None = None,
    ) -> dict[str, int | str]:
        """Re-read the locked inode and require the path to still name it."""

        if self._owner_pid != os.getpid() or not self._active:
            raise RuntimeError("Qwen27B mode lease is not active in this process")
        with _STATE_LOCK:
            if (
                _STATE_DESCRIPTOR is None
                or _STATE_MODE != self.mode
                or _STATE_GENERATION != self.generation
            ):
                raise RuntimeError("Qwen27B mode lease process state differs")
            current = self._state_identity(_STATE_DESCRIPTOR, self.generation)
            if expected is not None and current != expected:
                raise RuntimeError("Qwen27B mode lease identity differs")
            self.state_identity = current
            return dict(current)

    def __enter__(self) -> "Qwen27BModeLease":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


__all__ = ["LEASE_PATH", "PROTOCOL", "Qwen27BModeLease", "identity"]
