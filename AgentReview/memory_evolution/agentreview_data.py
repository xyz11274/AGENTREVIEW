"""Build leakage-safe review episodes from the local OpenReview corpus."""

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agentreview.memory_library import infer_reviewer_dimension
from agentreview.source_map import (
    build_source_anchors,
    format_source_anchors,
    load_content_list,
)

from .tasks import META_TASK, SPECIALIST_TASKS


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def work_family_identifiers(record: Dict[str, Any]) -> set:
    """Return every stable identifier that can connect two versions of a work."""
    content = record.get("content") or {}
    identifiers = set()
    paperhash = normalize_title(content.get("paperhash", ""))
    title = normalize_title(content.get("title", ""))
    if paperhash:
        identifiers.add(f"paperhash:{paperhash}")
    if title:
        identifiers.add(f"title:{title}")
    if not identifiers:
        identifiers.add(f"id:{record.get('id', '')}")
    return identifiers


def work_family_key(record: Dict[str, Any]) -> str:
    """Return an assigned component key or a deterministic single-record key."""
    assigned = record.get("_mstar_work_family")
    if assigned:
        return str(assigned)
    stable = "\n".join(sorted(work_family_identifiers(record)))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _assign_work_families(records: Sequence[Dict[str, Any]]) -> List[str]:
    """Union records sharing either paperhash or normalized title."""
    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owner: Dict[str, int] = {}
    identifiers_by_record = []
    for index, record in enumerate(records):
        identifiers = work_family_identifiers(record)
        identifiers_by_record.append(identifiers)
        for identifier in identifiers:
            if identifier in owner:
                union(index, owner[identifier])
            else:
                owner[identifier] = index

    component_identifiers: Dict[int, set] = {}
    for index, identifiers in enumerate(identifiers_by_record):
        component_identifiers.setdefault(find(index), set()).update(identifiers)
    component_keys = {
        root: hashlib.sha256(
            "\n".join(sorted(identifiers)).encode("utf-8")
        ).hexdigest()
        for root, identifiers in component_identifiers.items()
    }
    return [component_keys[find(index)] for index in range(len(records))]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_s2_cache(path: Optional[str]) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    """Load a frozen Semantic Scholar cache without prescribing one exporter."""
    if not path:
        return {}, None
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Semantic Scholar cache not found: {source}")
    cache: Dict[str, List[Dict[str, Any]]] = {}

    def add(value: Dict[str, Any], fallback_id: str = "") -> None:
        paper_id = str(
            value.get("paper_id")
            or value.get("id")
            or value.get("query_id")
            or fallback_id
        )
        papers = (
            value.get("retrieved_papers")
            or value.get("papers")
            or value.get("results")
            or []
        )
        if paper_id and isinstance(papers, list):
            cache[paper_id] = [paper for paper in papers if isinstance(paper, dict)]

    if source.suffix.lower() == ".jsonl":
        for value in _iter_jsonl(source):
            add(value)
    else:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for value in payload:
                if isinstance(value, dict):
                    add(value)
        elif isinstance(payload, dict):
            for paper_id, value in payload.items():
                if isinstance(value, list):
                    cache[str(paper_id)] = [
                        paper for paper in value if isinstance(paper, dict)
                    ]
                elif isinstance(value, dict):
                    add(value, fallback_id=str(paper_id))
        else:
            raise ValueError("Semantic Scholar cache must be JSON object/list or JSONL")
    return cache, _sha256_file(source)


def _format_related_work(papers: Sequence[Dict[str, Any]]) -> str:
    blocks = []
    for index, paper in enumerate(papers[:10], start=1):
        title = str(paper.get("title") or "").strip()
        abstract = str(paper.get("abstract") or paper.get("summary") or "").strip()
        year = str(paper.get("year") or "").strip()
        if not title and not abstract:
            continue
        blocks.append(
            f"[REF-{index:02d}] {title}"
            + (f" ({year})" if year else "")
            + (f"\n{abstract[:1500]}" if abstract else "")
        )
    return "\n\n".join(blocks) or "(no cached related work)"


def build_reviewer_memory_episode_text(
    record: Dict[str, Any], reviewer_name: str, review: str
) -> str:
    """Format an online write episode without changing the frozen reviewer role."""
    reference_titles = [
        paper.get("title", "")
        for paper in record.get("retrieved_papers", [])
        if paper.get("title")
    ]
    return "\n".join(
        [
            f"Paper title: {record.get('paper_title', '')}",
            f"Reviewer role: {reviewer_name}",
            f"Review dimension: {infer_reviewer_dimension(reviewer_name) or 'META'}",
            "Related references:",
            *[f"- {title}" for title in reference_titles[:10]],
            "",
            "Completed review:",
            str(review),
        ]
    ).strip()


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def _write_jsonl(path: Path, values: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _resolve_markdown(openreview_root: Path, record: Dict[str, Any]) -> Optional[Path]:
    raw = str(record.get("MD_path") or "")
    candidates = []
    if raw:
        raw_path = Path(raw)
        candidates.extend(
            [
                raw_path,
                openreview_root / raw_path,
                openreview_root / raw_path.name,
            ]
        )
    record_id = str(record.get("id") or "")
    if record_id:
        candidates.extend(openreview_root.glob(f"**/*{record_id}*"))

    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            return candidate.resolve()
        if candidate.is_dir():
            for filename in ("md_content_no_appendix.md", "md_content.md"):
                child = candidate / filename
                if child.is_file():
                    return child.resolve()
            markdown = sorted(candidate.glob("*.md"))
            if markdown:
                return markdown[0].resolve()
    return None


def _category_reference(record: Dict[str, Any], task_id: str) -> Tuple[str, List[str]]:
    by_category = record.get("opinions_md_by_category_dict") or {}
    selected: List[Tuple[str, str]] = []
    for category, value in sorted(by_category.items()):
        if not str(category).startswith(f"{task_id}-"):
            continue
        text = ""
        if isinstance(value, list):
            text = next(
                (str(item).strip() for item in reversed(value) if isinstance(item, str) and item.strip()),
                "",
            )
        elif isinstance(value, str):
            text = value.strip()
        if text:
            selected.append((str(category), text))
    reference = "\n\n".join(f"[{category}]\n{text}" for category, text in selected)
    return reference, [category for category, _ in selected]


def _meta_reference(record: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for group in record.get("metareview") or []:
        for item in group if isinstance(group, list) else []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            role, payload = item[0], item[1]
            if isinstance(payload, dict):
                for value in payload.values():
                    if isinstance(value, str) and value.strip():
                        blocks.append(f"{role}: {value.strip()}")
                    elif isinstance(value, dict):
                        text = value.get("value")
                        if isinstance(text, str) and text.strip():
                            blocks.append(f"{role}: {text.strip()}")
    decision = str(record.get("decision") or "").strip()
    if decision:
        blocks.append(f"Decision: {decision}")
    return "\n\n".join(blocks)


def _paper_question(
    task_id: str,
    title: str,
    paper_text: str,
    source_anchor_text: str,
    related_work_text: str,
) -> str:
    return (
        f"Review task: {task_id}\n"
        f"Paper title: {title}\n\n"
        "Paper Markdown:\n"
        f"{paper_text}\n\n"
        "=== SOURCE ANCHORS ===\n"
        f"{source_anchor_text}\n"
        "=== END SOURCE ANCHORS ===\n\n"
        "=== FROZEN RELATED-WORK CACHE ===\n"
        f"{related_work_text}\n"
        "=== END FROZEN RELATED-WORK CACHE ===\n\n"
        f"Return the complete {task_id} specialist review using the frozen output protocol."
    )


def _training_episode(question: str, reference: str) -> str:
    return (
        "TRAINING INPUT\n"
        f"{question}\n\n"
        "VERIFIED HUMAN REFERENCE OPINIONS\n"
        f"{reference}"
    )


def _select_families(
    records: List[Dict[str, Any]],
    validation_families: int,
    meta_validation_families: int,
    max_train_families: Optional[int],
    seed: int,
) -> Tuple[set, set, set]:
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        by_family.setdefault(work_family_key(record), []).append(record)
    families = sorted(by_family)
    rng = random.Random(seed)
    rng.shuffle(families)
    held_out_count = max(0, validation_families) + max(
        0, meta_validation_families
    )
    if len(families) <= held_out_count:
        raise ValueError(
            f"Need more than {held_out_count} work families, found {len(families)}"
        )
    validation = set(families[:validation_families])
    meta_start = validation_families
    meta_end = meta_start + max(0, meta_validation_families)
    meta_validation = set(families[meta_start:meta_end])
    train_values = families[meta_end:]
    if max_train_families is not None:
        train_values = train_values[: max(1, int(max_train_families))]
    return set(train_values), validation, meta_validation


def prepare_openreview_data(
    openreview_root: str,
    output_dir: str,
    validation_families: int = 250,
    meta_validation_families: int = 250,
    max_train_families: Optional[int] = 2000,
    max_paper_chars: int = 60000,
    seed: int = 0,
    s2_cache_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create five specialist datasets; the official test file remains untouched."""
    source_root = Path(openreview_root).expanduser().resolve()
    train_path = source_root / "train_clean.jsonl"
    test_path = source_root / "test.jsonl"
    if not train_path.is_file() or not test_path.is_file():
        raise FileNotFoundError(
            f"Expected train_clean.jsonl and test.jsonl under {source_root}"
        )

    train_records = list(_iter_jsonl(train_path))
    test_records = list(_iter_jsonl(test_path))
    combined = train_records + test_records
    family_keys = _assign_work_families(combined)
    test_families = set(family_keys[len(train_records) :])
    source_records = []
    for record, family in zip(train_records, family_keys[: len(train_records)]):
        if family in test_families:
            continue
        copied = dict(record)
        copied["_mstar_work_family"] = family
        source_records.append(copied)
    s2_cache, s2_cache_sha256 = _load_s2_cache(s2_cache_path)
    train_families, validation_family_set, meta_validation_family_set = _select_families(
        source_records,
        validation_families=validation_families,
        meta_validation_families=meta_validation_families,
        max_train_families=max_train_families,
        seed=seed,
    )
    allowed_families = (
        train_families | validation_family_set | meta_validation_family_set
    )
    records = [
        record for record in source_records if work_family_key(record) in allowed_families
    ]
    required_s2_ids = {str(record.get("id") or "") for record in records}
    missing_s2_ids = sorted(required_s2_ids - set(s2_cache))

    per_task: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        task_id: {"episodes": [], "validation": [], "queries": []}
        for task_id in SPECIALIST_TASKS
    }
    missing_markdown = 0
    for record in records:
        markdown_path = _resolve_markdown(source_root, record)
        if markdown_path is None:
            missing_markdown += 1
            continue
        paper_text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        paper_text = paper_text[: max(1, int(max_paper_chars))]
        anchors = build_source_anchors(load_content_list(str(markdown_path)))
        if not anchors:
            anchors = [
                {
                    "source_id": f"SRC-{index + 1:03d}",
                    "page_idx": 0,
                    "type": "text",
                    "text": paper_text[start : start + 500].replace("\n", " "),
                }
                for index, start in enumerate(range(0, min(len(paper_text), 40000), 1000))
                if paper_text[start : start + 500].strip()
            ][:40]
        source_anchor_text = format_source_anchors(anchors)
        content = record.get("content") or {}
        title = str(content.get("title") or record.get("id") or "")
        family = work_family_key(record)
        if family in validation_family_set:
            split = "specialist_validation"
        elif family in meta_validation_family_set:
            split = "meta_validation"
        else:
            split = "train"

        for task_id in SPECIALIST_TASKS:
            reference, categories = _category_reference(record, task_id)
            if not reference:
                continue
            question = _paper_question(
                task_id,
                title,
                paper_text,
                source_anchor_text,
                _format_related_work(s2_cache.get(str(record.get("id") or ""), [])),
            )
            common_metadata = {
                "paper_id": str(record.get("id") or ""),
                "paper_title": title,
                "work_family": family,
                "task_id": task_id,
                "categories": categories,
                "conference": record.get("conference"),
                "markdown_path": str(markdown_path),
                "split": split,
            }
            item_id = f"{record.get('id')}::{task_id}"
            if split == "train":
                per_task[task_id]["episodes"].append(
                    {
                        "id": item_id,
                        "text": _training_episode(question, reference),
                        "metadata": common_metadata,
                    }
                )
            elif split == "specialist_validation":
                per_task[task_id]["validation"].append(
                    {
                        "id": item_id,
                        "question": question,
                        "answer": reference,
                        "metadata": common_metadata,
                    }
                )
            if split in {"train", "meta_validation"}:
                per_task[task_id]["queries"].append(
                    {
                        "id": item_id,
                        "question": question,
                        "metadata": common_metadata,
                    }
                )

    output_root = Path(output_dir).expanduser().resolve()
    task_manifest = {}
    for task_id, values in per_task.items():
        task_root = output_root / task_id
        episodes_path = task_root / "episodes.jsonl"
        validation_path = task_root / "validation.jsonl"
        queries_path = task_root / "queries.jsonl"
        _write_jsonl(episodes_path, values["episodes"])
        _write_jsonl(validation_path, values["validation"])
        _write_jsonl(queries_path, values["queries"])
        task_manifest[task_id] = {
            "episodes": str(episodes_path),
            "validation": str(validation_path),
            "queries": str(queries_path),
            "episode_count": len(values["episodes"]),
            "validation_count": len(values["validation"]),
        }

    manifest = {
        "source": str(train_path),
        "held_out_test": str(test_path),
        "seed": seed,
        "test_family_count": len(test_families),
        "train_family_count": len(train_families),
        "validation_family_count": len(validation_family_set),
        "meta_validation_family_count": len(meta_validation_family_set),
        "family_overlap": len(
            (train_families & validation_family_set)
            | (train_families & meta_validation_family_set)
            | (validation_family_set & meta_validation_family_set)
        ),
        "test_overlap_removed": True,
        "work_family_identity": "transitive union by paperhash OR normalized title",
        "semantic_scholar_cache": (
            str(Path(s2_cache_path).expanduser().resolve()) if s2_cache_path else None
        ),
        "semantic_scholar_cache_sha256": s2_cache_sha256,
        "semantic_scholar_required_count": len(required_s2_ids),
        "semantic_scholar_cached_count": len(required_s2_ids) - len(missing_s2_ids),
        "semantic_scholar_missing_ids": missing_s2_ids,
        "formal_ready": bool(s2_cache_sha256) and not missing_s2_ids,
        "missing_markdown": missing_markdown,
        "tasks": task_manifest,
        "meta_status": "prepared only after five specialist winners are frozen",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def prepare_meta_data(
    openreview_root: str,
    specialist_outputs_path: str,
    output_dir: str,
) -> Dict[str, Any]:
    """Build the sixth task from cached outputs of the five frozen specialists."""
    source_root = Path(openreview_root).expanduser().resolve()
    records = {
        str(record.get("id")): record
        for record in _iter_jsonl(source_root / "train_clean.jsonl")
    }
    episodes: List[Dict[str, Any]] = []
    validation: List[Dict[str, Any]] = []
    families_by_split = {"train": set(), "validation": set()}
    for cached in _iter_jsonl(Path(specialist_outputs_path).expanduser().resolve()):
        paper_id = str(cached.get("id") or "")
        record = records.get(paper_id)
        if record is None:
            continue
        specialists = cached.get("specialists") or {}
        if set(specialists) != set(SPECIALIST_TASKS):
            raise ValueError(f"Meta cache for {paper_id} must contain all five specialists")
        reference = _meta_reference(record)
        if not reference:
            continue
        question = (
            f"Review task: {META_TASK}\nPaper id: {paper_id}\n\n"
            + "\n\n".join(
                f"=== {task_id} ===\n{specialists[task_id]}"
                for task_id in SPECIALIST_TASKS
            )
            + "\n\nReturn the complete final meta-review."
        )
        split = str(cached.get("split") or "")
        if split not in families_by_split:
            raise ValueError(f"Invalid meta cache split for {paper_id}: {split}")
        family = str(cached.get("work_family") or work_family_key(record))
        families_by_split[split].add(family)
        metadata = {
            "paper_id": paper_id,
            "paper_title": (record.get("content") or {}).get("title", ""),
            "work_family": family,
            "task_id": META_TASK,
            "split": split,
        }
        if split == "train":
            episodes.append(
                {
                    "id": f"{paper_id}::{META_TASK}",
                    "text": _training_episode(question, reference),
                    "metadata": metadata,
                }
            )
        else:
            validation.append(
                {
                    "id": f"{paper_id}::{META_TASK}",
                    "question": question,
                    "answer": reference,
                    "metadata": metadata,
                }
            )
    overlap = families_by_split["train"] & families_by_split["validation"]
    if overlap:
        raise ValueError(f"Meta cache leaks {len(overlap)} work families across splits")
    output_root = Path(output_dir).expanduser().resolve() / META_TASK
    _write_jsonl(output_root / "episodes.jsonl", episodes)
    _write_jsonl(output_root / "validation.jsonl", validation)
    manifest = {
        "task_id": META_TASK,
        "episodes": str(output_root / "episodes.jsonl"),
        "validation": str(output_root / "validation.jsonl"),
        "episode_count": len(episodes),
        "validation_count": len(validation),
        "family_overlap": 0,
        "specialist_outputs": str(Path(specialist_outputs_path).resolve()),
        "specialist_outputs_sha256": _sha256_file(
            Path(specialist_outputs_path).expanduser().resolve()
        ),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def prepare_held_out_test_data(
    openreview_root: str,
    output_dir: str,
    s2_cache_path: str,
    max_paper_chars: int = 60000,
) -> Dict[str, Any]:
    """Materialize sealed specialist targets and META references from test.jsonl."""
    source_root = Path(openreview_root).expanduser().resolve()
    test_path = source_root / "test.jsonl"
    if not test_path.is_file():
        raise FileNotFoundError(f"Expected test.jsonl under {source_root}")
    records = list(_iter_jsonl(test_path))
    family_keys = _assign_work_families(records)
    s2_cache, s2_sha256 = _load_s2_cache(s2_cache_path)
    required_ids = {str(record.get("id") or "") for record in records}
    missing_ids = sorted(required_ids - set(s2_cache))
    if missing_ids:
        raise ValueError(
            f"Frozen S2 cache is missing {len(missing_ids)} held-out paper IDs"
        )

    per_task = {task_id: [] for task_id in SPECIALIST_TASKS}
    meta_references = []
    missing_markdown = 0
    for record, family in zip(records, family_keys):
        markdown_path = _resolve_markdown(source_root, record)
        if markdown_path is None:
            missing_markdown += 1
            continue
        paper_text = markdown_path.read_text(
            encoding="utf-8", errors="ignore"
        )[: max(1, int(max_paper_chars))]
        anchors = build_source_anchors(load_content_list(str(markdown_path)))
        if not anchors:
            anchors = [
                {
                    "source_id": f"SRC-{index + 1:03d}",
                    "page_idx": 0,
                    "type": "text",
                    "text": paper_text[start : start + 500].replace("\n", " "),
                }
                for index, start in enumerate(
                    range(0, min(len(paper_text), 40000), 1000)
                )
                if paper_text[start : start + 500].strip()
            ][:40]
        title = str(
            (record.get("content") or {}).get("title")
            or record.get("id")
            or ""
        )
        source_anchor_text = format_source_anchors(anchors)
        paper_id = str(record.get("id") or "")
        for task_id in SPECIALIST_TASKS:
            reference, categories = _category_reference(record, task_id)
            if not reference:
                continue
            per_task[task_id].append(
                {
                    "id": f"{paper_id}::{task_id}",
                    "question": _paper_question(
                        task_id,
                        title,
                        paper_text,
                        source_anchor_text,
                        _format_related_work(s2_cache[paper_id]),
                    ),
                    "answer": reference,
                    "metadata": {
                        "paper_id": paper_id,
                        "paper_title": title,
                        "work_family": family,
                        "task_id": task_id,
                        "categories": categories,
                        "split": "test",
                    },
                }
            )
        meta_reference = _meta_reference(record)
        if meta_reference:
            meta_references.append(
                {
                    "id": f"{paper_id}::{META_TASK}",
                    "answer": meta_reference,
                    "metadata": {
                        "paper_id": paper_id,
                        "paper_title": title,
                        "work_family": family,
                        "task_id": META_TASK,
                        "split": "test",
                    },
                }
            )

    output_root = Path(output_dir).expanduser().resolve()
    tasks = {}
    for task_id, values in per_task.items():
        destination = output_root / task_id / "test.jsonl"
        _write_jsonl(destination, values)
        tasks[task_id] = {
            "path": str(destination),
            "sha256": _sha256_file(destination),
            "count": len(values),
        }
    meta_path = output_root / META_TASK / "references.jsonl"
    _write_jsonl(meta_path, meta_references)
    tasks[META_TASK] = {
        "path": str(meta_path),
        "sha256": _sha256_file(meta_path),
        "count": len(meta_references),
    }
    manifest = {
        "source": str(test_path),
        "source_sha256": _sha256_file(test_path),
        "semantic_scholar_cache": str(Path(s2_cache_path).resolve()),
        "semantic_scholar_cache_sha256": s2_sha256,
        "work_family_identity": "transitive union by paperhash OR normalized title",
        "paper_count": len(records),
        "missing_markdown": missing_markdown,
        "sealed_test_feedback": False,
        "tasks": tasks,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
