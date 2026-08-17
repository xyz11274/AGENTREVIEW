"""Generate and audit commands for the frozen three-arm, two-replicate matrix."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping

from agentreview.single_paper_review_roles import META_REVIEWER_NAME, REVIEWER_SPECS
from agentreview.single_paper_review_roles import EXPECTED_REVIEW_REQUEST_SHA256

from .prepare import DESIGN_VERSION, iter_jsonl, load_config, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path("/data0/xyj/miniconda3/envs/env_shengao/bin/python")


def _base_command(config: Mapping[str, Any], output_dir: Path, split: str):
    generation = config["generation"]
    output_root = Path(config["output_root"]).expanduser().resolve()
    return [
        str(PYTHON),
        str(PROJECT_ROOT / "run_single_paper_review_cli.py"),
        "--paper_context_manifest",
        str(output_root / "paper_context_manifest.json"),
        "--paper_context_split",
        split,
        "--s2_retrieval_pool",
        str(output_root / "frozen_related_work.jsonl"),
        "--output_dir",
        str(output_dir),
        "--model_name",
        str(generation["model"]),
        "--review_temperature",
        str(generation["temperature"]),
        "--reviewer_workers",
        "5",
        "--review_max_tokens",
        str(generation["review_max_tokens"]),
        "--review_retry_max_tokens",
        str(generation["review_retry_max_tokens"]),
        "--reviewer_reasoning_effort",
        str(generation["reviewer_reasoning_effort"]),
        "--paper_context_max_chars",
        str(generation.get("paper_context_max_chars", 60000)),
        "--meta_review_max_tokens",
        str(generation["meta_review_max_tokens"]),
        "--overwrite",
    ]


def historical_library_command(config: Mapping[str, Any]):
    """Legacy no-memory command retained for historical artifact inspection."""
    output_root = Path(config["output_root"]).expanduser().resolve()
    command = _base_command(
        config, output_root / "history_generation_no_memory", "memory_corpus"
    )
    command.extend(["--memory_mode", "none"])
    return command


def _program_arms(config: Mapping[str, Any]):
    experiment = config["experiment"]
    lineages = list(experiment.get("lineages") or [])
    arms = list(experiment["arms"])
    if not lineages:
        return [(arm, arm, None) for arm in arms]
    output = []
    for arm in arms:
        if arm == "no_memory":
            output.append((arm, arm, None))
            continue
        for lineage in lineages:
            if arm == "seed":
                label = f"seed_{lineage}"
            elif arm == "evolved":
                label = f"evolved_from_{lineage}"
            else:
                raise ValueError(f"Unknown experiment arm: {arm}")
            output.append((label, arm, lineage))
    return output


def _replicate_seed(config: Mapping[str, Any], replicate: Any) -> int:
    seeds = dict(config["experiment"].get("replicate_run_seeds") or {})
    key = str(replicate)
    if key not in seeds:
        raise ValueError(f"Missing explicit run seed for replicate {replicate}")
    return int(seeds[key])


def historical_replay_commands(config: Mapping[str, Any]):
    """Fresh, Program-specific A-zone replay for every replicate and lineage."""
    output_root = Path(config["output_root"]).expanduser().resolve()
    commands = []
    for replicate in config["experiment"]["replicates"]:
        for label, arm, lineage in _program_arms(config):
            if arm == "no_memory":
                continue
            history_output = output_root / "runs" / f"replicate_{replicate}" / label / "history"
            command = _base_command(config, history_output, "memory_corpus")
            command.extend(
                [
                    "--replicate_id",
                    str(replicate),
                    "--run_seed",
                    str(_replicate_seed(config, replicate)),
                    "--strict_history_resume",
                    "--memory_mode",
                    "mstar_program",
                    "--memory_program_dir",
                    str(output_root / "programs" / label),
                    "--memory_source_output_dir",
                    str(history_output),
                    "--memory_embedding_mode",
                    "deterministic_hash",
                ]
            )
            commands.append(
                {
                    "arm": arm,
                    "arm_label": label,
                    "lineage": lineage,
                    "replicate": int(replicate),
                    "output_dir": str(history_output),
                    "command": command,
                    "fresh_memory_state": True,
                    "experience_protocol": "review_then_write",
                }
            )
    return commands


def freeze_design(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    lock_path = output_root / "design_lock.json"
    project_sources = [
        PROJECT_ROOT / "effectiveness_screening" / "prepare.py",
        PROJECT_ROOT / "effectiveness_screening" / "memory_cache.py",
        PROJECT_ROOT / "effectiveness_screening" / "run_matrix.py",
        PROJECT_ROOT / "effectiveness_screening" / "evaluate_proxy.py",
        PROJECT_ROOT / "effectiveness_screening" / "summarize.py",
        PROJECT_ROOT / "run_single_paper_review_cli.py",
        PROJECT_ROOT / "agentreview" / "paper_review_player_md.py",
        PROJECT_ROOT / "agentreview" / "single_paper_review_roles.py",
        PROJECT_ROOT / "REVIEW_Request.md",
    ]
    frozen_inputs = {
        "effectiveness_validation": output_root
        / "effectiveness_validation.jsonl",
        "memory_corpus": output_root / "memory_corpus.jsonl",
        "selection_audit": output_root / "selection_audit.json",
        "evaluation_references_proxy": output_root
        / "evaluation_references_proxy.jsonl",
        "program_manifest": output_root
        / "programs"
        / "program_manifest.json",
    }
    missing = [
        str(path)
        for path in [*project_sources, *frozen_inputs.values()]
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Cannot freeze design; required inputs are missing: "
            + ", ".join(missing)
        )
    coco_root = Path(config["external_roots"]["cocoreviewbench"])
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=coco_root, text=True
    ).strip()
    if head != config["metrics"]["cocoreviewbench_commit"]:
        raise RuntimeError(f"CoCoReviewBench commit mismatch: {head}")
    judge_prompt = coco_root / "src" / "evaluation" / "prompt" / "judge.txt"
    lock = {
        "design_version": config["design_version"],
        "status": "data, config, metrics, and gate frozen before generation",
        "metric_status": config["metric_status"],
        "config": config["_config_path"],
        "config_sha256": config["_config_sha256"],
        "review_request_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        "cocoreviewbench_commit": head,
        "official_judge_prompt_sha256": sha256_file(judge_prompt),
        "frozen_input_sha256": {
            name: sha256_file(path) for name, path in frozen_inputs.items()
        },
        "implementation_sha256": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in project_sources
        },
        "pending_post_lock_artifacts": [
            "frozen_related_work.jsonl",
            "paper_context_manifest.json",
            "program-specific historical replay per lineage/replicate",
            "lineage-specific seed/evolved promotions",
            "blind target reviews",
            "proxy evaluator outputs",
            "blind evaluator outputs",
        ],
    }
    if lock_path.is_file():
        current = json.loads(lock_path.read_text(encoding="utf-8"))
        if current != lock:
            raise RuntimeError("Existing design lock differs; refusing overwrite")
        return current
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return lock


def matrix_commands(config: Mapping[str, Any]):
    output_root = Path(config["output_root"]).expanduser().resolve()
    commands = []
    for replicate in config["experiment"]["replicates"]:
        for label, arm, lineage in _program_arms(config):
            output_dir = output_root / "runs" / f"replicate_{replicate}" / label / "holdout"
            command = _base_command(
                config, output_dir, "effectiveness_validation"
            )
            command.extend(
                [
                    "--replicate_id",
                    str(replicate),
                    "--run_seed",
                    str(_replicate_seed(config, replicate)),
                ]
            )
            command.append("--disable_memory_writeback")
            if arm == "no_memory":
                command.extend(["--memory_mode", "none"])
            else:
                history_output = output_root / "runs" / f"replicate_{replicate}" / label / "history"
                command.extend(
                    [
                        "--memory_mode",
                        "mstar_program",
                        "--memory_program_dir",
                        str(output_root / "programs" / label),
                        "--memory_source_output_dir",
                        str(history_output),
                        "--memory_embedding_mode",
                        "deterministic_hash",
                    ]
                )
            commands.append(
                {
                    "arm": arm,
                    "arm_label": label,
                    "lineage": lineage,
                    "replicate": int(replicate),
                    "output_dir": str(output_dir),
                    "command": command,
                }
            )
    return commands


def write_preregistered_matrix(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    required = {
        "paper_context_manifest": output_root / "paper_context_manifest.json",
        "program_manifest": output_root / "programs" / "program_manifest.json",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Cannot preregister the run matrix before all frozen inputs exist: "
            + ", ".join(missing)
        )
    selection = list(iter_jsonl(output_root / "effectiveness_validation.jsonl"))
    commands = matrix_commands(config)
    cells = [
        {
            "paper_id": paper["paper_id"],
            "work_family": paper["work_family"],
            "arm": command["arm"],
            "arm_label": command["arm_label"],
            "lineage": command["lineage"],
            "replicate": command["replicate"],
        }
        for paper in selection
        for command in commands
    ]
    manifest = {
        "design_version": DESIGN_VERSION,
        "metric_status": config["metric_status"],
        "paper_count": len(selection),
        "arm_count": len(_program_arms(config)),
        "replicate_count": len(config["experiment"]["replicates"]),
        "expected_complete_reviews": len(cells),
        "generation": config["generation"],
        "commands": [
            {
                **{key: value for key, value in item.items() if key != "command"},
                "command": shlex.join(item["command"]),
            }
            for item in commands
        ],
        "historical_replay_commands": [
            {
                **{key: value for key, value in item.items() if key != "command"},
                "command": shlex.join(item["command"]),
            }
            for item in historical_replay_commands(config)
        ],
        "replicate_memory_protocol": "fresh empty state per replicate; no cross-replicate cache",
        "replicate_seed_provenance": {
            "run_seeds": dict(config["experiment"].get("replicate_run_seeds") or {}),
            "seed_control_available": False,
            "note": "backend does not expose deterministic generation seed",
        },
        "cells": cells,
        "screening_gate": config["screening_gate"],
        "frozen_input_sha256": {
            name: sha256_file(path) for name, path in required.items()
        },
    }
    path = output_root / "preregistered_matrix.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def audit_runs(config: Mapping[str, Any]) -> Dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    expected_ids = {
        str(value["paper_id"])
        for value in iter_jsonl(output_root / "effectiveness_validation.jsonl")
    }
    expected_reviewers = {
        *(spec["name"] for spec in REVIEWER_SPECS),
        META_REVIEWER_NAME,
    }
    failures = []
    completed = 0
    for command in matrix_commands(config):
        run_root = Path(command["output_dir"]) / "single_paper_review"
        if (run_root / "memory_library.jsonl").exists():
            failures.append(
                f"forbidden target writeback: {run_root / 'memory_library.jsonl'}"
            )
        actual_ids = set()
        for paper_id in sorted(expected_ids):
            paper_root = run_root / paper_id
            reviews_path = paper_root / "reviews.json"
            memory_path = paper_root / "memory.json"
            if not reviews_path.is_file() or not memory_path.is_file():
                failures.append(
                    f"missing output {command['replicate']}/{command['arm']}/{paper_id}"
                )
                continue
            reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
            if set(reviews) != expected_reviewers:
                failures.append(
                    f"reviewer mismatch {command['replicate']}/{command['arm']}/{paper_id}"
                )
                continue
            memory = json.loads(memory_path.read_text(encoding="utf-8"))
            bundle_path = Path(memory.get("paper_context_bundle") or "")
            if (
                not bundle_path.is_file()
                or memory.get("paper_context_bundle_sha256")
                != sha256_file(bundle_path)
            ):
                failures.append(
                    f"bundle provenance mismatch "
                    f"{command['replicate']}/{command['arm']}/{paper_id}"
                )
                continue
            actual_ids.add(paper_id)
            completed += 1
        if actual_ids != expected_ids:
            failures.append(
                f"paper set mismatch {command['replicate']}/{command['arm']}"
            )
    report = {
        "design_version": DESIGN_VERSION,
        "expected_complete_reviews": len(expected_ids)
        * len(_program_arms(config))
        * len(config["experiment"]["replicates"]),
        "completed_valid_reviews": completed,
        "failures": failures,
        "passed": not failures,
    }
    path = output_root / "run_audit.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "freeze-design",
            "history-command",
            "history-replay-commands",
            "matrix",
            "audit-runs",
        ),
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    if args.command == "freeze-design":
        result = freeze_design(config)
    elif args.command == "history-command":
        print(shlex.join(historical_library_command(config)))
        return
    elif args.command == "history-replay-commands":
        print(
            json.dumps(
                [
                    {**item, "command": shlex.join(item["command"])}
                    for item in historical_replay_commands(config)
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    elif args.command == "matrix":
        result = write_preregistered_matrix(config)
    else:
        result = audit_runs(config)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
