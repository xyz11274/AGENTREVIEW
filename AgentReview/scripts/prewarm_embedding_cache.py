#!/usr/bin/env python3
"""Precompute formal Qwen3-Embedding-8B vectors and truncation audit."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_embedding_adapter = importlib.import_module("memory_evolution.embedding_adapter")
DEFAULT_QWEN3_EMBEDDING_CACHE = _embedding_adapter.DEFAULT_QWEN3_EMBEDDING_CACHE
QWEN3_MODEL_BATCH_SIZE = _embedding_adapter.QWEN3_MODEL_BATCH_SIZE
QWEN3_MODEL_ID = _embedding_adapter.QWEN3_MODEL_ID
create_qwen3_embedding = _embedding_adapter.create_qwen3_embedding
embed_qwen3_on_devices = _embedding_adapter.embed_qwen3_on_devices

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
    allowed = {*DEFAULT_TASKS, "META"}
    unknown = sorted(set(tasks) - allowed)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown task(s): {', '.join(unknown)}")
    return list(dict.fromkeys(tasks))


def _parse_devices(value: str) -> list[int]:
    try:
        devices = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--embedding-devices must be comma-separated CUDA indices"
        ) from exc
    if not devices or len(devices) != len(set(devices)) or min(devices) < 0:
        raise argparse.ArgumentTypeError(
            "--embedding-devices must contain unique non-negative CUDA indices"
        )
    return devices


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Precompute validation-question and episode-text embeddings in the "
            "formal Qwen3-Embedding-8B cache. This command does not call an LLM."
        )
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_QWEN3_EMBEDDING_CACHE,
        help=f"Qwen-only SQLite cache (default: {DEFAULT_QWEN3_EMBEDDING_CACHE})",
    )
    parser.add_argument("--model-name", default=QWEN3_MODEL_ID)
    parser.add_argument(
        "--embedding-devices",
        type=_parse_devices,
        default=_parse_devices("0"),
        help="CUDA devices for independent deterministic shards, for example 0 or 0,1,2,3.",
    )
    parser.add_argument(
        "--embedding-batch-size", type=int, default=QWEN3_MODEL_BATCH_SIZE
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=list(DEFAULT_TASKS),
        metavar="TASK",
        help="tasks to prewarm; META may be added after its dataset exists",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="audit JSON path; defaults beside the cache",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    tasks = _parse_tasks(args.tasks)
    data_root = args.data_root.expanduser().resolve()
    cache_path = args.cache.expanduser().resolve()
    summary_path = (
        args.summary.expanduser().resolve()
        if args.summary
        else cache_path.with_name(cache_path.stem + ".audit.json")
    )

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("Qwen formal precompute requires a CUDA device")
    unavailable = [
        device
        for device in args.embedding_devices
        if device >= torch.cuda.device_count()
    ]
    if unavailable:
        raise RuntimeError(f"Requested CUDA devices are unavailable: {unavailable}")

    texts = []
    task_counts = {}
    for task in tasks:
        task_root = data_root / task
        questions = _load_field(task_root / "validation.jsonl", "question")
        episodes = _load_field(task_root / "episodes.jsonl", "text")
        task_counts[task] = {
            "validation_questions": len(questions),
            "episodes": len(episodes),
        }
        texts.extend(questions)
        texts.extend(episodes)

    print(f"[PREWARM] data_root={data_root}", flush=True)
    print(f"[PREWARM] cache={cache_path}", flush=True)
    print(f"[PREWARM] devices={args.embedding_devices}", flush=True)
    print(f"[PREWARM] tasks={','.join(tasks)} requested={len(texts)}", flush=True)

    audit_embedder = create_qwen3_embedding(
        model_name=args.model_name,
        revision=args.revision,
        cache_path=str(cache_path),
        device=f"cuda:{args.embedding_devices[0]}",
        batch_size=args.embedding_batch_size,
    )
    truncation = audit_embedder.audit_token_lengths(texts)
    vectors = embed_qwen3_on_devices(
        texts,
        model_name=args.model_name,
        revision=args.revision,
        devices=args.embedding_devices,
        cache_path=str(cache_path),
        batch_size=args.embedding_batch_size,
    )
    import numpy as np

    matrix_digest = hashlib.sha256()
    for text, vector in zip(texts, vectors):
        matrix_digest.update(hashlib.sha256(text.encode("utf-8")).digest())
        matrix_digest.update(np.asarray(vector, dtype="<f4").tobytes())
    single_multi_max_abs_diff = 0.0
    if len(args.embedding_devices) > 1 and texts:
        verification_count = min(3, len(texts))
        verifier = create_qwen3_embedding(
            model_name=args.model_name,
            revision=args.revision,
            cache_path=str(cache_path),
            cache_enabled=False,
            device=f"cuda:{args.embedding_devices[0]}",
            batch_size=args.embedding_batch_size,
        )
        single_vectors = verifier.embed(texts[:verification_count])
        single_multi_max_abs_diff = float(
            np.max(
                np.abs(
                    np.asarray(single_vectors, dtype=np.float32)
                    - np.asarray(vectors[:verification_count], dtype=np.float32)
                )
            )
        )
        if single_multi_max_abs_diff > 1e-4:
            raise RuntimeError(
                "Single-device and multi-device embeddings exceed tolerance: "
                f"max_abs_diff={single_multi_max_abs_diff}"
            )
    summary = {
        "protocol": "formal_temporal_v3_qwen3e8b",
        "embedding": {
            "specification": audit_embedder.embedding_spec,
            "fingerprint": audit_embedder.embedding_fingerprint,
            "cache": str(cache_path),
            "devices": list(args.embedding_devices),
            "worker_count": len(args.embedding_devices),
            "sharding": "stable_input_index_mod_worker_count",
            "merge": "stable_original_input_order",
            "single_multi_tolerance": 1e-4,
            "single_multi_max_abs_diff": single_multi_max_abs_diff,
        },
        "task_counts": task_counts,
        "truncation": truncation,
        "vector_count": len(vectors),
        "vector_dimension": len(vectors[0]) if vectors else 0,
        "ordered_embedding_matrix_sha256": matrix_digest.hexdigest(),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
