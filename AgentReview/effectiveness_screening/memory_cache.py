"""Freeze program snapshots and compile read-only KnowledgeItem caches."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agentreview.backends.openai import OpenAIChat
from agentreview.memory_library import infer_reviewer_dimension
from agentreview.reviewer_identity import META_REVIEWER_NAME, REVIEWER_SPECS
from agentreview.reviewer_runtime.episodes import build_reviewer_memory_episode_text
from agentreview.reviewer_runtime.program_loader import (
    record_manifest_path,
    resolve_manifest_path,
    verify_promoted_program,
)
from memory_evolution.sandbox import SandboxSession
from memory_evolution.task_agent import build_task_agent
from memory_evolution.tasks import ALL_TASKS, META_TASK
from memory_evolution.toolkit import MemoryToolkit

from .activation import ActivationPending, require_activation
from .arms import (
    ARM_SCHEMA_VERSION,
    ArmIdentity,
    arm_identity,
    program_arm_identities,
)
from .prepare import DESIGN_VERSION, iter_jsonl, load_config, sha256_file, write_jsonl


def _deployment_embedding_identity(config: Mapping[str, Any]) -> dict[str, str]:
    recorded = dict(config.get("memory_embedding") or {})
    identity = {
        "mode": str(recorded.get("mode") or ""),
        "adapter": str(recorded.get("adapter") or ""),
        "model_name": str(recorded.get("model_name") or ""),
        "revision": str(recorded.get("revision") or ""),
        "fingerprint": str(recorded.get("fingerprint") or ""),
    }
    frozen_ready = identity["mode"] == "frozen" and all(
        identity[name] for name in ("adapter", "model_name", "revision", "fingerprint")
    )
    if frozen_ready:
        return identity
    raise ValueError(
        "reportable screening requires one complete frozen memory_embedding identity"
    )


def _program_source(
    config: Mapping[str, Any], identity: ArmIdentity, task_id: str
) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    if identity.arm_kind == "seed":
        source = project_root / "memory_evolution" / "seeds" / f"{identity.lineage}.py"
        if source.is_file():
            return source
    if identity.arm_kind == "evolved":
        evolved_root = Path(config["evolved_program_root"]).expanduser().resolve()
        manifest_path = evolved_root / (
            "meta_manifest.json" if task_id == META_TASK else "suite_manifest.json"
        )
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Authoritative promotion manifest missing: {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = (
            (manifest.get("promoted_by_lineage") or {})
            .get(task_id, {})
            .get(identity.lineage, {})
        )
        if entry.get("program"):
            source = resolve_manifest_path(manifest_path, str(entry["program"]))
            if source.is_file() and sha256_file(source) == str(
                entry.get("program_sha256") or ""
            ):
                recorded_source_run = str(entry.get("source_run") or "").strip()
                if recorded_source_run:
                    source_run = resolve_manifest_path(
                        manifest_path, recorded_source_run
                    )
                else:
                    raise ValueError(
                        f"Promoted source_run is required: {manifest_path}: "
                        f"{task_id}/{identity.lineage}"
                    )
                source_config = source_run / "config.json"
                if source_config.is_file():
                    recorded_embedding = json.loads(
                        source_config.read_text(encoding="utf-8")
                    )
                else:
                    raise FileNotFoundError(
                        f"Promoted source config is required: {source_config}"
                    )
                expected_embedding = dict(config.get("memory_embedding") or {})
                expected = {
                    "embedding_model_name": str(
                        expected_embedding.get("model_name") or ""
                    ),
                    "embedding_revision": str(expected_embedding.get("revision") or ""),
                    "embedding_fingerprint": str(
                        expected_embedding.get("fingerprint") or ""
                    ),
                }
                actual = {
                    name: str(recorded_embedding.get(name) or "") for name in expected
                }
                if actual == expected:
                    return source
                raise RuntimeError(
                    f"Promoted embedding identity mismatch: {manifest_path}: "
                    f"{task_id}/{identity.lineage}; expected={expected}, actual={actual}"
                )
            raise RuntimeError(
                f"Promoted Program identity mismatch: {manifest_path}: "
                f"{task_id}/{identity.lineage}"
            )
    raise FileNotFoundError(
        f"Program source missing for arm_id={identity.arm_id}, task_id={task_id}"
    )


def freeze_programs(config: Mapping[str, Any]) -> dict[str, Any]:
    program_assets = {}
    if "evolved" in set(config.get("experiment", {}).get("arms") or []):
        evolved_root = Path(config["evolved_program_root"]).expanduser().resolve()
        program_assets = {
            "specialist_promotion_manifest": evolved_root / "suite_manifest.json",
            "meta_promotion_manifest": evolved_root / "meta_manifest.json",
        }
    require_activation(config, "program_freeze", program_assets)
    embedding = _deployment_embedding_identity(config)
    output_root = Path(config["output_root"]).expanduser().resolve()
    program_root = output_root / "programs"
    result: dict[str, Any] = {
        "design_version": DESIGN_VERSION,
        "source_run": str(Path(config["evolved_program_root"]).expanduser().resolve()),
        "arm_schema_version": ARM_SCHEMA_VERSION,
        "arm_identities": {},
        "arms": {},
        "embedding_identity": embedding,
        "deployment_by_arm": {},
    }
    identities = program_arm_identities(config)
    if len(identities) == 0:
        raise ValueError("No lineages configured for program freezing")
    for identity in identities:
        task_entries = {}
        arm_root = program_root / identity.arm_id
        for task_id in ALL_TASKS:
            source = _program_source(config, identity, task_id)
            if not source.is_file():
                raise FileNotFoundError(f"Program missing: {source}")
            destination = arm_root / task_id / "best_program.py"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            task_entries[task_id] = {
                "source": str(source),
                "program": str(destination),
                "program_sha256": sha256_file(destination),
            }
        deployment_source = arm_root / "deployment_provenance"
        deployment_source.mkdir(parents=True, exist_ok=True)
        deployment_config = {
            "embedding_model_name": str(embedding.get("model_name") or ""),
            "embedding_revision": str(embedding.get("revision") or ""),
            "embedding_fingerprint": str(embedding.get("fingerprint") or ""),
        }
        (deployment_source / "config.json").write_text(
            json.dumps(deployment_config, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        local_entries = {
            task_id: {
                "program": record_manifest_path(entry["program"], arm_root),
                "program_sha256": entry["program_sha256"],
                "source_run": record_manifest_path(deployment_source, arm_root),
                "source_program": entry["source"],
            }
            for task_id, entry in task_entries.items()
        }
        suite_manifest = {
            "design_version": DESIGN_VERSION,
            **identity.to_record(),
            "promoted": {
                task_id: local_entries[task_id]
                for task_id in ALL_TASKS
                if task_id != META_TASK
            },
        }
        meta_manifest = {
            "design_version": DESIGN_VERSION,
            **identity.to_record(),
            "promoted": local_entries.get(META_TASK, {}),
        }
        suite_path = arm_root / "suite_manifest.json"
        meta_path = arm_root / "meta_manifest.json"
        suite_path.write_text(
            json.dumps(suite_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        meta_path.write_text(
            json.dumps(meta_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        for task_id, entry in task_entries.items():
            verify_promoted_program(
                str(arm_root),
                task_id,
                Path(entry["program"]),
                deployment_config["embedding_model_name"],
                deployment_config["embedding_revision"],
                deployment_config["embedding_fingerprint"],
            )
        result["arm_identities"][identity.arm_id] = identity.to_record()
        result["arms"][identity.arm_id] = task_entries
        result["deployment_by_arm"][identity.arm_id] = {
            "suite_manifest": str(suite_path),
            "suite_manifest_sha256": sha256_file(suite_path),
            "meta_manifest": str(meta_path),
            "meta_manifest_sha256": sha256_file(meta_path),
            "embedding_config": str(deployment_source / "config.json"),
            "embedding_config_sha256": sha256_file(deployment_source / "config.json"),
        }
    result["lineages"] = list(config.get("experiment", {}).get("lineages") or [])
    result["legacy_iter5_used"] = False
    manifest_path = program_root / "program_manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def freeze_memory_library(
    config: Mapping[str, Any], source_output_dir: Path
) -> dict[str, Any]:
    """Legacy Frozen Library Route; never use for reportable experiments."""
    require_activation(config, "generation")
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


def _load_partial(
    path: Path,
    identity: ArmIdentity,
    task_id: str,
    program_sha256: str,
):
    if not path.is_file():
        return {}
    values = {}
    for value in iter_jsonl(path):
        expected_identity = identity.to_record()
        actual_identity = {name: value.get(name) for name in expected_identity}
        if actual_identity != expected_identity:
            raise RuntimeError(f"Arm identity mismatch in partial cache: {path}")
        if (
            value.get("task_id") != task_id
            or value.get("program_sha256") != program_sha256
        ):
            raise RuntimeError(f"Stale or mixed partial cache: {path}")
        values[str(value["episode_id"])] = value
    return values


def compile_cache(config: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    """Legacy shared-library compiler retained only for artifact analysis."""
    require_activation(config, "generation")
    identity = arm_identity(config, arm_id)
    if identity.arm_kind == "no_memory":
        raise ValueError("Knowledge cache compilation requires a program arm ID")
    output_root = Path(config["output_root"]).expanduser().resolve()
    library_path = output_root / "frozen_memory_library.jsonl"
    if not library_path.is_file():
        raise FileNotFoundError("Freeze the shared memory library before compilation")
    records = list(iter_jsonl(library_path))
    episodes_by_task = _episodes_by_task(records)
    program_root = output_root / "programs" / identity.arm_id
    cache_root = output_root / "knowledge_cache" / identity.arm_id
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
        cached = _load_partial(cache_path, identity, task_id, program_sha)
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
                    **identity.to_record(),
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
                    f"[{identity.arm_id}/{task_id} "
                    f"{index}/{len(episodes_by_task[task_id])}] "
                    f"compiled {episode_id}",
                    flush=True,
                )
        finally:
            session.close()
        if len(cached) != len(episodes_by_task[task_id]):
            raise RuntimeError(f"Incomplete {identity.arm_id}/{task_id} cache")
        task_entries[task_id] = {
            "program": str(program_path),
            "program_sha256": program_sha,
            "cache_file": cache_path.name,
            "cache_sha256": sha256_file(cache_path),
            "item_count": len(cached),
        }
    manifest = {
        "design_version": DESIGN_VERSION,
        **identity.to_record(),
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
    parser.add_argument(
        "--arm",
        help=(
            "Canonical lineage-qualified arm ID, for example "
            "seed_vector_search or evolved_from_vector_search"
        ),
    )
    parser.add_argument("--allow-legacy-nonreportable", action="store_true")
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    try:
        if args.command == "freeze-programs":
            result = freeze_programs(config)
        elif args.command == "freeze-memory-library":
            if args.allow_legacy_nonreportable:
                if args.source_output_dir:
                    result = freeze_memory_library(
                        config, Path(args.source_output_dir).expanduser().resolve()
                    )
                else:
                    parser.error("freeze-memory-library requires --source-output-dir")
            else:
                parser.error(
                    "Frozen Library Route requires --allow-legacy-nonreportable for historical analysis"
                )
        else:
            if args.allow_legacy_nonreportable:
                if args.arm:
                    result = compile_cache(config, args.arm)
                else:
                    parser.error("compile-cache requires --arm")
            else:
                parser.error(
                    "Shared KnowledgeItem cache analysis requires --allow-legacy-nonreportable"
                )
    except ActivationPending as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
