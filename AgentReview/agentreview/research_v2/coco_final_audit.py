"""Seal CoCo candidates before scoring and audit both completed score arms.

The public functions in this module deliberately form a narrow boundary around
the external CoCo process.  A pre-score seal is rebuilt entirely from the
formal Frozen System and completed winner batch.  A final audit then accepts
only two complete, scientifically identical, namespace-isolated CoCo runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import coco as coco_module
from .coco_review_batch import (
    SCORING_SOURCE_PROTOCOL,
    validate_completed_coco_review_batch,
)
from .coco_scoring_spec import (
    ARMS,
    CACHE_STAGES,
    validate_coco_scoring_spec,
)
from .formal_transition import validate_frozen_system_transition
from .io import (
    atomic_write_json,
    file_sha256,
    read_json,
    read_jsonl,
    value_sha256,
)


PRE_SCORE_SEAL_PROTOCOL = "research-v2-coco-pre-score-seal-v1"
FINAL_AUDIT_PROTOCOL = "research-v2-coco-final-audit-v1"
CANDIDATE_INPUT_NAME = "candidate_official_input.jsonl"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SYSTEM_SPEC_BINDING_FIELDS = {
    "path",
    "sha256",
    "spec_id",
    "winner_run_id",
}
_RUNNER_SPEC_BINDING_FIELDS = {"path", "sha256", "spec_id"}
_SCORING_SOURCE_FIELDS = (
    "source_id",
    "winner_run_id",
    "final_mode",
    "final_run_id",
    "count",
    "paper_ids_sha256",
    "final_review_ids_sha256",
    "source_review_ids_sha256",
    "records_sha256",
    "files_sha256",
)
_FIREWALL = {
    "review_evolution_artifacts_remain_immutable": True,
    "candidate_inputs_contain_only_paper_ids_and_final_review_text": True,
    "scoring_outputs_are_isolated_by_arm": True,
    "external_evaluation_is_downstream_of_frozen_reviews": True,
}
_FINAL_DECLARATIONS = {
    "output_roots_are_disjoint": True,
    "cache_versions_are_disjoint": True,
    "arm_scientific_settings_are_identical": True,
    "evaluation_artifacts_are_complete": True,
}


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_exact_fields(
    value: Any,
    fields: set[str],
    *,
    label: str,
) -> dict[str, Any]:
    result = _require_object(value, label=label)
    if set(result) != fields:
        raise ValueError(f"{label} fields differ")
    return result


def _canonical_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty path string")
    path = Path(value).expanduser().resolve()
    if str(path) != value:
        raise ValueError(f"{label} must be a canonical absolute path")
    return path


def _required_file(value: Any, *, label: str) -> Path:
    path = _canonical_path(value, label=label)
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    return path


def _file_binding(path: str | Path) -> dict[str, str]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Bound file is missing: {source}")
    return {"path": str(source), "sha256": file_sha256(source)}


def _validate_file_binding(value: Any, *, label: str) -> Path:
    binding = _require_exact_fields(value, {"path", "sha256"}, label=label)
    path = _required_file(binding["path"], label=f"{label}.path")
    digest = binding.get("sha256")
    if (
        not isinstance(digest, str)
        or not _SHA256_PATTERN.fullmatch(digest)
        or file_sha256(path) != digest
    ):
        raise ValueError(f"{label} SHA256 differs")
    return path


def _system_spec_binding(
    *,
    system: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    external = _require_object(
        system.get("external_evaluation"),
        label="Frozen System external_evaluation",
    )
    binding = _require_exact_fields(
        external.get("coco_scoring_spec"),
        _SYSTEM_SPEC_BINDING_FIELDS,
        label="Frozen System CoCo scoring spec binding",
    )
    spec_path = _required_file(
        binding.get("path"),
        label="Frozen System CoCo scoring spec",
    )
    spec = validate_coco_scoring_spec(spec_path)
    expected = {
        "path": str(spec_path),
        "sha256": file_sha256(spec_path),
        "spec_id": spec.get("spec_id"),
        "winner_run_id": spec.get("winner_run_id"),
    }
    if binding != expected:
        raise ValueError("Frozen System CoCo scoring spec binding differs")
    return dict(binding), spec


def _validated_system(
    *,
    frozen_system_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    before_sha256 = file_sha256(frozen_system_path)
    system = validate_frozen_system_transition(
        frozen_system_path=frozen_system_path,
        repository_root=repository_root,
    )
    if (
        not isinstance(system, dict)
        or read_json(frozen_system_path) != system
        or file_sha256(frozen_system_path) != before_sha256
    ):
        raise RuntimeError("Frozen System changed during pre-score validation")
    return system


def _validated_external_identity(
    *,
    system: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    repository = _require_object(
        spec.get("repository"),
        label="CoCo scoring spec repository",
    )
    coco_root = _canonical_path(
        repository.get("root"),
        label="CoCo scoring spec repository root",
    )
    external = _require_object(
        system.get("external_evaluation"),
        label="Frozen System external_evaluation",
    )
    identity = _require_object(
        external.get("coco_runner"),
        label="Frozen System external CoCo runner identity",
    )
    runner = _require_object(
        identity.get("runner"),
        label="Frozen System external CoCo runner",
    )
    runner_path = _required_file(
        runner.get("path"),
        label="Frozen System external CoCo runner",
    )
    validated, _ = coco_module._validate_external_coco_runtime(
        system=system,
        root=coco_root,
        command=[str(runner_path)],
    )
    if validated != identity:
        raise RuntimeError("External CoCo runtime identity changed during validation")
    if (
        _canonical_path(
            identity.get("repository_root"),
            label="Frozen System external CoCo repository root",
        )
        != coco_root
        or identity.get("git_commit") != repository.get("git_commit")
    ):
        raise ValueError(
            "Frozen System external CoCo root or commit differs from scoring spec"
        )
    return dict(identity)


def _validate_output_isolation(
    *,
    artifact_root: Path,
    spec: dict[str, Any],
) -> dict[str, Path]:
    arms = _require_exact_fields(
        spec.get("arms"), set(ARMS), label="CoCo scoring spec arms"
    )
    outputs: dict[str, Path] = {}
    for mode in ARMS:
        arm = _require_object(arms.get(mode), label=f"CoCo scoring spec {mode} arm")
        output = _canonical_path(
            arm.get("output_root"),
            label=f"CoCo scoring spec {mode} output root",
        )
        if not output.is_relative_to(artifact_root):
            raise ValueError(
                f"CoCo {mode} output root must be inside the artifact root"
            )
        coco_module._validate_output_isolation(
            output=output,
            winner_root=artifact_root,
        )
        outputs[mode] = output
    first, second = (outputs[mode] for mode in ARMS)
    if (
        first == second
        or first.is_relative_to(second)
        or second.is_relative_to(first)
    ):
        raise ValueError("CoCo scoring arms require disjoint output roots")
    return outputs


def _validate_cache_isolation(spec: dict[str, Any]) -> None:
    values: list[str] = []
    for mode in ARMS:
        arm = _require_object(
            spec["arms"].get(mode), label=f"CoCo scoring spec {mode} arm"
        )
        caches = _require_exact_fields(
            arm.get("cache_versions"),
            set(CACHE_STAGES),
            label=f"CoCo scoring spec {mode} cache versions",
        )
        for stage in CACHE_STAGES:
            cache = caches[stage]
            if not isinstance(cache, str) or not cache:
                raise ValueError(f"CoCo {mode}/{stage} cache version is invalid")
            values.append(cache)
    if len(set(values)) != len(ARMS) * len(CACHE_STAGES):
        raise ValueError("CoCo scoring arms require six disjoint cache versions")


def _manifest_binding(system: dict[str, Any]) -> dict[str, Any]:
    external = _require_object(
        system.get("external_evaluation"),
        label="Frozen System external_evaluation",
    )
    binding = _require_exact_fields(
        external.get("coco_1300_manifest"),
        {"path", "sha256", "protocol", "count", "manifest_sha256"},
        label="Frozen System CoCo manifest binding",
    )
    path = _required_file(binding.get("path"), label="Frozen System CoCo manifest")
    if binding.get("sha256") != file_sha256(path):
        raise ValueError("Frozen System CoCo manifest SHA256 differs")
    return dict(binding)


def _scoring_source_binding(
    source: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    arm = _require_object(
        _require_object(source.get("arms"), label="winner scoring source arms").get(
            mode
        ),
        label=f"winner scoring source {mode} arm",
    )
    values = {
        "source_id": source.get("source_id"),
        "winner_run_id": source.get("run_id"),
        "final_mode": mode,
        "final_run_id": arm.get("run_id"),
        "count": arm.get("count"),
        "paper_ids_sha256": arm.get("paper_ids_sha256"),
        "final_review_ids_sha256": arm.get("final_review_ids_sha256"),
        "source_review_ids_sha256": arm.get("source_review_ids_sha256"),
        "records_sha256": arm.get("records_sha256"),
        "files_sha256": arm.get("files_sha256"),
    }
    if any(values[name] is None for name in _SCORING_SOURCE_FIELDS):
        raise ValueError(f"winner scoring source {mode} arm is incomplete")
    return values


def _validated_candidate(
    *,
    path: Path,
    expected_count: Any,
    expected_paper_ids_sha256: Any,
) -> dict[str, Any]:
    values = read_jsonl(path)
    if any(
        set(value) != {"id", "text"}
        or not isinstance(value["id"], str)
        or not value["id"]
        or not isinstance(value["text"], str)
        or not value["text"]
        for value in values
    ):
        raise ValueError("CoCo candidate rows must contain only non-empty id/text")
    paper_ids = [value["id"] for value in values]
    if (
        type(expected_count) is not int
        or len(values) != expected_count
        or len(set(paper_ids)) != len(paper_ids)
        or value_sha256(paper_ids) != expected_paper_ids_sha256
    ):
        raise ValueError("CoCo candidate differs from the exact winner paper set")
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "count": len(values),
        "paper_ids_sha256": value_sha256(paper_ids),
    }


def _compact_source(
    *,
    source: dict[str, Any],
    artifact_root: Path,
    system_binding: dict[str, Any],
    manifest_binding: dict[str, Any],
) -> dict[str, Any]:
    if (
        source.get("protocol") != SCORING_SOURCE_PROTOCOL
        or source.get("run_id") is None
        or not isinstance(source.get("source_id"), str)
    ):
        raise ValueError("Completed winner scoring source identity differs")
    source_system = _require_object(
        source.get("system"), label="winner scoring source system"
    )
    if any(
        source_system.get(name) != system_binding[name]
        for name in ("path", "sha256", "system_id")
    ):
        raise ValueError("Completed winner scoring source Frozen System differs")
    source_manifest = _require_object(
        source.get("coco_manifest"), label="winner scoring source CoCo manifest"
    )
    if any(
        source_manifest.get(name) != manifest_binding[name]
        for name in ("path", "sha256", "manifest_sha256", "count")
    ):
        raise ValueError("Completed winner scoring source CoCo manifest differs")
    summary = _require_exact_fields(
        source.get("summary"),
        {"path", "sha256"},
        label="winner scoring source summary",
    )
    _validate_file_binding(summary, label="winner scoring source summary")
    return {
        "source_id": source["source_id"],
        "winner_run_id": source["run_id"],
        "artifact_root": str(artifact_root),
        "coco_manifest": {
            "path": source_manifest["path"],
            "sha256": source_manifest["sha256"],
            "manifest_sha256": source_manifest["manifest_sha256"],
            "count": source_manifest["count"],
            "paper_ids_sha256": source_manifest.get("paper_ids_sha256"),
        },
        "summary": dict(summary),
        "run_spec_sha256": source.get("run_spec_sha256"),
    }


def _build_pre_score_body(
    *,
    frozen_system_path: Path,
    artifact_root: Path,
    winner_run_id: str,
    repository_root: Path,
) -> dict[str, Any]:
    if not frozen_system_path.is_file():
        raise FileNotFoundError(f"Frozen System is missing: {frozen_system_path}")
    system = _validated_system(
        frozen_system_path=frozen_system_path,
        repository_root=repository_root,
    )
    spec_binding, spec = _system_spec_binding(system=system)
    if spec.get("winner_run_id") != winner_run_id:
        raise ValueError("CoCo scoring spec winner run differs")
    if _canonical_path(
        spec.get("artifact_root"), label="CoCo scoring spec artifact root"
    ) != artifact_root:
        raise ValueError("CoCo scoring spec artifact root differs")
    external_identity = _validated_external_identity(system=system, spec=spec)
    outputs = _validate_output_isolation(artifact_root=artifact_root, spec=spec)
    _validate_cache_isolation(spec)
    manifest = _manifest_binding(system)
    source = validate_completed_coco_review_batch(
        frozen_system_path=frozen_system_path,
        coco_manifest_path=manifest["path"],
        output_root=artifact_root,
        run_id=winner_run_id,
    )
    source = _require_object(source, label="completed winner scoring source")
    if source.get("run_id") != winner_run_id:
        raise ValueError("Completed winner scoring source run differs")

    system_binding = {
        "path": str(frozen_system_path),
        "sha256": file_sha256(frozen_system_path),
        "system_id": system.get("system_id"),
    }
    compact_source = _compact_source(
        source=source,
        artifact_root=artifact_root,
        system_binding=system_binding,
        manifest_binding=manifest,
    )
    sealed_arms: dict[str, Any] = {}
    for mode in ARMS:
        source_arm = _require_object(
            _require_object(source.get("arms"), label="winner scoring source arms").get(
                mode
            ),
            label=f"winner scoring source {mode} arm",
        )
        paths = source_arm.get("paths")
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError(f"winner scoring source {mode} paths differ")
        candidate_path = coco_module.prepare_coco_input(
            paths,
            outputs[mode] / CANDIDATE_INPUT_NAME,
        ).resolve()
        scoring_source = _scoring_source_binding(source, mode=mode)
        candidate = _validated_candidate(
            path=candidate_path,
            expected_count=scoring_source["count"],
            expected_paper_ids_sha256=scoring_source["paper_ids_sha256"],
        )
        sealed_arms[mode] = {
            "mode": mode,
            "output_root": str(outputs[mode]),
            "scoring_source": scoring_source,
            "candidate": candidate,
            "cache_versions": dict(spec["arms"][mode]["cache_versions"]),
        }

    return {
        "protocol": PRE_SCORE_SEAL_PROTOCOL,
        "frozen_system": system_binding,
        "scoring_spec": spec_binding,
        "external_coco": external_identity,
        "source": compact_source,
        "arms": sealed_arms,
        "firewall": dict(_FIREWALL),
    }


def _verify_pre_score_bindings(body: dict[str, Any]) -> None:
    frozen = _require_exact_fields(
        body.get("frozen_system"),
        {"path", "sha256", "system_id"},
        label="pre-score Frozen System",
    )
    frozen_path = _required_file(
        frozen.get("path"), label="pre-score Frozen System"
    )
    if frozen.get("sha256") != file_sha256(frozen_path):
        raise RuntimeError("Pre-score Frozen System changed during sealing")
    scoring_spec = _require_exact_fields(
        body.get("scoring_spec"),
        _SYSTEM_SPEC_BINDING_FIELDS,
        label="pre-score scoring spec",
    )
    spec_path = _required_file(
        scoring_spec.get("path"), label="pre-score scoring spec"
    )
    if scoring_spec.get("sha256") != file_sha256(spec_path):
        raise RuntimeError("Pre-score scoring spec changed during sealing")
    source = _require_object(body.get("source"), label="pre-score source")
    _validate_file_binding(source.get("summary"), label="pre-score winner summary")
    manifest = _require_object(
        source.get("coco_manifest"), label="pre-score CoCo manifest"
    )
    manifest_path = _required_file(
        manifest.get("path"), label="pre-score CoCo manifest"
    )
    if manifest.get("sha256") != file_sha256(manifest_path):
        raise RuntimeError("Pre-score CoCo manifest changed during sealing")
    external = _require_object(body.get("external_coco"), label="pre-score external CoCo")
    _validate_file_binding(external.get("runner"), label="pre-score external runner")
    arms = _require_exact_fields(body.get("arms"), set(ARMS), label="pre-score arms")
    for mode in ARMS:
        candidate = _require_object(
            _require_object(arms[mode], label=f"pre-score {mode} arm").get(
                "candidate"
            ),
            label=f"pre-score {mode} candidate",
        )
        path = _required_file(
            candidate.get("path"), label=f"pre-score {mode} candidate"
        )
        if candidate.get("sha256") != file_sha256(path):
            raise RuntimeError(f"Pre-score {mode} candidate changed during sealing")


def _validate_seal_shape(value: Any) -> dict[str, Any]:
    seal = _require_exact_fields(
        value,
        {
            "protocol",
            "frozen_system",
            "scoring_spec",
            "external_coco",
            "source",
            "arms",
            "firewall",
            "seal_id",
        },
        label="CoCo pre-score seal",
    )
    if seal.get("protocol") != PRE_SCORE_SEAL_PROTOCOL:
        raise ValueError("CoCo pre-score seal protocol differs")
    seal_id = seal.get("seal_id")
    if not isinstance(seal_id, str) or not _SHA256_PATTERN.fullmatch(seal_id):
        raise ValueError("CoCo pre-score seal_id is invalid")
    body = {name: item for name, item in seal.items() if name != "seal_id"}
    if seal_id != value_sha256(body):
        raise ValueError("CoCo pre-score seal self-hash differs")
    if seal.get("firewall") != _FIREWALL:
        raise ValueError("CoCo pre-score firewall declarations differ")
    arms = _require_exact_fields(seal.get("arms"), set(ARMS), label="pre-score arms")
    for mode in ARMS:
        arm = _require_exact_fields(
            arms[mode],
            {
                "mode",
                "output_root",
                "scoring_source",
                "candidate",
                "cache_versions",
            },
            label=f"pre-score {mode} arm",
        )
        if arm.get("mode") != mode:
            raise ValueError(f"Pre-score {mode} arm mode differs")
    return seal


def seal_coco_pre_score(
    *,
    frozen_system_path: str | Path,
    artifact_root: str | Path,
    winner_run_id: str,
    repository_root: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Build and immutably commit the exact two CoCo candidate inputs."""
    system_path = Path(frozen_system_path).expanduser().resolve()
    artifacts = Path(artifact_root).expanduser().resolve()
    repository = Path(repository_root).expanduser().resolve()
    if (
        not isinstance(winner_run_id, str)
        or not winner_run_id
        or Path(winner_run_id).name != winner_run_id
        or winner_run_id in {".", ".."}
    ):
        raise ValueError("CoCo pre-score winner_run_id must be one path-safe name")
    arguments = {
        "frozen_system_path": system_path,
        "artifact_root": artifacts,
        "winner_run_id": winner_run_id,
        "repository_root": repository,
    }
    first = _build_pre_score_body(**arguments)
    second = _build_pre_score_body(**arguments)
    if first != second:
        raise RuntimeError("CoCo pre-score complete rebuilds differ")
    _verify_pre_score_bindings(second)
    value = {**second, "seal_id": value_sha256(second)}
    _validate_seal_shape(value)
    target = atomic_write_json(output_path, value)
    committed = read_json(target)
    if committed != value:
        raise RuntimeError("Committed CoCo pre-score seal differs")
    _verify_pre_score_bindings(second)
    return value


def validate_coco_pre_score_seal(
    path: str | Path,
    *,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Rebuild a pre-score seal from formal upstream sources and match exactly."""
    source = Path(path).expanduser().resolve()
    before = source.read_bytes()
    try:
        value = json.loads(before)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CoCo pre-score seal is not valid JSON: {source}") from exc
    seal = _validate_seal_shape(value)
    _verify_pre_score_bindings(
        {name: item for name, item in seal.items() if name != "seal_id"}
    )
    frozen = _require_object(
        seal.get("frozen_system"), label="pre-score Frozen System"
    )
    scoring_spec = _require_object(
        seal.get("scoring_spec"), label="pre-score scoring spec"
    )
    spec = validate_coco_scoring_spec(scoring_spec["path"])
    expected_body = _build_pre_score_body(
        frozen_system_path=_required_file(
            frozen.get("path"), label="pre-score Frozen System"
        ),
        artifact_root=_canonical_path(
            spec.get("artifact_root"), label="CoCo scoring spec artifact root"
        ),
        winner_run_id=scoring_spec.get("winner_run_id"),
        repository_root=Path(repository_root).expanduser().resolve(),
    )
    expected = {**expected_body, "seal_id": value_sha256(expected_body)}
    if seal != expected:
        raise ValueError("CoCo pre-score seal differs from its complete rebuild")
    if source.read_bytes() != before:
        raise RuntimeError("CoCo pre-score seal changed during validation")
    _verify_pre_score_bindings(expected_body)
    return seal


def _runner_spec_binding(seal: dict[str, Any]) -> dict[str, Any]:
    binding = _require_object(
        seal.get("scoring_spec"), label="pre-score scoring spec"
    )
    return {
        "path": binding.get("path"),
        "sha256": binding.get("sha256"),
        "spec_id": binding.get("spec_id"),
    }


def _expected_runner_settings(
    *,
    spec: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    scientific = _require_exact_fields(
        spec.get("scientific_settings"),
        set(CACHE_STAGES),
        label="CoCo scoring scientific settings",
    )
    caches = _require_exact_fields(
        spec["arms"][mode].get("cache_versions"),
        set(CACHE_STAGES),
        label=f"CoCo scoring {mode} cache versions",
    )
    models = _require_exact_fields(
        spec.get("models"),
        {"split", "classify"},
        label="CoCo scoring model inventories",
    )
    settings: dict[str, Any] = {}
    for stage in ("split", "classify"):
        stage_settings = _require_object(
            scientific.get(stage), label=f"CoCo scoring {stage} settings"
        )
        model = _require_exact_fields(
            models.get(stage),
            {"root", "files"},
            label=f"CoCo scoring {stage} model inventory",
        )
        files = model.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"CoCo scoring {stage} model inventory is empty")
        settings[stage] = {
            **stage_settings,
            "model_tree": {
                "root": model["root"],
                "file_count": len(files),
                "tree_id": value_sha256(
                    {"root": model["root"], "files": files}
                ),
            },
            "cache_version": caches[stage],
        }

    evaluation = _require_object(
        scientific.get("evaluation"), label="CoCo scoring evaluation settings"
    )
    benchmark = _require_exact_fields(
        spec.get("benchmark"),
        {"files", "complete_human_classify", "reviewer_selection"},
        label="CoCo scoring benchmark assets",
    )
    critical = spec.get("critical_files")
    if not isinstance(critical, list):
        raise ValueError("CoCo scoring critical file bindings differ")

    def critical_binding(relative: str) -> dict[str, Any]:
        matches = [
            binding
            for binding in critical
            if isinstance(binding, dict)
            and Path(str(binding.get("path") or "")).as_posix().endswith(relative)
        ]
        if len(matches) != 1:
            raise ValueError(f"CoCo scoring critical binding differs: {relative}")
        return dict(matches[0])

    settings["evaluation"] = {
        "enabled": evaluation.get("enabled"),
        "benchmark_files": list(benchmark["files"]),
        "complete_human_classify_file": dict(
            benchmark["complete_human_classify"]
        ),
        "reviewer_selection_file": dict(benchmark["reviewer_selection"]),
        "judge_prompt_file": critical_binding(
            "src/evaluation/prompt/judge.txt"
        ),
        "code_files": {
            "prompt_registry": critical_binding(
                "src/benchmark/prompt_registry.py"
            ),
            "split": critical_binding("src/evaluation/_2_split.py"),
            "classify": critical_binding("src/evaluation/_3_classify.py"),
            "evaluation": critical_binding("src/evaluation/_4_evaluation.py"),
        },
        "base_url": evaluation.get("base_url"),
        "model": evaluation.get("model"),
        "max_in_flight_client_requests": evaluation.get(
            "max_in_flight_client_requests"
        ),
        "effort": evaluation.get("effort"),
        "cache_version": caches["evaluation"],
        "cuda_visible_devices": evaluation.get("cuda_visible_devices"),
    }
    return settings


def _scientific_projection(settings: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for stage in ("split", "classify"):
        stage_settings = _require_object(
            settings.get(stage), label=f"external CoCo {stage} settings"
        )
        if "cache_version" not in stage_settings or "model_tree" not in stage_settings:
            raise ValueError(f"External CoCo {stage} settings are incomplete")
        projection[stage] = {
            name: item
            for name, item in stage_settings.items()
            if name not in {"cache_version", "model_tree"}
        }
    evaluation = _require_object(
        settings.get("evaluation"), label="external CoCo evaluation settings"
    )
    required_assets = {
        "benchmark_files",
        "complete_human_classify_file",
        "reviewer_selection_file",
        "judge_prompt_file",
        "code_files",
    }
    if "cache_version" not in evaluation or not required_assets.issubset(evaluation):
        raise ValueError("External CoCo evaluation settings are incomplete")
    benchmark_files = evaluation["benchmark_files"]
    if not isinstance(benchmark_files, list):
        raise TypeError("External CoCo benchmark bindings must be a list")
    projection["evaluation"] = {
        "enabled": evaluation.get("enabled"),
        "benchmark_files": [
            _require_object(binding, label="external CoCo benchmark binding").get(
                "path"
            )
            for binding in benchmark_files
        ],
        "complete_human_classify_file": _require_object(
            evaluation["complete_human_classify_file"],
            label="external CoCo complete human classify binding",
        ).get("path"),
        "reviewer_selection_file": _require_object(
            evaluation["reviewer_selection_file"],
            label="external CoCo reviewer selection binding",
        ).get("path"),
        **{
            name: item
            for name, item in evaluation.items()
            if name
            not in required_assets
            | {
                "enabled",
                "cache_version",
            }
        },
    }
    return projection


def _candidate_ids(candidate: dict[str, Any], *, mode: str) -> set[str]:
    path = _required_file(
        candidate.get("path"), label=f"pre-score {mode} candidate"
    )
    if candidate.get("sha256") != file_sha256(path):
        raise RuntimeError(f"Pre-score {mode} candidate SHA256 differs")
    records = read_jsonl(path)
    ids = [str(record.get("id") or "") for record in records]
    if (
        "" in ids
        or len(ids) != len(set(ids))
        or len(ids) != candidate.get("count")
        or value_sha256(ids) != candidate.get("paper_ids_sha256")
    ):
        raise ValueError(f"Pre-score {mode} candidate paper set differs")
    return set(ids)


def _audit_arm(
    *,
    mode: str,
    seal: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    arm = _require_object(seal["arms"].get(mode), label=f"pre-score {mode} arm")
    output = _canonical_path(
        arm.get("output_root"), label=f"pre-score {mode} output root"
    )
    candidate = _require_object(
        arm.get("candidate"), label=f"pre-score {mode} candidate"
    )
    candidate_path = _required_file(
        candidate.get("path"), label=f"pre-score {mode} candidate"
    )
    expected_ids = _candidate_ids(candidate, mode=mode)
    run_path = output / "run.json"
    if not run_path.is_file():
        raise FileNotFoundError(f"Completed CoCo {mode} run is missing: {run_path}")
    run_sha256 = file_sha256(run_path)
    report = _require_object(read_json(run_path), label=f"CoCo {mode} run report")
    frozen = _require_object(
        seal.get("frozen_system"), label="pre-score Frozen System"
    )
    source = _require_object(seal.get("source"), label="pre-score source")
    manifest = _require_object(
        source.get("coco_manifest"), label="pre-score CoCo manifest"
    )
    if (
        report.get("protocol") != "research-v2-external-coco-v1"
        or report.get("status") != "complete"
        or type(report.get("returncode")) is not int
        or report.get("returncode") != 0
        or report.get("artifact_validation_error") != ""
        or report.get("runtime_validation_error") != ""
        or report.get("frozen_system_id") != frozen.get("system_id")
        or report.get("frozen_system_sha256") != frozen.get("sha256")
        or report.get("coco_1300_manifest_sha256") != manifest.get("sha256")
        or report.get("scoring_source") != arm.get("scoring_source")
        or report.get("candidate_input") != str(candidate_path)
        or report.get("candidate_input_sha256") != candidate.get("sha256")
        or report.get("external_coco_identity") != seal.get("external_coco")
    ):
        raise ValueError(f"Completed CoCo {mode} run report identity differs")

    runner_path = output / coco_module.COCO_RUNNER_ARTIFACT
    runner_before = runner_path.read_bytes() if runner_path.is_file() else None
    artifacts = coco_module._validate_coco_artifacts(
        output,
        expected_ids=expected_ids,
        candidate_input=candidate_path,
    )
    runner = _require_object(
        read_json(runner_path), label=f"external CoCo {mode} runner contract"
    )
    if runner_before is None or runner_path.read_bytes() != runner_before:
        raise RuntimeError(f"External CoCo {mode} runner contract changed during audit")
    if runner.get("evaluation_enabled") is not True:
        raise ValueError(f"Final CoCo {mode} audit requires evaluation to be enabled")
    if "evaluation" not in artifacts:
        raise ValueError(f"Final CoCo {mode} audit requires an evaluation artifact")
    if report.get("artifacts") != artifacts:
        raise ValueError(f"Completed CoCo {mode} artifact bindings differ")

    expected_settings = _expected_runner_settings(spec=spec, mode=mode)
    settings = runner.get("settings")
    if settings != expected_settings:
        raise ValueError(
            f"External CoCo {mode} runner settings differ from scoring spec"
        )
    runner_spec = runner.get("scoring_spec")
    expected_runner_spec = _runner_spec_binding(seal)
    if (
        not isinstance(runner_spec, dict)
        or set(runner_spec) != _RUNNER_SPEC_BINDING_FIELDS
        or runner_spec != expected_runner_spec
    ):
        raise ValueError(
            f"External CoCo {mode} runner scoring spec binding differs"
        )

    return (
        {
            "mode": mode,
            "output_root": str(output),
            "candidate": dict(candidate),
            "scoring_source": dict(arm["scoring_source"]),
            "run": {"path": str(run_path), "sha256": run_sha256},
            "artifacts": artifacts,
            "settings": settings,
            "scoring_spec": runner_spec,
        },
        _scientific_projection(settings),
    )


def _build_final_audit_body(
    *,
    pre_score_seal_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    seal_before = pre_score_seal_path.read_bytes()
    seal_sha256 = file_sha256(pre_score_seal_path)
    seal = validate_coco_pre_score_seal(
        pre_score_seal_path,
        repository_root=repository_root,
    )
    spec_binding = _require_object(
        seal.get("scoring_spec"), label="pre-score scoring spec"
    )
    spec = validate_coco_scoring_spec(spec_binding["path"])
    artifact_root = _canonical_path(
        spec.get("artifact_root"), label="CoCo scoring spec artifact root"
    )
    _validate_output_isolation(artifact_root=artifact_root, spec=spec)
    _validate_cache_isolation(spec)

    audited_arms: dict[str, Any] = {}
    scientific: dict[str, Any] = {}
    for mode in ARMS:
        audited_arms[mode], scientific[mode] = _audit_arm(
            mode=mode,
            seal=seal,
            spec=spec,
        )
    if scientific[ARMS[0]] != scientific[ARMS[1]]:
        raise ValueError("CoCo scoring arms use different scientific settings")
    if scientific[ARMS[0]] != spec.get("scientific_settings"):
        raise ValueError("CoCo scoring arm scientific settings differ from spec")

    confirmed = validate_coco_pre_score_seal(
        pre_score_seal_path,
        repository_root=repository_root,
    )
    if (
        confirmed != seal
        or pre_score_seal_path.read_bytes() != seal_before
        or file_sha256(pre_score_seal_path) != seal_sha256
    ):
        raise RuntimeError("CoCo pre-score seal changed during final audit")
    return {
        "protocol": FINAL_AUDIT_PROTOCOL,
        "pre_score_seal": {
            "path": str(pre_score_seal_path),
            "sha256": seal_sha256,
            "seal_id": seal["seal_id"],
        },
        "scoring_spec": dict(spec_binding),
        "arms": audited_arms,
        "declarations": dict(_FINAL_DECLARATIONS),
    }


def _verify_final_bindings(body: dict[str, Any]) -> None:
    seal = _require_object(body.get("pre_score_seal"), label="final audit seal")
    seal_path = _required_file(seal.get("path"), label="final audit seal")
    if seal.get("sha256") != file_sha256(seal_path):
        raise RuntimeError("Pre-score seal changed during final audit commit")
    arms = _require_exact_fields(body.get("arms"), set(ARMS), label="final audit arms")
    for mode in ARMS:
        arm = _require_object(arms[mode], label=f"final audit {mode} arm")
        _validate_file_binding(arm.get("run"), label=f"final audit {mode} run")
        candidate = _require_object(
            arm.get("candidate"), label=f"final audit {mode} candidate"
        )
        candidate_path = _required_file(
            candidate.get("path"), label=f"final audit {mode} candidate"
        )
        if candidate.get("sha256") != file_sha256(candidate_path):
            raise RuntimeError(f"Final audit {mode} candidate changed")
        artifacts = _require_object(
            arm.get("artifacts"), label=f"final audit {mode} artifacts"
        )
        for name, binding in artifacts.items():
            _validate_file_binding(
                binding,
                label=f"final audit {mode} {name} artifact",
            )


def audit_coco_final(
    *,
    pre_score_seal_path: str | Path,
    repository_root: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Audit both complete CoCo arms and immutably commit a self-hashed result."""
    seal_path = Path(pre_score_seal_path).expanduser().resolve()
    repository = Path(repository_root).expanduser().resolve()
    arguments = {
        "pre_score_seal_path": seal_path,
        "repository_root": repository,
    }
    first = _build_final_audit_body(**arguments)
    second = _build_final_audit_body(**arguments)
    if first != second:
        raise RuntimeError("CoCo final audit complete passes differ")
    _verify_final_bindings(second)
    value = {**second, "audit_id": value_sha256(second)}
    target = atomic_write_json(output_path, value)
    if read_json(target) != value:
        raise RuntimeError("Committed CoCo final audit differs")
    _verify_final_bindings(second)
    return value


__all__ = [
    "FINAL_AUDIT_PROTOCOL",
    "PRE_SCORE_SEAL_PROTOCOL",
    "audit_coco_final",
    "seal_coco_pre_score",
    "validate_coco_pre_score_seal",
]
