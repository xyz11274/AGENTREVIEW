"""Read-only adapters for displaying AgentReview output artifacts.

This module deliberately depends only on the Python standard library.  It does
not import or call the review pipeline, which keeps the result viewer isolated
from review generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SPECIALIST_REVIEWERS = [
    "Reviewer QUAL",
    "Reviewer CLAR",
    "Reviewer SIGN",
    "Reviewer ORIG",
    "Reviewer POL",
]
META_REVIEWER = "Final Meta-Reviewer"
EXPECTED_REVIEWERS = SPECIALIST_REVIEWERS + [META_REVIEWER]

CORE_ARTIFACTS = [
    "reviews.json",
    "memory.json",
    "review_history.json",
    "timing.json",
]
OPTIONAL_ARTIFACTS = [
    "specialist_checkpoint.json",
]
VIEWABLE_ARTIFACTS = CORE_ARTIFACTS + OPTIONAL_ARTIFACTS
MAX_JSON_BYTES = 50 * 1024 * 1024


def _empty_artifact(path: Path) -> Dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path),
        "exists": False,
        "valid": False,
        "data": None,
        "error": None,
        "raw_json": "",
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def safe_read_json(path: Path, allowed_root: Optional[Path] = None) -> Dict[str, Any]:
    """Read one JSON file without raising errors to the viewer.

    The returned dictionary always has the same keys.  When ``allowed_root`` is
    provided, symlinks or paths resolving outside that root are rejected.
    """

    path = Path(path)
    result = _empty_artifact(path)
    if allowed_root is not None and not _is_within(path, Path(allowed_root)):
        result["error"] = "Artifact path resolves outside the configured output directory."
        return result
    if not path.is_file():
        return result

    result["exists"] = True
    try:
        size = path.stat().st_size
        if size > MAX_JSON_BYTES:
            raise ValueError(
                f"JSON artifact is too large to display ({size} bytes; "
                f"limit {MAX_JSON_BYTES} bytes)."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        result["data"] = data
        result["valid"] = True
        result["raw_json"] = json.dumps(data, ensure_ascii=False, indent=2)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def resolve_review_root(output_dir: str) -> Path:
    """Resolve either an output root or its ``single_paper_review`` directory."""

    root = Path(output_dir).expanduser().resolve()
    if root.name == "single_paper_review":
        return root
    return root / "single_paper_review"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _load_artifacts(sample_dir: Path, review_root: Path) -> Dict[str, Dict[str, Any]]:
    return {
        name: safe_read_json(sample_dir / name, allowed_root=review_root)
        for name in VIEWABLE_ARTIFACTS
    }


def _artifact_dict(artifacts: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
    artifact = artifacts.get(name, {})
    return _as_dict(artifact.get("data")) if artifact.get("valid") else {}


def _normalise_messages(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = []
    for index, value in enumerate(_as_list(history.get("messages"))):
        if not isinstance(value, dict):
            continue
        messages.append(
            {
                "index": index,
                "agent_name": _string(value.get("agent_name"), "Unknown"),
                "content": _string(value.get("content")),
                "turn": value.get("turn"),
                "timestamp": str(value.get("timestamp", "")),
                "visible_to": value.get("visible_to", "all"),
                "msg_type": _string(value.get("msg_type"), "text"),
            }
        )
    return messages


def _normalise_timing(
    timing: Dict[str, Any], history: Dict[str, Any]
) -> Tuple[Dict[str, Any], str]:
    history_timing = _as_dict(history.get("timing"))
    source = "timing.json" if timing else "review_history.json"
    combined = dict(history_timing)
    combined.update(timing)
    return (
        {
            "sample_id": _string(combined.get("sample_id")),
            "paper_md_path": _string(combined.get("paper_md_path")),
            "started_at_local": _string(combined.get("started_at_local")),
            "ended_at_local": _string(combined.get("ended_at_local")),
            "duration_seconds": combined.get("duration_seconds"),
            "duration_text": _string(combined.get("duration_text")),
        },
        source,
    )


def _sample_status(
    artifacts: Dict[str, Dict[str, Any]], reviews: Dict[str, Any]
) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    for name in CORE_ARTIFACTS:
        artifact = artifacts[name]
        if not artifact["exists"]:
            warnings.append(f"Missing artifact: {name}")
        elif not artifact["valid"]:
            warnings.append(f"Invalid artifact {name}: {artifact['error']}")

    missing_reviewers = [
        name for name in EXPECTED_REVIEWERS if not _string(reviews.get(name)).strip()
    ]
    if missing_reviewers:
        warnings.append("Missing review output: " + ", ".join(missing_reviewers))

    complete_files = all(
        artifacts[name]["exists"] and artifacts[name]["valid"]
        for name in CORE_ARTIFACTS
    )
    return ("complete" if complete_files and not missing_reviewers else "partial", warnings)


def load_sample(output_dir: str, sample_id: str) -> Dict[str, Any]:
    """Load and normalise all viewable artifacts for one sample."""

    review_root = resolve_review_root(output_dir)
    sample_dir = (review_root / str(sample_id)).resolve()
    if not _is_within(sample_dir, review_root) or sample_dir.parent != review_root.resolve():
        raise ValueError("Invalid sample id outside the configured output directory.")
    if not sample_dir.is_dir():
        raise FileNotFoundError(f"Sample output directory not found: {sample_id}")

    artifacts = _load_artifacts(sample_dir, review_root)
    reviews = _artifact_dict(artifacts, "reviews.json")
    memory = _artifact_dict(artifacts, "memory.json")
    history = _artifact_dict(artifacts, "review_history.json")
    timing_data = _artifact_dict(artifacts, "timing.json")
    checkpoint = _artifact_dict(artifacts, "specialist_checkpoint.json")

    timing, timing_source = _normalise_timing(timing_data, history)
    status, warnings = _sample_status(artifacts, reviews)
    checkpoint_reviews = _as_dict(checkpoint.get("reviews"))

    paper_md_path = (
        _string(memory.get("paper_md_path"))
        or timing["paper_md_path"]
    )
    memory_mode = _string(memory.get("memory_mode"))
    if not memory_mode and memory.get("memory_retrieval_enabled") is False:
        memory_mode = "none"
    elif not memory_mode and memory.get("reviewer_memory_contexts"):
        memory_mode = "existing"

    raw_artifacts = {}
    for name, artifact in artifacts.items():
        if artifact["valid"]:
            raw_artifacts[name] = artifact["raw_json"]
        elif artifact["exists"]:
            raw_artifacts[name] = f"Unable to parse {name}:\n{artifact['error']}"
        else:
            raw_artifacts[name] = f"Artifact not found: {name}"

    return {
        "sample_id": str(sample_id),
        "sample_dir": str(sample_dir),
        "status": status,
        "warnings": warnings,
        "paper": {
            "title": _string(memory.get("paper_title"), str(sample_id)),
            "paper_md_path": paper_md_path,
            "paper_dir": _string(memory.get("paper_dir")),
            "semantic_scholar_query": _string(memory.get("semantic_scholar_query")),
            "review_model_name": _string(memory.get("review_model_name")),
            "keyword_model_name": _string(memory.get("keyword_model_name")),
        },
        "reviewers": {
            name: _string(reviews.get(name)) for name in SPECIALIST_REVIEWERS
        },
        "final_meta_review": _string(reviews.get(META_REVIEWER)),
        "memory": {
            "mode": memory_mode or "unknown",
            "retrieval_enabled": memory.get("memory_retrieval_enabled"),
            "retrieved_papers": _as_list(memory.get("retrieved_papers")),
            "reviewer_contexts": _as_dict(memory.get("reviewer_memory_contexts")),
            "source_anchors": _as_list(memory.get("source_anchors")),
            "references_text": _string(memory.get("references_text")),
        },
        "history": {
            "experiment_setting": _as_dict(history.get("experiment_setting")),
            "messages": _normalise_messages(history),
        },
        "timing": timing,
        "timing_source": timing_source,
        "checkpoint": {
            "exists": artifacts["specialist_checkpoint.json"]["exists"],
            "reviews": {
                name: _string(checkpoint_reviews.get(name))
                for name in SPECIALIST_REVIEWERS
                if _string(checkpoint_reviews.get(name))
            },
            "updated_at_local": _string(checkpoint.get("updated_at_local")),
            "paper_md_path": _string(checkpoint.get("paper_md_path")),
            "model_name": _string(checkpoint.get("model_name")),
        },
        "files": {
            name: {
                "exists": artifact["exists"],
                "valid": artifact["valid"],
                "error": artifact["error"],
            }
            for name, artifact in artifacts.items()
        },
        "raw_artifacts": raw_artifacts,
    }


def scan_output_dir(output_dir: str) -> Dict[str, Any]:
    """Scan sample directories and return a fault-tolerant catalog."""

    review_root = resolve_review_root(output_dir)
    catalog: Dict[str, Any] = {
        "output_dir": str(Path(output_dir).expanduser().resolve()),
        "review_root": str(review_root),
        "samples": [],
        "error": None,
    }
    if not review_root.is_dir():
        catalog["error"] = f"Review output directory not found: {review_root}"
        return catalog

    for sample_dir in sorted(path for path in review_root.iterdir() if path.is_dir()):
        try:
            sample = load_sample(output_dir, sample_dir.name)
            catalog["samples"].append(
                {
                    "sample_id": sample["sample_id"],
                    "title": sample["paper"]["title"],
                    "status": sample["status"],
                    "duration_text": sample["timing"]["duration_text"],
                    "memory_mode": sample["memory"]["mode"],
                    "has_checkpoint": sample["checkpoint"]["exists"],
                    "warnings": sample["warnings"],
                }
            )
        except (OSError, ValueError) as exc:
            catalog["samples"].append(
                {
                    "sample_id": sample_dir.name,
                    "title": sample_dir.name,
                    "status": "partial",
                    "duration_text": "",
                    "memory_mode": "unknown",
                    "has_checkpoint": False,
                    "warnings": [f"Unable to load sample: {type(exc).__name__}: {exc}"],
                }
            )
    return catalog


def raw_artifact_text(view: Dict[str, Any], artifact_name: str) -> str:
    """Return preformatted JSON or a readable missing/parse error message."""

    if artifact_name not in VIEWABLE_ARTIFACTS:
        return f"Unsupported artifact: {artifact_name}"
    return _string(
        _as_dict(view.get("raw_artifacts")).get(artifact_name),
        f"Artifact not found: {artifact_name}",
    )


def _read_jsonl(path: Path, allowed_root: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    values: List[Dict[str, Any]] = []
    errors: List[str] = []
    if not _is_within(path, allowed_root) or not path.is_file():
        return values, errors
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return values, [f"{type(exc).__name__}: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: {exc}")
            continue
        if isinstance(value, dict):
            values.append(value)
    return values, errors


def load_evolution(evolution_dir: str) -> Dict[str, Any]:
    """Load a ReviewHarness evolution run without importing its runtime."""

    root = Path(evolution_dir).expanduser().resolve()
    result: Dict[str, Any] = {
        "evolution_dir": str(root),
        "available": False,
        "error": None,
        "manifest": {},
        "lineage": [],
        "scores": [],
        "versions": {},
        "best_version_id": None,
    }
    manifest_artifact = safe_read_json(root / "manifest.json", allowed_root=root)
    if not manifest_artifact["exists"]:
        result["error"] = f"Evolution manifest not found: {root / 'manifest.json'}"
        return result
    if not manifest_artifact["valid"]:
        result["error"] = manifest_artifact["error"]
        return result

    lineage, lineage_errors = _read_jsonl(root / "lineage.jsonl", root)
    scores, score_errors = _read_jsonl(root / "scores.jsonl", root)
    recorded_manifest = _as_dict(manifest_artifact["data"])
    manifest = dict(recorded_manifest)
    normalised_scores = []
    for value in scores:
        item = dict(value)
        run_status = _string(_as_dict(item.get("run")).get("status"))
        if "eligible_for_best" in item:
            eligible = item.get("eligible_for_best") is True
        else:
            # Compatibility guard for runs produced before failure semantics were
            # persisted. A non-zero subprocess exit can never be an evaluated
            # Harness candidate, regardless of the recorded manifest status.
            eligible = run_status == "ok" and isinstance(
                item.get("score"), (int, float)
            )
        item["eligible_for_best"] = eligible
        item["round_status"] = "evaluated" if eligible else "failed"
        if not eligible:
            item["score"] = None
        normalised_scores.append(item)
    scores = normalised_scores

    valid_scores = [item for item in scores if item["eligible_for_best"]]
    failed_scores = [item for item in scores if not item["eligible_for_best"]]
    requested_rounds = int(manifest.get("rounds", len(scores)) or 0)
    derived_status = (
        "complete"
        if not failed_scores and len(valid_scores) == requested_rounds
        else ("partial" if valid_scores else "failed")
    )
    if manifest.get("status") != derived_status:
        manifest["recorded_status"] = manifest.get("status")
    manifest["status"] = derived_status
    manifest["attempted_rounds"] = len(scores)
    manifest["completed_rounds"] = len(valid_scores)
    if failed_scores and not manifest.get("terminal_failure"):
        failed = failed_scores[0]
        failure_list = _as_list(failed.get("failures"))
        first_failure = _as_dict(failure_list[0]) if failure_list else {}
        manifest["terminal_failure"] = {
            "round": failed.get("round"),
            "version_id": failed.get("version_id"),
            "run_status": _string(_as_dict(failed.get("run")).get("status")),
            "returncode": _as_dict(failed.get("run")).get("returncode"),
            "reason": _string(first_failure.get("details"), "Review run failed."),
            "failures": failure_list,
        }
    if valid_scores:
        best_record = max(valid_scores, key=lambda item: float(item.get("score", 0.0)))
        manifest["best_version_id"] = best_record.get("version_id")
        manifest["best_score"] = best_record.get("score")
    else:
        manifest["best_version_id"] = None
        manifest["best_score"] = None
        manifest["best_harness_path"] = None
    score_by_version = {
        str(item.get("version_id")): item for item in scores if item.get("version_id")
    }
    versions = {}
    for lineage_item in lineage:
        version_id = _string(lineage_item.get("version_id"))
        if not version_id:
            continue
        version_root = root / "versions" / version_id
        mutation = safe_read_json(version_root / "mutation.json", allowed_root=root)
        evaluation = safe_read_json(version_root / "evaluation.json", allowed_root=root)
        round_record = safe_read_json(version_root / "round.json", allowed_root=root)
        trace_artifact = _empty_artifact(version_root / "call_traces.json")
        round_data = _as_dict(round_record.get("data")) if round_record.get("valid") else {}
        round_number = round_data.get("round")
        if round_number is not None:
            matches = sorted(
                (root / "rounds").glob(f"round_{int(round_number):03d}_{version_id}/call_traces.json")
            )
            if matches:
                trace_artifact = safe_read_json(matches[0], allowed_root=root)
        versions[version_id] = {
            "version_id": version_id,
            "parent_version_id": lineage_item.get("parent_version_id"),
            "score": score_by_version.get(version_id, {}).get(
                "score", lineage_item.get("score", 0.0)
            ),
            "round": lineage_item.get("round"),
            "change_reason": lineage_item.get("change_reason", ""),
            "mutation": _as_dict(mutation.get("data")) if mutation.get("valid") else {},
            "evaluation": _as_dict(evaluation.get("data")) if evaluation.get("valid") else {},
            "round_record": round_data,
            "call_traces": _as_dict(trace_artifact.get("data")) if trace_artifact.get("valid") else {},
        }
    result.update(
        {
            "available": True,
            "manifest": manifest,
            "lineage": lineage,
            "scores": scores,
            "versions": versions,
            "best_version_id": manifest.get("best_version_id"),
            "warnings": lineage_errors
            + score_errors
            + (
                [
                    "Recorded manifest status was inconsistent with round run results; "
                    "the viewer is showing the derived status."
                ]
                if manifest.get("recorded_status") is not None
                else []
            ),
        }
    )
    return result


def evolution_version_detail(view: Dict[str, Any], version_id: str) -> Dict[str, Any]:
    versions = _as_dict(view.get("versions"))
    if version_id in versions:
        return _as_dict(versions[version_id])
    return {}
