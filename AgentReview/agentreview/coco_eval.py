"""Independent adapter from candidate_reviews.jsonl to official CoCo evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List

from agentreview.reviewer_runtime.artifacts import iter_candidate_reviews
from agentreview.reviewer_runtime.tasks import ALL_TASKS


PROTOCOL_FILES = {
    "src/evaluation/_0_human_review.py": "311d878ce82f39145011bba8b4f5e9f4ce11674412757df1bc4025cbfcef6328",
    "src/evaluation/_2_split.py": "0889e6a747709837fcfcc240503ed45dd7f9eda5a4bbfd2b56e3696072d7ac0b",
    "src/evaluation/_3_classify.py": "e62c64545d470675ec3fba6606f37efee371ccf33e23baeb4655d66f99cbc036",
    "src/evaluation/_4_evaluation.py": "d0c10721e1dc7cf82a80cbb705feeab808749fcb1dca0acf25f6c669f24a4c9b",
    "src/benchmark/prompt_registry.py": "15da4e63732705e84c1893e37188aa95c5d8719d4e1c9eab0a90b29435f37893",
    "src/evaluation/prompt/judge.txt": "305e68ae15805911fb44f2166fabcb95e08acf664546636036616ec1bbb294c5",
    "src/utils.py": "79e584d6ece0686e4fc486553ed95f3f93caba52c20a2154116bad00d825ddcb",
    "benchmark/benchmark_left_out_human.jsonl": "72de5f6b652896e915027f8531b48da75339c8cb79c1b4a82302c600d5e015e0",
    "benchmark/benchmark_all_human_classify.jsonl": "63a65fd04395d2ea7115677916688d2aadcb38d0785cd3391a906d1cfd0cb9d3",
}

BENCHMARK_FILES = {
    "benchmark_ICLR_2017.jsonl": "417857d8c9d98d6dbd6e9be85e31ed2bba87620a36b951ee6f7e693abc992dca",
    "benchmark_ICLR_2018.jsonl": "9c409b2a4d07f5958835eca69af2b5497355ebe7ad1f40262cec6fba46c92ff0",
    "benchmark_ICLR_2019.jsonl": "6cd22974813da9c8facc9f077242e5329864a07e86842d83fd60a7f48a195b57",
    "benchmark_ICLR_2020.jsonl": "9b77c22f3925db810210adbe225009dfa12dbe12746f0fca2c5c526a3ee86727",
    "benchmark_ICLR_2021.jsonl": "1a6402384b2843bf2e3680a1900abbe11b955b875a7987ee93a5a68e269cd29a",
    "benchmark_ICLR_2022.jsonl": "b774f195d75007df9293ceda43320900d243d5a9260de9cbd430b9abeb65ff85",
    "benchmark_ICLR_2023.jsonl": "4ab22ea2d2086849294397c7a0f6a5947b8c15cf73a89204981a9ea3833a07d6",
    "benchmark_ICLR_2024.jsonl": "aef119d13e311a9264a6b4dc80a9c5cf08df5b2438fb771e4105777387d9e432",
    "benchmark_ICLR_2025.jsonl": "2e927b85104afae43b0124dd8b2ae071fddd581d9a6e6750b3ca612f973e49eb",
    "benchmark_NeurIPS_2021.jsonl": "1df1c9c78d620bfbb6150e46b162c000ae061344597dcb3c36ce0399774d05e8",
    "benchmark_NeurIPS_2022.jsonl": "cfc831d4132aec252112c0853b8997cd16c9eae4ac3adf92d155af09a1610942",
    "benchmark_NeurIPS_2023.jsonl": "e7c71d09a2267b7d318aa9fc4522c3af8d762cf05045d97ef211fd18549731cc",
    "benchmark_NeurIPS_2024.jsonl": "34ee78508dee2aba246ec1af68f8e6c4377bfe53372a4a01ec8371ebb24ac911",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_coco_protocol(coco_root: str, benchmark_files: Iterable[Path]) -> Dict:
    root = Path(coco_root).expanduser().resolve()
    observed = {}
    for relative, expected in PROTOCOL_FILES.items():
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Pinned CoCo protocol file is missing: {path}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"CoCo protocol checksum mismatch: {relative}; "
                f"expected={expected}, actual={actual}"
            )
        observed[relative] = actual
    benchmark_observed = {}
    for path in benchmark_files:
        expected = BENCHMARK_FILES.get(path.name)
        if expected is None:
            raise ValueError(f"Unpinned CoCo benchmark file: {path}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"CoCo human-reference checksum mismatch: {path.name}"
            )
        benchmark_observed[path.name] = actual
    head = "unknown"
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        head = completed.stdout.strip()
    return {
        "protocol": "CoCoReviewBench-official-split-classify-evaluation",
        "actual_git_head": head,
        "protocol_files": observed,
        "benchmark_files": benchmark_observed,
    }


def candidate_rows(
    candidate_path: str, task_id: str, run_identity: str = ""
) -> List[Dict[str, str]]:
    selected = [
        value
        for value in iter_candidate_reviews(candidate_path)
        if value.task_id == task_id
        and (not run_identity or value.run_identity == run_identity)
    ]
    if not selected:
        raise ValueError(f"No candidate reviews selected for task_id={task_id}")
    duplicates = sorted(
        paper_id
        for paper_id in {value.paper_id for value in selected}
        if sum(value.paper_id == paper_id for value in selected) > 1
    )
    if duplicates:
        raise ValueError(
            "CoCo requires one review per paper after task/run filtering; "
            f"duplicates={duplicates[:20]}"
        )
    return [
        {"id": value.paper_id, "text": value.review_text}
        for value in selected
    ]


def _write_jsonl(path: Path, values: Iterable[Dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _benchmark_paths(args, root: Path) -> List[Path]:
    names = args.benchmark_file or list(BENCHMARK_FILES)
    paths = []
    for value in names:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / "benchmark" / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"CoCo benchmark file is missing: {path}")
        paths.append(path)
    return paths


def _run(command: List[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=str(cwd), env=os.environ.copy(), check=True)


def run(args) -> int:
    root = Path(args.coco_root).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        raise FileExistsError(f"CoCo output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    benchmark_paths = _benchmark_paths(args, root)
    official_benchmark_paths = []
    for path in benchmark_paths:
        try:
            official_benchmark_paths.append(path.relative_to(root).as_posix())
        except ValueError as exc:
            raise ValueError(
                f"CoCo benchmark must be inside coco_root: {path}"
            ) from exc
    protocol = verify_coco_protocol(str(root), benchmark_paths)
    protocol.update(
        {
            "judge_backend": args.judge_model,
            "judge_backend_policy": "single-backend-no-fallback",
            "candidate_input": str(Path(args.input).expanduser().resolve()),
            "task_id": args.task_id,
            "run_identity_filter": args.run_identity,
        }
    )
    rows = candidate_rows(args.input, args.task_id, args.run_identity)
    candidate_input = output / "candidate_official_input.jsonl"
    split_output = output / "candidate_split.jsonl"
    classify_output = output / "candidate_classify.jsonl"
    evaluation_output = output / "evaluation.json"
    selection_output = output / "official_reviewer_selection.json"
    protocol_output = output / "protocol_identity.json"
    _write_jsonl(candidate_input, rows)
    protocol_output.write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    _run(
        [
            sys.executable,
            str(root / "src/evaluation/_2_split.py"),
            "--input_jsonl",
            str(candidate_input),
            "--output_jsonl",
            str(split_output),
            "--model",
            args.split_model,
            "--effort",
            args.split_effort,
            "--max_workers",
            str(args.split_workers),
            "--base_url",
            args.split_base_url,
        ],
        cwd=root,
    )
    _run(
        [
            sys.executable,
            str(root / "src/evaluation/_3_classify.py"),
            "--input_jsonl",
            str(split_output),
            "--output_jsonl",
            str(classify_output),
            "--model",
            args.classify_model,
            "--effort",
            args.classify_effort,
            "--max_workers",
            str(args.classify_workers),
            "--base_url",
            args.classify_base_url,
        ],
        cwd=root,
    )
    runner = Path(__file__).resolve().with_name("_coco_official_runner.py")
    evaluation_command = [
        sys.executable,
        str(runner),
        "--coco-root",
        str(root),
        "--selection-output",
        str(selection_output),
        "--",
        "--input_file",
        str(classify_output),
        "--output_file",
        str(evaluation_output),
        "--benchmark_file",
        *official_benchmark_paths,
        "--complete_human_classify_file",
        str(root / "benchmark/benchmark_all_human_classify.jsonl"),
        "--model",
        args.judge_model,
        "--base_url",
        args.judge_base_url,
        "--max_workers",
        str(args.judge_workers),
        "--cache_version",
        args.cache_version,
        "--effort",
        args.judge_effort,
    ]
    for enabled, flag in (
        (args.category_level_eval, "--category_level_eval"),
        (args.category_incorrect_eval, "--category_incorrect_eval"),
        (args.calc_old_metrics, "--calc_old_metrics"),
        (args.paper_level_eval, "--paper_level_eval"),
    ):
        if enabled:
            evaluation_command.append(flag)
    _run(evaluation_command, cwd=root)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Official CoCo evaluation adapter")
    parser.add_argument("--input", required=True, help="candidate_reviews.jsonl")
    parser.add_argument("--task-id", choices=ALL_TASKS, required=True)
    parser.add_argument("--run-identity", default="")
    parser.add_argument("--coco-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--benchmark-file", action="append")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--split-model", default="Qwen3-8B-Split")
    parser.add_argument("--split-base-url", default="http://localhost:13099/v1")
    parser.add_argument("--split-effort", default="medium")
    parser.add_argument("--split-workers", type=int, default=64)
    parser.add_argument("--classify-model", default="Qwen3-8B-Classify")
    parser.add_argument("--classify-base-url", default="http://localhost:13098/v1")
    parser.add_argument("--classify-effort", default="medium")
    parser.add_argument("--classify-workers", type=int, default=128)
    parser.add_argument("--judge-model", default="qwen3.8-27b-fp8")
    parser.add_argument("--judge-base-url", default="http://localhost:13097/v1")
    parser.add_argument("--judge-effort", default="medium")
    parser.add_argument("--judge-workers", type=int, default=128)
    parser.add_argument("--cache-version", default="v1")
    parser.add_argument("--category-level-eval", action="store_true")
    parser.add_argument("--category-incorrect-eval", action="store_true")
    parser.add_argument("--calc-old-metrics", action="store_true")
    parser.add_argument("--paper-level-eval", action="store_true")
    return parser


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
