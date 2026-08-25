"""Independent command line entrypoint for AgentReview Reviewer Core."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from agentreview.backends.openai import OpenAIChat
from agentreview.reviewer_runtime import (
    CandidateReviewWriter,
    Episode,
    ReviewRequest,
    ReviewerEngine,
)
from agentreview.reviewer_runtime.requests import (
    build_review_question,
    fallback_source_anchors,
)
from agentreview.reviewer_runtime.tasks import ALL_TASKS, META_TASK
from agentreview.reviewer_runtime.toolkit import MemoryToolkit
from agentreview.source_map import build_source_anchors, load_content_list


TEMPORAL_REVIEW_ZONES = (
    "effectiveness_holdout",
    "held_out_benchmark_test",
)


def _iter_jsonl(path: str | Path) -> Iterable[Dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {source}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {source}:{line_number}")
            yield value


def _load_episodes(path: str) -> List[Episode]:
    return [Episode.from_value(value) for value in _iter_jsonl(path)] if path else []


def _embedding_client(args):
    if not args.embedding_adapter:
        return None
    module_name, separator, factory_name = args.embedding_adapter.partition(":")
    if not separator:
        raise ValueError("--embedding-adapter must use module:factory syntax")
    factory = getattr(importlib.import_module(module_name), factory_name)
    kwargs = {
        "model_name": args.embedding_model,
        "revision": args.embedding_revision,
        "cache_path": args.embedding_cache,
        "cache_enabled": True,
        "device": args.embedding_device,
        "batch_size": args.embedding_batch_size,
        "fail_on_cache_miss": args.embedding_cache_read_only,
        "cache_namespace": "candidate_runtime_v1",
    }
    try:
        return factory(**kwargs)
    except TypeError:
        return factory()


def _backend(args):
    return OpenAIChat(
        model=args.model,
        max_tokens=args.max_tokens,
        retry_max_tokens=args.retry_max_tokens,
        temperature=args.temperature,
        openai_base_url=args.openai_base_url,
        openai_client_type=args.openai_client_type,
        reasoning_effort=(None if args.reasoning_effort == "none" else args.reasoning_effort),
        reasoning_effort_supported=args.reasoning_effort_supported,
        request_timeout_seconds=args.request_timeout_seconds,
    )


def _load_temporal_entries(manifest_path: str, zone: str) -> List[Dict[str, Any]]:
    if zone not in TEMPORAL_REVIEW_ZONES:
        raise ValueError(
            "Reviewer temporal batch accepts only effectiveness_holdout or "
            "held_out_benchmark_test"
        )
    manifest = Path(manifest_path).expanduser().resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    zone_info = dict((payload.get("zones") or {}).get(zone) or {})
    relative = str(zone_info.get("path") or f"{zone}.jsonl")
    path = (manifest.parent / relative).resolve()
    values = list(_iter_jsonl(path))
    for value in values:
        if str(value.get("zone") or "") != zone:
            raise ValueError(f"Temporal zone mismatch for {value.get('paper_id')}")
    return values


def _benchmark_records(coco_root: str, entries: Iterable[Dict[str, Any]]):
    root = Path(coco_root).expanduser().resolve()
    files: Dict[str, Path] = {}
    for entry in entries:
        basename = str(entry.get("source_asset") or "").rsplit("/", 1)[-1]
        if not basename:
            raise ValueError(f"Temporal entry lacks source_asset: {entry}")
        files[basename] = root / "benchmark" / basename
    records = {}
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(f"CoCo benchmark source is missing: {path}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            paper_id = str(value.get("id") or "")
            if paper_id:
                records[(path.name, paper_id)] = (value, path, line_number, line)
    return records


def _questions_by_paper(path: str) -> Dict[str, str]:
    if not path:
        return {}
    output = {}
    for value in _iter_jsonl(path):
        paper_id = str(value.get("paper_id") or value.get("id") or "").split("::", 1)[0]
        question = str(value.get("question") or "")
        if not paper_id or not question:
            raise ValueError("Question JSONL requires paper_id/id and question")
        if paper_id in output:
            raise ValueError(f"Duplicate question for paper_id={paper_id}")
        output[paper_id] = question
    return output


def _temporal_requests(args) -> List[ReviewRequest]:
    entries = _load_temporal_entries(args.temporal_manifest, args.zone)
    if args.limit is not None:
        entries = entries[: args.limit]
    records = _benchmark_records(args.coco_root, entries)
    supplied_questions = _questions_by_paper(args.questions_jsonl)
    requests = []
    for entry in entries:
        paper_id = str(entry.get("paper_id") or "")
        source_name = str(entry.get("source_asset") or "").rsplit("/", 1)[-1]
        record_key = (source_name, paper_id)
        if record_key not in records:
            raise ValueError(f"CoCo benchmark lacks temporal paper {paper_id}")
        record, source_path, source_line, raw_line = records[record_key]
        if int(entry.get("source_line") or source_line) != source_line:
            raise ValueError(f"CoCo source line drift for {paper_id}")
        expected_sha = str(entry.get("source_record_sha256") or "")
        if expected_sha and hashlib.sha256(raw_line.encode("utf-8")).hexdigest() != expected_sha:
            raise ValueError(f"CoCo source record checksum mismatch for {paper_id}")
        question = supplied_questions.get(paper_id, "")
        if not question:
            if args.task_id == META_TASK:
                raise ValueError(
                    "Temporal META generation requires --questions-jsonl with "
                    "the already-frozen specialist-review synthesis question"
                )
            md_value = str(record.get("MD_path") or "")
            md_path = Path(md_value)
            if not md_path.is_absolute():
                md_path = Path(args.coco_root).expanduser().resolve() / md_path
            if not md_path.is_file():
                raise FileNotFoundError(f"Paper Markdown is missing: {md_path}")
            paper_text = md_path.read_text(encoding="utf-8", errors="ignore")
            anchors = build_source_anchors(load_content_list(str(md_path)))
            if not anchors:
                anchors = fallback_source_anchors(paper_text)
            content = dict(record.get("content") or {})
            venue = str(record.get("conference") or entry.get("venue") or "")
            question = build_review_question(
                task_id=args.task_id,
                paper_id=paper_id,
                title=str(content.get("title") or entry.get("title") or paper_id),
                paper_text=paper_text,
                source_anchors=anchors,
                venue=venue,
                year=entry.get("year") or "",
                max_paper_chars=args.max_paper_chars,
            )
        requests.append(
            ReviewRequest(
                paper_id=paper_id,
                task_id=args.task_id,
                question=question,
                metadata={
                    "temporal_zone": args.zone,
                    "source_asset": str(source_path),
                },
                model=args.model,
                revision=args.revision,
                run_identity=args.run_identity,
            )
        )
    return requests


def _single_request(args) -> ReviewRequest:
    question = args.question
    if args.question_file:
        question = Path(args.question_file).expanduser().read_text(encoding="utf-8")
    if not args.paper_id or not question:
        raise ValueError("Single review requires --paper-id and --question/--question-file")
    return ReviewRequest(
        paper_id=args.paper_id,
        task_id=args.task_id,
        question=question,
        model=args.model,
        revision=args.revision,
        run_identity=args.run_identity,
    )


def run_review(args) -> int:
    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"Output already exists; pass --overwrite: {output}")
    if output.exists():
        output.unlink()
    episodes = _load_episodes(args.episodes_jsonl)
    if args.temporal_manifest and not episodes and not args.allow_fresh_memory:
        raise ValueError(
            "Temporal C/D generation requires explicit frozen --episodes-jsonl; "
            "use --allow-fresh-memory only for a deliberate debug run"
        )
    requests = (
        _temporal_requests(args) if args.temporal_manifest else [_single_request(args)]
    )
    toolkit = MemoryToolkit(
        llm_client=_backend(args),
        llm_model=args.model,
        embedding_client=_embedding_client(args),
        embedding_model=args.embedding_model,
    )
    engine = ReviewerEngine(
        toolkit,
        require_bubblewrap=not args.allow_unsandboxed,
        timeout_seconds=args.runtime_timeout_seconds,
    )
    writer = CandidateReviewWriter(output)
    with engine.open_session(args.task_id, args.memory_program) as session:
        for episode in episodes:
            session.ingest_episode(episode)
        frozen_history = session.snapshot()
        for request in requests:
            session.restore(frozen_history)
            writer.append(session.review(request))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentReview Reviewer Core CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    review = subparsers.add_parser("review")
    review.add_argument("--task-id", choices=ALL_TASKS, required=True)
    review.add_argument("--memory-program", required=True)
    review.add_argument("--output", required=True)
    review.add_argument("--run-identity", required=True)
    review.add_argument("--model", default="qwen3.8-27b-fp8")
    review.add_argument("--revision", default="unspecified")
    review.add_argument("--paper-id", default="")
    review.add_argument("--question", default="")
    review.add_argument("--question-file", default="")
    review.add_argument("--temporal-manifest", default="")
    review.add_argument("--zone", choices=TEMPORAL_REVIEW_ZONES)
    review.add_argument("--coco-root", default="")
    review.add_argument("--questions-jsonl", default="")
    review.add_argument("--episodes-jsonl", default="")
    review.add_argument("--allow-fresh-memory", action="store_true")
    review.add_argument("--limit", type=int)
    review.add_argument("--max-paper-chars", type=int, default=60000)
    review.add_argument("--overwrite", action="store_true")
    review.add_argument("--allow-unsandboxed", action="store_true")
    review.add_argument("--runtime-timeout-seconds", type=float, default=600.0)
    review.add_argument("--temperature", type=float, default=0.3)
    review.add_argument("--max-tokens", type=int, default=8192)
    review.add_argument("--retry-max-tokens", type=int, default=16384)
    review.add_argument("--reasoning-effort", default="low")
    review.add_argument("--reasoning-effort-supported", action="store_true")
    review.add_argument("--request-timeout-seconds", type=float, default=600.0)
    review.add_argument("--openai-base-url", default=None)
    review.add_argument("--openai-client-type", default="openai")
    review.add_argument("--embedding-adapter", default="")
    review.add_argument("--embedding-model", default="")
    review.add_argument("--embedding-revision", default="")
    review.add_argument("--embedding-cache", default="")
    review.add_argument("--embedding-cache-read-only", action="store_true")
    review.add_argument("--embedding-device", default="")
    review.add_argument("--embedding-batch-size", type=int, default=1)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "review":
        if bool(args.temporal_manifest) != bool(args.zone and args.coco_root):
            raise ValueError(
                "Temporal batch requires --temporal-manifest, --zone, and --coco-root"
            )
        return run_review(args)
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
