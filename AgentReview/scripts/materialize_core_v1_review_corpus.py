#!/usr/bin/env python3
"""Project a frozen 625-record Corpus after the bootstrap Qwen is stopped."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from agentreview.review_corpus import (
    ROLE_SUBDIMENSIONS,
    CanonicalFullReviewRecord,
    FrozenTokenizer,
    canonical_json_bytes,
    materialize_corpus,
)


def _tokenizer_identity(root: Path) -> str:
    files = []
    for name in (
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ):
        path = root / name
        if path.is_file():
            files.append(
                {
                    "name": name,
                    "byte_length": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    if not files:
        raise FileNotFoundError("Embedding tokenizer identity assets are missing")
    return "qwen3-embedding-tokenizer-" + hashlib.sha256(
        canonical_json_bytes(files)
    ).hexdigest()


def _load_records(path: Path):
    records = []
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8", errors="strict"))
                records.append(CanonicalFullReviewRecord.from_value(value))
            except Exception as exc:
                raise ValueError(
                    f"Invalid canonical Full Review at line {line_number}"
                ) from exc
    if len(records) != 625:
        raise ValueError(f"Frozen Corpus must contain 625 records, got {len(records)}")
    counts = {
        role: sum(record.role == role for record in records)
        for role in ROLE_SUBDIMENSIONS
    }
    if any(count != 125 for count in counts.values()):
        raise ValueError(f"Frozen Corpus role cardinality mismatch: {counts}")
    if len({(record.paper_id, record.role) for record in records}) != 625:
        raise ValueError("Frozen Corpus contains duplicate paper×role records")
    return tuple(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-build", required=True)
    parser.add_argument("--embedding-model-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-projection-tokens", type=int, default=2048)
    args = parser.parse_args()

    corpus_build = Path(args.corpus_build).expanduser().resolve()
    records = _load_records(corpus_build / "canonical_full_reviews.jsonl")
    build_manifest = json.loads(
        (corpus_build / "build_manifest.json").read_text(encoding="utf-8")
    )
    if build_manifest.get("canonical_record_count") != 625:
        raise ValueError("Corpus build manifest is incomplete")

    from transformers import AutoTokenizer

    model_path = Path(args.embedding_model_path).expanduser().resolve()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    frozen = FrozenTokenizer(
        lambda text: tokenizer.encode(text, add_special_tokens=False),
        _tokenizer_identity(model_path),
    )
    result = materialize_corpus(
        records,
        tokenizer=frozen,
        max_projection_tokens=args.max_projection_tokens,
    )
    result.write(args.output)
    print(
        json.dumps(
            {
                "canonical_reviews": len(result.records),
                "primary_projections": len(result.projections),
                "projection_fragments": len(result.fragments),
                "corpus_manifest_sha256": result.manifest[
                    "corpus_manifest_sha256"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

