"""Load CoCo's official evaluator with its shipped left-out selection."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load official CoCo module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coco-root", required=True)
    parser.add_argument("--selection-output", required=True)
    parser.add_argument("official_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = Path(args.coco_root).expanduser().resolve()
    evaluation_root = root / "src" / "evaluation"
    previous_path = list(sys.path)
    sys.path.insert(0, str(evaluation_root))
    sys.path.insert(0, str(root))
    try:
        human = _load("_0_human_review", evaluation_root / "_0_human_review.py")
        sys.modules["_0_human_review"] = human
        selection = human.load_selection_from_left_out_file(
            root / "benchmark" / "benchmark_left_out_human.jsonl"
        )
        selection_path = Path(args.selection_output).expanduser().resolve()
        selection_path.write_text(
            json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        evaluator = _load(
            "_agentreview_official_coco_evaluation",
            evaluation_root / "_4_evaluation.py",
        )
        evaluator.benchmark_reviewer_selection_file = selection_path
        official = list(args.official_args)
        if official and official[0] == "--":
            official = official[1:]
        previous_argv = sys.argv
        sys.argv = [str(evaluation_root / "_4_evaluation.py"), *official]
        try:
            evaluator.main()
        finally:
            sys.argv = previous_argv
    finally:
        sys.path[:] = previous_path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
