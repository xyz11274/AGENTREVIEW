"""Stable loader and identity rules for standalone MemoryProgram files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Union

from .tasks import ALL_TASKS, META_TASK


@dataclass(frozen=True)
class MemoryProgramIdentity:
    path: str
    logical_identity: str
    source_kind: str
    sha256: str

    def to_dict(self) -> dict:
        """Return the stable public identity without a host-specific path."""
        return {
            "logical_identity": self.logical_identity,
            "source_kind": self.source_kind,
        }


class MemoryProgramLoader:
    """Resolve a seed, promoted winner, or future optimizer output directly."""

    @staticmethod
    def resolve(value: Union[str, Path, MemoryProgramIdentity], task_id: str = "") -> Path:
        if isinstance(value, MemoryProgramIdentity):
            path = Path(value.path).expanduser().resolve()
        else:
            path = Path(value).expanduser().resolve()
        if path.is_file():
            if path.suffix.lower() != ".py":
                raise ValueError(f"MemoryProgram must be a Python file: {path}")
            return path
        if not task_id:
            raise FileNotFoundError(f"MemoryProgram not found: {path}")
        if task_id not in ALL_TASKS:
            raise ValueError(f"Unknown review task: {task_id}")
        candidates = (
            path / task_id / "best_program.py",
            path / task_id.lower() / "best_program.py",
            path / f"{task_id}.py",
        )
        matches = [candidate for candidate in candidates if candidate.is_file()]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Expected exactly one {task_id} MemoryProgram under {path}; "
                f"found {[str(candidate) for candidate in matches]}"
            )
        return matches[0].resolve()

    @classmethod
    def identity(
        cls, value: Union[str, Path, MemoryProgramIdentity], task_id: str = ""
    ) -> MemoryProgramIdentity:
        path = cls.resolve(value, task_id)
        name = path.name
        if name == "best_program.py":
            source_kind = "promoted_best_program"
            logical_identity = f"{path.parent.name}/best_program"
        elif "seed" in name or path.parent.name == "seeds":
            source_kind = "seed"
            logical_identity = path.stem
        else:
            source_kind = "optimizer_output"
            logical_identity = path.stem
        return MemoryProgramIdentity(
            path=str(path),
            logical_identity=logical_identity,
            source_kind=source_kind,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def resolve_program_path(program_root: str, task_id: str) -> Path:
    """Backward-compatible suite resolver."""
    return MemoryProgramLoader.resolve(program_root, task_id)


def verify_promoted_program(
    program_root: str,
    task_id: str,
    program_path: Path,
    embedding_model_name: str,
    embedding_revision: str,
    embedding_fingerprint: str = "",
) -> None:
    """Preserve legacy promotion provenance checks without importing MSTAR."""
    root = Path(program_root).expanduser().resolve()
    manifest_path = root / (
        "meta_manifest.json" if task_id == META_TASK else "suite_manifest.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Promotion manifest is required: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    promoted = manifest.get("promoted") or {}
    entry = promoted if task_id == META_TASK else promoted.get(task_id) or {}
    expected_hash = str(entry.get("program_sha256") or "")
    actual_hash = hashlib.sha256(Path(program_path).read_bytes()).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise RuntimeError(
            f"{task_id} promoted program checksum mismatch: "
            f"expected={expected_hash or '<missing>'}, actual={actual_hash}"
        )
    source_run = Path(str(entry.get("source_run") or ""))
    config_path = source_run / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Promoted source config is required: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected_model = str(config.get("embedding_model_name") or "")
    expected_revision = str(config.get("embedding_revision") or "")
    expected_fingerprint = str(config.get("embedding_fingerprint") or "")
    if (
        expected_model != str(embedding_model_name)
        or expected_revision != str(embedding_revision)
        or (
            expected_fingerprint
            and expected_fingerprint != str(embedding_fingerprint)
        )
    ):
        raise RuntimeError(
            f"{task_id} embedding provenance mismatch: evolved with "
            f"{expected_model}@{expected_revision} "
            f"fingerprint={expected_fingerprint or '<legacy>'}, deployed with "
            f"{embedding_model_name}@{embedding_revision} "
            f"fingerprint={embedding_fingerprint or '<missing>'}"
        )
