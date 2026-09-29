"""Pre-register the complete immutable CoCo scoring environment.

The module deliberately exposes only materialization and validation.  Callers do
not need to know how the CoCo repository, benchmark closure, model closure,
scientific settings, service identities, or arm-local namespaces are bound.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .io import atomic_write_json, file_sha256, value_sha256
from .judge_lease import identity as qwen27b_mode_lease_identity


PROTOCOL = "research-v2-coco-scoring-spec-v2"
ARMS = ("meta", "concat")
CACHE_STAGES = ("split", "classify", "evaluation")
BENCHMARK_GLOB = "benchmark_*_20*.jsonl"

CRITICAL_FILE_RELATIVE_PATHS = (
    "scripts/run_official_candidate.py",
    "src/evaluation/_0_human_review.py",
    "src/evaluation/_2_split.py",
    "src/evaluation/_3_classify.py",
    "src/evaluation/_4_evaluation.py",
    "src/evaluation/table_1_contract.py",
    "src/utils.py",
    "src/benchmark/prompt_registry.py",
    "src/evaluation/prompt/judge.txt",
)

MODEL_RELATIVE_ROOTS = {
    "split": "model/split_model",
    "classify": "model/classify_model",
    "evaluation": "model/bertscore_roberta_large",
}
EVALUATION_MODEL_TYPE = "roberta-large"
EVALUATION_MODEL_NUM_LAYERS = 17
FORMAL_EVALUATION_MAX_COMPLETION_TOKENS = 8192
FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS = 65536
FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM = "vllm-chat-tokenize-v1"
FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS = 4096
FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS = 32768
FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM = "qwen-classify-chat-template-v1"
FORMAL_SPLIT_MAX_COMPLETION_TOKENS = 8192
FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS = 40960
FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM = "qwen-chat-template-v1"
FORMAL_JUDGE_HOST = "127.0.0.1"
FORMAL_JUDGE_PORT = 18001
FORMAL_JUDGE_BASE_URL = f"http://{FORMAL_JUDGE_HOST}:{FORMAL_JUDGE_PORT}/v1"
FORMAL_JUDGE_MODEL_NAME = "qwen3.8-27b"
FORMAL_JUDGE_MODEL_ROOT = "/data0/xyj/models/Qwen3.8-27B"
FORMAL_JUDGE_SOURCE_MODEL = "Qwen/Qwen3.8-27B"
FORMAL_JUDGE_REVISION = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
FORMAL_JUDGE_TOKENIZER_SHA256 = (
    "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3"
)
FORMAL_JUDGE_CHAT_TEMPLATE_SHA256 = (
    "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041"
)
FORMAL_JUDGE_WORKING_DIRECTORY = str(Path(__file__).resolve().parents[2])
EVALUATION_REQUIRED_PACKAGES = {
    "bert-score": "bert_score",
    "nltk": "nltk",
    "numpy": "numpy",
    "openai": "openai",
    "requests": "requests",
    "rouge-score": "rouge_score",
    "torch": "torch",
    "tqdm": "tqdm",
    "transformers": "transformers",
}
_EVALUATION_PROBE_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
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
_JUDGE_WEIGHT_IDENTITY_CACHE: dict[
    str, tuple[dict[str, Any], dict[str, tuple[int, ...]]]
] = {}
_JUDGE_WEIGHT_IDENTITY_LOCK = threading.Lock()


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


def judge_weight_identity() -> dict[str, Any]:
    """Hash and compactly bind the exact local 27B shard closure."""
    root = Path(FORMAL_JUDGE_MODEL_ROOT)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or root.resolve() != root
        or not root.is_dir()
    ):
        raise ValueError("CoCo judge model root is not a canonical directory")
    key = str(root)
    with _JUDGE_WEIGHT_IDENTITY_LOCK:
        cached = _JUDGE_WEIGHT_IDENTITY_CACHE.get(key)
        if cached is not None:
            value, observations = cached
            try:
                _verify_observations(observations)
            except RuntimeError:
                pass
            else:
                return dict(value)

        semantic_paths = {
            "config_sha256": root / "config.json",
            "tokenizer_config_sha256": root / "tokenizer_config.json",
            "generation_config_sha256": root / "generation_config.json",
            "model_index_sha256": root / "model.safetensors.index.json",
        }
        index_path = semantic_paths["model_index_sha256"]
        metadata_path = root / ".hfd" / "repo_metadata.json"
        for path, label in (
            *((path, name) for name, path in semantic_paths.items()),
            (metadata_path, "source metadata"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"CoCo judge {label} file identity is invalid")
        semantic_fingerprints = {
            name: _stable_file_fingerprint(path)
            for name, path in semantic_paths.items()
        }
        _, _, metadata_observation = _stable_file_fingerprint(metadata_path)
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("CoCo judge weight metadata is unreadable") from exc
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        siblings = metadata.get("siblings") if isinstance(metadata, dict) else None
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("CoCo judge weight index is invalid")
        if not isinstance(siblings, list):
            raise ValueError("CoCo judge source metadata has no file inventory")
        names = sorted(set(weight_map.values()))
        if (
            any(
                not isinstance(name, str)
                or not name.endswith(".safetensors")
                or Path(name).name != name
                for name in names
            )
            or len(names) != 18
        ):
            raise ValueError("CoCo judge weight shard closure is invalid")
        actual_names = sorted(path.name for path in root.glob("*.safetensors"))
        if actual_names != names:
            raise ValueError("CoCo judge weight shard directory differs from its index")
        source_files = {
            item.get("rfilename"): item
            for item in siblings
            if isinstance(item, dict) and isinstance(item.get("rfilename"), str)
        }
        if any(
            name not in source_files
            or not isinstance(source_files[name].get("lfs"), dict)
            for name in names
        ):
            raise ValueError("CoCo judge source metadata omits a weight shard")

        def fingerprint(name: str) -> tuple[str, int, tuple[int, ...]]:
            path = root / name
            if path.is_symlink():
                raise ValueError("CoCo judge weight shard must not be a symlink")
            return _stable_file_fingerprint(path)

        with ThreadPoolExecutor(max_workers=4) as executor:
            fingerprints = list(executor.map(fingerprint, names))
        entries: list[dict[str, Any]] = []
        observations = {str(metadata_path): metadata_observation}
        observations.update(
            {
                str(semantic_paths[name]): fingerprint[2]
                for name, fingerprint in semantic_fingerprints.items()
            }
        )
        for name, (digest, size, observation) in zip(names, fingerprints, strict=True):
            lfs = source_files[name]["lfs"]
            if lfs.get("sha256") != digest or lfs.get("size") != size:
                raise ValueError(
                    f"CoCo judge weight shard differs from source metadata: {name}"
                )
            entries.append({"path": name, "size_bytes": size, "sha256": digest})
            observations[str(root / name)] = observation
        _verify_observations(observations)
        value = {
            **{
                name: semantic_fingerprints[name][0]
                for name in semantic_paths
            },
            "weight_file_count": len(entries),
            "weight_total_bytes": sum(item["size_bytes"] for item in entries),
            "weights_tree_sha256": value_sha256(entries),
        }
        _JUDGE_WEIGHT_IDENTITY_CACHE[key] = (dict(value), observations)
        return value


def _evaluation_model_identity(model: dict[str, Any]) -> dict[str, Any]:
    files = model["files"]
    return {
        "model_type": model["model_type"],
        "root": model["root"],
        "num_layers": model["num_layers"],
        "rescale_with_baseline": model["rescale_with_baseline"],
        "file_count": len(files),
        "tree_id": value_sha256({"root": model["root"], "files": files}),
    }


def _evaluation_probe(python_executable: Path) -> dict[str, Any]:
    package_mapping = json.dumps(EVALUATION_REQUIRED_PACKAGES, sort_keys=True)
    probe_source = f"""
import importlib
import importlib.metadata
import json
import platform
import site
import sys

packages = {{}}
for distribution, import_name in {package_mapping}.items():
    importlib.import_module(import_name)
    packages[distribution] = importlib.metadata.version(distribution)
print(json.dumps({{
    "python_executable": str(__import__("pathlib").Path(sys.executable).resolve()),
    "python_version": platform.python_version(),
    "sys_prefix": str(__import__("pathlib").Path(sys.prefix).resolve()),
    "user_site_enabled": site.ENABLE_USER_SITE,
    "isolated_mode": bool(sys.flags.isolated),
    "no_user_site": bool(sys.flags.no_user_site),
    "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
    "ignore_environment": bool(sys.flags.ignore_environment),
    "packages": packages,
}}, sort_keys=True))
"""
    try:
        with tempfile.TemporaryDirectory(
            prefix="agentreview-coco-evaluation-probe-"
        ) as directory:
            result = subprocess.run(
                [str(python_executable), "-s", "-B", "-c", probe_source],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                env=dict(_EVALUATION_PROBE_ENVIRONMENT),
                cwd=directory,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("CoCo evaluation Python probe failed") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"CoCo evaluation Python probe failed{suffix}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CoCo evaluation Python probe output is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("CoCo evaluation Python probe output is invalid")
    return value


def _stable_symlink_target(path: Path, *, prefix: Path) -> str:
    before = path.lstat()
    if not stat.S_ISLNK(before.st_mode):
        raise ValueError(f"CoCo evaluation prefix entry is not a symlink: {path}")
    target = os.readlink(path)
    try:
        resolved_target = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(
            f"CoCo evaluation prefix contains a dangling symlink: {path}"
        ) from exc
    after = path.lstat()
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
        raise RuntimeError(
            f"CoCo evaluation prefix symlink changed while being hashed: {path}"
        )
    if not _is_within(resolved_target, prefix):
        raise ValueError(
            f"CoCo evaluation prefix symlink escapes its sys.prefix: {path}"
        )
    return target


def _evaluation_prefix_tree(prefix: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    file_count = 0
    total_bytes = 0
    symlink_count = 0
    pyc_file_count = 0

    def walk_error(error: OSError) -> None:
        raise RuntimeError("CoCo evaluation Python prefix is unreadable") from error

    for directory, directory_names, file_names in os.walk(
        prefix,
        topdown=True,
        followlinks=False,
        onerror=walk_error,
    ):
        directory_path = Path(directory)
        retained_directories: list[str] = []
        for name in sorted(directory_names):
            path = directory_path / name
            relative = path.relative_to(prefix).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                entries.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "target": _stable_symlink_target(path, prefix=prefix),
                    }
                )
                symlink_count += 1
            elif stat.S_ISDIR(mode):
                retained_directories.append(name)
            else:
                raise ValueError(
                    f"CoCo evaluation prefix contains a special entry: {path}"
                )
        directory_names[:] = retained_directories

        for name in sorted(file_names):
            path = directory_path / name
            relative = path.relative_to(prefix).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                entries.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "target": _stable_symlink_target(path, prefix=prefix),
                    }
                )
                symlink_count += 1
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(
                    f"CoCo evaluation prefix contains a special entry: {path}"
                )
            digest, size, _ = _stable_file_fingerprint(path)
            entries.append(
                {
                    "path": relative,
                    "type": "regular",
                    "size_bytes": size,
                    "sha256": digest,
                }
            )
            file_count += 1
            total_bytes += size
            if path.suffix == ".pyc":
                pyc_file_count += 1

    entries.sort(key=lambda item: (item["path"], item["type"]))
    return {
        "root": str(prefix),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "symlink_count": symlink_count,
        "pyc_file_count": pyc_file_count,
        "tree_sha256": value_sha256(entries),
    }


def _evaluation_runtime_identity(value: str | Path) -> dict[str, Any]:
    requested = Path(value).expanduser()
    try:
        python_executable = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"CoCo evaluation Python executable is missing: {requested}"
        ) from exc
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise ValueError(
            f"CoCo evaluation Python executable is not executable: {requested}"
        )
    probe = _evaluation_probe(python_executable)
    expected_probe_fields = {
        "python_executable",
        "python_version",
        "sys_prefix",
        "user_site_enabled",
        "isolated_mode",
        "no_user_site",
        "dont_write_bytecode",
        "ignore_environment",
        "packages",
    }
    if set(probe) != expected_probe_fields:
        raise RuntimeError("CoCo evaluation Python probe fields differ")
    try:
        observed_executable = Path(probe["python_executable"]).resolve(strict=True)
        prefix = Path(probe["sys_prefix"]).resolve(strict=True)
    except (FileNotFoundError, TypeError) as exc:
        raise RuntimeError("CoCo evaluation Python probe paths are invalid") from exc
    if observed_executable != python_executable:
        raise ValueError("CoCo evaluation Python executable identity differs")
    if not prefix.is_dir() or not _is_within(python_executable, prefix):
        raise ValueError("CoCo evaluation Python executable escapes its sys.prefix")
    if (
        not isinstance(probe["python_version"], str)
        or not probe["python_version"]
        or probe["user_site_enabled"] is not False
        or probe["isolated_mode"] is not False
        or probe["no_user_site"] is not True
        or probe["dont_write_bytecode"] is not True
        or probe["ignore_environment"] is not False
    ):
        raise ValueError("CoCo evaluation Python isolation identity differs")
    packages = probe["packages"]
    if (
        not isinstance(packages, dict)
        or set(packages) != set(EVALUATION_REQUIRED_PACKAGES)
        or any(not isinstance(version, str) or not version for version in packages.values())
    ):
        raise ValueError("CoCo evaluation Python package closure differs")
    return {
        "python_executable": str(python_executable),
        "python_version": probe["python_version"],
        "sys_prefix": str(prefix),
        "user_site_enabled": False,
        "isolated_mode": False,
        "no_user_site": True,
        "dont_write_bytecode": True,
        "ignore_environment": False,
        "packages": dict(sorted(packages.items())),
        "prefix_tree": _evaluation_prefix_tree(prefix),
    }


def _scientific_settings(
    benchmark: dict[str, Any],
    models: dict[str, Any],
) -> dict[str, Any]:
    return {
        "split": {
            "base_url": "http://127.0.0.1:13099/v1",
            "model": "Qwen3-8B-Split",
            "max_in_flight_client_requests": 64,
            "max_completion_tokens": FORMAL_SPLIT_MAX_COMPLETION_TOKENS,
            "context_window_tokens": FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS,
            "prompt_preflight": {
                "algorithm": FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM,
                "max_model_len": FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS,
                "max_completion_tokens": FORMAL_SPLIT_MAX_COMPLETION_TOKENS,
                "enable_thinking": True,
            },
            "effort": "medium",
        },
        "classify": {
            "base_url": "http://127.0.0.1:13098/v1",
            "model": "Qwen3-8B-Classify",
            "max_in_flight_client_requests": 128,
            "max_completion_tokens": FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS,
            "context_window_tokens": FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS,
            "prompt_preflight": {
                "algorithm": FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM,
                "max_model_len": FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS,
                "max_completion_tokens": FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS,
                "enable_thinking": True,
            },
            "effort": "medium",
        },
        "evaluation": {
            "enabled": True,
            "require_complete": True,
            "benchmark_files": [item["path"] for item in benchmark["files"]],
            "complete_human_classify_file": benchmark["complete_human_classify"][
                "path"
            ],
            "reviewer_selection_file": benchmark["reviewer_selection"]["path"],
            "base_url": FORMAL_JUDGE_BASE_URL,
            "model": FORMAL_JUDGE_MODEL_NAME,
            "max_in_flight_client_requests": 4,
            "effort": "none",
            "temperature": 0.0,
            "max_completion_tokens": FORMAL_EVALUATION_MAX_COMPLETION_TOKENS,
            "context_window_tokens": FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS,
            "prompt_preflight": {
                "algorithm": FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM,
                "max_model_len": FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS,
                "max_completion_tokens": FORMAL_EVALUATION_MAX_COMPLETION_TOKENS,
                "enable_thinking": False,
            },
            "cuda_visible_devices": "",
            "bert_score_model": _evaluation_model_identity(models["evaluation"]),
        },
    }


def _verify_judge_model_identity() -> None:
    root = Path(FORMAL_JUDGE_MODEL_ROOT)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or root.resolve() != root
        or not root.is_dir()
    ):
        raise ValueError("CoCo judge model root is not a canonical directory")
    metadata_path = root / ".hfd" / "repo_metadata.json"
    tokenizer_path = root / "tokenizer.json"
    chat_template_path = root / "chat_template.jinja"
    for path, label in (
        (metadata_path, "source metadata"),
        (tokenizer_path, "tokenizer"),
        (chat_template_path, "chat template"),
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"CoCo judge {label} file identity is invalid")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CoCo judge source metadata is unreadable") from exc
    observed = {
        "source_model": metadata.get("id") if isinstance(metadata, dict) else None,
        "revision": metadata.get("sha") if isinstance(metadata, dict) else None,
        "tokenizer_sha256": file_sha256(tokenizer_path),
        "chat_template_sha256": file_sha256(chat_template_path),
    }
    if observed != {
        "source_model": FORMAL_JUDGE_SOURCE_MODEL,
        "revision": FORMAL_JUDGE_REVISION,
        "tokenizer_sha256": FORMAL_JUDGE_TOKENIZER_SHA256,
        "chat_template_sha256": FORMAL_JUDGE_CHAT_TEMPLATE_SHA256,
    }:
        raise ValueError("CoCo judge model source identity differs")


def _service_identities(
    models: dict[str, Any],
    *,
    evaluation_python: str | Path,
) -> dict[str, Any]:
    _verify_judge_model_identity()
    services = {
        "software": {"vllm": "0.20.1", "transformers": "5.8.1"},
        "split": {
            "cuda_visible_devices": "0",
            "python_executable": "/data0/xyj/miniconda3/envs/qwen38serve/bin/python",
            "vllm_attention_backend": "FLASH_ATTN",
            "host": "127.0.0.1",
            "port": 13099,
            "served_model_name": "Qwen3-8B-Split",
            "model_root": models["split"]["root"],
            "max_model_len": 40960,
            "max_num_seqs": 8,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
            "dtype": "bfloat16",
            "language_model_only": True,
            "reasoning_parser": "qwen3",
            "enable_prefix_caching": True,
            "enforce_eager": True,
        },
        "classify": {
            "cuda_visible_devices": "1",
            "python_executable": "/data0/xyj/miniconda3/envs/qwen38serve/bin/python",
            "vllm_attention_backend": "FLASH_ATTN",
            "host": "127.0.0.1",
            "port": 13098,
            "served_model_name": "Qwen3-8B-Classify",
            "model_root": models["classify"]["root"],
            "max_model_len": 32768,
            "max_num_seqs": 16,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
            "dtype": "bfloat16",
            "language_model_only": True,
            "reasoning_parser": "qwen3",
            "enable_prefix_caching": True,
            "enforce_eager": True,
        },
        "judge": {
            "cuda_visible_devices": "3,6",
            "python_executable": "/data0/xyj/miniconda3/envs/qwen38serve/bin/python",
            "vllm_attention_backend": "",
            "host": FORMAL_JUDGE_HOST,
            "port": FORMAL_JUDGE_PORT,
            "served_model_name": FORMAL_JUDGE_MODEL_NAME,
            "model_root": FORMAL_JUDGE_MODEL_ROOT,
            "source_model": FORMAL_JUDGE_SOURCE_MODEL,
            "revision": FORMAL_JUDGE_REVISION,
            "tokenizer_sha256": FORMAL_JUDGE_TOKENIZER_SHA256,
            "chat_template_sha256": FORMAL_JUDGE_CHAT_TEMPLATE_SHA256,
            "max_model_len": FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS,
            "max_num_seqs": 8,
            "tensor_parallel_size": 2,
            "gpu_memory_utilization": 0.90,
            "dtype": "bfloat16",
            "language_model_only": True,
            "reasoning_parser": "qwen3",
            "enable_prefix_caching": True,
            "enforce_eager": False,
            "disable_custom_all_reduce": True,
            "default_chat_template_kwargs": {"enable_thinking": False},
            "working_directory": FORMAL_JUDGE_WORKING_DIRECTORY,
            "mode_lease": qwen27b_mode_lease_identity(),
            **judge_weight_identity(),
        },
    }
    services["evaluation"] = {
        **_evaluation_runtime_identity(evaluation_python),
    }
    return services


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
    if scoring_root.parent.resolve() != scoring_root.parent or scoring_root.is_symlink():
        raise ValueError("CoCo scoring output root must not use a symbolic link")
    bindings: dict[str, Any] = {}
    for mode in ARMS:
        output = scoring_root / f"{winner_run_id}-{mode}"
        if output.parent.resolve() != output.parent or output.is_symlink():
            raise ValueError(
                f"CoCo scoring {mode} output must not use a symbolic link"
            )
        bindings[mode] = {
            "mode": mode,
            "output_root": str(output),
            "cache_versions": {
                stage: f"{winner_run_id}-{mode}-{stage}-cache-v1"
                for stage in CACHE_STAGES
            },
        }
    return bindings


def _build_body(
    *,
    coco_root: Path,
    artifact_root: Path,
    winner_run_id: str,
    evaluation_python: str | Path,
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
            raise ValueError("CoCo scoring model roots must be distinct")
        resolved_model_roots.add(inventory["root"])
        if role == "evaluation":
            inventory = {
                "model_type": EVALUATION_MODEL_TYPE,
                **inventory,
                "num_layers": EVALUATION_MODEL_NUM_LAYERS,
                "rescale_with_baseline": False,
            }
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
        "scientific_settings": _scientific_settings(benchmark, models),
        "services": _service_identities(
            models,
            evaluation_python=evaluation_python,
        ),
        "arms": _arm_bindings(
            artifact_root=artifact_root,
            winner_run_id=winner_run_id,
        ),
    }


def _require_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"CoCo scoring spec {label} fields differ")
    return value


def _require_non_empty_text(value: Any, *, label: str) -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"CoCo scoring spec {label} must be a non-empty string")


def _require_positive_integer(value: Any, *, label: str) -> int:
    if type(value) is int and value > 0:
        return value
    raise ValueError(f"CoCo scoring spec {label} must be a positive integer")


def _require_gpu_utilization(value: Any, *, label: str) -> float:
    if (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0.0 < float(value) <= 1.0
    ):
        return float(value)
    raise ValueError(
        f"CoCo scoring spec {label} must be a finite number in (0, 1]"
    )


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
    _require_non_empty_text(value.get("artifact_root"), label="artifact_root")

    repository = _require_fields(
        value.get("repository"),
        {"root", "git_commit", "clean"},
        label="repository",
    )
    if (
        not isinstance(repository["root"], str)
        or not repository["root"]
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
        inventory_fields = {"root", "files"}
        if role == "evaluation":
            inventory_fields |= {
                "model_type",
                "num_layers",
                "rescale_with_baseline",
            }
        inventory = _require_fields(
            models[role],
            inventory_fields,
            label=f"models.{role}",
        )
        if role == "evaluation" and (
            inventory["model_type"] != EVALUATION_MODEL_TYPE
            or type(inventory["num_layers"]) is not int
            or inventory["num_layers"] != EVALUATION_MODEL_NUM_LAYERS
            or inventory["rescale_with_baseline"] is not False
        ):
            raise ValueError(
                "CoCo scoring spec models.evaluation scientific identity differs"
            )
        _require_non_empty_text(
            inventory["root"],
            label=f"models.{role}.root",
        )
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
        fields = {"base_url", "model", "max_in_flight_client_requests", "effort"}
        if role in {"split", "classify"}:
            fields |= {
                "max_completion_tokens",
                "context_window_tokens",
                "prompt_preflight",
            }
        stage_settings = _require_fields(
            settings[role],
            fields,
            label=f"scientific_settings.{role}",
        )
        for name in ("base_url", "model", "effort"):
            _require_non_empty_text(
                stage_settings[name],
                label=f"scientific_settings.{role}.{name}",
            )
        _require_positive_integer(
            stage_settings["max_in_flight_client_requests"],
            label=(
                f"scientific_settings.{role}.max_in_flight_client_requests"
            ),
        )
        expected_max_completion_tokens = (
            FORMAL_SPLIT_MAX_COMPLETION_TOKENS
            if role == "split"
            else FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS
        )
        expected_context_window_tokens = (
            FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS
            if role == "split"
            else FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS
        )
        expected_preflight_algorithm = (
            FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM
            if role == "split"
            else FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM
        )
        if (
            type(stage_settings["max_completion_tokens"]) is not int
            or stage_settings["max_completion_tokens"]
            != expected_max_completion_tokens
            or type(stage_settings["context_window_tokens"]) is not int
            or stage_settings["context_window_tokens"]
            != expected_context_window_tokens
        ):
            raise ValueError(
                f"CoCo scoring spec scientific_settings.{role} token budgets differ"
            )
        preflight = _require_fields(
            stage_settings["prompt_preflight"],
            {
                "algorithm",
                "max_model_len",
                "max_completion_tokens",
                "enable_thinking",
            },
            label=f"scientific_settings.{role}.prompt_preflight",
        )
        if (
            preflight["algorithm"] != expected_preflight_algorithm
            or type(preflight["max_model_len"]) is not int
            or preflight["max_model_len"] != expected_context_window_tokens
            or type(preflight["max_completion_tokens"]) is not int
            or preflight["max_completion_tokens"]
            != expected_max_completion_tokens
            or preflight["enable_thinking"] is not True
        ):
            raise ValueError(
                f"CoCo scoring spec scientific_settings.{role} prompt preflight "
                "identity differs"
            )
    evaluation_settings = _require_fields(
        settings["evaluation"],
        {
            "enabled",
            "require_complete",
            "benchmark_files",
            "complete_human_classify_file",
            "reviewer_selection_file",
            "base_url",
            "model",
            "max_in_flight_client_requests",
            "effort",
            "temperature",
            "max_completion_tokens",
            "context_window_tokens",
            "prompt_preflight",
            "cuda_visible_devices",
            "bert_score_model",
        },
        label="scientific_settings.evaluation",
    )
    if evaluation_settings["enabled"] is not True:
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.enabled must be true"
        )
    if evaluation_settings["require_complete"] is not True:
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.require_complete "
            "must be true"
        )
    benchmark_files = evaluation_settings["benchmark_files"]
    if not isinstance(benchmark_files, list) or not benchmark_files or any(
        not isinstance(path, str) or not path for path in benchmark_files
    ):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.benchmark_files "
            "must be a non-empty string list"
        )
    for name in (
        "complete_human_classify_file",
        "reviewer_selection_file",
        "base_url",
        "model",
        "effort",
    ):
        _require_non_empty_text(
            evaluation_settings[name],
            label=f"scientific_settings.evaluation.{name}",
        )
    _require_positive_integer(
        evaluation_settings["max_in_flight_client_requests"],
        label="scientific_settings.evaluation.max_in_flight_client_requests",
    )
    if evaluation_settings["base_url"] != FORMAL_JUDGE_BASE_URL:
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.base_url differs"
        )
    if evaluation_settings["model"] != FORMAL_JUDGE_MODEL_NAME:
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.model differs"
        )
    if evaluation_settings["max_in_flight_client_requests"] != 4:
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation client limit differs"
        )
    if evaluation_settings["effort"] != "none":
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation effort differs"
        )
    if (
        isinstance(evaluation_settings["temperature"], bool)
        or not isinstance(evaluation_settings["temperature"], (int, float))
        or float(evaluation_settings["temperature"]) != 0.0
    ):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.temperature "
            "must be zero"
        )
    if (
        type(evaluation_settings["max_completion_tokens"]) is not int
        or evaluation_settings["max_completion_tokens"]
        != FORMAL_EVALUATION_MAX_COMPLETION_TOKENS
        or type(evaluation_settings["context_window_tokens"]) is not int
        or evaluation_settings["context_window_tokens"]
        != FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS
    ):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation token budgets differ"
        )
    evaluation_preflight = _require_fields(
        evaluation_settings["prompt_preflight"],
        {
            "algorithm",
            "max_model_len",
            "max_completion_tokens",
            "enable_thinking",
        },
        label="scientific_settings.evaluation.prompt_preflight",
    )
    if (
        evaluation_preflight["algorithm"]
        != FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM
        or type(evaluation_preflight["max_model_len"]) is not int
        or evaluation_preflight["max_model_len"]
        != FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS
        or type(evaluation_preflight["max_completion_tokens"]) is not int
        or evaluation_preflight["max_completion_tokens"]
        != FORMAL_EVALUATION_MAX_COMPLETION_TOKENS
        or evaluation_preflight["enable_thinking"] is not False
    ):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation prompt preflight "
            "identity differs"
        )
    if not isinstance(evaluation_settings["cuda_visible_devices"], str):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.cuda_visible_devices "
            "must be a string"
        )
    bert_score_model = _require_fields(
        evaluation_settings["bert_score_model"],
        {
            "model_type",
            "root",
            "num_layers",
            "rescale_with_baseline",
            "file_count",
            "tree_id",
        },
        label="scientific_settings.evaluation.bert_score_model",
    )
    if (
        bert_score_model["model_type"] != EVALUATION_MODEL_TYPE
        or type(bert_score_model["num_layers"]) is not int
        or bert_score_model["num_layers"] != EVALUATION_MODEL_NUM_LAYERS
        or bert_score_model["rescale_with_baseline"] is not False
        or type(bert_score_model["file_count"]) is not int
        or bert_score_model["file_count"] < 1
        or not isinstance(bert_score_model["root"], str)
        or not bert_score_model["root"]
        or not isinstance(bert_score_model["tree_id"], str)
        or not _SHA256_PATTERN.fullmatch(bert_score_model["tree_id"])
    ):
        raise ValueError(
            "CoCo scoring spec scientific_settings.evaluation.bert_score_model "
            "is invalid"
        )
    if bert_score_model != _evaluation_model_identity(models["evaluation"]):
        raise ValueError(
            "CoCo scoring spec scientific evaluation model projection differs"
        )

    services = _require_fields(
        value.get("services"),
        {"software", "split", "classify", "judge", "evaluation"},
        label="services",
    )
    software = _require_fields(
        services["software"],
        {"vllm", "transformers"},
        label="services.software",
    )
    for name in ("vllm", "transformers"):
        _require_non_empty_text(software[name], label=f"services.software.{name}")
    service_fields = {
        "cuda_visible_devices",
        "python_executable",
        "vllm_attention_backend",
        "host",
        "port",
        "served_model_name",
        "model_root",
        "max_model_len",
        "max_num_seqs",
        "tensor_parallel_size",
        "gpu_memory_utilization",
        "dtype",
        "language_model_only",
        "reasoning_parser",
        "enable_prefix_caching",
        "enforce_eager",
    }
    for role in ("split", "classify"):
        service = _require_fields(
            services[role],
            service_fields,
            label=f"services.{role}",
        )
        for name in (
            "cuda_visible_devices",
            "python_executable",
            "vllm_attention_backend",
            "host",
            "served_model_name",
            "model_root",
            "dtype",
            "reasoning_parser",
        ):
            _require_non_empty_text(
                service[name],
                label=f"services.{role}.{name}",
            )
        for name in (
            "port",
            "max_model_len",
            "max_num_seqs",
            "tensor_parallel_size",
        ):
            _require_positive_integer(
                service[name],
                label=f"services.{role}.{name}",
            )
        _require_gpu_utilization(
            service["gpu_memory_utilization"],
            label=f"services.{role}.gpu_memory_utilization",
        )
        for name in (
            "language_model_only",
            "enable_prefix_caching",
            "enforce_eager",
        ):
            if service[name] is not True:
                raise ValueError(
                    f"CoCo scoring spec services.{role}.{name} must be true"
                )

    judge = _require_fields(
        services["judge"],
        service_fields
        | {
            "disable_custom_all_reduce",
            "default_chat_template_kwargs",
            "working_directory",
            "source_model",
            "revision",
            "tokenizer_sha256",
            "chat_template_sha256",
            "config_sha256",
            "tokenizer_config_sha256",
            "generation_config_sha256",
            "model_index_sha256",
            "weight_file_count",
            "weight_total_bytes",
            "weights_tree_sha256",
            "mode_lease",
        },
        label="services.judge",
    )
    for name in (
        "cuda_visible_devices",
        "python_executable",
        "host",
        "served_model_name",
        "model_root",
        "dtype",
        "reasoning_parser",
        "working_directory",
        "source_model",
        "revision",
    ):
        _require_non_empty_text(judge[name], label=f"services.judge.{name}")
    if judge["vllm_attention_backend"] != "":
        raise ValueError(
            "CoCo scoring spec services.judge.vllm_attention_backend must be empty"
        )
    for name in ("port", "max_model_len", "max_num_seqs", "tensor_parallel_size"):
        _require_positive_integer(judge[name], label=f"services.judge.{name}")
    _require_gpu_utilization(
        judge["gpu_memory_utilization"],
        label="services.judge.gpu_memory_utilization",
    )
    expected_judge = {
        "cuda_visible_devices": "3,6",
        "python_executable": "/data0/xyj/miniconda3/envs/qwen38serve/bin/python",
        "vllm_attention_backend": "",
        "host": FORMAL_JUDGE_HOST,
        "port": FORMAL_JUDGE_PORT,
        "served_model_name": FORMAL_JUDGE_MODEL_NAME,
        "model_root": FORMAL_JUDGE_MODEL_ROOT,
        "source_model": FORMAL_JUDGE_SOURCE_MODEL,
        "revision": FORMAL_JUDGE_REVISION,
        "tokenizer_sha256": FORMAL_JUDGE_TOKENIZER_SHA256,
        "chat_template_sha256": FORMAL_JUDGE_CHAT_TEMPLATE_SHA256,
        "max_model_len": FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS,
        "max_num_seqs": 8,
        "tensor_parallel_size": 2,
        "gpu_memory_utilization": 0.90,
        "dtype": "bfloat16",
        "language_model_only": True,
        "reasoning_parser": "qwen3",
        "enable_prefix_caching": True,
        "enforce_eager": False,
        "disable_custom_all_reduce": True,
        "default_chat_template_kwargs": {"enable_thinking": False},
        "working_directory": FORMAL_JUDGE_WORKING_DIRECTORY,
        "mode_lease": qwen27b_mode_lease_identity(),
        **judge_weight_identity(),
    }
    if judge != expected_judge:
        raise ValueError("CoCo scoring spec services.judge identity differs")

    evaluation_runtime = _require_fields(
        services["evaluation"],
        {
            "python_executable",
            "python_version",
            "sys_prefix",
            "user_site_enabled",
            "isolated_mode",
            "no_user_site",
            "dont_write_bytecode",
            "ignore_environment",
            "packages",
            "prefix_tree",
        },
        label="services.evaluation",
    )
    for name in ("python_executable", "python_version", "sys_prefix"):
        _require_non_empty_text(
            evaluation_runtime[name],
            label=f"services.evaluation.{name}",
        )
    if (
        evaluation_runtime["user_site_enabled"] is not False
        or evaluation_runtime["isolated_mode"] is not False
        or evaluation_runtime["no_user_site"] is not True
        or evaluation_runtime["dont_write_bytecode"] is not True
        or evaluation_runtime["ignore_environment"] is not False
    ):
        raise ValueError(
            "CoCo scoring spec evaluation Python isolation identity differs"
        )
    packages = _require_fields(
        evaluation_runtime["packages"],
        set(EVALUATION_REQUIRED_PACKAGES),
        label="services.evaluation.packages",
    )
    for name, version in packages.items():
        _require_non_empty_text(
            version,
            label=f"services.evaluation.packages.{name}",
        )
    prefix_tree = _require_fields(
        evaluation_runtime["prefix_tree"],
        {
            "root",
            "file_count",
            "total_bytes",
            "symlink_count",
            "pyc_file_count",
            "tree_sha256",
        },
        label="services.evaluation.prefix_tree",
    )
    _require_non_empty_text(
        prefix_tree["root"],
        label="services.evaluation.prefix_tree.root",
    )
    for name in ("file_count", "total_bytes", "symlink_count", "pyc_file_count"):
        if type(prefix_tree[name]) is not int or prefix_tree[name] < 0:
            raise ValueError(
                f"CoCo scoring spec services.evaluation.prefix_tree.{name} "
                "must be a non-negative integer"
            )
    if prefix_tree["file_count"] < 1:
        raise ValueError(
            "CoCo scoring spec services.evaluation.prefix_tree.file_count "
            "must be positive"
        )
    if prefix_tree["pyc_file_count"] > prefix_tree["file_count"]:
        raise ValueError(
            "CoCo scoring spec evaluation Python pyc inventory is invalid"
        )
    if (
        prefix_tree["root"] != evaluation_runtime["sys_prefix"]
        or not isinstance(prefix_tree["tree_sha256"], str)
        or not _SHA256_PATTERN.fullmatch(prefix_tree["tree_sha256"])
    ):
        raise ValueError(
            "CoCo scoring spec evaluation Python prefix identity is invalid"
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
        if (
            arm["mode"] != mode
            or not isinstance(arm["output_root"], str)
            or not arm["output_root"]
        ):
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
    evaluation_python: str | Path,
) -> dict[str, Any]:
    """Materialize one deterministic, immutable, self-hashed scoring spec."""
    root = Path(coco_root).expanduser().resolve()
    artifacts = Path(os.path.abspath(Path(artifact_root).expanduser()))
    if artifacts.parent.resolve() != artifacts.parent or artifacts.is_symlink():
        raise ValueError("CoCo artifact root must not use a symbolic link")
    winner = _validate_winner_run_id(winner_run_id)
    body = _build_body(
        coco_root=root,
        artifact_root=artifacts,
        winner_run_id=winner,
        evaluation_python=evaluation_python,
    )
    confirmed_body = _build_body(
        coco_root=root,
        artifact_root=artifacts,
        winner_run_id=winner,
        evaluation_python=evaluation_python,
    )
    if confirmed_body != body:
        raise RuntimeError(
            "CoCo scoring inputs changed between materialization passes"
        )
    value = {**body, "spec_id": value_sha256(body)}
    _validate_shape(value)
    target = atomic_write_json(output_path, value, reject_symlink=True)
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
    source = Path(os.path.abspath(Path(path).expanduser()))
    if source.parent.resolve() != source.parent or source.is_symlink():
        raise ValueError("CoCo scoring spec must not use a symbolic link")
    if not source.is_file():
        raise FileNotFoundError(f"CoCo scoring spec is missing: {source}")
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
        evaluation_python=value["services"]["evaluation"]["python_executable"],
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
    "FORMAL_CLASSIFY_CONTEXT_WINDOW_TOKENS",
    "FORMAL_CLASSIFY_MAX_COMPLETION_TOKENS",
    "FORMAL_CLASSIFY_PROMPT_PREFLIGHT_ALGORITHM",
    "FORMAL_EVALUATION_CONTEXT_WINDOW_TOKENS",
    "FORMAL_EVALUATION_MAX_COMPLETION_TOKENS",
    "FORMAL_EVALUATION_PROMPT_PREFLIGHT_ALGORITHM",
    "FORMAL_JUDGE_BASE_URL",
    "FORMAL_JUDGE_HOST",
    "FORMAL_JUDGE_MODEL_NAME",
    "FORMAL_JUDGE_MODEL_ROOT",
    "FORMAL_JUDGE_SOURCE_MODEL",
    "FORMAL_JUDGE_REVISION",
    "FORMAL_JUDGE_TOKENIZER_SHA256",
    "FORMAL_JUDGE_CHAT_TEMPLATE_SHA256",
    "FORMAL_JUDGE_PORT",
    "FORMAL_JUDGE_WORKING_DIRECTORY",
    "FORMAL_SPLIT_CONTEXT_WINDOW_TOKENS",
    "FORMAL_SPLIT_MAX_COMPLETION_TOKENS",
    "FORMAL_SPLIT_PROMPT_PREFLIGHT_ALGORITHM",
    "PROTOCOL",
    "judge_weight_identity",
    "materialize_coco_scoring_spec",
    "validate_coco_scoring_spec",
]
