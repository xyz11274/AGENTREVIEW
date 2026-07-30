"""Generate leakage-safe specialist inputs for the sixth (META) M★ task."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .integration import MemoryHarnessAdapter
from .models import Episode
from .task_agent import build_task_agent
from .tasks import SPECIALIST_TASKS, resolve_program_path


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _selected_episode_ids(program_root: Path, task_id: str) -> Optional[set]:
    manifest_path = program_root / "suite_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_run = (
        (manifest.get("promoted") or {}).get(task_id) or {}
    ).get("source_run")
    if not source_run:
        return None
    selection_path = Path(source_run) / "selection.json"
    if not selection_path.is_file():
        return None
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    return set(selection.get("episode_ids") or [])


def _fold(family: str, folds: int) -> int:
    digest = hashlib.sha256(str(family).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % folds


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_group(program_path, toolkit, task_id, episodes, queries):
    adapter = MemoryHarnessAdapter(
        str(program_path),
        toolkit,
        build_task_agent(toolkit, task_id),
    )
    try:
        adapter.ingest_episodes(episodes)
        return {
            str(query["metadata"]["paper_id"]): adapter.answer(query["question"])
            for query in queries
        }
    finally:
        adapter.close()


def build_meta_input_cache(
    data_root: str,
    program_root: str,
    output_path: str,
    toolkit_factory,
    folds: int = 5,
) -> Dict[str, Any]:
    """Use out-of-fold specialist predictions for train and frozen predictions for val."""
    data = Path(data_root).expanduser().resolve()
    programs = Path(program_root).expanduser().resolve()
    folds = max(2, int(folds))
    combined: Dict[str, Dict[str, Any]] = {}
    program_hashes = {}

    for task_id in SPECIALIST_TASKS:
        task_root = data / task_id
        episodes = [
            Episode.from_value(value)
            for value in _iter_jsonl(task_root / "episodes.jsonl")
        ]
        selected_ids = _selected_episode_ids(programs, task_id)
        if selected_ids is not None:
            episodes = [episode for episode in episodes if episode.id in selected_ids]
        queries = list(_iter_jsonl(task_root / "queries.jsonl"))
        program_path = resolve_program_path(str(programs), task_id)
        program_hashes[task_id] = _sha256(program_path)

        task_outputs: Dict[str, str] = {}
        validation_queries = [
            query
            for query in queries
            if query["metadata"].get("split") == "meta_validation"
        ]
        task_outputs.update(
            _run_group(
                program_path,
                toolkit_factory(),
                task_id,
                episodes,
                validation_queries,
            )
        )

        for fold_index in range(folds):
            fold_queries = [
                query
                for query in queries
                if query["metadata"].get("split") == "train"
                and _fold(query["metadata"].get("work_family", ""), folds)
                == fold_index
            ]
            if not fold_queries:
                continue
            fold_episodes = [
                episode
                for episode in episodes
                if _fold(episode.metadata.get("work_family", ""), folds)
                != fold_index
            ]
            task_outputs.update(
                _run_group(
                    program_path,
                    toolkit_factory(),
                    task_id,
                    fold_episodes,
                    fold_queries,
                )
            )

        for query in queries:
            paper_id = str(query["metadata"]["paper_id"])
            if paper_id not in task_outputs:
                continue
            record = combined.setdefault(
                paper_id,
                {
                    "id": paper_id,
                    "split": (
                        "validation"
                        if query["metadata"]["split"] == "meta_validation"
                        else query["metadata"]["split"]
                    ),
                    "work_family": query["metadata"]["work_family"],
                    "specialists": {},
                },
            )
            normalized_split = (
                "validation"
                if query["metadata"]["split"] == "meta_validation"
                else query["metadata"]["split"]
            )
            if record["split"] != normalized_split:
                raise ValueError(f"Inconsistent split for meta input {paper_id}")
            record["specialists"][task_id] = task_outputs[paper_id]

    complete = [
        record
        for record in combined.values()
        if set(record["specialists"]) == set(SPECIALIST_TASKS)
    ]
    complete.sort(key=lambda value: value["id"])
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for record in complete:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    result = {
        "output": str(destination),
        "output_sha256": _sha256(destination),
        "paper_count": len(complete),
        "train_count": sum(record["split"] == "train" for record in complete),
        "validation_count": sum(
            record["split"] == "validation" for record in complete
        ),
        "folds": folds,
        "program_root": str(programs),
        "specialist_program_sha256": program_hashes,
        "suite_manifest_sha256": (
            _sha256(programs / "suite_manifest.json")
            if (programs / "suite_manifest.json").is_file()
            else None
        ),
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["manifest"] = str(manifest_path)
    return result
