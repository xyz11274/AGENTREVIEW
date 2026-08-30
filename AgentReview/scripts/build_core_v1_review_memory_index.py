#!/usr/bin/env python3
"""Materialize five exact-flat role Review Memory indices from frozen Corpus."""

import argparse
import json
from pathlib import Path

from agentreview.review_corpus import CorpusMaterialization, ROLE_SUBDIMENSIONS
from agentreview.review_memory import ReviewMemoryIndex
from memory_evolution.embedding_adapter import create_qwen3_embedding


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default="Qwen/Qwen3-Embedding-8B")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--device", default="cuda:6")
    parser.add_argument("--batch-size", type=int, choices=(1, 4, 8), required=True)
    args = parser.parse_args()
    corpus = CorpusMaterialization.load(args.corpus)
    embedder = create_qwen3_embedding(
        model_name=args.model_name,
        revision=args.revision,
        cache_path=args.cache,
        cache_enabled=True,
        device=args.device,
        batch_size=args.batch_size,
        fail_on_cache_miss=False,
        cache_namespace="review_corpus_v1",
    )
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_sha = corpus.manifest["corpus_manifest_sha256"]
    index_manifest = {
        "source_corpus_manifest_sha256": manifest_sha,
        "embedding_identity": embedder.embedding_fingerprint,
        "roles": {},
    }
    for role in ROLE_SUBDIMENSIONS:
        index = ReviewMemoryIndex.build(
            role=role,
            fragments=corpus.fragments,
            embed=embedder.embed,
            embedding_identity=embedder.embedding_fingerprint,
            source_manifest_sha256=manifest_sha,
        )
        path = output / f"{role.lower()}.review_index.json"
        index.write(path)
        restored = ReviewMemoryIndex.load(path)
        if restored.index_identity != index.index_identity:
            raise RuntimeError("Review Memory index round-trip identity mismatch")
        index_manifest["roles"][role] = {
            "path": path.name,
            "index_identity": index.index_identity,
            "record_count": len(index.records),
            "dimension": index.dimension,
        }
    (output / "index_manifest.json").write_text(
        json.dumps(index_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(index_manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
