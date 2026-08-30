#!/usr/bin/env python3
"""Build the 125×5 no-memory Review Corpus for a new formal run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentreview.corpus_bootstrap import (
    canonical_context,
    materialize_corpus_bootstrap,
    selected_paper_ids,
)
from agentreview.corpus_transport import CorpusOpenAICompatibleAdapter
from agentreview.corpus_checkpoint import run_checkpointed_corpus_build
from agentreview.review_corpus_builder import CorpusPaper, NoMemoryCorpusBuilder


def iter_jsonl(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc
            yield value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-root", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--bootstrap-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--request-timeout", type=float, required=True)
    args = parser.parse_args()

    bootstrap_dir = Path(args.bootstrap_dir).expanduser().resolve()
    profile = json.loads(
        (bootstrap_dir / "corpus_build_runtime_profile.json").read_text(
            encoding="utf-8"
        )
    )
    request_manifest = json.loads(
        (bootstrap_dir / "corpus_request_manifest.json").read_text(encoding="utf-8")
    )
    if profile.get("profile_name") != "CORPUS_BUILD_RUNTIME_PROFILE":
        raise ValueError("Corpus bootstrap runtime profile is invalid")
    if profile.get("max_num_seqs") != 1 or profile.get("tensor_parallel_size") != 4:
        raise ValueError("Corpus bootstrap requires frozen TP4/max-num-seqs=1")
    generation = profile["generation_semantics"]
    model_revision = profile["model_revision"]
    served_model_name = generation["served_model_name"]
    output_token_cap = generation["requested_output_token_cap"]
    model_context_limit = profile["model_context_limit"]

    from transformers import AutoTokenizer

    reviewer_tokenizer = AutoTokenizer.from_pretrained(
        args.model_path, local_files_only=True, trust_remote_code=False
    )
    recomputed = materialize_corpus_bootstrap(
        episodes_root=args.episodes_root,
        selection_path=args.selection,
        model_path=args.model_path,
        tokenizer=reviewer_tokenizer,
        output_token_cap=output_token_cap,
        model_revision=model_revision,
        served_model_name=served_model_name,
        model_context_limit=model_context_limit,
        gpu_memory_utilization=float(profile["gpu_memory_utilization"]),
    )
    if recomputed.runtime_profile != profile:
        raise RuntimeError("Corpus bootstrap runtime profile no longer reproduces")
    if recomputed.request_manifest != request_manifest:
        raise RuntimeError("Corpus request materialization no longer reproduces")

    ids = selected_paper_ids(args.selection)
    qual_path = Path(args.episodes_root) / "QUAL" / "episodes.jsonl"
    by_id = {
        str(value.get("id") or "").split("::", 1)[0]: value
        for value in iter_jsonl(qual_path)
    }
    missing = [paper_id for paper_id in ids if paper_id not in by_id]
    if missing:
        raise ValueError(f"Frozen A selection missing from episodes: {missing}")
    papers = [
        CorpusPaper.from_context(
            paper_id=paper_id,
            canonical_paper_context=canonical_context(by_id[paper_id]),
            structural_context="source anchors embedded in canonical paper context",
        )
        for paper_id in ids
    ]
    generation_sha = request_manifest["generation_identity_sha256"]
    expected_tokens = {
        item.messages_sha256: item.prompt_token_count for item in recomputed.requests
    }
    backend = CorpusOpenAICompatibleAdapter(
        endpoint=args.endpoint,
        request_config=generation["request_json"],
        expected_prompt_tokens_by_messages_sha256=expected_tokens,
        timeout_seconds=args.request_timeout,
        transport_attempts=2,
    )
    builder = NoMemoryCorpusBuilder(
        complete=backend,
        reviewer_identity_sha256=generation_sha,
        generation_identity_sha256=generation_sha,
        max_workers=1,
    )
    output = Path(args.output).expanduser().resolve()
    result = run_checkpointed_corpus_build(
        builder=builder,
        papers=papers,
        checkpoint_directory=output / "checkpoint",
        expected_paper_count=125,
        progress=lambda completed, total, paper_id, disposition: print(
            json.dumps(
                {
                    "completed": completed,
                    "total": total,
                    "paper_id": paper_id,
                    "disposition": disposition,
                },
                ensure_ascii=False,
            ),
            flush=True,
        ),
    )
    result.write_audit(output)
    print(json.dumps({
        "canonical_reviews": len(result.records),
        "raw_attempts": len(result.raw_attempts),
        "build_manifest_sha256": result.manifest["build_manifest_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
