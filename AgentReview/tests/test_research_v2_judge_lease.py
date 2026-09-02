from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from agentreview.research_v2 import judge_lease


def _judge_worker(start, stop, results) -> None:  # type: ignore[no-untyped-def]
    start.wait(5)
    try:
        with judge_lease.Qwen27BModeLease("judge") as lease:
            results.put(("acquired", lease.generation, lease.state_identity))
            stop.wait(5)
    except Exception as exc:  # pragma: no cover - asserted across process boundary
        results.put(("error", type(exc).__name__, str(exc)))


def _fork_conflict_worker(results) -> None:  # type: ignore[no-untyped-def]
    try:
        with judge_lease.Qwen27BModeLease("judge"):
            results.put("acquired")
    except Exception as exc:  # pragma: no cover - asserted across process boundary
        results.put((type(exc).__name__, str(exc)))


@pytest.fixture
def lease_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "runtime" / "qwen38_27b_mode.lock"
    monkeypatch.setattr(judge_lease, "LEASE_PATH", path)
    return path


def test_reviewer_generation_is_process_reentrant(lease_path: Path) -> None:
    with judge_lease.Qwen27BModeLease("reviewer") as first:
        assert first.generation == 1
        with judge_lease.Qwen27BModeLease("reviewer") as nested:
            assert nested.generation == 1
            assert nested.state_identity == first.state_identity
    with judge_lease.Qwen27BModeLease("reviewer") as second:
        assert second.generation == 2
    assert json.loads(lease_path.read_text(encoding="utf-8")) == {
        "protocol": judge_lease.PROTOCOL,
        "reviewer_generation": 2,
    }


def test_two_first_judges_initialize_once_and_share(lease_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    start = context.Event()
    stop = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_judge_worker, args=(start, stop, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    observed = [results.get(timeout=5), results.get(timeout=5)]
    assert [item[0] for item in observed] == ["acquired", "acquired"], observed
    assert {item[1] for item in observed} == {0}
    assert observed[0][2] == observed[1][2]
    stop.set()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0
    assert lease_path.is_file()


def test_fork_child_closes_inherited_descriptor_and_reacquires(
    lease_path: Path,
) -> None:
    context = multiprocessing.get_context("fork")
    with judge_lease.Qwen27BModeLease("reviewer"):
        results = context.Queue()
        process = context.Process(target=_fork_conflict_worker, args=(results,))
        process.start()
        observed = results.get(timeout=5)
        process.join(5)
        assert process.exitcode == 0
        assert observed[0] == "RuntimeError"
        assert "active Reviewer/Evolution" in observed[1]
    with judge_lease.Qwen27BModeLease("judge") as lease:
        assert lease.generation == 1


def test_reviewer_generation_rejects_a_stale_seal(lease_path: Path) -> None:
    with judge_lease.Qwen27BModeLease("judge") as initial:
        sealed = dict(initial.state_identity)
    with judge_lease.Qwen27BModeLease("reviewer"):
        pass
    with judge_lease.Qwen27BModeLease("judge") as current:
        assert current.generation == 1
        with pytest.raises(RuntimeError, match="identity differs"):
            current.assert_state_identity(sealed)


def test_path_replacement_breaks_nested_acquire_without_leaking_outer_lock(
    lease_path: Path,
) -> None:
    outer = judge_lease.Qwen27BModeLease("judge")
    original = lease_path.with_suffix(".original")
    os.rename(lease_path, original)
    descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        raw = (
            json.dumps(
                {
                    "protocol": judge_lease.PROTOCOL,
                    "reviewer_generation": outer.generation,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    with pytest.raises(RuntimeError, match="inode changed"):
        judge_lease.Qwen27BModeLease("judge")
    with pytest.raises(RuntimeError, match="inode changed"):
        outer.close()

    original_descriptor = os.open(original, os.O_RDWR)
    try:
        fcntl.flock(
            original_descriptor,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    finally:
        os.close(original_descriptor)


def test_assert_state_identity_detects_path_replacement(lease_path: Path) -> None:
    lease = judge_lease.Qwen27BModeLease("judge")
    original = lease_path.with_suffix(".original")
    os.rename(lease_path, original)
    descriptor = os.open(
        lease_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    os.close(descriptor)
    with pytest.raises(RuntimeError, match="inode changed"):
        lease.assert_state_identity()
    with pytest.raises(RuntimeError, match="inode changed"):
        lease.close()
