"""Fault-tolerant, read-only loading of M★ run artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

JSON_OBJECT = dict[str, Any]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _read_json(path: Path, root: Path) -> tuple[JSON_OBJECT, list[str]]:
    if not _is_within(path, root) or not path.is_file():
        return {}, []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"{path.name}: {type(exc).__name__}: {exc}"]
    if not isinstance(value, dict):
        return {}, [f"{path.name}: expected a JSON object"]
    return value, []


def _read_jsonl(path: Path, root: Path) -> tuple[list[JSON_OBJECT], list[str]]:
    values: list[JSON_OBJECT] = []
    errors: list[str] = []
    if not _is_within(path, root) or not path.is_file():
        return values, errors
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path.name}:{line_number}: {exc}")
                    continue
                if isinstance(value, dict):
                    values.append(value)
                else:
                    errors.append(f"{path.name}:{line_number}: expected a JSON object")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return values, errors


def _read_text(path: Path, root: Path) -> tuple[str, list[str]]:
    if not _is_within(path, root) or not path.is_file():
        return "", []
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeError) as exc:
        return "", [f"{path.name}: {type(exc).__name__}: {exc}"]


def _run_identity(runs_root: Path, run_path: Path, config: JSON_OBJECT) -> JSON_OBJECT:
    relative = run_path.relative_to(runs_root)
    parts = relative.parts
    task_id = str(config.get("task_id") or "").strip()
    if not parts:
        suite = run_path.name
        task = task_id or "直接运行"
        run_name = "default"
    elif len(parts) == 1:
        suite = parts[0]
        task = task_id or "直接运行"
        run_name = "default"
    elif len(parts) == 2:
        suite = parts[0]
        task = task_id or parts[1]
        run_name = "default" if task_id else parts[1]
    else:
        suite = parts[0]
        task = task_id or parts[-2]
        run_name = parts[-1]
    return {
        "id": relative.as_posix() if parts else ".",
        "path": str(run_path),
        "suite": suite,
        "task": task,
        "run": run_name,
        "label": f"{suite} / {task} / {run_name}",
    }


def scan_runs(runs_dir: str) -> JSON_OBJECT:
    """Discover directories containing M★ lineage/scores artifacts."""

    root = Path(runs_dir).expanduser().resolve()
    result: JSON_OBJECT = {
        "runs_dir": str(root),
        "entries": [],
        "errors": [],
    }
    if not root.is_dir():
        result["errors"].append(f"runs 目录不存在：{root}")
        return result

    candidates = set()
    for artifact_name in ("lineage.jsonl", "scores.jsonl"):
        try:
            for artifact in root.rglob(artifact_name):
                if artifact.is_file() and _is_within(artifact, root):
                    candidates.add(artifact.parent.resolve())
        except OSError as exc:
            result["errors"].append(
                f"扫描 {artifact_name} 失败：{type(exc).__name__}: {exc}"
            )

    entries = []
    for run_path in sorted(candidates, key=lambda item: item.as_posix()):
        config, errors = _read_json(run_path / "config.json", root)
        result["errors"].extend(
            f"{run_path.relative_to(root).as_posix()}: {error}" for error in errors
        )
        identity = _run_identity(root, run_path, config)
        identity["task_id"] = config.get("task_id")
        identity["seed"] = config.get("seed")
        identity["iterations"] = config.get("iterations")
        identity["has_lineage"] = (run_path / "lineage.jsonl").is_file()
        identity["has_scores"] = (run_path / "scores.jsonl").is_file()
        entries.append(identity)
    result["entries"] = entries
    return result


def resolve_run_path(runs_dir: str, run_id: str) -> Path:
    root = Path(runs_dir).expanduser().resolve()
    candidate = root if run_id in ("", ".") else (root / str(run_id)).resolve()
    if not _is_within(candidate, root) or not candidate.is_dir():
        raise ValueError(f"无效或不存在的 run：{run_id}")
    return candidate


def _score_key(record: JSON_OBJECT) -> tuple[Any, Any]:
    return record.get("program_id"), record.get("iteration")


def _first_dict(*values: Any) -> JSON_OBJECT:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list):
            return value
    return []


def _program_source(
    run_path: Path, runs_root: Path, program_id: str | None
) -> tuple[str, str | None, list[str]]:
    if not program_id:
        return "", None, []
    path = run_path / "pool" / f"{program_id}.py"
    source, errors = _read_text(path, runs_root)
    return (
        source,
        str(path) if path.is_file() and _is_within(path, runs_root) else None,
        errors,
    )


def _best_program_id(
    best_report: JSON_OBJECT, score_records: Iterable[JSON_OBJECT]
) -> str | None:
    explicit = best_report.get("program_id")
    if explicit:
        return str(explicit)
    eligible = [
        item
        for item in score_records
        if item.get("program_id")
        and item.get("quality_passed") in (None, True)
        and isinstance(item.get("static_score"), (int, float))
    ]
    if not eligible:
        return None
    return str(
        max(eligible, key=lambda item: float(item["static_score"]))["program_id"]
    )


def load_run(runs_dir: str, run_id: str) -> JSON_OBJECT:
    """Load and normalize one run without importing AgentReview runtime code."""

    root = Path(runs_dir).expanduser().resolve()
    run_path = resolve_run_path(runs_dir, run_id)
    warnings: list[str] = []

    config, errors = _read_json(run_path / "config.json", root)
    warnings.extend(errors)
    selection, errors = _read_json(run_path / "selection.json", root)
    warnings.extend(errors)
    best_report, errors = _read_json(run_path / "best_eval_report.json", root)
    warnings.extend(errors)
    lineage, errors = _read_jsonl(run_path / "lineage.jsonl", root)
    warnings.extend(errors)
    scores, errors = _read_jsonl(run_path / "scores.jsonl", root)
    warnings.extend(errors)

    if not (run_path / "lineage.jsonl").is_file():
        warnings.append("缺少 lineage.jsonl")
    if not (run_path / "scores.jsonl").is_file():
        warnings.append("缺少 scores.jsonl")

    score_records: dict[tuple[Any, Any], list[JSON_OBJECT]] = {}
    score_by_program: dict[str, JSON_OBJECT] = {}
    for score in scores:
        score_records.setdefault(_score_key(score), []).append(score)
        if score.get("program_id"):
            score_by_program[str(score["program_id"])] = score

    transformations: list[JSON_OBJECT] = []
    programs: dict[str, JSON_OBJECT] = {}
    failed_index = 0
    for index, lineage_item in enumerate(lineage):
        item = dict(lineage_item)
        program_id_value = item.get("program_id")
        program_id = str(program_id_value) if program_id_value else None
        iteration = item.get("iteration", 0)
        score_matches = score_records.get((program_id_value, iteration), [])
        score = score_matches.pop(0) if score_matches else {}
        parent_id = item.get("parent_id", score.get("parent_id"))
        parent_id = str(parent_id) if parent_id else None
        source, source_path, source_errors = _program_source(run_path, root, program_id)
        warnings.extend(source_errors)

        quality = _first_dict(item.get("quality"))
        change_log = _first_dict(item.get("change_log"))
        compile_errors = list(
            dict.fromkeys(
                str(error)
                for collection in (
                    score.get("compile_errors"),
                    quality.get("compile_errors"),
                    quality.get("static_errors"),
                    quality.get("smoke_errors"),
                )
                if isinstance(collection, list)
                for error in collection
                if error
            )
        )
        quality_passed = score.get("quality_passed")
        if quality_passed is None:
            quality_passed = quality.get("passed")
        admitted = item.get("admitted")
        if admitted is None:
            admitted = bool(program_id)

        if not program_id:
            failed_index += 1
            uid = f"failed_iter_{int(iteration or 0):03d}_{failed_index:02d}"
            status = "failed"
        else:
            uid = program_id
            if int(iteration or 0) == 0:
                status = "seed"
            elif quality_passed is False:
                status = "quality_failed"
            elif not source_path:
                status = "missing_code"
            else:
                status = "success"

        static_score = item.get("static_score", score.get("static_score"))
        if static_score is None and program_id:
            static_score = score_by_program.get(program_id, {}).get("static_score")
        rotating_score = item.get("rotating_score", score.get("rotating_score"))
        parent_static_score = item.get("parent_static_score")
        if parent_static_score is None and parent_id:
            parent_static_score = score_by_program.get(parent_id, {}).get(
                "static_score"
            )

        trace_path = (
            run_path / "traces" / f"iter_{int(iteration or 0):03d}_rotating.jsonl"
        )
        record: JSON_OBJECT = {
            "uid": uid,
            "index": index,
            "program_id": program_id,
            "parent_id": parent_id,
            "iteration": iteration,
            "status": status,
            "admitted": bool(admitted),
            "source": source,
            "source_path": source_path,
            "parent_static_score": parent_static_score,
            "static_score": static_score,
            "rotating_score": rotating_score,
            "change_log": change_log,
            "change_summary": (
                change_log.get("summary")
                or score.get("change_summary")
                or item.get("error")
                or ""
            ),
            "change_details": _first_list(change_log.get("details")),
            "quality": quality,
            "quality_passed": quality_passed,
            "compile_errors": compile_errors,
            "compile_fix_attempts": item.get("compile_fix_attempts", 0),
            "error": item.get("error"),
            "trace_path": (
                str(trace_path)
                if trace_path.is_file() and _is_within(trace_path, root)
                else None
            ),
            "raw_lineage": lineage_item,
            "raw_score": score,
        }
        transformations.append(record)
        if program_id:
            programs[program_id] = record

    # Preserve score-only failed iterations if a partially written run has not
    # appended the corresponding lineage record yet.
    represented = {(_score_key(item["raw_score"])) for item in transformations}
    for score in scores:
        if _score_key(score) in represented:
            continue
        failed_index += 1
        iteration = score.get("iteration", 0)
        program_id = str(score["program_id"]) if score.get("program_id") else None
        uid = program_id or f"score_only_{int(iteration or 0):03d}_{failed_index:02d}"
        source, source_path, source_errors = _program_source(run_path, root, program_id)
        warnings.extend(source_errors)
        record = {
            "uid": uid,
            "index": len(transformations),
            "program_id": program_id,
            "parent_id": score.get("parent_id"),
            "iteration": iteration,
            "status": "missing_lineage" if program_id else "failed",
            "admitted": bool(program_id),
            "source": source,
            "source_path": source_path,
            "parent_static_score": None,
            "static_score": score.get("static_score"),
            "rotating_score": score.get("rotating_score"),
            "change_log": {},
            "change_summary": score.get("change_summary") or "缺少 lineage 记录",
            "change_details": [],
            "quality": {},
            "quality_passed": score.get("quality_passed"),
            "compile_errors": _first_list(score.get("compile_errors")),
            "compile_fix_attempts": 0,
            "error": None,
            "trace_path": None,
            "raw_lineage": {},
            "raw_score": score,
        }
        transformations.append(record)
        if program_id:
            programs[program_id] = record

    transformations.sort(
        key=lambda item: (
            int(item.get("iteration") or 0),
            int(item.get("index") or 0),
        )
    )
    best_program_id = _best_program_id(best_report, scores)
    identity = _run_identity(root, run_path, config)
    return {
        **identity,
        "runs_dir": str(root),
        "config": config,
        "selection": selection,
        "best_report": best_report,
        "best_program_id": best_program_id,
        "lineage": lineage,
        "scores": scores,
        "transformations": transformations,
        "programs": programs,
        "warnings": warnings,
    }


def load_trace_diagnostics(
    runs_dir: str,
    run_id: str,
    transformation: JSON_OBJECT,
    *,
    preview_chars: int = 480,
    max_records: int = 200,
) -> JSON_OBJECT:
    """Summarize an iteration trace and return safely truncated raw records."""

    root = Path(runs_dir).expanduser().resolve()
    run_path = resolve_run_path(runs_dir, run_id)
    trace_path_value = transformation.get("trace_path")
    empty = {
        "available": False,
        "counts": {},
        "failures": [],
        "rows": [],
        "raw_excerpt": "该迭代没有 trace 文件。",
        "warnings": [],
    }
    if not trace_path_value:
        return empty
    trace_path = Path(str(trace_path_value)).resolve()
    if not _is_within(trace_path, run_path) or not _is_within(trace_path, root):
        empty["warnings"].append("trace 路径超出所选 run")
        return empty

    records, warnings = _read_jsonl(trace_path, root)
    rows: list[JSON_OBJECT] = []
    failures: list[JSON_OBJECT] = []
    counts: dict[str, int] = {}
    raw_values: list[JSON_OBJECT] = []
    long_fields = {
        "raw_text",
        "question",
        "gold",
        "context",
        "answer",
        "knowledge_item",
        "query",
        "output",
    }
    for index, record in enumerate(records[:max_records], start=1):
        phase = str(record.get("phase") or "unknown")
        counts[phase] = counts.get(phase, 0) + 1
        row = {
            "index": index,
            "phase": phase,
            "case_id": record.get("episode_id") or record.get("id") or "",
            "success": record.get("success"),
            "duration_seconds": record.get("duration_seconds"),
            "score": record.get("score"),
            "error": record.get("error") or "",
        }
        rows.append(row)
        if record.get("success") is False or record.get("error"):
            failures.append(row)

        shortened: JSON_OBJECT = {}
        for key, value in record.items():
            if (
                key in long_fields
                and isinstance(value, str)
                and len(value) > preview_chars
            ):
                shortened[key] = (
                    value[:preview_chars]
                    + f"\n… [已截断 {len(value) - preview_chars} 个字符]"
                )
            else:
                shortened[key] = value
        raw_values.append(shortened)
    if len(records) > max_records:
        warnings.append(
            f"{trace_path.name}: 仅展示前 {max_records}/{len(records)} 条 trace"
        )
    return {
        "available": True,
        "counts": counts,
        "failures": failures,
        "rows": rows,
        "raw_excerpt": "\n".join(
            json.dumps(item, ensure_ascii=False, indent=2) for item in raw_values
        ),
        "warnings": warnings,
    }


def ancestor_root(programs: JSON_OBJECT, program_id: str | None) -> str | None:
    """Return the oldest available ancestor for a candidate."""

    current = program_id
    visited = set()
    last = current
    while current and current not in visited:
        visited.add(current)
        item = programs.get(current)
        if not isinstance(item, dict):
            break
        last = current
        parent = item.get("parent_id")
        current = str(parent) if parent else None
    return last
