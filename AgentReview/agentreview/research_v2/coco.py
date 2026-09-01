"""External CoCo-only process boundary consuming saved Final Reviews."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .io import (
    atomic_write_json,
    file_sha256,
    read_json,
    read_jsonl,
    write_derived_jsonl,
)

COCO_RUNNER_ARTIFACT = "candidate_official_runner.json"
COCO_RUNNER_PROTOCOL = "coco-official-candidate-runner-v1"
COCO_SPLIT_ARTIFACT = "candidate_official_split.jsonl"
COCO_CLASSIFY_ARTIFACT = "candidate_official_classify.jsonl"
COCO_EVALUATION_ARTIFACT = "candidate_official_evaluation.json"
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
    if not isinstance(declared_artifacts, dict):
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
        try:
            evaluation = read_json(evaluation_path)
        except Exception as exc:
            raise RuntimeError(
                f"External CoCo evaluation artifact is invalid: {evaluation_path}"
            ) from exc
        if not isinstance(evaluation, dict):
            raise RuntimeError(
                f"External CoCo evaluation artifact is invalid: {evaluation_path}"
            )
        _validate_contract_binding(
            declared_artifacts.get("evaluation"),
            expected_path=evaluation_path,
            label="evaluation artifact",
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
    return write_derived_jsonl(output_path, values)


def run_coco_evaluation(
    *,
    final_review_paths: list[str | Path],
    frozen_system_path: str | Path,
    coco_root: str | Path,
    output_root: str | Path,
    command: list[str],
) -> dict[str, Any]:
    """Launch an explicit external command; Review/Evolution modules never import CoCo."""
    system = read_json(frozen_system_path)
    if not system.get("system_id"):
        raise ValueError("CoCo Evaluation requires a Frozen System Bundle")
    root = Path(coco_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
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
    supplied_ids = {
        str(read_json(path).get("paper_id") or "") for path in final_review_paths
    }
    if supplied_ids != expected_ids or len(final_review_paths) != len(expected_ids):
        missing = sorted(expected_ids - supplied_ids)
        extra = sorted(supplied_ids - expected_ids)
        raise ValueError(
            "CoCo Evaluation requires the exact frozen paper set: "
            f"expected={len(expected_ids)}, supplied={len(final_review_paths)}, "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    input_path = prepare_coco_input(
        final_review_paths,
        output / "candidate_official_input.jsonl",
    )
    if not command:
        raise ValueError("CoCo Evaluation requires an explicit external command")
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
        command,
        cwd=str(root),
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    artifacts: dict[str, dict[str, str]] = {}
    artifact_error: RuntimeError | None = None
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
    report = {
        "protocol": "research-v2-external-coco-v1",
        "frozen_system_id": system["system_id"],
        "frozen_system_sha256": file_sha256(frozen_system_path),
        "coco_1300_manifest_sha256": manifest_binding["sha256"],
        "candidate_input": str(input_path),
        "candidate_input_sha256": file_sha256(input_path),
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": (
            "complete"
            if completed.returncode == 0 and artifact_error is None
            else "failed"
        ),
        "artifacts": artifacts,
        "artifact_validation_error": str(artifact_error or ""),
    }
    atomic_write_json(output / "run.json", report)
    if completed.returncode != 0:
        raise RuntimeError(f"External CoCo process failed: {completed.returncode}")
    if artifact_error is not None:
        raise artifact_error
    return report
