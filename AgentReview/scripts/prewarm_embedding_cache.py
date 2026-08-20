#!/usr/bin/env python3
"""Precompute frozen BGE-M3 embeddings for all specialist reviewers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.embedding_adapter import (
    DEFAULT_EMBEDDING_CACHE,
    create_bge_m3,
)


DEFAULT_TASKS = ("CLAR", "SIGN", "ORIG", "QUAL", "POL")


def _load_field(path: Path, field: str) -> list[str]:
    values = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if field not in value:
                raise ValueError(f"Missing {field!r} in {path}:{line_number}")
            values.append(str(value[field]))
    return values


def _parse_tasks(values: Iterable[str]) -> list[str]:
    tasks = [value.strip().upper() for value in values if value.strip()]
    if not tasks:
        raise argparse.ArgumentTypeError("at least one task is required")
    unknown = sorted(set(tasks) - set(DEFAULT_TASKS))
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown specialist task(s): {', '.join(unknown)}"
        )
    return list(dict.fromkeys(tasks))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Precompute validation-question and episode-text embeddings in the "
            "shared persistent BGE-M3 cache. This does not call any LLM."
        )
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_EMBEDDING_CACHE,
        help=f"shared SQLite cache (default: {DEFAULT_EMBEDDING_CACHE})",
    )
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=list(DEFAULT_TASKS),
        metavar="TASK",
        help="specialist tasks to prewarm (default: CLAR SIGN ORIG QUAL POL)",
    )
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="permit CPU execution when CUDA is unavailable",
    )
    return parser


def _ignore_broken_optional_torchvision() -> None:
    """Keep a broken optional vision install from blocking text-only BGE-M3."""
    try:
        import torchvision  # noqa: F401
    except (ImportError, RuntimeError) as exc:
        import transformers.utils.import_utils as import_utils

        import_utils._torchvision_available = False
        print(
            "[PREWARM] optional torchvision disabled for this process: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


def main() -> None:
    args = _build_parser().parse_args()
    tasks = _parse_tasks(args.tasks)
    data_root = args.data_root.expanduser().resolve()
    cache_path = args.cache.expanduser().resolve()

    import torch

    if not torch.cuda.is_available() and not args.allow_cpu:
        raise RuntimeError(
            "CUDA is unavailable. Select a GPU with CUDA_VISIBLE_DEVICES or pass "
            "--allow-cpu explicitly."
        )
    _ignore_broken_optional_torchvision()
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"[PREWARM] device={device}", flush=True)
    print(f"[PREWARM] data_root={data_root}", flush=True)
    print(f"[PREWARM] cache={cache_path}", flush=True)
    print(f"[PREWARM] tasks={','.join(tasks)}", flush=True)

    embedder = create_bge_m3(
        model_name=args.model_name,
        revision=args.revision,
        cache_path=str(cache_path),
        cache_enabled=True,
    )

    total = 0
    for task in tasks:
        task_root = data_root / task
        questions = _load_field(task_root / "validation.jsonl", "question")
        episodes = _load_field(task_root / "episodes.jsonl", "text")
        print(
            f"[PREWARM] task={task} questions={len(questions)} "
            f"episodes={len(episodes)}",
            flush=True,
        )
        embedder.embed(questions)
        embedder.embed(episodes)
        total += len(questions) + len(episodes)
        print(f"[PREWARM] task={task} complete", flush=True)

    print(
        f"[PREWARM] complete requested={total} cache={cache_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
