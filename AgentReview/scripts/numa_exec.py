#!/usr/bin/env python3
"""Exec a command with verified CPU affinity and Linux NUMA memory policy."""

from __future__ import annotations

import argparse
import ctypes
import os


SYS_SET_MEMPOLICY_X86_64 = 238
SYS_GET_MEMPOLICY_X86_64 = 239
MPOL_BIND = 2


def _cpu_set(value: str):
    output = set()
    for part in value.split(","):
        left, separator, right = part.partition("-")
        if separator:
            output.update(range(int(left), int(right) + 1))
        else:
            output.add(int(left))
    if not output:
        raise ValueError("CPU set is empty")
    return output


def _bind_memory_node(node: int):
    if node < 0 or node >= 64:
        raise ValueError("This launcher supports NUMA node ordinals 0-63")
    libc = ctypes.CDLL(None, use_errno=True)
    mask = ctypes.c_ulong(1 << node)
    # Linux rejects maxnode=1 even for a node-0-only mask; pass the full
    # one-word mask width and verify the returned policy below.
    maximum_node = ctypes.c_ulong(64)
    result = libc.syscall(
        SYS_SET_MEMPOLICY_X86_64,
        MPOL_BIND,
        ctypes.byref(mask),
        maximum_node,
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    actual_mode = ctypes.c_int(-1)
    actual_mask = ctypes.c_ulong(0)
    result = libc.syscall(
        SYS_GET_MEMPOLICY_X86_64,
        ctypes.byref(actual_mode),
        ctypes.byref(actual_mask),
        maximum_node,
        None,
        0,
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    if actual_mode.value != MPOL_BIND or actual_mask.value != mask.value:
        raise RuntimeError(
            "NUMA memory policy did not bind exactly: "
            f"mode={actual_mode.value}, mask={actual_mask.value:#x}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpus", required=True)
    parser.add_argument("--memory-node", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("Command is required after --")
    cpus = _cpu_set(args.cpus)
    os.sched_setaffinity(0, cpus)
    if os.sched_getaffinity(0) != cpus:
        raise RuntimeError("CPU affinity did not apply exactly")
    _bind_memory_node(args.memory_node)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
