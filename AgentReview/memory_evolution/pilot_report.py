"""Summarize seed-versus-evolved validation scores for a pilot suite."""

import argparse
import json
from pathlib import Path

from .tasks import ALL_TASKS


def _read_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _best(rows, *, evolved: bool):
    candidates = [
        row
        for row in rows
        if bool(int(row.get("iteration", 0)) > 0) == evolved
        and row.get("quality_passed")
        and isinstance(row.get("static_score"), (int, float))
    ]
    return max(candidates, key=lambda row: row["static_score"]) if candidates else None


def summarize(run_root: str, seed: int):
    root = Path(run_root).expanduser().resolve()
    result = []
    for task_id in ALL_TASKS:
        task_run = root / task_id / f"seed_{seed}"
        scores_path = task_run / "scores.jsonl"
        report_path = task_run / "best_eval_report.json"
        if not scores_path.is_file():
            continue
        rows = _read_jsonl(scores_path)
        seed_best = _best(rows, evolved=False)
        evolved_best = _best(rows, evolved=True)
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else {}
        )
        seed_score = seed_best["static_score"] if seed_best else None
        evolved_score = evolved_best["static_score"] if evolved_best else None
        result.append(
            {
                "task": task_id,
                "seed_best": seed_score,
                "evolved_best": evolved_score,
                "delta": (
                    evolved_score - seed_score
                    if seed_score is not None and evolved_score is not None
                    else None
                ),
                "selected": report.get("program_id", ""),
                "selection_mean": report.get("selection_mean"),
                "mutations": sum(int(row.get("iteration", 0)) > 0 for row in rows),
            }
        )
    return result


def _score(value):
    return "n/a" if value is None else f"{value:.6f}"


def main():
    parser = argparse.ArgumentParser(
        description="Compare initial seed and evolved candidate scores."
    )
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rows = summarize(args.run_root, args.seed)
    if not rows:
        raise SystemExit(f"No task scores found under {args.run_root}")
    print("| task | best seed | best evolved | delta | selected | mutations |")
    print("|---|---:|---:|---:|---|---:|")
    for row in rows:
        print(
            f"| {row['task']} | {_score(row['seed_best'])} | "
            f"{_score(row['evolved_best'])} | {_score(row['delta'])} | "
            f"{row['selected'] or 'n/a'} | {row['mutations']} |"
        )


if __name__ == "__main__":
    main()
