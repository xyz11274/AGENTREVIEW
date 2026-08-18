"""Thin bridge to CoCoReviewBench's official human-reference program.

AgentReview owns temporal allocation and artifact access control. It does not
reimplement CoCo's reviewer selection, correctness filtering, opinion-group
resolution, or category-reference construction.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CoCoReviewBench module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_official_coco_human_references(
    coco_root: str,
    benchmark_records: Dict[str, Dict[str, Any]],
    benchmark_files: Iterable[Path],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Run CoCo's official ``load_human_review_data`` on selected papers.

    CoCo's shipped ``benchmark_left_out_human.jsonl`` already records the
    selected held-out reviewer. The current checkout lacks the optional JSON
    selection sidecar expected by ``_4_evaluation.left_out_one``, so this bridge
    feeds the equivalent shipped selection into the official function without
    reproducing its reference-selection algorithm.
    """
    root = Path(coco_root).expanduser().resolve()
    evaluation_root = root / "src" / "evaluation"
    human_module_path = evaluation_root / "_0_human_review.py"
    evaluation_module_path = evaluation_root / "_4_evaluation.py"
    left_out_path = root / "benchmark" / "benchmark_left_out_human.jsonl"
    for required in (
        human_module_path,
        evaluation_module_path,
        left_out_path,
    ):
        if not required.is_file():
            raise FileNotFoundError(
                f"Required CoCoReviewBench reference artifact missing: {required}"
            )

    resolved_benchmark_files = [Path(path).expanduser().resolve() for path in benchmark_files]
    for path in resolved_benchmark_files:
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"Invalid CoCo benchmark source path: {path}")

    module_suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    human_module = _load_module(
        f"_agentreview_coco_human_{module_suffix}", human_module_path
    )
    previous_human_module = sys.modules.get("_0_human_review")
    previous_eval_path = list(sys.path)
    sys.modules["_0_human_review"] = human_module
    sys.path.insert(0, str(evaluation_root))
    try:
        try:
            evaluation_module = _load_module(
                f"_agentreview_coco_evaluation_{module_suffix}",
                evaluation_module_path,
            )
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Loading CoCoReviewBench's official evaluator requires its "
                "declared dependencies; install CoCoReviewBench/requirements.txt"
            ) from exc
    finally:
        sys.path[:] = previous_eval_path
        if previous_human_module is None:
            sys.modules.pop("_0_human_review", None)
        else:
            sys.modules["_0_human_review"] = previous_human_module

    selection = human_module.load_selection_from_left_out_file(left_out_path)
    missing_selection = sorted(set(benchmark_records) - set(selection))
    if missing_selection:
        raise ValueError(
            "CoCo left-out artifact lacks reviewer selections for "
            f"{len(missing_selection)} requested papers; examples: "
            f"{missing_selection[:10]}"
        )
    selected_reviewers = {
        paper_id: selection[paper_id] for paper_id in benchmark_records
    }
    reviewer_texts = human_module.collect_reviewer_texts(benchmark_records)
    invalid_selection = sorted(
        paper_id
        for paper_id, reviewer in selected_reviewers.items()
        if reviewer not in reviewer_texts.get(paper_id, {})
    )
    if invalid_selection:
        raise ValueError(
            "CoCo left-out artifact selects unavailable reviewers for "
            f"{len(invalid_selection)} requested papers; examples: "
            f"{invalid_selection[:10]}"
        )

    official_left_out_one = evaluation_module.left_out_one
    evaluation_module.left_out_one = lambda _files: dict(selected_reviewers)
    try:
        references = evaluation_module.load_human_review_data(
            benchmark_records, resolved_benchmark_files
        )
    finally:
        evaluation_module.left_out_one = official_left_out_one

    return references, {
        "implementation": "CoCoReviewBench.src.evaluation._4_evaluation.load_human_review_data",
        "implementation_path": str(evaluation_module_path),
        "implementation_sha256": _sha256(evaluation_module_path),
        "selection_loader_path": str(human_module_path),
        "selection_loader_sha256": _sha256(human_module_path),
        "left_out_selection": str(left_out_path),
        "left_out_selection_sha256": _sha256(left_out_path),
        "requested_paper_count": len(benchmark_records),
        "benchmark_files": [str(path) for path in resolved_benchmark_files],
        "benchmark_file_sha256": {
            str(path): _sha256(path) for path in resolved_benchmark_files
        },
    }
