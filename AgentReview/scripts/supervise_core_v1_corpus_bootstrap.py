#!/usr/bin/env python3
"""Safely close the Corpus bootstrap service and materialize its projections.

This supervisor never advances to embedding, capacity, or formal execution.  It
only observes an already-running Corpus builder, stops its dedicated Qwen
service at the terminal boundary, and (on success) invokes the deterministic
Corpus projection materializer.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # kill(pid, 0) also succeeds for an unreaped zombie.  Such a process
        # can no longer produce a Corpus result and is terminal for supervision.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    state = stat.rsplit(")", 1)[1].strip().split()[0]
    if state in {"Z", "X"}:
        return False
    return True


def _wait_for_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(1)
    return not _alive(pid)


def _stop_qwen(*, pid: int, tmux_session: str) -> dict:
    disposition = {
        "qwen_pid": pid,
        "tmux_session": tmux_session,
        "sigint_sent": False,
        "sigterm_sent": False,
        "stopped": not _alive(pid),
    }
    if disposition["stopped"]:
        return disposition
    # The tmux pane owns the API server process, so SIGINT preserves vLLM's
    # normal shutdown path and releases all TP workers.
    os.kill(pid, signal.SIGINT)
    disposition["sigint_sent"] = True
    if not _wait_for_exit(pid, 120):
        os.kill(pid, signal.SIGTERM)
        disposition["sigterm_sent"] = True
        disposition["stopped"] = _wait_for_exit(pid, 60)
    else:
        disposition["stopped"] = True
    return disposition


def _validate_success(corpus_build: Path) -> dict:
    manifest_path = corpus_build / "build_manifest.json"
    corpus_path = corpus_build / "canonical_full_reviews.jsonl"
    if not manifest_path.is_file() or not corpus_path.is_file():
        raise RuntimeError("Corpus builder exited without authoritative success files")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("canonical_record_count") != 625:
        raise RuntimeError("Corpus success manifest does not contain 625 records")
    with corpus_path.open("rb") as handle:
        record_count = sum(1 for line in handle if line.strip())
    if record_count != 625:
        raise RuntimeError(f"Canonical Corpus line count is {record_count}, expected 625")
    return {
        "build_manifest_sha256": manifest.get("build_manifest_sha256"),
        "canonical_record_count": record_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder-pid", type=int, required=True)
    parser.add_argument("--qwen-pid", type=int, required=True)
    parser.add_argument("--qwen-tmux-session", required=True)
    parser.add_argument("--corpus-build", required=True)
    parser.add_argument("--embedding-model-path", required=True)
    parser.add_argument("--projection-output", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--max-projection-tokens", type=int, default=2048)
    args = parser.parse_args()

    corpus_build = Path(args.corpus_build).expanduser().resolve()
    projection_output = Path(args.projection_output).expanduser().resolve()
    status_path = Path(args.status).expanduser().resolve()
    failure_path = corpus_build / "checkpoint" / "CORPUS_BUILD_FAILED.json"

    if projection_output.exists():
        raise FileExistsError(f"Projection output already exists: {projection_output}")

    _atomic_json(
        status_path,
        {
            "state": "WAITING_FOR_CORPUS_BUILD",
            "builder_pid": args.builder_pid,
            "qwen_pid": args.qwen_pid,
            "corpus_build": str(corpus_build),
            "projection_output": str(projection_output),
        },
    )
    while _alive(args.builder_pid):
        time.sleep(args.poll_seconds)

    if failure_path.is_file():
        qwen_stop = _stop_qwen(pid=args.qwen_pid, tmux_session=args.qwen_tmux_session)
        _atomic_json(
            status_path,
            {
                "state": "CORPUS_BUILD_FAILED",
                "failure_record": str(failure_path),
                "qwen_stop": qwen_stop,
                "capacity_or_formal_started": False,
            },
        )
        return 2

    try:
        corpus_identity = _validate_success(corpus_build)
    except Exception as exc:
        qwen_stop = _stop_qwen(pid=args.qwen_pid, tmux_session=args.qwen_tmux_session)
        _atomic_json(
            status_path,
            {
                "state": "CORPUS_BUILD_TERMINAL_INVALID",
                "error": f"{type(exc).__name__}: {exc}",
                "qwen_stop": qwen_stop,
                "capacity_or_formal_started": False,
            },
        )
        return 3

    qwen_stop = _stop_qwen(pid=args.qwen_pid, tmux_session=args.qwen_tmux_session)
    if not qwen_stop["stopped"]:
        _atomic_json(
            status_path,
            {
                "state": "QWEN_STOP_FAILED",
                "corpus": corpus_identity,
                "qwen_stop": qwen_stop,
                "capacity_or_formal_started": False,
            },
        )
        return 4

    command = [
        sys.executable,
        str(Path(__file__).with_name("materialize_core_v1_review_corpus.py")),
        "--corpus-build",
        str(corpus_build),
        "--embedding-model-path",
        str(Path(args.embedding_model_path).expanduser().resolve()),
        "--output",
        str(projection_output),
        "--max-projection-tokens",
        str(args.max_projection_tokens),
    ]
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        _atomic_json(
            status_path,
            {
                "state": "PROJECTION_MATERIALIZATION_FAILED",
                "corpus": corpus_identity,
                "qwen_stop": qwen_stop,
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "capacity_or_formal_started": False,
            },
        )
        return 5

    projection_manifest = json.loads(
        (projection_output / "manifest.json").read_text(encoding="utf-8")
    )
    _atomic_json(
        status_path,
        {
            "state": "CORPUS_AND_PROJECTION_READY",
            "corpus": corpus_identity,
            "qwen_stop": qwen_stop,
            "projection_manifest_sha256": projection_manifest.get(
                "corpus_manifest_sha256"
            ),
            "projection_stdout": completed.stdout,
            "capacity_or_formal_started": False,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
