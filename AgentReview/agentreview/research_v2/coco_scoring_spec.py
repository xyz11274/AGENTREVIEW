"""Pre-register the complete immutable CoCo scoring environment.

The module deliberately exposes only materialization and validation.  Callers do
not need to know how the CoCo repository, benchmark closure, model closure,
scientific settings, service identities, or arm-local namespaces are bound.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .io import atomic_write_json, file_sha256, value_sha256


PROTOCOL = "research-v2-coco-scoring-spec-v1"
ARMS = ("meta", "concat")
CACHE_STAGES = ("split", "classify", "evaluation")
BENCHMARK_GLOB = "benchmark_*_20*.jsonl"

CRITICAL_FILE_RELATIVE_PATHS = (
    "scripts/run_official_candidate.py",
    "src/evaluation/_0_human_review.py",
    "src/evaluation/_2_split.py",
    "src/evaluation/_3_classify.py",
    "src/evaluation/_4_evaluation.py",
    "src/benchmark/prompt_registry.py",
    "src/evaluation/prompt/judge.txt",
)

MODEL_RELATIVE_ROOTS = {
    "split": "model/split_model",
    "classify": "model/classify_model",
}

_TOP_LEVEL_FIELDS = {
    "protocol",
    "winner_run_id",
    "artifact_root",
    "repository",
    "critical_files",
    "benchmark",
    "models",
    "scientific_settings",
    "services",
    "arms",
    "spec_id",
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def _run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"CoCo Git identity is unreadable{suffix}")
    return result.stdout.strip()


def _clean_git_identity(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise FileNotFoundError(f"CoCo repository root is missing: {root}")
    top_level = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise ValueError(
            f"CoCo root must be the Git worktree root: requested={root}, actual={top_level}"
        )
    commit = _run_git(root, "rev-parse", "HEAD")
    if not _GIT_COMMIT_PATTERN.fullmatch(commit):
        raise RuntimeError("CoCo Git HEAD is not a full SHA-1 commit identity")
    dirty = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if dirty:
        raise RuntimeError("CoCo scoring requires a clean Git worktree")
    return {"root": str(root), "git_commit": commit, "clean": True}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _stable_file_fingerprint(path: Path) -> tuple[str, int, tuple[int, ...]]:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"CoCo binding requires an ordinary file: {path}")
    digest = file_sha256(path)
    after = path.stat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise RuntimeError(f"CoCo input changed while it was being hashed: {path}")
    return digest, before.st_size, before_identity


def _required_file(
    path: Path,
    *,
    root: Path,
    label: str,
) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"CoCo scoring requires {label}: {path}") from exc
    if not _is_within(resolved, root):
        raise ValueError(f"CoCo {label} escapes the repository root: {path}")
    if not resolved.is_file():
        raise ValueError(f"CoCo {label} is not a file: {path}")
    return resolved


def _file_binding(path: Path) -> dict[str, Any]:
    digest, _, _ = _stable_file_fingerprint(path)
    return {"path": str(path), "sha256": digest}


def _critical_file_bindings(root: Path) -> list[dict[str, Any]]:
    return [
        _file_binding(
            _required_file(
                root / relative,
                root=root,
                label=f"critical file {relative}",
            )
        )
        for relative in CRITICAL_FILE_RELATIVE_PATHS
    ]


def _benchmark_bindings(root: Path) -> dict[str, Any]:
    benchmark_root = root / "benchmark"
    if not benchmark_root.is_dir() or benchmark_root.is_symlink():
        raise FileNotFoundError(
            f"CoCo scoring requires the benchmark directory: {benchmark_root}"
        )
    resolved_benchmark_root = benchmark_root.resolve()
    benchmark_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for candidate in sorted(benchmark_root.glob(BENCHMARK_GLOB)):
        path = _required_file(
            candidate,
            root=resolved_benchmark_root,
            label=f"resolved benchmark file matching {BENCHMARK_GLOB}",
        )
        if path in seen_paths:
            raise ValueError(f"Duplicate resolved CoCo benchmark file: {path}")
        seen_paths.add(path)
        benchmark_paths.append(path)
    if not benchmark_paths:
        raise FileNotFoundError(
            f"CoCo scoring requires benchmark/{BENCHMARK_GLOB} files"
        )

    complete_human = _required_file(
        benchmark_root / "benchmark_all_human_classify.jsonl",
        root=resolved_benchmark_root,
        label="benchmark_all_human_classify.jsonl",
    )
    reviewer_selection = _required_file(
        benchmark_root / "benchmark_reviewer_selection.json",
        root=resolved_benchmark_root,
        label="benchmark_reviewer_selection.json",
    )
    return {
        "files": [_file_binding(path) for path in benchmark_paths],
        "complete_human_classify": _file_binding(complete_human),
        "reviewer_selection": _file_binding(reviewer_selection),
    }


def _model_inventory(
    root: Path,
    *,
    role: str,
    relative_root: str,
) -> tuple[dict[str, Any], dict[str, tuple[int, ...]]]:
    model_root = root / relative_root
    if model_root.is_symlink():
        raise ValueError(f"CoCo {role} model root must not be a symlink: {model_root}")
    if not model_root.is_dir():
        raise FileNotFoundError(f"CoCo {role} model directory is missing: {model_root}")
    resolved_root = model_root.resolve()
    if not _is_within(resolved_root, root):
        raise ValueError(f"CoCo {role} model directory escapes the repository root")

    files: list[dict[str, Any]] = []
    observations: dict[str, tuple[int, ...]] = {}
    seen_paths: set[Path] = set()
    seen_file_identities: set[tuple[int, int]] = set()
    for candidate in sorted(model_root.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"CoCo {role} model contains a dangling symlink: {candidate}"
                ) from exc
            if not _is_within(resolved, resolved_root):
                raise ValueError(
                    f"CoCo {role} model symlink escapes its model directory: {candidate}"
                )
            if resolved.is_dir():
                raise ValueError(
                    f"CoCo {role} model contains a directory symlink: {candidate}"
                )
        elif candidate.is_dir():
            continue
        else:
            resolved = candidate.resolve()

        if not _is_within(resolved, resolved_root):
            raise ValueError(f"CoCo {role} model file escapes its model directory")
        if resolved in seen_paths:
            raise ValueError(f"Duplicate resolved CoCo {role} model file: {resolved}")
        digest, size, identity = _stable_file_fingerprint(resolved)
        file_identity = (identity[0], identity[1])
        if file_identity in seen_file_identities:
            raise ValueError(
                f"Duplicate underlying CoCo {role} model file: {candidate}"
            )
        seen_paths.add(resolved)
        seen_file_identities.add(file_identity)
        path_text = str(resolved)
        observations[path_text] = identity
        files.append({"path": path_text, "size_bytes": size, "sha256": digest})

    files.sort(key=lambda item: item["path"])
    if not files:
        raise FileNotFoundError(f"CoCo {role} model directory contains no files")
    return {"root": str(resolved_root), "files": files}, observations


def _verify_observations(observations: dict[str, tuple[int, ...]]) -> None:
    for path_text, expected in observations.items():
        path = Path(path_text)
        try:
            observed = path.stat()
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"CoCo input disappeared during inventory: {path}"
            ) from exc
        identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        )
        if identity != expected:
            raise RuntimeError(f"CoCo input changed during inventory: {path}")


def _scientific_settings(benchmark: dict[str, Any]) -> dict[str, Any]:
    return {
        "split": {
            "base_url": "http://127.0.0.1:13099/v1",
            "model": "Qwen3-8B-Split",
            "max_in_flight_client_requests": 64,
            "effort": "medium",
        },
        "classify": {
            "base_url": "http://127.0.0.1:13098/v1",
            "model": "Qwen3-8B-Classify",
            "max_in_flight_client_requests": 128,
            "effort": "medium",
        },
        "evaluation": {
            "enabled": True,
            "benchmark_files": [item["path"] for item in benchmark["files"]],
            "complete_human_classify_file": benchmark["complete_human_classify"][
                "path"
            ],
            "reviewer_selection_file": benchmark["reviewer_selection"]["path"],
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-5-mini",
            "max_in_flight_client_requests": 128,
            "effort": "medium",
            "cuda_visible_devices": "",
        },
    }


def _service_identities(models: dict[str, Any]) -> dict[str, Any]:
    return {
        "software": {"vllm": "0.20.1", "transformers": "5.8.1"},
        "split": {
            "cuda_visible_devices": "0",
            "host": "127.0.0.1",
            "port": 13099,
            "served_model_name": "Qwen3-8B-Split",
            "model_root": models["split"]["root"],
            "max_model_len": 40960,
            "max_num_seqs": 8,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
        },
        "classify": {
            "cuda_visible_devices": "1",
            "host": "127.0.0.1",
            "port": 13098,
            "served_model_name": "Qwen3-8B-Classify",
            "model_root": models["classify"]["root"],
            "max_model_len": 32768,
            "max_num_seqs": 16,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
        },
    }


def _validate_winner_run_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        raise ValueError("CoCo scoring winner_run_id must be one path-safe name")
    return value


def _arm_bindings(
    *,
    artifact_root: Path,
    winner_run_id: str,
) -> dict[str, Any]:
    scoring_root = artifact_root / "coco_scoring"
    return {
        mode: {
            "mode": mode,
            "output_root": str((scoring_root / f"{winner_run_id}-{mode}").resolve()),
            "cache_versions": {
                stage: f"{winner_run_id}-{mode}-{stage}-cache-v1"
                for stage in CACHE_STAGES
            },
        }
        for mode in ARMS
    }


def _build_body(
    *,
    coco_root: Path,
    artifact_root: Path,
    winner_run_id: str,
) -> dict[str, Any]:
    repository = _clean_git_identity(coco_root)
    critical_files = _critical_file_bindings(coco_root)
    benchmark = _benchmark_bindings(coco_root)
    models: dict[str, Any] = {}
    observations: dict[str, tuple[int, ...]] = {}
    resolved_model_roots: set[str] = set()
    for role, relative_root in MODEL_RELATIVE_ROOTS.items():
        inventory, role_observations = _model_inventory(
            coco_root,
            role=role,
            relative_root=relative_root,
        )
        if inventory["root"] in resolved_model_roots:
            raise ValueError("CoCo split and classify model roots must be distinct")
        resolved_model_roots.add(inventory["root"])
        models[role] = inventory
        observations.update(role_observations)
    _verify_observations(observations)
    if _clean_git_identity(coco_root) != repository:
        raise RuntimeError("CoCo Git identity changed while materializing bindings")

    return {
        "protocol": PROTOCOL,
        "winner_run_id": winner_run_id,
        "artifact_root": str(artifact_root),
        "repository": repository,
        "critical_files": critical_files,
        "benchmark": benchmark,
        "models": models,
        "scientific_settings": _scientific_settings(benchmark),
        "services": _service_identities(models),
        "arms": _arm_bindings(
            artifact_root=artifact_root,
            winner_run_id=winner_run_id,
        ),
    }


def _require_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"CoCo scoring spec {label} fields differ")
    return value


def _validate_file_binding_shape(value: Any, *, label: str) -> None:
    binding = _require_fields(value, {"path", "sha256"}, label=label)
    if not isinstance(binding["path"], str) or not binding["path"]:
        raise ValueError(f"CoCo scoring spec {label} path is invalid")
    if not isinstance(binding["sha256"], str) or not _SHA256_PATTERN.fullmatch(
        binding["sha256"]
    ):
        raise ValueError(f"CoCo scoring spec {label} SHA256 is invalid")


def _validate_shape(value: dict[str, Any]) -> None:
    if set(value) != _TOP_LEVEL_FIELDS:
        raise ValueError("CoCo scoring spec envelope fields differ")
    if value.get("protocol") != PROTOCOL:
        raise ValueError("CoCo scoring spec protocol differs")
    _validate_winner_run_id(value.get("winner_run_id"))
    if not isinstance(value.get("artifact_root"), str):
        raise ValueError("CoCo scoring spec artifact_root must be a path string")

    repository = _require_fields(
        value.get("repository"),
        {"root", "git_commit", "clean"},
        label="repository",
    )
    if (
        not isinstance(repository["root"], str)
        or not _GIT_COMMIT_PATTERN.fullmatch(str(repository["git_commit"]))
        or repository["clean"] is not True
    ):
        raise ValueError("CoCo scoring spec repository identity is invalid")

    critical_files = value.get("critical_files")
    if not isinstance(critical_files, list) or len(critical_files) != len(
        CRITICAL_FILE_RELATIVE_PATHS
    ):
        raise ValueError("CoCo scoring spec critical file closure differs")
    for index, binding in enumerate(critical_files):
        _validate_file_binding_shape(binding, label=f"critical_files[{index}]")

    benchmark = _require_fields(
        value.get("benchmark"),
        {"files", "complete_human_classify", "reviewer_selection"},
        label="benchmark",
    )
    if not isinstance(benchmark["files"], list) or not benchmark["files"]:
        raise ValueError("CoCo scoring spec benchmark file closure is empty")
    for index, binding in enumerate(benchmark["files"]):
        _validate_file_binding_shape(binding, label=f"benchmark.files[{index}]")
    _validate_file_binding_shape(
        benchmark["complete_human_classify"],
        label="benchmark.complete_human_classify",
    )
    _validate_file_binding_shape(
        benchmark["reviewer_selection"],
        label="benchmark.reviewer_selection",
    )

    models = _require_fields(
        value.get("models"),
        set(MODEL_RELATIVE_ROOTS),
        label="models",
    )
    for role in MODEL_RELATIVE_ROOTS:
        inventory = _require_fields(
            models[role],
            {"root", "files"},
            label=f"models.{role}",
        )
        if not isinstance(inventory["root"], str):
            raise ValueError(f"CoCo scoring spec models.{role}.root is invalid")
        if not isinstance(inventory["files"], list) or not inventory["files"]:
            raise ValueError(f"CoCo scoring spec models.{role}.files is empty")
        for index, binding in enumerate(inventory["files"]):
            model_file = _require_fields(
                binding,
                {"path", "size_bytes", "sha256"},
                label=f"models.{role}.files[{index}]",
            )
            if (
                not isinstance(model_file["path"], str)
                or type(model_file["size_bytes"]) is not int
                or model_file["size_bytes"] < 0
                or not isinstance(model_file["sha256"], str)
                or not _SHA256_PATTERN.fullmatch(model_file["sha256"])
            ):
                raise ValueError(
                    f"CoCo scoring spec models.{role}.files[{index}] is invalid"
                )

    settings = _require_fields(
        value.get("scientific_settings"),
        {"split", "classify", "evaluation"},
        label="scientific_settings",
    )
    for role in ("split", "classify"):
        _require_fields(
            settings[role],
            {"base_url", "model", "max_in_flight_client_requests", "effort"},
            label=f"scientific_settings.{role}",
        )
    _require_fields(
        settings["evaluation"],
        {
            "enabled",
            "benchmark_files",
            "complete_human_classify_file",
            "reviewer_selection_file",
            "base_url",
            "model",
            "max_in_flight_client_requests",
            "effort",
            "cuda_visible_devices",
        },
        label="scientific_settings.evaluation",
    )

    services = _require_fields(
        value.get("services"),
        {"software", "split", "classify"},
        label="services",
    )
    _require_fields(
        services["software"],
        {"vllm", "transformers"},
        label="services.software",
    )
    service_fields = {
        "cuda_visible_devices",
        "host",
        "port",
        "served_model_name",
        "model_root",
        "max_model_len",
        "max_num_seqs",
        "tensor_parallel_size",
        "gpu_memory_utilization",
    }
    for role in ("split", "classify"):
        _require_fields(
            services[role],
            service_fields,
            label=f"services.{role}",
        )

    arms = _require_fields(value.get("arms"), set(ARMS), label="arms")
    output_roots: list[str] = []
    cache_versions: list[str] = []
    for mode in ARMS:
        arm = _require_fields(
            arms[mode],
            {"mode", "output_root", "cache_versions"},
            label=f"arms.{mode}",
        )
        if arm["mode"] != mode or not isinstance(arm["output_root"], str):
            raise ValueError(f"CoCo scoring spec {mode} arm identity is invalid")
        caches = _require_fields(
            arm["cache_versions"],
            set(CACHE_STAGES),
            label=f"arms.{mode}.cache_versions",
        )
        if any(
            not isinstance(caches[stage], str) or not caches[stage]
            for stage in CACHE_STAGES
        ):
            raise ValueError(f"CoCo scoring spec {mode} cache version is invalid")
        output_roots.append(arm["output_root"])
        cache_versions.extend(caches[stage] for stage in CACHE_STAGES)
    if len(set(output_roots)) != len(ARMS):
        raise ValueError("CoCo scoring arms require distinct output roots")
    if len(set(cache_versions)) != len(ARMS) * len(CACHE_STAGES):
        raise ValueError("CoCo scoring arms require six distinct cache versions")

    spec_id = value.get("spec_id")
    if not isinstance(spec_id, str) or not _SHA256_PATTERN.fullmatch(spec_id):
        raise ValueError("CoCo scoring spec_id is invalid")


def materialize_coco_scoring_spec(
    *,
    coco_root: str | Path,
    artifact_root: str | Path,
    winner_run_id: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Materialize one deterministic, immutable, self-hashed scoring spec."""
    root = Path(coco_root).expanduser().resolve()
    artifacts = Path(artifact_root).expanduser().resolve()
    winner = _validate_winner_run_id(winner_run_id)
    body = _build_body(
        coco_root=root,
        artifact_root=artifacts,
        winner_run_id=winner,
    )
    value = {**body, "spec_id": value_sha256(body)}
    _validate_shape(value)
    target = atomic_write_json(output_path, value)
    try:
        committed = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Committed CoCo scoring spec is not valid JSON") from exc
    if committed != value:
        raise RuntimeError(
            "Committed CoCo scoring spec differs from its materialization"
        )
    return value


def validate_coco_scoring_spec(
    path: str | Path,
    *,
    coco_root: str | Path | None = None,
) -> dict[str, Any]:
    """Revalidate the strict envelope and every current external binding."""
    source = Path(path).expanduser().resolve()
    before = source.read_bytes()
    try:
        value = json.loads(before)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CoCo scoring spec is not valid JSON: {source}") from exc
    if not isinstance(value, dict):
        raise TypeError("CoCo scoring spec must be a JSON object")
    _validate_shape(value)

    body = {name: item for name, item in value.items() if name != "spec_id"}
    if value["spec_id"] != value_sha256(body):
        raise ValueError("CoCo scoring spec self-hash differs")

    recorded_root = Path(value["repository"]["root"]).expanduser().resolve()
    if str(recorded_root) != value["repository"]["root"]:
        raise ValueError("CoCo scoring spec repository root is not canonical")
    if coco_root is not None:
        requested_root = Path(coco_root).expanduser().resolve()
        if requested_root != recorded_root:
            raise ValueError("CoCo scoring spec repository root differs")

    artifact_root = Path(value["artifact_root"]).expanduser().resolve()
    if str(artifact_root) != value["artifact_root"]:
        raise ValueError("CoCo scoring spec artifact_root is not canonical")
    current_repository = _clean_git_identity(recorded_root)
    if current_repository != value["repository"]:
        raise ValueError("CoCo scoring spec Git root or commit differs")

    expected = _build_body(
        coco_root=recorded_root,
        artifact_root=artifact_root,
        winner_run_id=value["winner_run_id"],
    )
    if body != expected:
        raise ValueError(
            "CoCo scoring spec paths, hashes, settings, services, or arms differ"
        )
    if source.read_bytes() != before:
        raise RuntimeError("CoCo scoring spec changed while it was being validated")
    if _clean_git_identity(recorded_root) != current_repository:
        raise RuntimeError("CoCo Git identity changed during spec validation")
    return value


__all__ = [
    "PROTOCOL",
    "materialize_coco_scoring_spec",
    "validate_coco_scoring_spec",
]
