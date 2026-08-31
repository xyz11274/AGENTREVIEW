"""Read-only adapters for displaying AgentReview output artifacts.

This module deliberately depends only on the Python standard library.  It does
not import or call the review pipeline, which keeps the result viewer isolated
from review generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _empty_artifact(path: Path) -> dict[str, Any]:
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


def safe_read_json(path: Path, allowed_root: Path | None = None) -> dict[str, Any]:
    """Read one JSON file without raising errors to the viewer.

    The returned dictionary always has the same keys.  When ``allowed_root`` is
    provided, symlinks or paths resolving outside that root are rejected.
    """

    path = Path(path)
    result = _empty_artifact(path)
    if allowed_root is not None and not _is_within(path, Path(allowed_root)):
        result["error"] = (
            "Artifact path resolves outside the configured output directory."
        )
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


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _load_artifacts(sample_dir: Path, review_root: Path) -> dict[str, dict[str, Any]]:
    return {
        name: safe_read_json(sample_dir / name, allowed_root=review_root)
        for name in VIEWABLE_ARTIFACTS
    }


def _artifact_dict(artifacts: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    artifact = artifacts.get(name, {})
    return _as_dict(artifact.get("data")) if artifact.get("valid") else {}


def _normalise_messages(history: dict[str, Any]) -> list[dict[str, Any]]:
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
    timing: dict[str, Any], history: dict[str, Any]
) -> tuple[dict[str, Any], str]:
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
    artifacts: dict[str, dict[str, Any]], reviews: dict[str, Any]
) -> tuple[str, list[str]]:
    warnings: list[str] = []
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
    return (
        "complete" if complete_files and not missing_reviewers else "partial",
        warnings,
    )


def load_sample(output_dir: str, sample_id: str) -> dict[str, Any]:
    """Load and normalise all viewable artifacts for one sample."""

    review_root = resolve_review_root(output_dir)
    sample_dir = (review_root / str(sample_id)).resolve()
    if (
        not _is_within(sample_dir, review_root)
        or sample_dir.parent != review_root.resolve()
    ):
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

    paper_md_path = _string(memory.get("paper_md_path")) or timing["paper_md_path"]
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


def scan_output_dir(output_dir: str) -> dict[str, Any]:
    """Scan sample directories and return a fault-tolerant catalog."""

    review_root = resolve_review_root(output_dir)
    catalog: dict[str, Any] = {
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


def raw_artifact_text(view: dict[str, Any], artifact_name: str) -> str:
    """Return preformatted JSON or a readable missing/parse error message."""

    if artifact_name not in VIEWABLE_ARTIFACTS:
        return f"Unsupported artifact: {artifact_name}"
    return _string(
        _as_dict(view.get("raw_artifacts")).get(artifact_name),
        f"Artifact not found: {artifact_name}",
    )
