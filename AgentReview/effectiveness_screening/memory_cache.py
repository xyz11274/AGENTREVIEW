"""Freeze program snapshots and compile read-only KnowledgeItem caches."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from agentreview.backends.openai import OpenAIChat
from agentreview.memory_library import infer_reviewer_dimension
from agentreview.single_paper_review_roles import META_REVIEWER_NAME, REVIEWER_SPECS
from memory_evolution.agentreview_data import build_reviewer_memory_episode_text
from memory_evolution.sandbox import SandboxSession
from memory_evolution.task_agent import build_task_agent
from memory_evolution.tasks import ALL_TASKS
from memory_evolution.toolkit import MemoryToolkit

from .prepare import DESIGN_VERSION, iter_jsonl, load_config, sha256_file, write_jsonl


def _program_source(config: Mapping[str, Any], arm: str, task_id: str) -> Path:
    evolved_root = Path(config["evolved_program_root"]).expanduser().resolve()
    if arm == "evolved":
        return evolved_root / task_id / "best_program.py"
    if arm == "seed_experience":
        return (
            evolved_root
            / task_id
            / "seed_0"
            / "pool"
            / "program_002_seed_experience.py"
        )
    raise ValueError(f"Unsupported program arm: {arm}")


def freeze_programs(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    program_root = output_root / "programs"
    result: Dict[str, Any] = {
        "design_version": DESIGN_VERSION,
        "source_run": str(
            Path(config["evolved_program_root"]).expanduser().resolve()
        ),
        "arms": {},
    }
    for arm in ("seed_experience", "evolved"):
        task_entries = {}
        for task_id in ALL_TASKS:
            source = _program_source(config, arm, task_id)
            if not source.is_file():
                raise FileNotFoundError(f"Program missing: {source}")
            destination = program_root / arm / task_id / "best_program.py"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            task_entries[task_id] = {
                "source": str(source),
                "program": str(destination),
                "program_sha256": sha256_file(destination),
            }
        result["arms"][arm] = task_entries
    seed_pol = result["arms"]["seed_experience"]["POL"]["program_sha256"]
    evolved_pol = result["arms"]["evolved"]["POL"]["program_sha256"]
    if seed_pol != evolved_pol:
        raise RuntimeError("Frozen design requires POL evolved=seed")
    result["POL_evolved_equals_seed"] = True
    manifest_path = program_root / "program_manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def freeze_memory_library(
    config: Mapping[str, Any], source_output_dir: Path
) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    source = source_output_dir / "single_paper_review" / "memory_library.jsonl"
    if not source.is_file():
        raise FileNotFoundError(f"Generated memory library missing: {source}")
    expected = {
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "memory_corpus.jsonl")
    }
    records = list(iter_jsonl(source))
    actual = [str(value.get("sample_id") or "") for value in records]
    duplicates = sorted({value for value in actual if actual.count(value) > 1})
    if duplicates:
        raise RuntimeError(f"Duplicate memory records: {duplicates}")
    if set(actual) != expected:
        raise RuntimeError(
            "Generated memory library IDs differ from frozen corpus: "
            f"missing={sorted(expected - set(actual))}, "
            f"extra={sorted(set(actual) - expected)}"
        )
    expected_reviewers = {
        *(spec["name"] for spec in REVIEWER_SPECS),
        META_REVIEWER_NAME,
    }
    incomplete = {
        str(record.get("sample_id")): sorted(
            expected_reviewers - set((record.get("reviews") or {}).keys())
        )
        for record in records
        if set((record.get("reviews") or {}).keys()) != expected_reviewers
    }
    if incomplete:
        raise RuntimeError(f"Incomplete historical review records: {incomplete}")
    destination = output_root / "frozen_memory_library.jsonl"
    shutil.copyfile(source, destination)
    manifest = {
        "design_version": DESIGN_VERSION,
        "generation_arm": "no_memory",
        "source": str(source),
        "frozen_library": str(destination),
        "frozen_library_sha256": sha256_file(destination),
        "paper_count": len(records),
        "reviewers_per_paper": len(expected_reviewers),
        "target_writeback_allowed": False,
    }
    path = output_root / "frozen_memory_library.manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def _episodes_by_task(records: Iterable[Mapping[str, Any]]):
    output = {task_id: [] for task_id in ALL_TASKS}
    for record in records:
        sample_id = str(record.get("sample_id") or "")
        for reviewer_name, review in sorted((record.get("reviews") or {}).items()):
            task_id = infer_reviewer_dimension(reviewer_name) or "META"
            if task_id not in output or not review:
                continue
            output[task_id].append(
                {
                    "episode_id": f"{sample_id}::{reviewer_name}",
                    "raw_text": build_reviewer_memory_episode_text(
                        dict(record), reviewer_name, str(review)
                    ),
                }
            )
    return output


def _load_partial(path: Path, task_id: str, program_sha256: str):
    if not path.is_file():
        return {}
    values = {}
    for value in iter_jsonl(path):
        if (
            value.get("task_id") != task_id
            or value.get("program_sha256") != program_sha256
        ):
            raise RuntimeError(f"Stale or mixed partial cache: {path}")
        values[str(value["episode_id"])] = value
    return values


def compile_cache(config: Mapping[str, Any], arm: str) -> Dict[str, Any]:
    if arm not in {"seed_experience", "evolved"}:
        raise ValueError("Cache arm must be seed_experience or evolved")
    output_root = Path(config["output_root"]).expanduser().resolve()
    library_path = output_root / "frozen_memory_library.jsonl"
    if not library_path.is_file():
        raise FileNotFoundError("Freeze the shared memory library before compilation")
    records = list(iter_jsonl(library_path))
    episodes_by_task = _episodes_by_task(records)
    program_root = output_root / "programs" / arm
    cache_root = output_root / "knowledge_cache" / arm
    cache_root.mkdir(parents=True, exist_ok=True)
    generation = config["generation"]
    backend = OpenAIChat(
        model=str(generation["model"]),
        temperature=float(generation["temperature"]),
        max_tokens=int(generation["review_max_tokens"]),
        retry_max_tokens=int(generation["review_retry_max_tokens"]),
        reasoning_effort=str(generation["reviewer_reasoning_effort"]),
        request_timeout_seconds=600,
        connect_timeout_seconds=30,
    )
    task_entries = {}
    for task_id in ALL_TASKS:
        program_path = program_root / task_id / "best_program.py"
        program_sha = sha256_file(program_path)
        cache_path = cache_root / f"{task_id}.jsonl"
        cached = _load_partial(cache_path, task_id, program_sha)
        toolkit = MemoryToolkit(
            llm_client=backend,
            llm_model=str(generation["model"]),
            embedding_client=None,
            embedding_model="deterministic_hash_256",
        )
        session = SandboxSession(str(program_path), toolkit)
        program = session.program_interface()
        task_agent = build_task_agent(toolkit, task_id)
        try:
            for index, episode in enumerate(episodes_by_task[task_id], start=1):
                episode_id = episode["episode_id"]
                if episode_id in cached:
                    continue
                item = task_agent.extract_knowledge_item(
                    program, str(episode["raw_text"])
                )
                value = {
                    "design_version": DESIGN_VERSION,
                    "arm": arm,
                    "task_id": task_id,
                    "episode_id": episode_id,
                    "program_sha256": program_sha,
                    "raw_text": episode["raw_text"],
                    "raw_text_sha256": hashlib.sha256(
                        str(episode["raw_text"]).encode("utf-8")
                    ).hexdigest(),
                    "item": asdict(item),
                }
                cached[episode_id] = value
                write_jsonl(cache_path, [cached[key] for key in sorted(cached)])
                print(
                    f"[{arm}/{task_id} {index}/{len(episodes_by_task[task_id])}] "
                    f"compiled {episode_id}",
                    flush=True,
                )
        finally:
            session.close()
        if len(cached) != len(episodes_by_task[task_id]):
            raise RuntimeError(f"Incomplete {arm}/{task_id} cache")
        task_entries[task_id] = {
            "program": str(program_path),
            "program_sha256": program_sha,
            "cache_file": cache_path.name,
            "cache_sha256": sha256_file(cache_path),
            "item_count": len(cached),
        }
    manifest = {
        "design_version": DESIGN_VERSION,
        "arm": arm,
        "library": str(library_path),
        "library_sha256": sha256_file(library_path),
        "compiler": {
            "model": generation["model"],
            "temperature": generation["temperature"],
            "reasoning_effort": generation["reviewer_reasoning_effort"],
            "embedding_mode": "deterministic_hash_256",
        },
        "tasks": task_entries,
        "target_runtime": "load cached KnowledgeItems; no history extraction or writeback",
    }
    manifest_path = cache_root / "cache_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("freeze-programs", "freeze-memory-library", "compile-cache"),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-output-dir")
    parser.add_argument("--arm", choices=("seed_experience", "evolved"))
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    if args.command == "freeze-programs":
        result = freeze_programs(config)
    elif args.command == "freeze-memory-library":
        if not args.source_output_dir:
            parser.error("freeze-memory-library requires --source-output-dir")
        result = freeze_memory_library(
            config, Path(args.source_output_dir).expanduser().resolve()
        )
    else:
        if not args.arm:
            parser.error("compile-cache requires --arm")
        result = compile_cache(config, args.arm)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

