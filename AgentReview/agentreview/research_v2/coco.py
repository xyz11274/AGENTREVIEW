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

from .coco_scoring_spec import (
    FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS,
    FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS,
    FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM,
    FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS,
    FORMAL_EVALUATION_MAX_COMPLETION_TOKENS,
    FORMAL_EVALUATION_PROMPT_OVERHEAD_TOKENS,
    FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM,
    FORMAL_EVALUATION_PROXY_URL,
    FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS,
    FORMAL_SPLIT_MAX_COMPLETION_TOKENS,
    FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM,
)
from .io import (
    atomic_write_json,
    canonical_json_bytes,
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
TABLE_1_FULL_COUNT_COLUMNS = {
    "BLEU",
    "ROUGE-L",
    "BERT.",
    "Paper.",
    "Complete.",
}
TABLE_1_ELIGIBLE_COUNT_COLUMNS = {
    "Correct.",
    "Thoro.",
    "Ground.",
    "Clarity",
    "Average",
}
TABLE_1_VERIFY_COLUMN = "Verify."
COCO_API_KEY_ENV = "COCO_EVALUATION_API_KEY"
COCO_FIXED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
COCO_RUN_REPORT_FIELDS = frozenset(
    {
        "protocol",
        "frozen_system_id",
        "frozen_system_sha256",
        "coco_1300_manifest_sha256",
        "scoring_source",
        "pre_score_seal",
        "scoring_spec",
        "service_runtime",
        "candidate_input",
        "candidate_input_sha256",
        "external_coco_identity",
        "command",
        "environment",
        "returncode",
        "status",
        "artifacts",
        "artifact_validation_error",
        "runtime_validation_error",
    }
)


def _fixed_output_path(
    root: Path,
    relative: str | Path,
    *,
    label: str,
    require_file: bool = False,
) -> Path:
    """Return one fixed lexical descendant and reject symlink redirection."""
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"{label} must be a fixed relative output path")
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError(f"{label} output root must be canonical and non-symlinked")
    target = root / relative_path
    if target.parent.resolve() != target.parent or target.is_symlink():
        raise ValueError(f"{label} must not use a symbolic link")
    if require_file and not target.is_file():
        raise FileNotFoundError(f"{label} is missing: {target}")
    return target


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
    if (
        expected_path.is_symlink()
        or expected_path.parent.resolve() != expected_path.parent
        or binding.get("path") != str(expected_path)
        or binding.get("sha256") != file_sha256(expected_path)
    ):
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
    if not normalized_command or any(
        not isinstance(argument, str) for argument in normalized_command
    ):
        raise ValueError("CoCo Evaluation requires an explicit external command")
    if normalized_command.count(str(expected_runner)) != 1:
        raise RuntimeError("CoCo command does not execute the frozen official runner")
    return identity, normalized_command


def _validate_stage_checkpoint_binding(
    *,
    output: Path,
    stage: str,
    binding: Any,
    runner: dict[str, Any],
    scoring_spec_binding: dict[str, str],
    input_binding: dict[str, str],
    output_binding: dict[str, str],
) -> None:
    expected_path = (
        _fixed_output_path(
            output,
            Path(COCO_STAGE_ROOT) / stage / COCO_STAGE_CHECKPOINT,
            label=f"{stage} stage checkpoint",
            require_file=True,
        )
    )
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
        "scoring_spec": scoring_spec_binding,
        "settings": settings.get(stage) if isinstance(settings, dict) else None,
        "output_filename": Path(output_binding["path"]).name,
        "output_sha256": output_binding["sha256"],
    }
    if (
        canonical_json_bytes(body) != canonical_json_bytes(expected)
        or checkpoint.get("checkpoint_id") != value_sha256(body)
    ):
        raise RuntimeError(f"External CoCo {stage} stage checkpoint differs")


def _validate_candidate_stage(
    path: Path,
    *,
    stage: str,
    expected_ids: set[str],
) -> list[dict[str, Any]]:
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
    return records


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
    counts_have_exact_types = isinstance(counts, dict) and all(
        type(counts.get(name)) is int for name in TABLE_1_COLUMNS
    )
    full_counts_match = counts_have_exact_types and all(
        counts[name] == expected_count for name in TABLE_1_FULL_COUNT_COLUMNS
    )
    eligible_count = counts.get("Correct.") if counts_have_exact_types else None
    eligible_counts_match = (
        type(eligible_count) is int
        and 1 <= eligible_count <= expected_count
        and all(
            counts[name] == eligible_count
            for name in TABLE_1_ELIGIBLE_COUNT_COLUMNS
        )
    )
    verify_count = counts.get(TABLE_1_VERIFY_COLUMN) if counts_have_exact_types else None
    verify_count_matches = (
        eligible_counts_match
        and type(verify_count) is int
        and 0 <= verify_count <= eligible_count
    )

    def finite_score(name: str) -> bool:
        score = scores.get(name) if isinstance(scores, dict) else None
        return (
            not isinstance(score, bool)
            and isinstance(score, (int, float))
            and math.isfinite(float(score))
        )

    verify_score = scores.get(TABLE_1_VERIFY_COLUMN) if isinstance(scores, dict) else None
    verify_score_matches = (
        verify_count_matches
        and (
            (verify_count == 0 and verify_score is None)
            or (verify_count > 0 and finite_score(TABLE_1_VERIFY_COLUMN))
        )
    )
    if (
        not isinstance(scores, dict)
        or set(scores) != TABLE_1_COLUMNS
        or not isinstance(counts, dict)
        or set(counts) != TABLE_1_COLUMNS
        or not full_counts_match
        or not eligible_counts_match
        or not verify_score_matches
        or any(
            not finite_score(name)
            for name in TABLE_1_COLUMNS - {TABLE_1_VERIFY_COLUMN}
        )
        or not isinstance(coverage, dict)
        or type(coverage.get("item_count")) is not int
        or coverage.get("item_count") != expected_count
        or not isinstance(coverage.get("metric_counts"), dict)
        or type(coverage["metric_counts"].get("Complete.")) is not int
        or coverage["metric_counts"].get("Complete.") != expected_count
    ):
        raise RuntimeError(
            "External CoCo evaluation does not cover the exact frozen paper set"
        )


def _validated_split_prompt_preflight(
    value: Any,
    *,
    expected_count: int,
) -> dict[str, Any]:
    fields = {
        "algorithm",
        "max_model_len",
        "max_completion_tokens",
        "enable_thinking",
        "candidate_count",
        "max_prompt_tokens",
        "max_total_tokens",
        "prompt_token_counts_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RuntimeError("External CoCo split prompt preflight fields differ")
    max_prompt_tokens = value.get("max_prompt_tokens")
    max_total_tokens = value.get("max_total_tokens")
    if (
        value.get("algorithm") != FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM
        or type(value.get("max_model_len")) is not int
        or value["max_model_len"] != FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS
        or type(value.get("max_completion_tokens")) is not int
        or value["max_completion_tokens"] != FORMAL_SPLIT_MAX_COMPLETION_TOKENS
        or value.get("enable_thinking") is not True
        or type(value.get("candidate_count")) is not int
        or value["candidate_count"] < 1
        or value["candidate_count"] != expected_count
        or type(max_prompt_tokens) is not int
        or max_prompt_tokens < 1
        or type(max_total_tokens) is not int
        or max_total_tokens
        != max_prompt_tokens + FORMAL_SPLIT_MAX_COMPLETION_TOKENS
        or max_total_tokens > FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS
        or not isinstance(value.get("prompt_token_counts_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["prompt_token_counts_sha256"])
        is None
    ):
        raise RuntimeError("External CoCo split prompt preflight identity differs")
    return dict(value)


def _validated_classify_prompt_preflight(
    value: Any,
    *,
    expected_count: int,
    expected_opinion_count: int | None = None,
) -> dict[str, Any]:
    fields = {
        "algorithm",
        "max_model_len",
        "max_completion_tokens",
        "enable_thinking",
        "candidate_count",
        "opinion_count",
        "max_prompt_tokens",
        "max_total_tokens",
        "prompt_token_counts_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RuntimeError("External CoCo classify prompt preflight fields differ")
    opinion_count = value.get("opinion_count")
    max_prompt_tokens = value.get("max_prompt_tokens")
    max_total_tokens = value.get("max_total_tokens")
    if (
        value.get("algorithm") != FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM
        or type(value.get("max_model_len")) is not int
        or value["max_model_len"] != FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS
        or type(value.get("max_completion_tokens")) is not int
        or value["max_completion_tokens"]
        != FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        or value.get("enable_thinking") is not True
        or type(value.get("candidate_count")) is not int
        or value["candidate_count"] < 1
        or value["candidate_count"] != expected_count
        or type(opinion_count) is not int
        or opinion_count < expected_count
        or (
            expected_opinion_count is not None
            and opinion_count != expected_opinion_count
        )
        or type(max_prompt_tokens) is not int
        or max_prompt_tokens < 1
        or type(max_total_tokens) is not int
        or max_total_tokens
        != max_prompt_tokens + FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        or max_total_tokens > FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS
        or not isinstance(value.get("prompt_token_counts_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["prompt_token_counts_sha256"])
        is None
    ):
        raise RuntimeError("External CoCo classify prompt preflight identity differs")
    return dict(value)


def _validate_coco_artifacts(
    output: Path,
    *,
    expected_ids: set[str],
    candidate_input: Path,
    expected_scoring_spec: dict[str, str] | None = None,
    require_evaluation: bool = False,
) -> dict[str, dict[str, str]]:
    runner_path = _fixed_output_path(
        output,
        COCO_RUNNER_ARTIFACT,
        label="External CoCo runner artifact",
    )
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
    scoring_spec = runner.get("scoring_spec")
    if (
        not isinstance(scoring_spec, dict)
        or set(scoring_spec) != {"path", "sha256", "spec_id"}
        or any(not isinstance(scoring_spec.get(name), str) for name in scoring_spec)
        or (expected_scoring_spec is not None and scoring_spec != expected_scoring_spec)
    ):
        raise RuntimeError("External CoCo contract has an invalid scoring spec binding")
    if require_evaluation and runner["evaluation_enabled"] is not True:
        raise RuntimeError("Formal CoCo scoring requires the evaluation stage")
    _validate_contract_binding(
        runner.get("candidate_input"),
        expected_path=candidate_input,
        label="candidate input",
    )
    declared_artifacts = runner.get("artifacts")
    settings = runner.get("settings")
    stage_checkpoints = runner.get("stage_checkpoints")
    evaluation_settings = (
        settings.get("evaluation") if isinstance(settings, dict) else None
    )
    split_settings = settings.get("split") if isinstance(settings, dict) else None
    classify_settings = (
        settings.get("classify") if isinstance(settings, dict) else None
    )
    expected_stages = {"split", "classify"} | (
        {"evaluation"} if runner["evaluation_enabled"] else set()
    )
    if (
        not isinstance(declared_artifacts, dict)
        or set(declared_artifacts) != expected_stages
        or not isinstance(settings, dict)
        or set(settings) != {"split", "classify", "evaluation"}
        or not isinstance(split_settings, dict)
        or not isinstance(classify_settings, dict)
        or not isinstance(evaluation_settings, dict)
        or evaluation_settings.get("enabled") is not True
        or evaluation_settings.get("require_complete") is not True
        or not isinstance(stage_checkpoints, dict)
        or set(stage_checkpoints) != expected_stages
    ):
        raise RuntimeError("External CoCo contract requires artifact bindings")
    _validated_split_prompt_preflight(
        split_settings.get("prompt_preflight"),
        expected_count=len(expected_ids),
    )

    split_path = _fixed_output_path(
        output,
        COCO_SPLIT_ARTIFACT,
        label="External CoCo split artifact",
    )
    classify_path = _fixed_output_path(
        output,
        COCO_CLASSIFY_ARTIFACT,
        label="External CoCo classify artifact",
    )
    split_records = _validate_candidate_stage(
        split_path,
        stage="split",
        expected_ids=expected_ids,
    )
    _validate_candidate_stage(classify_path, stage="classify", expected_ids=expected_ids)
    opinion_count = sum(len(record["opinions"]) for record in split_records)
    _validated_classify_prompt_preflight(
        classify_settings.get("prompt_preflight"),
        expected_count=len(expected_ids),
        expected_opinion_count=opinion_count,
    )
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
        scoring_spec_binding=scoring_spec,
        input_binding=runner["candidate_input"],
        output_binding=declared_artifacts["split"],
    )
    _validate_stage_checkpoint_binding(
        output=output,
        stage="classify",
        binding=stage_checkpoints["classify"],
        runner=runner,
        scoring_spec_binding=scoring_spec,
        input_binding=declared_artifacts["split"],
        output_binding=declared_artifacts["classify"],
    )
    artifacts = {
        "runner": _artifact_binding(runner_path),
        "split": _artifact_binding(split_path),
        "classify": _artifact_binding(classify_path),
    }
    if runner["evaluation_enabled"]:
        evaluation_path = _fixed_output_path(
            output,
            COCO_EVALUATION_ARTIFACT,
            label="External CoCo evaluation artifact",
        )
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
            scoring_spec_binding=scoring_spec,
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
    return write_immutable_jsonl(output_path, values, reject_symlink=True)


@contextmanager
def _exclusive_evaluation_lock(output_root: Path) -> Iterator[None]:
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = _fixed_output_path(
        output_root,
        COCO_EVALUATION_LOCK,
        label="AgentReview CoCo evaluation lock",
    )
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


def _validate_pre_score_seal(path: Path) -> dict[str, Any]:
    # Local import avoids a module cycle: the final-audit boundary also delegates
    # its external artifact checks to this module.
    from .coco_final_audit import validate_coco_pre_score_seal

    return validate_coco_pre_score_seal(
        path,
        repository_root=Path(__file__).resolve().parents[3],
    )


def _canonical_bound_file(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a canonical absolute file path")
    path = Path(value).expanduser()
    if (
        not path.is_absolute()
        or str(path) != value
        or path.parent.resolve() != path.parent
        or path.is_symlink()
    ):
        raise ValueError(f"{label} must be a canonical absolute file path")
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    return path


def _lexical_input_file(value: str | Path, *, label: str) -> Path:
    """Accept relative CLI input while preserving and checking its lexical path."""
    path = Path(os.path.abspath(Path(value).expanduser()))
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise ValueError(f"{label} must not use a symbolic link")
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    return path


def _absolute_executable(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an absolute executable path")
    path = Path(value).expanduser()
    if not path.is_absolute() or str(path) != value or not path.is_file():
        raise ValueError(f"{label} must be an absolute executable path")
    if not os.access(path, os.X_OK):
        raise ValueError(f"{label} must be executable")
    return path


def _validated_sealed_candidate(
    *,
    arm: dict[str, Any],
    output: Path,
) -> tuple[Path, set[str]]:
    candidate = arm.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != {
        "path",
        "sha256",
        "count",
        "paper_ids_sha256",
    }:
        raise ValueError("CoCo pre-score arm has an invalid candidate binding")
    expected_path = _fixed_output_path(
        output,
        "candidate_official_input.jsonl",
        label="CoCo pre-score candidate",
        require_file=True,
    )
    if candidate.get("path") != str(expected_path):
        raise ValueError("CoCo pre-score candidate path differs from its sealed arm")
    path = expected_path
    if candidate.get("sha256") != file_sha256(path):
        raise RuntimeError("CoCo pre-score candidate changed after sealing")
    records = read_jsonl(path)
    if any(
        not isinstance(value, dict)
        or set(value) != {"id", "text"}
        or not isinstance(value.get("id"), str)
        or not value["id"]
        or not isinstance(value.get("text"), str)
        or not value["text"]
        for value in records
    ):
        raise ValueError("CoCo pre-score candidate rows must contain only id/text")
    paper_ids = [value["id"] for value in records]
    if (
        type(candidate.get("count")) is not int
        or len(records) != candidate["count"]
        or len(paper_ids) != len(set(paper_ids))
        or value_sha256(paper_ids) != candidate.get("paper_ids_sha256")
    ):
        raise ValueError("CoCo pre-score candidate paper set differs")
    return path, set(paper_ids)


def _required_text(value: Any, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"CoCo scoring spec {label} is invalid")
    return value


def _required_client_limit(value: Any, *, label: str) -> str:
    if type(value) is not int or value < 1:
        raise ValueError(f"CoCo scoring spec {label} must be a positive integer")
    return str(value)


def _formal_coco_environment_identity(
    *,
    spec: dict[str, Any],
    final_mode: str,
    candidate_input: Path,
    output: Path,
    scoring_spec_path: Path,
) -> dict[str, str]:
    scientific = spec.get("scientific_settings")
    services = spec.get("services")
    arms = spec.get("arms")
    if (
        not isinstance(scientific, dict)
        or set(scientific) != {"split", "classify", "evaluation"}
        or not isinstance(services, dict)
        or not isinstance(arms, dict)
        or set(arms) != {"meta", "concat"}
        or not isinstance(arms.get(final_mode), dict)
    ):
        raise ValueError("CoCo scoring spec scientific settings are invalid")
    split = scientific["split"]
    classify = scientific["classify"]
    evaluation = scientific["evaluation"]
    evaluation_runtime = services.get("evaluation")
    caches = arms[final_mode].get("cache_versions")
    if (
        not isinstance(split, dict)
        or not isinstance(classify, dict)
        or not isinstance(evaluation, dict)
        or not isinstance(evaluation_runtime, dict)
        or not isinstance(caches, dict)
        or set(caches) != {"split", "classify", "evaluation"}
    ):
        raise ValueError("CoCo scoring spec stage settings are invalid")
    if evaluation_runtime.get("transport") != {
        "HTTP_PROXY": FORMAL_EVALUATION_PROXY_URL,
        "HTTPS_PROXY": FORMAL_EVALUATION_PROXY_URL,
    }:
        raise ValueError("Formal CoCo evaluation transport identity is invalid")
    if (
        type(split.get("max_completion_tokens")) is not int
        or split["max_completion_tokens"] != FORMAL_SPLIT_MAX_COMPLETION_TOKENS
        or type(split.get("context_window_tokens")) is not int
        or split["context_window_tokens"] != FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS
    ):
        raise ValueError("Formal CoCo split completion budget identity is invalid")
    split_preflight = split.get("prompt_preflight")
    if (
        not isinstance(split_preflight, dict)
        or set(split_preflight)
        != {
            "algorithm",
            "max_model_len",
            "max_completion_tokens",
            "enable_thinking",
        }
        or split_preflight.get("algorithm")
        != FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM
        or type(split_preflight.get("max_model_len")) is not int
        or split_preflight["max_model_len"]
        != FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS
        or type(split_preflight.get("max_completion_tokens")) is not int
        or split_preflight["max_completion_tokens"]
        != FORMAL_SPLIT_MAX_COMPLETION_TOKENS
        or split_preflight.get("enable_thinking") is not True
    ):
        raise ValueError("Formal CoCo split prompt preflight identity is invalid")
    if (
        type(classify.get("max_completion_tokens")) is not int
        or classify["max_completion_tokens"]
        != FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        or type(classify.get("context_window_tokens")) is not int
        or classify["context_window_tokens"]
        != FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS
    ):
        raise ValueError("Formal CoCo classify completion budget identity is invalid")
    classify_preflight = classify.get("prompt_preflight")
    if (
        not isinstance(classify_preflight, dict)
        or set(classify_preflight)
        != {
            "algorithm",
            "max_model_len",
            "max_completion_tokens",
            "enable_thinking",
        }
        or classify_preflight.get("algorithm")
        != FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM
        or type(classify_preflight.get("max_model_len")) is not int
        or classify_preflight["max_model_len"]
        != FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS
        or type(classify_preflight.get("max_completion_tokens")) is not int
        or classify_preflight["max_completion_tokens"]
        != FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        or classify_preflight.get("enable_thinking") is not True
    ):
        raise ValueError("Formal CoCo classify prompt preflight identity is invalid")
    if (
        type(evaluation.get("max_completion_tokens")) is not int
        or evaluation["max_completion_tokens"]
        != FORMAL_EVALUATION_MAX_COMPLETION_TOKENS
        or type(evaluation.get("context_window_tokens")) is not int
        or evaluation["context_window_tokens"]
        != FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS
    ):
        raise ValueError("Formal CoCo evaluation completion budget identity is invalid")
    evaluation_preflight = evaluation.get("prompt_preflight")
    if (
        not isinstance(evaluation_preflight, dict)
        or set(evaluation_preflight)
        != {
            "algorithm",
            "max_model_len",
            "max_completion_tokens",
            "chat_overhead_tokens",
        }
        or evaluation_preflight.get("algorithm")
        != FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM
        or type(evaluation_preflight.get("max_model_len")) is not int
        or evaluation_preflight["max_model_len"]
        != FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS
        or type(evaluation_preflight.get("max_completion_tokens")) is not int
        or evaluation_preflight["max_completion_tokens"]
        != FORMAL_EVALUATION_MAX_COMPLETION_TOKENS
        or type(evaluation_preflight.get("chat_overhead_tokens")) is not int
        or evaluation_preflight["chat_overhead_tokens"]
        != FORMAL_EVALUATION_PROMPT_OVERHEAD_TOKENS
    ):
        raise ValueError("Formal CoCo evaluation prompt preflight identity is invalid")
    benchmark_files = evaluation.get("benchmark_files")
    if (
        evaluation.get("enabled") is not True
        or evaluation.get("require_complete") is not True
        or evaluation.get("temperature") is not None
        or evaluation.get("cuda_visible_devices") != ""
        or not isinstance(benchmark_files, list)
        or not benchmark_files
        or any(
            not isinstance(path, str) or not path or os.pathsep in path
            for path in benchmark_files
        )
    ):
        raise ValueError("Formal CoCo scoring requires a registered CPU evaluation")
    bert_score_model = evaluation.get("bert_score_model")
    if (
        not isinstance(bert_score_model, dict)
        or set(bert_score_model)
        != {
            "model_type",
            "root",
            "num_layers",
            "rescale_with_baseline",
            "file_count",
            "tree_id",
        }
        or bert_score_model.get("model_type") != "roberta-large"
        or type(bert_score_model.get("num_layers")) is not int
        or bert_score_model.get("num_layers") != 17
        or bert_score_model.get("rescale_with_baseline") is not False
        or type(bert_score_model.get("file_count")) is not int
        or bert_score_model.get("file_count", 0) < 1
        or not isinstance(bert_score_model.get("tree_id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", bert_score_model["tree_id"]) is None
    ):
        raise ValueError("Formal CoCo BERTScore model identity is invalid")
    bert_score_root = Path(
        _required_text(
            bert_score_model.get("root"),
            label="evaluation.bert_score_model.root",
        )
    )
    if (
        not bert_score_root.is_absolute()
        or bert_score_root.resolve() != bert_score_root
        or not bert_score_root.is_dir()
    ):
        raise ValueError("Formal CoCo BERTScore model root must be canonical")

    fixed = {
        "COCO_CANDIDATE_INPUT": str(candidate_input),
        "COCO_OUTPUT_ROOT": str(output),
        "COCO_SCORING_SPEC": str(scoring_spec_path),
        "COCO_SCORING_ARM": final_mode,
        "COCO_REQUIRE_SCORING_SPEC": "1",
        "COCO_REQUIRE_EVALUATION": "1",
        "COCO_RUN_EVALUATION": "1",
        "COCO_SPLIT_BASE_URL": _required_text(
            split.get("base_url"), label="split.base_url"
        ),
        "COCO_SPLIT_MODEL": _required_text(split.get("model"), label="split.model"),
        "COCO_SPLIT_MAX_WORKERS": _required_client_limit(
            split.get("max_in_flight_client_requests"),
            label="split.max_in_flight_client_requests",
        ),
        "COCO_SPLIT_MAX_COMPLETION_TOKENS": str(
            FORMAL_SPLIT_MAX_COMPLETION_TOKENS
        ),
        "COCO_SPLIT_EFFORT": _required_text(
            split.get("effort"), label="split.effort"
        ),
        "COCO_SPLIT_CACHE_VERSION": _required_text(
            caches.get("split"), label=f"arms.{final_mode}.cache_versions.split"
        ),
        "COCO_CLASSIFY_BASE_URL": _required_text(
            classify.get("base_url"), label="classify.base_url"
        ),
        "COCO_CLASSIFY_MODEL": _required_text(
            classify.get("model"), label="classify.model"
        ),
        "COCO_CLASSIFY_MAX_WORKERS": _required_client_limit(
            classify.get("max_in_flight_client_requests"),
            label="classify.max_in_flight_client_requests",
        ),
        "COCO_CLASSIFY_MAX_COMPLETION_TOKENS": str(
            FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        ),
        "COCO_CLASSIFY_EFFORT": _required_text(
            classify.get("effort"), label="classify.effort"
        ),
        "COCO_CLASSIFY_CACHE_VERSION": _required_text(
            caches.get("classify"),
            label=f"arms.{final_mode}.cache_versions.classify",
        ),
        "COCO_EVALUATION_BENCHMARK_FILES": os.pathsep.join(benchmark_files),
        "COCO_EVALUATION_COMPLETE_HUMAN_CLASSIFY_FILE": _required_text(
            evaluation.get("complete_human_classify_file"),
            label="evaluation.complete_human_classify_file",
        ),
        "COCO_EVALUATION_MODEL": _required_text(
            evaluation.get("model"), label="evaluation.model"
        ),
        "COCO_EVALUATION_BASE_URL": _required_text(
            evaluation.get("base_url"), label="evaluation.base_url"
        ),
        "COCO_EVALUATION_MAX_WORKERS": _required_client_limit(
            evaluation.get("max_in_flight_client_requests"),
            label="evaluation.max_in_flight_client_requests",
        ),
        "COCO_EVALUATION_MAX_COMPLETION_TOKENS": str(
            FORMAL_EVALUATION_MAX_COMPLETION_TOKENS
        ),
        "COCO_EVALUATION_EFFORT": _required_text(
            evaluation.get("effort"), label="evaluation.effort"
        ),
        "COCO_EVALUATION_CACHE_VERSION": _required_text(
            caches.get("evaluation"),
            label=f"arms.{final_mode}.cache_versions.evaluation",
        ),
        "COCO_EVALUATION_CUDA_VISIBLE_DEVICES": _required_text(
            evaluation.get("cuda_visible_devices"),
            label="evaluation.cuda_visible_devices",
            allow_empty=True,
        ),
        "COCO_BERT_SCORE_MODEL_ROOT": str(bert_score_root),
        "COCO_BERT_SCORE_NUM_LAYERS": str(bert_score_model["num_layers"]),
    }
    return {
        "PATH": COCO_FIXED_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        **fixed,
    }


def _formal_coco_environment(
    *,
    spec: dict[str, Any],
    final_mode: str,
    candidate_input: Path,
    output: Path,
    scoring_spec_path: Path,
) -> dict[str, str]:
    """Build the launch environment and append the required secret in memory."""
    environment = _formal_coco_environment_identity(
        spec=spec,
        final_mode=final_mode,
        candidate_input=candidate_input,
        output=output,
        scoring_spec_path=scoring_spec_path,
    )
    for name, expected in environment.items():
        if (
            name.startswith("COCO_")
            and name in os.environ
            and os.environ[name] != expected
        ):
            raise RuntimeError(
                f"Ambient {name} differs from the sealed CoCo scoring spec"
            )
    api_key = os.environ.get(COCO_API_KEY_ENV)
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError(
            f"Formal CoCo evaluation requires a non-empty {COCO_API_KEY_ENV}"
        )
    environment[COCO_API_KEY_ENV] = api_key
    return environment


def _formal_runner_command(
    *,
    spec: dict[str, Any],
    external_identity: dict[str, Any],
    normalized_command: list[str],
) -> list[str]:
    services = spec.get("services")
    if not isinstance(services, dict):
        raise ValueError("CoCo scoring spec service identities are invalid")
    evaluation = services.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("CoCo scoring spec service identities are invalid")
    evaluation_python = _absolute_executable(
        evaluation.get("python_executable"),
        label="CoCo evaluation Python executable",
    )
    runner = external_identity.get("runner")
    if not isinstance(runner, dict):
        raise ValueError("CoCo pre-score seal has no external runner binding")
    runner_path = _canonical_bound_file(
        runner.get("path"),
        label="CoCo official runner",
    )
    expected = [str(evaluation_python), "-s", "-B", str(runner_path)]
    if normalized_command != expected:
        raise RuntimeError(
            "Formal CoCo command must be exactly the sealed Python and official runner"
        )
    return expected


def _persist_failed_attempt(output: Path, report: dict[str, Any]) -> Path:
    if set(report) != COCO_RUN_REPORT_FIELDS:
        raise RuntimeError("AgentReview CoCo failed report fields differ")
    attempt = {
        **report,
        "attempt_id": value_sha256(report),
    }
    path = _fixed_output_path(
        output,
        Path(COCO_EVALUATION_ATTEMPT_ROOT) / f"{attempt['attempt_id']}.json",
        label="AgentReview CoCo failed attempt",
    )
    atomic_write_json(path, attempt, reject_symlink=True)
    return path


def _validate_existing_complete_report(
    *,
    path: Path,
    system: dict[str, Any],
    frozen_system_path: Path,
    manifest_binding: dict[str, Any],
    source_binding: dict[str, Any],
    pre_score_seal_binding: dict[str, Any],
    scoring_spec_binding: dict[str, Any],
    service_runtime: dict[str, Any],
    external_coco_identity: dict[str, Any],
    expected_command: list[str],
    environment_identity: dict[str, str],
    expected_ids: set[str],
    candidate_input: Path,
    output: Path,
) -> dict[str, Any]:
    report = read_json(path)
    if (
        not isinstance(report, dict)
        or set(report) != COCO_RUN_REPORT_FIELDS
        or report.get("protocol") != "research-v2-external-coco-v1"
        or report.get("status") != "complete"
        or type(report.get("returncode")) is not int
        or report.get("returncode") != 0
        or report.get("frozen_system_id") != system["system_id"]
        or report.get("frozen_system_sha256") != file_sha256(frozen_system_path)
        or report.get("coco_1300_manifest_sha256") != manifest_binding["sha256"]
        or canonical_json_bytes(report.get("scoring_source"))
        != canonical_json_bytes(source_binding)
        or canonical_json_bytes(report.get("pre_score_seal"))
        != canonical_json_bytes(pre_score_seal_binding)
        or canonical_json_bytes(report.get("scoring_spec"))
        != canonical_json_bytes(scoring_spec_binding)
        or canonical_json_bytes(report.get("service_runtime"))
        != canonical_json_bytes(service_runtime)
        or report.get("candidate_input") != str(candidate_input)
        or report.get("candidate_input_sha256") != file_sha256(candidate_input)
        or canonical_json_bytes(report.get("external_coco_identity"))
        != canonical_json_bytes(external_coco_identity)
        or canonical_json_bytes(report.get("command"))
        != canonical_json_bytes(expected_command)
        or canonical_json_bytes(report.get("environment"))
        != canonical_json_bytes(environment_identity)
        or report.get("artifact_validation_error") != ""
        or report.get("runtime_validation_error") != ""
    ):
        raise RuntimeError("Completed AgentReview CoCo report identity differs")
    artifacts = _validate_coco_artifacts(
        output,
        expected_ids=expected_ids,
        candidate_input=candidate_input,
        expected_scoring_spec={
            name: scoring_spec_binding[name]
            for name in ("path", "sha256", "spec_id")
        },
        require_evaluation=True,
    )
    if canonical_json_bytes(report.get("artifacts")) != canonical_json_bytes(artifacts):
        raise RuntimeError("Completed AgentReview CoCo artifact bindings differ")
    return report


def run_coco_evaluation(
    *,
    pre_score_seal_path: str | Path,
    final_mode: str,
    command: list[str],
) -> dict[str, Any]:
    """Run one sealed CoCo arm without accepting any mutable scientific inputs."""
    if final_mode not in {"meta", "concat"}:
        raise ValueError("CoCo Evaluation final_mode must be meta or concat")
    seal_path = _lexical_input_file(
        pre_score_seal_path,
        label="CoCo pre-score seal",
    )
    seal = _validate_pre_score_seal(seal_path)
    seal_binding = {
        "path": str(seal_path),
        "sha256": file_sha256(seal_path),
        "seal_id": seal.get("seal_id"),
    }

    frozen_binding = seal.get("frozen_system")
    if not isinstance(frozen_binding, dict) or set(frozen_binding) != {
        "path",
        "sha256",
        "system_id",
    }:
        raise ValueError("CoCo pre-score seal has an invalid Frozen System binding")
    frozen_system_path = _canonical_bound_file(
        frozen_binding.get("path"),
        label="CoCo pre-score Frozen System",
    )
    if frozen_binding.get("sha256") != file_sha256(frozen_system_path):
        raise RuntimeError("CoCo pre-score Frozen System changed after sealing")
    system = read_json(frozen_system_path)
    if (
        not isinstance(system, dict)
        or not system.get("system_id")
        or system.get("system_id") != frozen_binding.get("system_id")
    ):
        raise ValueError("CoCo pre-score seal does not bind a Frozen System Bundle")

    scoring_spec_binding = seal.get("scoring_spec")
    if not isinstance(scoring_spec_binding, dict) or set(scoring_spec_binding) != {
        "path",
        "sha256",
        "spec_id",
        "winner_run_id",
    }:
        raise ValueError("CoCo pre-score seal has an invalid scoring spec binding")
    scoring_spec_path = _canonical_bound_file(
        scoring_spec_binding.get("path"),
        label="CoCo pre-score scoring spec",
    )
    spec = read_json(scoring_spec_path)
    if (
        scoring_spec_binding.get("sha256") != file_sha256(scoring_spec_path)
        or not isinstance(spec, dict)
        or scoring_spec_binding.get("spec_id") != spec.get("spec_id")
        or scoring_spec_binding.get("winner_run_id") != spec.get("winner_run_id")
    ):
        raise RuntimeError("CoCo pre-score scoring spec changed after sealing")

    repository = spec.get("repository")
    if not isinstance(repository, dict):
        raise ValueError("CoCo scoring spec repository binding is invalid")
    root_value = repository.get("root")
    if not isinstance(root_value, str) or not root_value:
        raise ValueError("CoCo scoring spec repository root is invalid")
    root = Path(root_value).expanduser().resolve()
    if str(root) != root_value or not root.is_dir():
        raise ValueError("CoCo scoring spec repository root is not canonical")

    arms = seal.get("arms")
    spec_arms = spec.get("arms")
    if (
        not isinstance(arms, dict)
        or set(arms) != {"meta", "concat"}
        or not isinstance(arms.get(final_mode), dict)
        or not isinstance(spec_arms, dict)
        or not isinstance(spec_arms.get(final_mode), dict)
    ):
        raise ValueError("CoCo pre-score seal has invalid scoring arms")
    arm = arms[final_mode]
    output_value = arm.get("output_root")
    if not isinstance(output_value, str) or not output_value:
        raise ValueError("CoCo pre-score arm output root is invalid")
    output = Path(output_value).expanduser().resolve()
    artifact_root_value = spec.get("artifact_root")
    if not isinstance(artifact_root_value, str) or not artifact_root_value:
        raise ValueError("CoCo scoring spec artifact root is invalid")
    artifact_root = Path(artifact_root_value).expanduser().resolve()
    if (
        str(output) != output_value
        or str(artifact_root) != artifact_root_value
        or output
        != Path(str(spec_arms[final_mode].get("output_root") or ""))
        .expanduser()
        .resolve()
        or arm.get("cache_versions")
        != spec_arms[final_mode].get("cache_versions")
    ):
        raise ValueError("CoCo pre-score arm differs from its scoring spec")
    _validate_output_isolation(output=output, winner_root=artifact_root)
    candidate_input, expected_ids = _validated_sealed_candidate(
        arm=arm,
        output=output,
    )

    source_binding = arm.get("scoring_source")
    source = seal.get("source")
    manifest_binding = source.get("coco_manifest") if isinstance(source, dict) else None
    if (
        not isinstance(source_binding, dict)
        or source_binding.get("final_mode") != final_mode
        or source_binding.get("winner_run_id")
        != scoring_spec_binding.get("winner_run_id")
        or not isinstance(manifest_binding, dict)
        or not isinstance(manifest_binding.get("sha256"), str)
    ):
        raise ValueError("CoCo pre-score winner source binding is invalid")

    sealed_external_identity = seal.get("external_coco")
    if not isinstance(sealed_external_identity, dict):
        raise ValueError("CoCo pre-score external runner identity is invalid")
    service_runtime = seal.get("service_runtime")
    if not isinstance(service_runtime, dict):
        raise ValueError("CoCo pre-score service runtime proof is invalid")
    external_coco_identity, normalized_command = _validate_external_coco_runtime(
        system=system,
        root=root,
        command=command,
    )
    if external_coco_identity != sealed_external_identity:
        raise RuntimeError("External CoCo runtime differs from the pre-score seal")
    normalized_command = _formal_runner_command(
        spec=spec,
        external_identity=external_coco_identity,
        normalized_command=normalized_command,
    )
    environment = _formal_coco_environment(
        spec=spec,
        final_mode=final_mode,
        candidate_input=candidate_input,
        output=output,
        scoring_spec_path=scoring_spec_path,
    )
    environment_identity = {
        name: value
        for name, value in environment.items()
        if name != COCO_API_KEY_ENV
    }
    runner_scoring_spec = {
        name: scoring_spec_binding[name]
        for name in ("path", "sha256", "spec_id")
    }

    with _exclusive_evaluation_lock(output):
        confirmed_seal = _validate_pre_score_seal(seal_path)
        if confirmed_seal != seal or file_sha256(seal_path) != seal_binding["sha256"]:
            raise RuntimeError("CoCo pre-score seal changed before scoring")
        _validated_sealed_candidate(arm=arm, output=output)
        complete_report_path = _fixed_output_path(
            output,
            "run.json",
            label="AgentReview CoCo complete report",
        )
        if complete_report_path.is_file():
            report = _validate_existing_complete_report(
                path=complete_report_path,
                system=system,
                frozen_system_path=frozen_system_path,
                manifest_binding=manifest_binding,
                source_binding=source_binding,
                pre_score_seal_binding=seal_binding,
                scoring_spec_binding=scoring_spec_binding,
                service_runtime=service_runtime,
                external_coco_identity=external_coco_identity,
                expected_command=normalized_command,
                environment_identity=environment_identity,
                expected_ids=expected_ids,
                candidate_input=candidate_input,
                output=output,
            )
            final_seal = _validate_pre_score_seal(seal_path)
            if final_seal != seal or file_sha256(seal_path) != seal_binding["sha256"]:
                raise RuntimeError("CoCo pre-score seal changed during report validation")
            return report
        launch_error: RuntimeError | None = None
        try:
            completed = subprocess.run(
                normalized_command,
                cwd=str(root),
                env=environment,
                check=False,
            )
        except OSError:
            completed = subprocess.CompletedProcess(normalized_command, -1)
            launch_error = RuntimeError("External CoCo process launch failed")
        runtime_error: RuntimeError | None = launch_error
        artifacts: dict[str, dict[str, str]] = {}
        artifact_error: RuntimeError | None = None
        if completed.returncode == 0:
            try:
                artifacts = _validate_coco_artifacts(
                    output,
                    expected_ids=expected_ids,
                    candidate_input=candidate_input,
                    expected_scoring_spec=runner_scoring_spec,
                    require_evaluation=True,
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
            final_seal = _validate_pre_score_seal(seal_path)
            if final_seal != seal or file_sha256(seal_path) != seal_binding["sha256"]:
                raise RuntimeError("CoCo pre-score seal or its upstream changed during scoring")
            _validated_sealed_candidate(arm=arm, output=output)
        except Exception as exc:
            if runtime_error is None:
                runtime_error = (
                    exc
                    if isinstance(exc, RuntimeError)
                    else RuntimeError("External CoCo runtime validation failed")
                )
        report = {
            "protocol": "research-v2-external-coco-v1",
            "frozen_system_id": system["system_id"],
            "frozen_system_sha256": frozen_binding["sha256"],
            "coco_1300_manifest_sha256": manifest_binding["sha256"],
            "scoring_source": source_binding,
            "pre_score_seal": seal_binding,
            "scoring_spec": scoring_spec_binding,
            "service_runtime": service_runtime,
            "candidate_input": str(candidate_input),
            "candidate_input_sha256": arm["candidate"]["sha256"],
            "external_coco_identity": external_coco_identity,
            "command": normalized_command,
            "environment": environment_identity,
            "returncode": completed.returncode,
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
            atomic_write_json(
                complete_report_path,
                report,
                reject_symlink=True,
            )
        else:
            _persist_failed_attempt(output, report)
        if completed.returncode != 0:
            raise RuntimeError(f"External CoCo process failed: {completed.returncode}")
        if artifact_error is not None:
            raise artifact_error
        if runtime_error is not None:
            raise runtime_error
        return report
