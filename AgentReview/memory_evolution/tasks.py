"""Fixed task definitions for the six independent M★ searches."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from agentreview.single_paper_review_roles import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    REVIEWER_SPECS,
    load_review_request_sections,
)


SPECIALIST_TASKS: Tuple[str, ...] = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
META_TASK = "META"
ALL_TASKS: Tuple[str, ...] = SPECIALIST_TASKS + (META_TASK,)
MSTAR_PAPER_SHA256 = (
    "52f01b5b30582e6b2ecd3004c9861e1350129c0579d1914dea4e7e56e19c49b5"
)
COCOREVIEWBENCH_PAPER_SHA256 = (
    "06c29b7879bb40be15d5611ceca4cf2cde6634e1fc9a3fa89d26ed2e34e00da5"
)
COCOREVIEWBENCH_COMMIT = "2580687ee5d4ad2ac4b9730afb8f1dfda22e40db"


@dataclass(frozen=True)
class ReviewTask:
    task_id: str
    reviewer_name: str
    frozen_prompt: str
    category_prefixes: Tuple[str, ...]


def load_review_tasks() -> Dict[str, ReviewTask]:
    """Load the immutable reviewer prompts after verifying REVIEW_Request.md."""
    sections = load_review_request_sections()
    tasks: Dict[str, ReviewTask] = {}
    for spec in REVIEWER_SPECS:
        task_id = spec["dimension"]
        tasks[task_id] = ReviewTask(
            task_id=task_id,
            reviewer_name=spec["name"],
            frozen_prompt=sections[task_id],
            category_prefixes=(f"{task_id}-",),
        )
    tasks[META_TASK] = ReviewTask(
        task_id=META_TASK,
        reviewer_name="Final Meta-Reviewer",
        frozen_prompt=(
            "Synthesize all five specialist reviews. Resolve disagreements, give an "
            "overall score and confidence, and return one justified decision."
        ),
        category_prefixes=tuple(f"{task_id}-" for task_id in SPECIALIST_TASKS),
    )
    return tasks


def resolve_program_path(program_root: str, task_id: str) -> Path:
    """Resolve one task's winner without accepting ambiguous shared programs."""
    if task_id not in ALL_TASKS:
        raise ValueError(f"Unknown review task: {task_id}")
    root = Path(program_root).expanduser().resolve()
    candidates = (
        root / task_id / "best_program.py",
        root / task_id.lower() / "best_program.py",
        root / f"{task_id}.py",
    )
    matches = [path for path in candidates if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {task_id} MemoryProgram under {root}; "
            f"found {[str(path) for path in matches]}"
        )
    return matches[0]


def verify_promoted_program(
    program_root: str,
    task_id: str,
    program_path: Path,
    embedding_model_name: str,
    embedding_revision: str,
) -> None:
    """Refuse deployment when promotion or embedding provenance has drifted."""
    root = Path(program_root).expanduser().resolve()
    manifest_path = root / (
        "meta_manifest.json" if task_id == META_TASK else "suite_manifest.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Promotion manifest is required: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    promoted = manifest.get("promoted") or {}
    entry = promoted if task_id == META_TASK else promoted.get(task_id) or {}
    expected_hash = str(entry.get("program_sha256") or "")
    actual_hash = hashlib.sha256(program_path.read_bytes()).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise RuntimeError(
            f"{task_id} promoted program checksum mismatch: "
            f"expected={expected_hash or '<missing>'}, actual={actual_hash}"
        )
    source_run = Path(str(entry.get("source_run") or ""))
    config_path = source_run / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Promoted source config is required: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected_model = str(config.get("embedding_model_name") or "")
    expected_revision = str(config.get("embedding_revision") or "")
    if (
        expected_model != str(embedding_model_name)
        or expected_revision != str(embedding_revision)
    ):
        raise RuntimeError(
            f"{task_id} embedding provenance mismatch: evolved with "
            f"{expected_model}@{expected_revision}, deployed with "
            f"{embedding_model_name}@{embedding_revision}"
        )


def experiment_provenance() -> Dict[str, object]:
    return {
        "architecture": "Mstar executable MemoryProgram",
        "tasks": list(ALL_TASKS),
        "review_request_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        "mstar_paper_sha256": MSTAR_PAPER_SHA256,
        "cocoreviewbench_paper_sha256": COCOREVIEWBENCH_PAPER_SHA256,
        "cocoreviewbench_reference_commit": COCOREVIEWBENCH_COMMIT,
        "cocoreviewbench_role": "metric/taxonomy reference only",
        "program_scope": ["schema", "write_logic", "read_logic", "instructions"],
    }
