"""External CoCo-only process boundary consuming saved Final Reviews."""

from __future__ import annotations

import fcntl
import math
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .coco_review_batch import validate_completed_coco_review_batch
from .io import (
    atomic_write_json,
    file_sha256,
    read_json,
    read_jsonl,
    value_sha256,
    write_immutable_jsonl,
)

COCO_RUNNER_ARTIFACT = "candidate_official_runner.json"
COCO_RUNNER_PROTOCOL = "coco-official-candidate-runner-v1"
COCO_SPLIT_ARTIFACT = "candidate_official_split.jsonl"
COCO_CLASSIFY_ARTIFACT = "candidate_official_classify.jsonl"
COCO_EVALUATION_ARTIFACT = "candidate_official_evaluation.json"
COCO_STAGE_PROTOCOL = "coco-official-candidate-stage-v1"
COCO_STAGE_ROOT = ".candidate_official_stages"
COCO_STAGE_CHECKPOINT = "stage.json"
EXTERNAL_COCO_IDENTITY_PROTOCOL = "research-v2-external-coco-identity-v1"
EXTERNAL_COCO_RUNNER_RELATIVE_PATH = Path("scripts/run_official_candidate.py")
COCO_EVALUATION_LOCK = ".agentreview-coco-evaluation.lock"
COCO_EVALUATION_ATTEMPT_ROOT = ".agentreview-coco-evaluation-attempts"
TABLE_1_COLUMNS = {
    "BLEU",
    "ROUGE-L",
    "BERT.",
    "Correct.",
    "Thoro.",
    "Ground.",
    "Verify.",
    "Clarity",
    "Average",
    "Paper.",
    "Complete.",
}
COCO_FORWARDED_ENV = (
    "COCO_SPLIT_BASE_URL",
    "COCO_SPLIT_MODEL",
    "COCO_SPLIT_MAX_WORKERS",
    "COCO_SPLIT_EFFORT",
    "COCO_SPLIT_CACHE_VERSION",
    "COCO_CLASSIFY_BASE_URL",
    "COCO_CLASSIFY_MODEL",
    "COCO_CLASSIFY_MAX_WORKERS",
    "COCO_CLASSIFY_EFFORT",
    "COCO_CLASSIFY_CACHE_VERSION",
    "COCO_RUN_EVALUATION",
    "COCO_EVALUATION_BENCHMARK_FILES",
    "COCO_EVALUATION_COMPLETE_HUMAN_CLASSIFY_FILE",
    "COCO_EVALUATION_MODEL",
    "COCO_EVALUATION_BASE_URL",
    "COCO_EVALUATION_API_KEY",
    "COCO_EVALUATION_MAX_WORKERS",
    "COCO_EVALUATION_EFFORT",
    "COCO_EVALUATION_CACHE_VERSION",
    "COCO_EVALUATION_CUDA_VISIBLE_DEVICES",
)


def _artifact_binding(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}


def _validate_contract_binding(
    binding: Any,
    *,
    expected_path: Path,
    label: str,
) -> None:
    if not isinstance(binding, dict):
        raise RuntimeError(f"External CoCo contract requires a {label} binding")
    bound_path = Path(str(binding.get("path") or "")).expanduser().resolve()
    if bound_path != expected_path or binding.get("sha256") != file_sha256(expected_path):
        raise RuntimeError(f"External CoCo contract has an invalid {label} binding")


def _git_head_and_status(root: Path) -> tuple[str, str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commit = head.stdout.strip()
    if (
        head.returncode != 0
        or status.returncode != 0
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        raise RuntimeError("External CoCo repository identity is unreadable")
    return commit, status.stdout.strip()


def _resolve_command_path(value: str, *, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _validate_external_coco_runtime(
    *,
    system: dict[str, Any],
    root: Path,
    command: list[str],
) -> tuple[dict[str, Any], list[str]]:
    external = system.get("external_evaluation")
    identity = external.get("coco_runner") if isinstance(external, dict) else None
    if not isinstance(identity, dict) or set(identity) != {
        "protocol",
        "repository_root",
        "git_commit",
        "runner",
    }:
        raise RuntimeError("Frozen System lacks its external CoCo runner identity")
    runner = identity.get("runner")
    expected_runner = (root / EXTERNAL_COCO_RUNNER_RELATIVE_PATH).resolve()
    commit, dirty = _git_head_and_status(root)
    if (
        identity.get("protocol") != EXTERNAL_COCO_IDENTITY_PROTOCOL
        or Path(str(identity.get("repository_root") or "")).expanduser().resolve()
        != root
        or commit != identity.get("git_commit")
        or dirty
        or not isinstance(runner, dict)
        or set(runner) != {"path", "sha256"}
        or Path(str(runner.get("path") or "")).expanduser().resolve()
        != expected_runner
        or not expected_runner.is_file()
        or runner.get("sha256") != file_sha256(expected_runner)
    ):
        raise RuntimeError("External CoCo runtime identity differs from Frozen System")
    normalized_command = list(command)
    if normalized_command and normalized_command[0] == "--":
        normalized_command = normalized_command[1:]
    if not normalized_command:
        raise ValueError("CoCo Evaluation requires an explicit external command")
    command_paths = [_resolve_command_path(normalized_command[0], root=root)]
    if len(normalized_command) > 1:
        command_paths.append(_resolve_command_path(normalized_command[1], root=root))
    if expected_runner not in command_paths:
        raise RuntimeError("CoCo command does not execute the frozen official runner")
    return identity, normalized_command


def _validate_stage_checkpoint_binding(
    *,
    output: Path,
    stage: str,
    binding: Any,
    runner: dict[str, Any],
    input_binding: dict[str, str],
    output_binding: dict[str, str],
) -> None:
    expected_path = (
        output / COCO_STAGE_ROOT / stage / COCO_STAGE_CHECKPOINT
    ).resolve()
    _validate_contract_binding(
        binding,
        expected_path=expected_path,
        label=f"{stage} stage checkpoint",
    )
    checkpoint = read_json(expected_path)
    body = (
        {name: value for name, value in checkpoint.items() if name != "checkpoint_id"}
        if isinstance(checkpoint, dict)
        else {}
    )
    settings = runner.get("settings")
    candidate_input = runner.get("candidate_input")
    expected = {
        "protocol": COCO_STAGE_PROTOCOL,
        "stage": stage,
        "candidate_input": candidate_input,
        "input_artifact": input_binding,
        "settings": settings.get(stage) if isinstance(settings, dict) else None,
        "output_filename": Path(output_binding["path"]).name,
        "output_sha256": output_binding["sha256"],
    }
    if (
        body != expected
        or checkpoint.get("checkpoint_id") != value_sha256(body)
    ):
        raise RuntimeError(f"External CoCo {stage} stage checkpoint differs")


def _validate_candidate_stage(
    path: Path,
    *,
    stage: str,
    expected_ids: set[str],
) -> None:
    if not path.is_file():
        raise RuntimeError(f"External CoCo process requires the {stage} artifact: {path}")
    try:
        records = read_jsonl(path)
    except Exception as exc:
        raise RuntimeError(f"External CoCo {stage} artifact is invalid: {path}") from exc
    ids = [str(value.get("id") or "") for value in records]
    if "" in ids or len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise RuntimeError(
            f"External CoCo {stage} artifact must contain the exact frozen paper set"
        )
    for value in records:
        if not isinstance(value.get("sentence_texts"), list) or not isinstance(
            value.get("opinions"), list
        ):
            raise RuntimeError(
                f"External CoCo {stage} artifact has an invalid processed record"
            )
        if stage == "classify" and not isinstance(value.get("category"), list):
            raise RuntimeError(
                "External CoCo classify artifact has an invalid category record"
            )


def _validate_evaluation_summary(path: Path, *, expected_count: int) -> None:
    try:
        value = read_json(path)
    except Exception as exc:
        raise RuntimeError(f"External CoCo evaluation artifact is invalid: {path}") from exc
    if not isinstance(value, dict) or set(value) != {
        "input_label",
        "overall_metrics",
        "category_breakdown",
        "table_1_summary",
    }:
        raise RuntimeError(f"External CoCo evaluation artifact is invalid: {path}")
    expected_label = Path(COCO_CLASSIFY_ARTIFACT).stem
    overall = value.get("overall_metrics")
    breakdown = value.get("category_breakdown")
    table = value.get("table_1_summary")
    if (
        value.get("input_label") != expected_label
        or not isinstance(overall, dict)
        or not {"full_text", "category_correct", "paper_level"}.issubset(overall)
        or not isinstance(breakdown, dict)
        or "category_correct" not in breakdown
        or not isinstance(table, dict)
        or set(table) != {
            "input_label",
            "absolute_scores",
            "paper_counts",
            "category_coverage",
        }
        or table.get("input_label") != expected_label
    ):
        raise RuntimeError("External CoCo evaluation summary is incomplete")
    scores = table.get("absolute_scores")
    counts = table.get("paper_counts")
    coverage = table.get("category_coverage")
    if (
        not isinstance(scores, dict)
        or set(scores) != TABLE_1_COLUMNS
        or not isinstance(counts, dict)
        or set(counts) != TABLE_1_COLUMNS
        or any(type(counts[name]) is not int for name in TABLE_1_COLUMNS)
        or any(counts[name] != expected_count for name in TABLE_1_COLUMNS)
        or any(
            isinstance(scores[name], bool)
            or not isinstance(scores[name], (int, float))
            or not math.isfinite(float(scores[name]))
            for name in TABLE_1_COLUMNS
        )
        or not isinstance(coverage, dict)
        or coverage.get("item_count") != expected_count
        or (coverage.get("metric_counts") or {}).get("Complete.") != expected_count
    ):
        raise RuntimeError(
            "External CoCo evaluation does not cover the exact frozen paper set"
        )
def _validate_coco_artifacts(
    output: Path,
    *,
    expected_ids: set[str],
    candidate_input: Path,
) -> dict[str, dict[str, str]]:
    runner_path = output / COCO_RUNNER_ARTIFACT
    if not runner_path.is_file():
        raise RuntimeError(
            f"External CoCo process requires its contract artifact: {runner_path}"
        )
    try:
        runner = read_json(runner_path)
    except Exception as exc:
        raise RuntimeError(f"External CoCo contract artifact is invalid: {runner_path}") from exc
    if (
        not isinstance(runner, dict)
        or runner.get("protocol") != COCO_RUNNER_PROTOCOL
        or runner.get("status") != "complete"
        or not isinstance(runner.get("evaluation_enabled"), bool)
    ):
        raise RuntimeError(f"External CoCo contract artifact is invalid: {runner_path}")
    _validate_contract_binding(
        runner.get("candidate_input"),
        expected_path=candidate_input,
        label="candidate input",
    )
    declared_artifacts = runner.get("artifacts")
    settings = runner.get("settings")
    stage_checkpoints = runner.get("stage_checkpoints")
    expected_stages = {"split", "classify"} | (
        {"evaluation"} if runner["evaluation_enabled"] else set()
    )
    if (
        not isinstance(declared_artifacts, dict)
        or set(declared_artifacts) != expected_stages
        or not isinstance(settings, dict)
        or set(settings) != {"split", "classify", "evaluation"}
        or not isinstance(stage_checkpoints, dict)
        or set(stage_checkpoints) != expected_stages
    ):
        raise RuntimeError("External CoCo contract requires artifact bindings")

    split_path = output / COCO_SPLIT_ARTIFACT
    classify_path = output / COCO_CLASSIFY_ARTIFACT
    _validate_candidate_stage(split_path, stage="split", expected_ids=expected_ids)
    _validate_candidate_stage(classify_path, stage="classify", expected_ids=expected_ids)
    _validate_contract_binding(
        declared_artifacts.get("split"),
        expected_path=split_path,
        label="split artifact",
    )
    _validate_contract_binding(
        declared_artifacts.get("classify"),
        expected_path=classify_path,
        label="classify artifact",
    )
    _validate_stage_checkpoint_binding(
        output=output,
        stage="split",
        binding=stage_checkpoints["split"],
        runner=runner,
        input_binding=runner["candidate_input"],
        output_binding=declared_artifacts["split"],
    )
    _validate_stage_checkpoint_binding(
        output=output,
        stage="classify",
        binding=stage_checkpoints["classify"],
        runner=runner,
        input_binding=declared_artifacts["split"],
        output_binding=declared_artifacts["classify"],
    )
    artifacts = {
        "runner": _artifact_binding(runner_path),
        "split": _artifact_binding(split_path),
        "classify": _artifact_binding(classify_path),
    }
    if runner["evaluation_enabled"]:
        evaluation_path = output / COCO_EVALUATION_ARTIFACT
        if not evaluation_path.is_file():
            raise RuntimeError(
                "External CoCo process enabled evaluation and requires the evaluation "
                f"artifact: {evaluation_path}"
            )
        _validate_evaluation_summary(
            evaluation_path,
            expected_count=len(expected_ids),
        )
        _validate_contract_binding(
            declared_artifacts.get("evaluation"),
            expected_path=evaluation_path,
            label="evaluation artifact",
        )
        _validate_stage_checkpoint_binding(
            output=output,
            stage="evaluation",
            binding=stage_checkpoints["evaluation"],
            runner=runner,
            input_binding=declared_artifacts["classify"],
            output_binding=declared_artifacts["evaluation"],
        )
        artifacts["evaluation"] = _artifact_binding(evaluation_path)
    return artifacts


def prepare_coco_input(
    final_review_paths: list[str | Path],
    output_path: str | Path,
) -> Path:
    values = []
    seen: set[str] = set()
    for path in final_review_paths:
        review = read_json(path)
        paper_id = str(review.get("paper_id") or "")
        text = str(review.get("review_text") or "")
        if not paper_id or not text:
            raise ValueError("CoCo input requires saved Final Review id/text")
        if paper_id in seen:
            raise ValueError(f"Duplicate Final Review paper_id: {paper_id}")
        seen.add(paper_id)
        values.append({"id": paper_id, "text": text})
    return write_immutable_jsonl(output_path, values)


@contextmanager
def _exclusive_evaluation_lock(output_root: Path) -> Iterator[None]:
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / COCO_EVALUATION_LOCK
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Another AgentReview CoCo evaluation owns this output root: {output_root}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _scoring_source_binding(
    source: dict[str, Any],
    *,
    final_mode: str,
) -> dict[str, Any]:
    arm = (source.get("arms") or {}).get(final_mode)
    if not isinstance(arm, dict):
        raise ValueError(f"Winner batch lacks the {final_mode} Final Review arm")
    return {
        "source_id": source["source_id"],
        "winner_run_id": source["run_id"],
        "final_mode": final_mode,
        "final_run_id": arm["run_id"],
        "count": arm["count"],
        "paper_ids_sha256": arm["paper_ids_sha256"],
        "final_review_ids_sha256": arm["final_review_ids_sha256"],
        "source_review_ids_sha256": arm["source_review_ids_sha256"],
        "records_sha256": arm["records_sha256"],
        "files_sha256": arm["files_sha256"],
    }


def _validate_output_isolation(*, output: Path, winner_root: Path) -> None:
    protected = (
        "reviews",
        "final_reviews",
        "evolution",
        "memories",
        "data",
        "s2",
        "frozen",
        "runs",
    )
    for name in protected:
        path = (winner_root / name).resolve()
        if output == path or output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError(
                "CoCo scoring output must be isolated from Review/Evolution artifacts: "
                f"{output}"
            )


def _persist_failed_attempt(output: Path, report: dict[str, Any]) -> Path:
    attempt = {
        **report,
        "attempt_id": value_sha256(report),
    }
    path = (
        output
        / COCO_EVALUATION_ATTEMPT_ROOT
        / f"{attempt['attempt_id']}.json"
    )
    atomic_write_json(path, attempt)
    return path


def _validate_existing_complete_report(
    *,
    path: Path,
    system: dict[str, Any],
    frozen_system_path: str | Path,
    manifest_binding: dict[str, Any],
    source_binding: dict[str, Any],
    external_coco_identity: dict[str, Any],
    expected_command: list[str],
    expected_ids: set[str],
    candidate_input: Path,
    output: Path,
) -> dict[str, Any]:
    report = read_json(path)
    if (
        not isinstance(report, dict)
        or report.get("protocol") != "research-v2-external-coco-v1"
        or report.get("status") != "complete"
        or report.get("returncode") != 0
        or report.get("frozen_system_id") != system["system_id"]
        or report.get("frozen_system_sha256") != file_sha256(frozen_system_path)
        or report.get("coco_1300_manifest_sha256") != manifest_binding["sha256"]
        or report.get("scoring_source") != source_binding
        or report.get("candidate_input") != str(candidate_input)
        or report.get("candidate_input_sha256") != file_sha256(candidate_input)
        or report.get("external_coco_identity") != external_coco_identity
        or report.get("command") != expected_command
        or report.get("artifact_validation_error") != ""
        or report.get("runtime_validation_error") != ""
    ):
        raise RuntimeError("Completed AgentReview CoCo report identity differs")
    artifacts = _validate_coco_artifacts(
        output,
        expected_ids=expected_ids,
        candidate_input=candidate_input,
    )
    if report.get("artifacts") != artifacts:
        raise RuntimeError("Completed AgentReview CoCo artifact bindings differ")
    return report


def run_coco_evaluation(
    *,
    frozen_system_path: str | Path,
    winner_output_root: str | Path,
    winner_run_id: str,
    final_mode: str,
    coco_root: str | Path,
    output_root: str | Path,
    command: list[str],
) -> dict[str, Any]:
    """Launch an explicit external command; Review/Evolution modules never import CoCo."""
    if final_mode not in {"meta", "concat"}:
        raise ValueError("CoCo Evaluation final_mode must be meta or concat")
    system = read_json(frozen_system_path)
    if not system.get("system_id"):
        raise ValueError("CoCo Evaluation requires a Frozen System Bundle")
    root = Path(coco_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    winner_root = Path(winner_output_root).expanduser().resolve()
    _validate_output_isolation(output=output, winner_root=winner_root)
    manifest_binding = (
        (system.get("external_evaluation") or {}).get("coco_1300_manifest") or {}
    )
    manifest_path = str(manifest_binding.get("path") or "")
    if not manifest_path or file_sha256(manifest_path) != manifest_binding.get("sha256"):
        raise ValueError("Frozen System has no valid CoCo-1300 manifest binding")
    manifest = read_json(manifest_path)
    manifest_records = list(manifest.get("records") or [])
    expected_ids = {str(value.get("paper_id") or "") for value in manifest_records}
    if "" in expected_ids or len(expected_ids) != len(manifest_records):
        raise ValueError("Frozen CoCo-1300 manifest contains missing or duplicate paper IDs")
    with _exclusive_evaluation_lock(output):
        source = validate_completed_coco_review_batch(
            frozen_system_path=frozen_system_path,
            coco_manifest_path=manifest_path,
            output_root=winner_root,
            run_id=winner_run_id,
        )
        arm = source["arms"][final_mode]
        final_review_paths = list(arm["paths"])
        source_binding = _scoring_source_binding(source, final_mode=final_mode)
        source_ids = {
            str(read_json(path).get("paper_id") or "")
            for path in final_review_paths
        }
        if source_ids != expected_ids or len(final_review_paths) != len(expected_ids):
            raise ValueError(
                "Validated winner arm differs from the exact frozen CoCo paper set"
            )
        external_coco_identity, normalized_command = _validate_external_coco_runtime(
            system=system,
            root=root,
            command=command,
        )
        input_path = prepare_coco_input(
            final_review_paths,
            output / "candidate_official_input.jsonl",
        )
        confirmed_source = validate_completed_coco_review_batch(
            frozen_system_path=frozen_system_path,
            coco_manifest_path=manifest_path,
            output_root=winner_root,
            run_id=winner_run_id,
        )
        if confirmed_source.get("source_id") != source.get("source_id"):
            raise RuntimeError("Winner scoring source changed while sealing candidate input")
        complete_report_path = output / "run.json"
        if complete_report_path.is_file():
            return _validate_existing_complete_report(
                path=complete_report_path,
                system=system,
                frozen_system_path=frozen_system_path,
                manifest_binding=manifest_binding,
                source_binding=source_binding,
                external_coco_identity=external_coco_identity,
                expected_command=normalized_command,
                expected_ids=expected_ids,
                candidate_input=input_path,
                output=output,
            )
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "COCO_CANDIDATE_INPUT": str(input_path),
            "COCO_OUTPUT_ROOT": str(output),
        }
        environment.update(
            {name: os.environ[name] for name in COCO_FORWARDED_ENV if name in os.environ}
        )
        completed = subprocess.run(
            normalized_command,
            cwd=str(root),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        artifacts: dict[str, dict[str, str]] = {}
        artifact_error: RuntimeError | None = None
        runtime_error: RuntimeError | None = None
        if completed.returncode == 0:
            try:
                artifacts = _validate_coco_artifacts(
                    output,
                    expected_ids=expected_ids,
                    candidate_input=input_path,
                )
            except Exception as exc:
                artifact_error = (
                    exc
                    if isinstance(exc, RuntimeError)
                    else RuntimeError("External CoCo artifact validation failed")
                )
        try:
            confirmed_identity, _ = _validate_external_coco_runtime(
                system=system,
                root=root,
                command=normalized_command,
            )
            if confirmed_identity != external_coco_identity:
                raise RuntimeError("External CoCo runtime identity changed during scoring")
            final_source = validate_completed_coco_review_batch(
                frozen_system_path=frozen_system_path,
                coco_manifest_path=manifest_path,
                output_root=winner_root,
                run_id=winner_run_id,
            )
            if final_source.get("source_id") != source.get("source_id"):
                raise RuntimeError("Winner scoring source changed during scoring")
        except Exception as exc:
            runtime_error = (
                exc
                if isinstance(exc, RuntimeError)
                else RuntimeError("External CoCo runtime validation failed")
            )
        report = {
            "protocol": "research-v2-external-coco-v1",
            "frozen_system_id": system["system_id"],
            "frozen_system_sha256": file_sha256(frozen_system_path),
            "coco_1300_manifest_sha256": manifest_binding["sha256"],
            "scoring_source": source_binding,
            "candidate_input": str(input_path),
            "candidate_input_sha256": file_sha256(input_path),
            "external_coco_identity": external_coco_identity,
            "command": normalized_command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "status": (
                "complete"
                if (
                    completed.returncode == 0
                    and artifact_error is None
                    and runtime_error is None
                )
                else "failed"
            ),
            "artifacts": artifacts,
            "artifact_validation_error": str(artifact_error or ""),
            "runtime_validation_error": str(runtime_error or ""),
        }
        if report["status"] == "complete":
            atomic_write_json(complete_report_path, report)
        else:
            _persist_failed_attempt(output, report)
        if completed.returncode != 0:
            raise RuntimeError(f"External CoCo process failed: {completed.returncode}")
        if artifact_error is not None:
            raise artifact_error
        if runtime_error is not None:
            raise runtime_error
        return report
