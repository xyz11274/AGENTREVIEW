"""Generate and audit the lineage-expanded effectiveness run matrix."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentreview.reviewer_identity import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    META_REVIEWER_NAME,
    REVIEWER_SPECS,
    load_reviewer_suite,
)

from .activation import ActivationPending, require_activation
from .arms import arm_identities, program_arm_identities
from .prepare import DESIGN_VERSION, iter_jsonl, load_config, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path("/data0/xyj/miniconda3/envs/env_shengao/bin/python")


def _generation_assets(
    config: Mapping[str, Any], *, include_programs: bool = True
) -> dict[str, Path]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    assets = {
        "paper_context_manifest": output_root / "paper_context_manifest.json",
        "frozen_s2_pool": Path(str(config.get("frozen_s2_pool") or ""))
        .expanduser()
        .resolve(),
        "frozen_s2_pool_manifest": Path(
            str(config.get("frozen_s2_pool_manifest") or "")
        )
        .expanduser()
        .resolve(),
    }
    if include_programs:
        assets["program_manifest"] = output_root / "programs" / "program_manifest.json"
    return assets


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
        str(Path(config["frozen_s2_pool"]).expanduser().resolve()),
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


def _memory_embedding_args(config: Mapping[str, Any]) -> list[str]:
    identity = dict(config.get("memory_embedding") or {})
    mode = str(identity.get("mode") or "")
    adapter = str(identity.get("adapter") or "")
    model_name = str(identity.get("model_name") or "")
    revision = str(identity.get("revision") or "")
    fingerprint = str(identity.get("fingerprint") or "")
    frozen_ready = mode == "frozen" and all(
        (adapter, model_name, revision, fingerprint)
    )
    if frozen_ready:
        return [
            "--memory_embedding_mode",
            mode,
            "--memory_embedding_adapter",
            adapter,
            "--memory_embedding_model_name",
            model_name,
            "--memory_embedding_revision",
            revision,
        ]
    raise ValueError(
        "reportable screening requires a complete frozen memory_embedding identity"
    )


def historical_library_command(config: Mapping[str, Any]):
    """Legacy no-memory command retained for historical artifact inspection."""
    require_activation(
        config, "generation", _generation_assets(config, include_programs=False)
    )
    output_root = Path(config["output_root"]).expanduser().resolve()
    command = _base_command(
        config, output_root / "history_generation_no_memory", "memory_corpus"
    )
    command.extend(["--memory_mode", "none"])
    return command


def _replicate_seed(config: Mapping[str, Any], replicate: Any) -> int:
    seeds = dict(config["experiment"].get("replicate_run_seeds") or {})
    key = str(replicate)
    if key not in seeds:
        raise ValueError(f"Missing explicit run seed for replicate {replicate}")
    return int(seeds[key])


def historical_replay_commands(config: Mapping[str, Any]):
    """Fresh, Program-specific A-zone replay for every replicate and lineage."""
    require_activation(config, "generation", _generation_assets(config))
    output_root = Path(config["output_root"]).expanduser().resolve()
    commands = []
    for replicate in config["experiment"]["replicates"]:
        for identity in program_arm_identities(config):
            history_output = (
                output_root
                / "runs"
                / f"replicate_{replicate}"
                / identity.arm_id
                / "history"
            )
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
                    str(output_root / "programs" / identity.arm_id),
                    "--memory_source_output_dir",
                    str(history_output),
                ]
            )
            command.extend(_memory_embedding_args(config))
            commands.append(
                {
                    **identity.to_record(),
                    "replicate": int(replicate),
                    "output_dir": str(history_output),
                    "command": command,
                    "fresh_memory_state": True,
                    "experience_protocol": "review_then_write",
                    "execution_seam": "external_process_runner",
                }
            )
    return commands


def freeze_design(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    require_activation(
        config,
        "design_freeze",
        {
            "frozen_s2_pool": config.get("frozen_s2_pool") or "",
            "frozen_s2_pool_manifest": config.get("frozen_s2_pool_manifest") or "",
            "program_manifest": output_root / "programs" / "program_manifest.json",
        },
    )
    lock_path = output_root / "design_lock.json"
    project_sources = [
        PROJECT_ROOT / "effectiveness_screening" / "prepare.py",
        PROJECT_ROOT / "effectiveness_screening" / "arms.py",
        PROJECT_ROOT / "effectiveness_screening" / "memory_cache.py",
        PROJECT_ROOT / "effectiveness_screening" / "run_matrix.py",
        PROJECT_ROOT / "effectiveness_screening" / "evaluate_proxy.py",
        PROJECT_ROOT / "effectiveness_screening" / "summarize.py",
        PROJECT_ROOT / "run_single_paper_review_cli.py",
        PROJECT_ROOT / "agentreview" / "paper_review_player_md.py",
        PROJECT_ROOT / "agentreview" / "single_paper_review_roles.py",
        PROJECT_ROOT / "agentreview" / "reviewer_identity.py",
        PROJECT_ROOT / "REVIEW_Request.md",
    ]
    frozen_inputs = {
        "effectiveness_validation": output_root / "effectiveness_validation.jsonl",
        "memory_corpus": output_root / "memory_corpus.jsonl",
        "selection_audit": output_root / "selection_audit.json",
        "evaluation_references_proxy": output_root
        / "evaluation_references_proxy.jsonl",
        "program_manifest": output_root / "programs" / "program_manifest.json",
        "frozen_s2_pool": Path(config["frozen_s2_pool"]).expanduser().resolve(),
    }
    missing = [
        str(path)
        for path in [*project_sources, *frozen_inputs.values()]
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Cannot freeze design; required inputs are missing: " + ", ".join(missing)
        )
    coco_root = Path(config["external_roots"]["cocoreviewbench"])
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=coco_root, text=True
    ).strip()
    if head != config["metrics"]["cocoreviewbench_commit"]:
        raise RuntimeError(f"CoCoReviewBench commit mismatch: {head}")
    judge_prompt = coco_root / "src" / "evaluation" / "prompt" / "judge.txt"
    reviewer_suite = load_reviewer_suite()
    lock = {
        "design_version": config["design_version"],
        "status": "data, config, metrics, and gate frozen before generation",
        "metric_status": config["metric_status"],
        "config": config["_config_path"],
        "config_sha256": config["_config_sha256"],
        "reviewer_suite_identity_sha256": reviewer_suite.suite_identity_sha256,
        "reviewer_system_prompt_sha256": {
            reviewer.task_id: reviewer.system_prompt_sha256
            for reviewer in reviewer_suite.reviewers
        },
        "reviewer_identity_source_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
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
    require_activation(config, "generation", _generation_assets(config))
    output_root = Path(config["output_root"]).expanduser().resolve()
    commands = []
    for replicate in config["experiment"]["replicates"]:
        for identity in arm_identities(config):
            output_dir = (
                output_root
                / "runs"
                / f"replicate_{replicate}"
                / identity.arm_id
                / "holdout"
            )
            command = _base_command(config, output_dir, "effectiveness_validation")
            command.extend(
                [
                    "--replicate_id",
                    str(replicate),
                    "--run_seed",
                    str(_replicate_seed(config, replicate)),
                ]
            )
            command.append("--disable_memory_writeback")
            if identity.arm_kind == "no_memory":
                command.extend(["--memory_mode", "none"])
            else:
                history_output = (
                    output_root
                    / "runs"
                    / f"replicate_{replicate}"
                    / identity.arm_id
                    / "history"
                )
                command.extend(
                    [
                        "--memory_mode",
                        "mstar_program",
                        "--memory_program_dir",
                        str(output_root / "programs" / identity.arm_id),
                        "--memory_source_output_dir",
                        str(history_output),
                    ]
                )
                command.extend(_memory_embedding_args(config))
            commands.append(
                {
                    **identity.to_record(),
                    "replicate": int(replicate),
                    "output_dir": str(output_dir),
                    "command": command,
                    "execution_seam": "external_process_runner",
                }
            )
    return commands


def write_preregistered_matrix(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    required = {
        "paper_context_manifest": output_root / "paper_context_manifest.json",
        "program_manifest": output_root / "programs" / "program_manifest.json",
    }
    require_activation(
        config,
        "generation",
        {
            **_generation_assets(config),
            "effectiveness_selection": output_root / "effectiveness_validation.jsonl",
        },
    )
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
            "arm_schema_version": command["arm_schema_version"],
            "arm_id": command["arm_id"],
            "arm_kind": command["arm_kind"],
            "lineage": command["lineage"],
            "replicate": command["replicate"],
        }
        for paper in selection
        for command in commands
    ]
    manifest = {
        "design_version": DESIGN_VERSION,
        "metric_status": config["metric_status"],
        "execution_seam": "external_process_runner",
        "execution_scope": "commands are preregistered and audited by this module",
        "paper_count": len(selection),
        "arm_count": len(arm_identities(config)),
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


def audit_runs(config: Mapping[str, Any]) -> dict[str, Any]:
    require_activation(config, "generation", _generation_assets(config))
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
                f"target writeback present: {run_root / 'memory_library.jsonl'}"
            )
        actual_ids = set()
        for paper_id in sorted(expected_ids):
            paper_root = run_root / paper_id
            reviews_path = paper_root / "reviews.json"
            memory_path = paper_root / "memory.json"
            if not reviews_path.is_file() or not memory_path.is_file():
                failures.append(
                    f"missing output "
                    f"{command['replicate']}/{command['arm_id']}/{paper_id}"
                )
                continue
            reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
            if set(reviews) != expected_reviewers:
                failures.append(
                    f"reviewer mismatch "
                    f"{command['replicate']}/{command['arm_id']}/{paper_id}"
                )
                continue
            memory = json.loads(memory_path.read_text(encoding="utf-8"))
            bundle_path = Path(memory.get("paper_context_bundle") or "")
            if not bundle_path.is_file() or memory.get(
                "paper_context_bundle_sha256"
            ) != sha256_file(bundle_path):
                failures.append(
                    f"bundle provenance mismatch "
                    f"{command['replicate']}/{command['arm_id']}/{paper_id}"
                )
                continue
            actual_ids.add(paper_id)
            completed += 1
        if actual_ids != expected_ids:
            failures.append(
                f"paper set mismatch {command['replicate']}/{command['arm_id']}"
            )
    report = {
        "design_version": DESIGN_VERSION,
        "expected_complete_reviews": len(expected_ids)
        * len(arm_identities(config))
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
    try:
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
    except ActivationPending as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
