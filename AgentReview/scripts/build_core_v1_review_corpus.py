#!/usr/bin/env python3
"""Build the 125×5 no-memory Review Corpus for a new formal run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from agentreview.backends.openai import OpenAIChat
from agentreview.review_corpus import FrozenTokenizer, canonical_json_bytes
from agentreview.review_corpus_builder import CorpusPaper, NoMemoryCorpusBuilder
from memory_evolution.core_scheduler import (
    GlobalQwenService,
    ScheduledToolkitProxy,
)


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


def selected_ids(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = value.get("selected_episode_ids") or value.get("episode_ids") or value.get("paper_ids")
    if not isinstance(ids, list):
        raise ValueError("Selection must contain selected_episode_ids/episode_ids/paper_ids")
    output = []
    for value in ids:
        paper_id = str(value).split("::", 1)[0]
        if paper_id not in output:
            output.append(paper_id)
    return output


def canonical_context(episode):
    text = str(episode.get("text") or "")
    if "SELF-GENERATED REVIEW EXPERIENCE" in text:
        raise ValueError("A Corpus input contains old Memory/self-generated experience")
    marker = "Context protocol:"
    start = text.find(marker)
    if start < 0:
        raise ValueError("A episode lacks canonical paper context")
    value = text[start:]
    suffix = "\n\nReturn the complete "
    if suffix in value:
        value = value.split(suffix, 1)[0]
    return value


def tokenizer_identity(path):
    root = Path(path).expanduser().resolve()
    files = []
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"):
        source = root / name
        if source.is_file():
            files.append((name, hashlib.sha256(source.read_bytes()).hexdigest()))
    if not files:
        raise FileNotFoundError("Embedding tokenizer manifest files are missing")
    return "qwen3-embedding-tokenizer-" + hashlib.sha256(canonical_json_bytes(files)).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-root", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-artifact-sha256", required=True)
    parser.add_argument("--embedding-model-path", required=True)
    parser.add_argument("--max-workers", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--request-timeout", type=float, required=True)
    parser.add_argument("--max-projection-tokens", type=int, default=2048)
    args = parser.parse_args()

    ids = selected_ids(args.selection)
    if len(ids) != 125:
        raise ValueError(f"Frozen A selection must contain 125 papers, got {len(ids)}")
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
    generation = {
        "model": args.model,
        "revision": args.model_revision,
        "artifact_sha256": args.model_artifact_sha256,
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "retry_max_tokens": args.max_tokens,
    }
    generation_sha = hashlib.sha256(canonical_json_bytes(generation)).hexdigest()
    backend = OpenAIChat(
        model=args.model,
        max_tokens=args.max_tokens,
        retry_max_tokens=args.max_tokens,
        temperature=0,
        openai_base_url=args.endpoint,
        openai_client_type="openai",
        request_timeout_seconds=args.request_timeout,
    )

    with GlobalQwenService(args.max_workers) as qwen_service:
        proxies = {
            role: ScheduledToolkitProxy(backend, qwen_service, role)
            for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
        }
        completions = {
            role: (
                lambda messages, proxy=proxy: proxy.llm_completion(
                    list(messages), agent_name="No-Memory Corpus Reviewer"
                )
            )
            for role, proxy in proxies.items()
        }
        builder = NoMemoryCorpusBuilder(
            complete_by_role=completions,
            reviewer_identity_sha256=generation_sha,
            generation_identity_sha256=generation_sha,
            max_workers=args.max_workers,
        )
        result = builder.build(papers, expected_paper_count=125)
    output = Path(args.output).expanduser().resolve()
    result.write_audit(output)

    from transformers import AutoTokenizer

    frozen = AutoTokenizer.from_pretrained(
        args.embedding_model_path, local_files_only=True, trust_remote_code=False
    )
    tokenizer = FrozenTokenizer(
        lambda text: frozen.encode(text, add_special_tokens=False),
        tokenizer_identity(args.embedding_model_path),
    )
    materialization = result.materialize(
        tokenizer=tokenizer,
        max_projection_tokens=args.max_projection_tokens,
    )
    materialization.write(output)
    print(json.dumps({
        "canonical_reviews": len(materialization.records),
        "primary_projections": len(materialization.projections),
        "fragments": len(materialization.fragments),
        "corpus_manifest_sha256": materialization.manifest["corpus_manifest_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
