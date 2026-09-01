"""Formal CoCo-1300 Review generation from one frozen evolved system.

The coordinator deliberately projects the frozen CoCo manifest down to paper
asset identities before scheduling work. CoCo scores, taxonomies, and human
reviews are never exposed to either evolved Reviewer Programs or the model.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Protocol

from agentreview.reviewer_identity import (
    SPECIALIST_REQUEST_TEMPLATE,
    build_meta_review_question,
    load_meta_synthesizer,
    load_reviewer_suite,
)

from .constants import META_MAX_TOKENS, ROLES, SCHEMA_VERSION
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient, EmptyCompletionOutput
from .memory import (
    DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY,
    validate_memory_snapshot_artifact,
)
from .meta import (
    META_MAX_ATTEMPTS,
    META_RAW_PROTOCOL,
    MetaSynthesisInfrastructureFailure,
    MetaSynthesisPaperFailure,
    _final_review_id,
    _load_resumable_meta_raw,
    generate_final_review,
)
from .models import Paper
from .program_checks import REVIEW_PLAN_PROTOCOL, program_capability_manifest
from .review import (
    REVIEW_REQUEST_MATERIALIZATION_PROTOCOL,
    SpecialistReviewInfrastructureFailure,
    SpecialistReviewPaperFailure,
    _load_review_request,
    _load_resumable_raw_artifact,
    _review_id,
    frozen_paper_context,
    run_specialist_review,
    specialist_generation_identity,
)
from .review_preparation import (
    FORMAL_S2_INTERVENTION,
    RETRIEVAL_PROVENANCE_PROTOCOL,
    ReviewPlanError,
)
from .s2 import FrozenS2Store, format_s2_evidence

COCO_1300_PROTOCOL = "coco_1300_protocol_compatible_resample_v1"
COCO_1300_COUNT = 1300
BATCH_PROTOCOL = "research-v2-coco-review-batch-v1"
FAILURE_PROTOCOL = "research-v2-coco-review-failure-v1"
SCORING_SOURCE_PROTOCOL = "research-v2-coco-scoring-source-v1"
_SUMMARY_CHECKPOINT_INTERVAL = 100
_PAPER_FIELDS = {
    "schema_version",
    "paper_id",
    "title",
    "abstract",
    "text",
    "source",
    "paper_asset_id",
}
_FORBIDDEN_DATA_KEYS = {
    "review",
    "reviews",
    "human_review",
    "human_reviews",
    "meta_review",
    "reference_review",
    "reference_reviews",
    "decision",
    "score",
    "scores",
    "taxonomy",
    "taxonomies",
}
ProgressCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _contains_forbidden_data_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for name, item in value.items():
            normalized = str(name).strip().lower()
            if normalized in _FORBIDDEN_DATA_KEYS:
                return str(name)
            nested = _contains_forbidden_data_key(item)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _contains_forbidden_data_key(item)
            if nested is not None:
                return nested
    return None


class CocoReviewPaperFailure(RuntimeError):
    """One paper-local generation failed while the shared runtime stayed usable."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class CocoReviewInfrastructureFailure(RuntimeError):
    """A shared dependency failed; committed work can be resumed by run ID."""


class _CocoReviewSourceDrift(CocoReviewInfrastructureFailure):
    """A frozen file changed while an isolated task was executing."""


@dataclass(frozen=True)
class CocoReviewBatchOptions:
    """Operational settings that do not alter the frozen scientific system."""

    run_id: str
    max_workers: int = 8

    def validate(self) -> None:
        if not self.run_id.strip():
            raise ValueError("CoCo Review batch run_id is required")
        if Path(self.run_id).name != self.run_id or self.run_id in {".", ".."}:
            raise ValueError("CoCo Review batch run_id must be one path-safe name")
        if not 1 <= int(self.max_workers) <= 8:
            raise ValueError("CoCo Review batch max_workers must be in [1, 8]")


@dataclass(frozen=True)
class _RoleRuntime:
    role: str
    program_path: Path
    program_sha256: str
    program_version: str
    capability_manifest: dict[str, Any]
    snapshot_path: Path
    snapshot_sha256: str
    snapshot_id: str
    seed_kind: str


@dataclass(frozen=True)
class _CocoPaper:
    index: int
    paper_id: str
    paper_asset_id: str
    paper_path: Path
    paper_sha256: str
    paper: Paper
    s2_values: tuple[dict[str, Any], ...]
    s2_pool_version: Any


@dataclass(frozen=True)
class SpecialistInvocation:
    """All and only the frozen inputs for one Specialist Review."""

    paper_id: str
    paper_path: Path
    role: str
    program_path: Path
    snapshot_path: Path
    run_id: str
    output_root: Path
    model_identity: dict[str, Any]
    embedding_identity: dict[str, Any]
    require_bubblewrap: bool
    program_version: str
    frozen_s2_path: Path
    frozen_s2_store: Any
    s2_intervention: dict[str, str]

    @property
    def output_path(self) -> Path:
        return (
            self.output_root
            / "reviews"
            / self.run_id
            / self.paper_id
            / f"{self.role}.json"
        )


@dataclass(frozen=True)
class FinalInvocation:
    """Frozen five-Review bundle for one Meta or deterministic concat output."""

    paper_id: str
    paper_path: Path
    review_paths: tuple[Path, ...]
    mode: str
    run_id: str
    output_root: Path
    model_identity: dict[str, Any]
    embedding_identity: dict[str, Any]
    require_bubblewrap: bool

    @property
    def output_path(self) -> Path:
        return (
            self.output_root / "final_reviews" / self.run_id / f"{self.paper_id}.json"
        )


class ReviewGenerationAdapter(Protocol):
    """Synchronous boundary whose calls are globally bounded by the coordinator."""

    def validate_frozen_runtime(
        self,
        *,
        model_identity: dict[str, Any],
        embedding_identity: dict[str, Any],
        require_bubblewrap: bool,
    ) -> None: ...

    def generate_specialist(
        self,
        invocation: SpecialistInvocation,
    ) -> dict[str, Any]: ...

    def generate_final(self, invocation: FinalInvocation) -> dict[str, Any]: ...


class ResearchV2ReviewGenerationAdapter:
    """Production adapter over the existing Specialist and Meta seams."""

    def __init__(
        self,
        *,
        completion_client: CompletionClient,
        embedding_client: Any = None,
        embedding_identity: dict[str, Any],
        require_bubblewrap: bool = True,
    ):
        if not isinstance(require_bubblewrap, bool):
            raise TypeError("Review adapter require_bubblewrap must be a boolean")
        self.completion_client = completion_client
        self.embedding_client = embedding_client
        self.model_identity = (
            ResearchV2ReviewGenerationAdapter._actual_model_identity(self)
        )
        self.embedding_identity = (
            ResearchV2ReviewGenerationAdapter._actual_embedding_identity(
                self,
                embedding_identity,
            )
        )
        self.require_bubblewrap = require_bubblewrap

    def validate_frozen_runtime(
        self,
        *,
        model_identity: dict[str, Any],
        embedding_identity: dict[str, Any],
        require_bubblewrap: bool,
    ) -> None:
        """Fail before generation when production clients differ from the freeze."""
        actual_model = ResearchV2ReviewGenerationAdapter._actual_model_identity(
            self
        )
        if actual_model != self.model_identity or actual_model != model_identity:
            raise CocoReviewInfrastructureFailure(
                "Completion client identity differs from the frozen system"
            )
        actual_embedding = (
            ResearchV2ReviewGenerationAdapter._actual_embedding_identity(
                self,
                self.embedding_identity,
            )
        )
        if actual_embedding != embedding_identity:
            raise CocoReviewInfrastructureFailure(
                "Embedding client identity differs from the frozen system"
            )
        if (
            not isinstance(require_bubblewrap, bool)
            or self.require_bubblewrap is not require_bubblewrap
        ):
            raise CocoReviewInfrastructureFailure(
                "Bubblewrap requirement differs from the frozen system"
            )

    def generate_specialist(
        self,
        invocation: SpecialistInvocation,
    ) -> dict[str, Any]:
        ResearchV2ReviewGenerationAdapter.validate_frozen_runtime(
            self,
            model_identity=invocation.model_identity,
            embedding_identity=invocation.embedding_identity,
            require_bubblewrap=invocation.require_bubblewrap,
        )
        try:
            return run_specialist_review(
                paper_path=invocation.paper_path,
                role=invocation.role,
                program_path=invocation.program_path,
                snapshot_path=invocation.snapshot_path,
                run_id=invocation.run_id,
                output_root=invocation.output_root,
                completion_client=self.completion_client,
                model_identity=invocation.model_identity,
                program_version=invocation.program_version,
                frozen_s2_path=invocation.frozen_s2_path,
                frozen_s2_store=invocation.frozen_s2_store,
                s2_intervention=invocation.s2_intervention,
                embedding_client=self.embedding_client,
                embedding_identity=invocation.embedding_identity,
                require_bubblewrap=self.require_bubblewrap,
            )
        except SpecialistReviewPaperFailure as exc:
            raise CocoReviewPaperFailure("specialist_output", str(exc)) from exc
        except ReviewPlanError as exc:
            raise CocoReviewPaperFailure("review_plan", str(exc)) from exc
        except EmptyCompletionOutput as exc:
            raise CocoReviewPaperFailure("memory_output", str(exc)) from exc
        except SpecialistReviewInfrastructureFailure as exc:
            raise CocoReviewInfrastructureFailure(str(exc)) from exc

    def generate_final(self, invocation: FinalInvocation) -> dict[str, Any]:
        ResearchV2ReviewGenerationAdapter.validate_frozen_runtime(
            self,
            model_identity=invocation.model_identity,
            embedding_identity=invocation.embedding_identity,
            require_bubblewrap=invocation.require_bubblewrap,
        )
        try:
            return generate_final_review(
                paper_path=invocation.paper_path,
                review_paths=list(invocation.review_paths),
                mode=invocation.mode,
                run_id=invocation.run_id,
                output_root=invocation.output_root,
                completion_client=(
                    self.completion_client if invocation.mode == "meta" else None
                ),
                model_identity=(
                    invocation.model_identity if invocation.mode == "meta" else None
                ),
            )
        except MetaSynthesisPaperFailure as exc:
            raise CocoReviewPaperFailure("meta_output", str(exc)) from exc
        except MetaSynthesisInfrastructureFailure as exc:
            raise CocoReviewInfrastructureFailure(str(exc)) from exc

    def _actual_model_identity(self) -> dict[str, Any]:
        config = getattr(self.completion_client, "config", None)
        identity = getattr(config, "identity", None)
        actual = identity() if callable(identity) else None
        if not isinstance(actual, dict) or not actual:
            raise CocoReviewInfrastructureFailure(
                "Completion client must expose a non-empty config.identity()"
            )
        return dict(actual)

    def _actual_embedding_identity(
        self,
        declared: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(declared, dict) or not declared:
            raise ValueError("Review adapter embedding_identity is required")
        if self.embedding_client is None:
            fallback = dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY)
            if declared != fallback:
                raise ValueError(
                    "Review adapter without an embedding client requires the "
                    "deterministic fallback embedding identity"
                )
            return fallback
        expected_fields = {"provider", "model", "revision", "cache_path"}
        if set(declared) != expected_fields:
            raise ValueError(
                "Review adapter embedding_identity must contain exactly "
                "provider, model, revision, and cache_path"
            )
        cache_path = declared.get("cache_path")
        normalized = {
            "provider": declared.get("provider"),
            "model": declared.get("model"),
            "revision": declared.get("revision"),
            "cache_path": str(Path(str(cache_path or "")).expanduser().resolve()),
        }
        if (
            declared != normalized
            or any(
                not _is_nonempty_text(normalized[name])
                for name in ("provider", "model", "revision", "cache_path")
            )
        ):
            raise ValueError("Review adapter embedding_identity is not canonical")
        actual_cache_path = getattr(self.embedding_client, "cache_path", None)
        if not _is_nonempty_text(str(actual_cache_path or "")):
            raise CocoReviewInfrastructureFailure(
                "Embedding client cache_path is required"
            )
        actual = {
            "provider": getattr(self.embedding_client, "provider", None),
            "model": getattr(self.embedding_client, "model_name", None),
            "revision": getattr(self.embedding_client, "revision", None),
            "cache_path": str(
                Path(str(actual_cache_path or "")).expanduser().resolve()
            ),
        }
        if (
            any(
                not _is_nonempty_text(actual[name])
                for name in ("provider", "model", "revision", "cache_path")
            )
            or actual != normalized
        ):
            raise CocoReviewInfrastructureFailure(
                "Embedding client attributes differ from its declared identity"
            )
        return normalized


class _BoundedGenerationAdapter:
    def __init__(self, delegate: ReviewGenerationAdapter, limit: int):
        self.delegate = delegate
        self.semaphore = BoundedSemaphore(limit)

    def generate_specialist(
        self,
        invocation: SpecialistInvocation,
    ) -> dict[str, Any]:
        with self.semaphore:
            return self.delegate.generate_specialist(invocation)

    def generate_final(self, invocation: FinalInvocation) -> dict[str, Any]:
        with self.semaphore:
            return self.delegate.generate_final(invocation)


@dataclass(frozen=True)
class _FrozenRuntime:
    system_path: Path
    system_sha256: str
    system_id: str
    formal_spec_id: str | None
    manifest_path: Path
    manifest_sha256: str
    manifest_id: str
    roles: dict[str, _RoleRuntime]
    papers: tuple[_CocoPaper, ...]
    model_identity: dict[str, Any]
    embedding_identity: dict[str, Any]
    require_bubblewrap: bool
    s2_path: Path
    s2_sha256: str
    s2_store: Any


@dataclass(frozen=True)
class _ReadOnlyFrozenS2Store:
    """Narrow immutable view over one coordinator-validated in-memory pool."""

    path: Path
    sha256: str
    _store: FrozenS2Store

    def retrieve(
        self,
        *,
        paper_id: str,
        target_title: str,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        values, provenance = self._store.retrieve(
            paper_id=paper_id,
            target_title=target_title,
            limit=limit,
        )
        return deepcopy(values), deepcopy(provenance)


@dataclass(frozen=True)
class _WorkItem:
    kind: str
    paper_index: int
    role: str = ""


def _require_file_sha256(path: Path, expected: str, *, label: str) -> None:
    try:
        observed = file_sha256(path)
    except OSError as exc:
        raise _CocoReviewSourceDrift(
            f"Frozen source became unreadable during {label}: {path}"
        ) from exc
    if observed != expected:
        raise _CocoReviewSourceDrift(
            f"Frozen source changed during {label}: {path}"
        )


def _validate_task_sources(
    *,
    runtime: _FrozenRuntime,
    paper: _CocoPaper,
    role: str = "",
    review_bindings: tuple[tuple[Path, str], ...] = (),
    label: str,
) -> None:
    """Recheck every file one task can read before canonical promotion."""
    bindings = [
        (paper.paper_path, paper.paper_sha256, f"Paper {paper.paper_id}"),
    ]
    if role:
        role_runtime = runtime.roles[role]
        bindings.extend(
            [
                (
                    role_runtime.program_path,
                    role_runtime.program_sha256,
                    f"{role} Program",
                ),
                (
                    role_runtime.snapshot_path,
                    role_runtime.snapshot_sha256,
                    f"{role} Memory snapshot",
                ),
            ]
        )
    bindings.extend(
        (path, sha256, f"source Specialist Review {path.name}")
        for path, sha256 in review_bindings
    )
    for path, sha256, source_label in bindings:
        _require_file_sha256(
            path,
            sha256,
            label=f"{label} ({source_label})",
        )


def _review_file_bindings(
    review_paths: tuple[Path, ...],
) -> tuple[tuple[Path, str], ...]:
    values = []
    for path in review_paths:
        try:
            values.append((path, file_sha256(path)))
        except OSError as exc:
            raise CocoReviewInfrastructureFailure(
                f"Source Specialist Review became unreadable: {path}"
            ) from exc
    return tuple(values)


def _task_staging_root(
    *,
    staging_parent: Path,
    kind: str,
    paper_id: str,
    role: str = "",
) -> Path:
    task_key = hashlib.sha256(
        f"{kind}\n{paper_id}\n{role}".encode("utf-8")
    ).hexdigest()[:20]
    path = (staging_parent / f"{kind}-{task_key}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _discard_task_stage(*, staging_root: Path, staging_parent: Path) -> None:
    root = staging_root.resolve()
    parent = staging_parent.resolve()
    if root.parent != parent:
        raise ValueError(f"Task staging directory escaped its parent: {root}")
    if root.exists():
        shutil.rmtree(root)


@contextmanager
def _persistent_task_stage(
    *,
    staging_parent: Path,
    kind: str,
    paper_id: str,
    role: str = "",
):
    """Keep resumable prefixes across outages and hard process interruption."""
    staging_root = _task_staging_root(
        staging_parent=staging_parent,
        kind=kind,
        paper_id=paper_id,
        role=role,
    )
    try:
        yield staging_root
    except _CocoReviewSourceDrift:
        _discard_task_stage(
            staging_root=staging_root,
            staging_parent=staging_parent,
        )
        raise
    except (CocoReviewPaperFailure, CocoReviewInfrastructureFailure):
        raise
    except Exception:
        _discard_task_stage(
            staging_root=staging_root,
            staging_parent=staging_parent,
        )
        raise


def _seed_staged_json(
    *,
    canonical_path: Path,
    staged_path: Path,
) -> None:
    """Carry a committed resumable prefix into an isolated task workspace."""
    if canonical_path.is_file():
        atomic_write_json(staged_path, read_json(canonical_path))


def _promote_staged_json(
    *,
    staged_path: Path,
    canonical_path: Path,
) -> None:
    if not staged_path.is_file():
        raise CocoReviewInfrastructureFailure(
            f"Validated staged artifact is missing: {staged_path}"
        )
    atomic_write_json(canonical_path, read_json(staged_path))


def _validate_staged_value(
    *,
    staged_path: Path,
    returned_value: dict[str, Any],
    label: str,
) -> None:
    if not staged_path.is_file() or read_json(staged_path) != returned_value:
        raise ValueError(
            f"{label} adapter return differs from its staged artifact"
        )


def _validate_manifest_envelope(
    *,
    system: dict[str, Any],
    manifest: dict[str, Any],
    manifest_source: Path,
) -> list[dict[str, Any]]:
    external_evaluation = system.get("external_evaluation")
    if not isinstance(external_evaluation, dict):
        raise TypeError("Frozen system external_evaluation must be an object")
    external = external_evaluation.get("coco_1300_manifest")
    expected_binding = {
        "path": str(manifest_source),
        "sha256": file_sha256(manifest_source),
        "protocol": manifest.get("protocol"),
        "count": manifest.get("count"),
        "manifest_sha256": manifest.get("manifest_sha256"),
    }
    if external != expected_binding:
        raise ValueError("CoCo manifest differs from the frozen system binding")
    manifest_body = {
        name: value for name, value in manifest.items() if name != "manifest_sha256"
    }
    if manifest.get("manifest_sha256") != value_sha256(manifest_body):
        raise ValueError("CoCo manifest identity mismatch")
    records = manifest.get("records")
    count = manifest.get("count")
    if (
        manifest.get("protocol") != COCO_1300_PROTOCOL
        or isinstance(count, bool)
        or count != COCO_1300_COUNT
        or not isinstance(records, list)
        or len(records) != COCO_1300_COUNT
    ):
        raise ValueError("Formal CoCo Review generation requires exactly 1,300 papers")
    return records


def _project_papers(records: list[dict[str, Any]]) -> list[_CocoPaper]:
    papers = []
    seen_paper_ids: set[str] = set()
    seen_asset_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"CoCo manifest record {index} must be an object")
        forbidden_record_key = _contains_forbidden_data_key(record)
        if forbidden_record_key is not None:
            raise ValueError(
                "CoCo manifest record contains a forbidden evaluation field: "
                f"{forbidden_record_key!r}"
            )
        paper_id = record.get("paper_id")
        paper_asset_id = record.get("paper_asset_id")
        recorded_path = record.get("path")
        if not all(
            _is_nonempty_text(value)
            for value in (paper_id, paper_asset_id, recorded_path)
        ):
            raise ValueError(f"CoCo manifest record {index} lacks paper asset identity")
        paper_id = str(paper_id)
        paper_asset_id = str(paper_asset_id)
        if paper_id in seen_paper_ids:
            raise ValueError(f"CoCo manifest has duplicate paper_id: {paper_id}")
        if paper_asset_id in seen_asset_ids:
            raise ValueError(
                f"CoCo manifest has duplicate paper_asset_id: {paper_asset_id}"
            )
        seen_paper_ids.add(paper_id)
        seen_asset_ids.add(paper_asset_id)
        paper_path = Path(str(recorded_path)).expanduser().resolve()
        paper_value = read_json(paper_path)
        if not isinstance(paper_value, dict):
            raise TypeError(f"Frozen CoCo Paper must be an object: {paper_path}")
        missing = sorted(_PAPER_FIELDS - set(paper_value))
        unexpected = sorted(set(paper_value) - _PAPER_FIELDS)
        if missing or unexpected:
            raise ValueError(
                f"Frozen CoCo Paper fields differ at {paper_path}: "
                f"missing={missing}, unexpected={unexpected}"
            )
        if paper_value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Frozen CoCo Paper schema mismatch: {paper_path}")
        forbidden = _contains_forbidden_data_key(paper_value.get("source"))
        if forbidden is not None:
            raise ValueError(
                "Frozen CoCo Paper source contains forbidden evaluation field "
                f"{forbidden!r}"
            )
        computed_asset_id = value_sha256(
            {
                name: value
                for name, value in paper_value.items()
                if name != "paper_asset_id"
            }
        )
        if (
            paper_value.get("paper_asset_id") != paper_asset_id
            or computed_asset_id != paper_asset_id
        ):
            raise ValueError(f"Frozen CoCo Paper asset identity mismatch: {paper_path}")
        paper = Paper.from_dict(paper_value)
        if paper.paper_id != paper_id:
            raise ValueError(f"Frozen CoCo Paper ID mismatch: {paper_path}")
        papers.append(
            _CocoPaper(
                index=index,
                paper_id=paper_id,
                paper_asset_id=paper_asset_id,
                paper_path=paper_path,
                paper_sha256=file_sha256(paper_path),
                paper=paper,
                s2_values=(),
                s2_pool_version=None,
            )
        )
    return papers


def _validate_role_runtime(
    *,
    runtime: dict[str, Any],
    embedding_identity: dict[str, Any],
) -> dict[str, _RoleRuntime]:
    programs = runtime.get("programs")
    snapshots = runtime.get("memory_snapshots")
    if not isinstance(programs, dict) or set(programs) != set(ROLES):
        raise ValueError("Frozen system must contain exactly five role-local Programs")
    if not isinstance(snapshots, dict) or set(snapshots) != set(ROLES):
        raise ValueError("Frozen system must contain exactly five role-local snapshots")
    values = {}
    for role in ROLES:
        program_binding = programs[role]
        snapshot_binding = snapshots[role]
        if not isinstance(program_binding, dict) or not isinstance(
            snapshot_binding, dict
        ):
            raise TypeError(f"Frozen runtime binding for {role} must be an object")
        program_path = (
            Path(str(program_binding.get("path") or "")).expanduser().resolve()
        )
        program_sha256 = file_sha256(program_path)
        capability_manifest = program_capability_manifest(path=program_path)
        program_version = str(program_binding.get("version") or "")
        expected_program_binding = {
            "path": str(program_path),
            "sha256": program_sha256,
            "version": program_version,
            "capability_manifest": capability_manifest,
        }
        if program_binding != expected_program_binding or not program_version:
            raise ValueError(f"Frozen Program identity mismatch for {role}")
        snapshot_path = (
            Path(str(snapshot_binding.get("path") or "")).expanduser().resolve()
        )
        snapshot_sha256 = file_sha256(snapshot_path)
        snapshot = validate_memory_snapshot_artifact(
            read_json(snapshot_path),
            path=snapshot_path,
        )
        expected_snapshot_binding = {
            "path": str(snapshot_path),
            "sha256": snapshot_sha256,
            "snapshot_id": snapshot.get("snapshot_id"),
        }
        if snapshot_binding != expected_snapshot_binding:
            raise ValueError(f"Frozen snapshot binding mismatch for {role}")
        expected_snapshot_values = {
            "role": role,
            "program_version": program_version,
            "program_path": str(program_path),
            "program_sha256": program_sha256,
            "capability_manifest": capability_manifest,
        }
        observed = {name: snapshot.get(name) for name in expected_snapshot_values}
        if observed != expected_snapshot_values:
            raise ValueError(
                f"Frozen snapshot differs from its Program for {role}: "
                f"observed={observed!r}, expected={expected_snapshot_values!r}"
            )
        seed_kind = str(snapshot.get("seed_kind") or "")
        if not seed_kind:
            raise ValueError(f"Frozen snapshot seed_kind is missing for {role}")
        if (
            seed_kind != "no_memory"
            and snapshot.get("embedding_identity") != embedding_identity
        ):
            raise ValueError(f"Frozen snapshot embedding identity mismatch for {role}")
        values[role] = _RoleRuntime(
            role=role,
            program_path=program_path,
            program_sha256=program_sha256,
            program_version=program_version,
            capability_manifest=capability_manifest,
            snapshot_path=snapshot_path,
            snapshot_sha256=snapshot_sha256,
            snapshot_id=str(snapshot["snapshot_id"]),
            seed_kind=seed_kind,
        )
    return values


def _validate_runtime(
    *,
    system: dict[str, Any],
    papers: list[_CocoPaper],
) -> tuple[
    dict[str, _RoleRuntime],
    dict[str, Any],
    dict[str, Any],
    bool,
    Path,
    str,
    _ReadOnlyFrozenS2Store,
    tuple[_CocoPaper, ...],
]:
    runtime = system.get("review_runtime")
    if not isinstance(runtime, dict):
        raise TypeError("Frozen system review_runtime must be an object")
    model_identity = runtime.get("model_identity")
    embedding_identity = runtime.get("embedding_identity")
    require_bubblewrap = runtime.get("require_bubblewrap")
    if not isinstance(model_identity, dict) or not model_identity:
        raise ValueError("Frozen system model_identity is required")
    if not isinstance(embedding_identity, dict) or not embedding_identity:
        raise ValueError("Frozen system embedding_identity is required")
    if not isinstance(require_bubblewrap, bool):
        raise TypeError("Frozen system require_bubblewrap must be a boolean")
    if runtime.get("specialist_identity") != load_reviewer_suite().manifest_payload():
        raise ValueError("Frozen Specialist identity differs from the checked-in suite")
    if runtime.get("primary_meta") != {
        "enabled": True,
        "identity": load_meta_synthesizer().identity(),
    }:
        raise ValueError("Formal CoCo Review generation requires frozen primary Meta")
    if runtime.get("concat_ablation") != {
        "enabled": True,
        "role_order": list(ROLES),
    }:
        raise ValueError(
            "Formal CoCo Review generation requires the five-role concat ablation"
        )
    if runtime.get("review_plan") != {
        "protocol": REVIEW_PLAN_PROTOCOL,
        "decision_authority": "role-local-evolved-program",
        "s2_capability": {
            "name": "retrieve_current_s2",
            "scope": "current-paper-frozen-pool",
            "network_access": False,
            "llm_calls": 0,
        },
        "primary_intervention": None,
    }:
        raise ValueError(
            "Frozen system does not authorize autonomous evolved S2 planning"
        )
    if runtime.get("retrieval_provenance") != {
        "protocol": RETRIEVAL_PROVENANCE_PROTOCOL,
        "records_program_decision": True,
        "records_memory_trace": True,
        "records_s2_trace": True,
    }:
        raise ValueError("Frozen system retrieval provenance protocol mismatch")
    roles = _validate_role_runtime(
        runtime=runtime,
        embedding_identity=embedding_identity,
    )
    reproducibility = system.get("reproducibility")
    if not isinstance(reproducibility, dict):
        raise TypeError("Frozen system reproducibility must be an object")
    evolution_run_ids = reproducibility.get("evolution_run_ids")
    winners = reproducibility.get("evolution_winners")
    if (
        not isinstance(evolution_run_ids, dict)
        or set(evolution_run_ids) != set(ROLES)
        or any(not _is_nonempty_text(evolution_run_ids[role]) for role in ROLES)
        or not isinstance(winners, dict)
        or set(winners) != set(ROLES)
    ):
        raise ValueError("Frozen system lacks five completed Evolution winner bindings")
    for role in ROLES:
        winner = winners[role]
        if not isinstance(winner, dict) or {
            "program_sha256": winner.get("program_sha256"),
            "memory_snapshot_sha256": winner.get("memory_snapshot_sha256"),
            "memory_snapshot_id": winner.get("memory_snapshot_id"),
        } != {
            "program_sha256": roles[role].program_sha256,
            "memory_snapshot_sha256": roles[role].snapshot_sha256,
            "memory_snapshot_id": roles[role].snapshot_id,
        }:
            raise ValueError(
                f"Frozen runtime differs from its Evolution winner: {role}"
            )
    pools = runtime.get("frozen_s2_pools")
    if not isinstance(pools, dict) or not isinstance(pools.get("coco_1300"), dict):
        raise ValueError("Frozen system lacks its CoCo-1300 S2 pool")
    pool_binding = pools["coco_1300"]
    s2_path = Path(str(pool_binding.get("path") or "")).expanduser().resolve()
    s2_sha256 = file_sha256(s2_path)
    if pool_binding != {"path": str(s2_path), "sha256": s2_sha256}:
        raise ValueError("Frozen CoCo-1300 S2 pool identity mismatch")
    store = FrozenS2Store(s2_path)
    paper_ids = {paper.paper_id for paper in papers}
    if set(store.pool) != paper_ids:
        missing = sorted(paper_ids - set(store.pool))[:5]
        unexpected = sorted(set(store.pool) - paper_ids)[:5]
        raise ValueError(
            "Frozen CoCo-1300 S2 pool must cover the exact manifest IDs: "
            f"missing={missing}, unexpected={unexpected}"
        )
    hydrated = []
    for paper in papers:
        values, provenance = store.retrieve(
            paper_id=paper.paper_id,
            target_title=paper.paper.title,
            limit=5,
        )
        if (
            provenance.get("pool_path") != str(s2_path)
            or provenance.get("pool_sha256") != s2_sha256
        ):
            raise ValueError(f"Frozen S2 provenance mismatch for {paper.paper_id}")
        forbidden = _contains_forbidden_data_key(values)
        if forbidden is not None:
            raise ValueError(
                f"Frozen S2 evidence contains forbidden evaluation field {forbidden!r}"
            )
        hydrated.append(
            _CocoPaper(
                index=paper.index,
                paper_id=paper.paper_id,
                paper_asset_id=paper.paper_asset_id,
                paper_path=paper.paper_path,
                paper_sha256=paper.paper_sha256,
                paper=paper.paper,
                s2_values=tuple(dict(value) for value in values),
                s2_pool_version=store.pool[paper.paper_id].get("pool_version"),
            )
        )
    return (
        roles,
        dict(model_identity),
        dict(embedding_identity),
        require_bubblewrap,
        s2_path,
        s2_sha256,
        _ReadOnlyFrozenS2Store(
            path=s2_path,
            sha256=s2_sha256,
            _store=store,
        ),
        tuple(hydrated),
    )


def _load_frozen_runtime(
    *,
    frozen_system_path: str | Path,
    coco_manifest_path: str | Path,
) -> _FrozenRuntime:
    system_source = Path(frozen_system_path).expanduser().resolve()
    manifest_source = Path(coco_manifest_path).expanduser().resolve()
    system = read_json(system_source)
    manifest = read_json(manifest_source)
    if not isinstance(system, dict) or not isinstance(manifest, dict):
        raise TypeError("Frozen system and CoCo manifest must be JSON objects")
    system_body = {name: value for name, value in system.items() if name != "system_id"}
    if system.get("protocol") != "research-v2-frozen-system-v2" or system.get(
        "system_id"
    ) != value_sha256(system_body):
        raise ValueError("Frozen system identity mismatch")
    reproducibility = system.get("reproducibility")
    formal_binding = (
        reproducibility.get("formal_freeze_spec")
        if isinstance(reproducibility, dict)
        else None
    )
    formal_spec_id = (
        str(formal_binding.get("spec_id") or "")
        if isinstance(formal_binding, dict)
        else ""
    )
    records = _validate_manifest_envelope(
        system=system,
        manifest=manifest,
        manifest_source=manifest_source,
    )
    papers = _project_papers(records)
    (
        roles,
        model,
        embedding,
        require_bubblewrap,
        s2_path,
        s2_sha256,
        s2_store,
        hydrated,
    ) = _validate_runtime(system=system, papers=papers)
    return _FrozenRuntime(
        system_path=system_source,
        system_sha256=file_sha256(system_source),
        system_id=str(system["system_id"]),
        formal_spec_id=formal_spec_id or None,
        manifest_path=manifest_source,
        manifest_sha256=file_sha256(manifest_source),
        manifest_id=str(manifest["manifest_sha256"]),
        roles=roles,
        papers=hydrated,
        model_identity=model,
        embedding_identity=embedding,
        require_bubblewrap=require_bubblewrap,
        s2_path=s2_path,
        s2_sha256=s2_sha256,
        s2_store=s2_store,
    )


def run_coco_review_batch(
    *,
    frozen_system_path: str | Path,
    coco_manifest_path: str | Path,
    output_root: str | Path,
    options: CocoReviewBatchOptions,
    generation_adapter: ReviewGenerationAdapter,
    expected_frozen_identity: dict[str, str] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Generate or resume exact CoCo-1300 Specialist and Final Reviews.

    Specialist and Meta tasks share one bounded executor. A ready paper's Meta
    synthesis can therefore run beside another paper's Specialist Review while
    the whole coordinator stays at or below ``max_workers`` active 27B calls.
    """
    options.validate()
    root = Path(output_root).expanduser().resolve()
    run_dir = root / "runs" / options.run_id
    summary_path = run_dir / "coco_review_batch.json"
    event_path = run_dir / "coco_review_batch.events.jsonl"
    with _exclusive_run_lock(run_dir):
        runtime = _load_frozen_runtime(
            frozen_system_path=frozen_system_path,
            coco_manifest_path=coco_manifest_path,
        )
        if expected_frozen_identity is not None:
            expected_fields = {"system_id", "system_sha256", "spec_id"}
            if set(expected_frozen_identity) != expected_fields or any(
                not _is_nonempty_text(expected_frozen_identity.get(name))
                for name in expected_fields
            ):
                raise ValueError(
                    "Expected frozen identity requires system_id, system_sha256, "
                    "and spec_id"
                )
            observed_frozen_identity = {
                "system_id": runtime.system_id,
                "system_sha256": runtime.system_sha256,
                "spec_id": runtime.formal_spec_id,
            }
            if observed_frozen_identity != expected_frozen_identity:
                raise ValueError(
                    "Frozen system changed after formal transition validation"
                )
        if isinstance(generation_adapter, ResearchV2ReviewGenerationAdapter):
            ResearchV2ReviewGenerationAdapter.validate_frozen_runtime(
                generation_adapter,
                model_identity=runtime.model_identity,
                embedding_identity=runtime.embedding_identity,
                require_bubblewrap=runtime.require_bubblewrap,
            )
        else:
            validate_runtime = getattr(
                generation_adapter,
                "validate_frozen_runtime",
                None,
            )
            if not callable(validate_runtime):
                raise TypeError(
                    "CoCo Review generation adapter must validate the frozen runtime"
                )
            validate_runtime(
                model_identity=runtime.model_identity,
                embedding_identity=runtime.embedding_identity,
                require_bubblewrap=runtime.require_bubblewrap,
            )
        run_spec = _run_spec(runtime, options)
        run_spec_sha256 = value_sha256(run_spec)
        started_at = (
            _validate_existing_summary(
                summary_path,
                run_id=options.run_id,
                run_spec_sha256=run_spec_sha256,
            )
            or _utc_now()
        )
        specialist, meta, concat, pending, ready_meta = _load_existing_artifacts(
            runtime=runtime,
            output_root=root,
            run_id=options.run_id,
        )
        failures: list[dict[str, Any]] = []
        infrastructure_failures: list[dict[str, Any]] = []
        initial = _summary(
            options=options,
            run_spec=run_spec,
            run_spec_sha256=run_spec_sha256,
            started_at=started_at,
            specialist=specialist,
            meta=meta,
            concat=concat,
            failures=failures,
            infrastructure_failures=infrastructure_failures,
            status="running",
        )
        atomic_write_json(summary_path, initial, immutable=False)
        _append_event(
            event_path,
            callback=progress,
            event={
                "event": "coco_review_batch_plan",
                "run_id": options.run_id,
                "run_spec_sha256": run_spec_sha256,
                "paper_count": COCO_1300_COUNT,
                "specialist_pending": len(pending),
                "meta_ready": len(ready_meta),
                "max_workers": options.max_workers,
            },
        )
        bounded_adapter = _BoundedGenerationAdapter(
            generation_adapter,
            options.max_workers,
        )
        staging_parent = run_dir / ".staging"
        staging_parent.mkdir(parents=True, exist_ok=True)

        def execute(item: _WorkItem) -> dict[str, Any]:
            paper = runtime.papers[item.paper_index]
            if item.kind == "specialist":
                canonical_invocation = _specialist_invocation(
                    runtime=runtime,
                    output_root=root,
                    run_id=options.run_id,
                    paper=paper,
                    role=item.role,
                )
                _validate_task_sources(
                    runtime=runtime,
                    paper=paper,
                    role=item.role,
                    label="Specialist preflight",
                )
                with _persistent_task_stage(
                    staging_parent=staging_parent,
                    kind="specialist",
                    paper_id=paper.paper_id,
                    role=item.role,
                ) as staging_root:
                    invocation = replace(
                        canonical_invocation,
                        output_root=staging_root,
                    )
                    for suffix in ("request", "raw"):
                        canonical_prefix = canonical_invocation.output_path.with_name(
                            f"{item.role}.{suffix}.json"
                        )
                        staged_prefix = invocation.output_path.with_name(
                            f"{item.role}.{suffix}.json"
                        )
                        _seed_staged_json(
                            canonical_path=canonical_prefix,
                            staged_path=staged_prefix,
                        )
                    try:
                        value = bounded_adapter.generate_specialist(invocation)
                    except (
                        CocoReviewPaperFailure,
                        CocoReviewInfrastructureFailure,
                    ):
                        _validate_task_sources(
                            runtime=runtime,
                            paper=paper,
                            role=item.role,
                            label="failed Specialist staging",
                        )
                        raise
                    value = _validated_specialist_payload(
                        value,
                        invocation=invocation,
                        paper=paper,
                        role_runtime=runtime.roles[item.role],
                    )
                    _validate_staged_value(
                        staged_path=invocation.output_path,
                        returned_value=value,
                        label="Specialist",
                    )
                    _load_review_request(
                        invocation.output_path.with_name(
                            f"{item.role}.request.json"
                        ),
                        input_identity=_specialist_input_identity(
                            invocation,
                            paper=paper,
                            role_runtime=runtime.roles[item.role],
                        ),
                        allow_legacy_missing_require_bubblewrap=False,
                    )
                    _validate_task_sources(
                        runtime=runtime,
                        paper=paper,
                        role=item.role,
                        label="Specialist promotion",
                    )
                    for suffix in ("raw", "request"):
                        _promote_staged_json(
                            staged_path=invocation.output_path.with_name(
                                f"{item.role}.{suffix}.json"
                            ),
                            canonical_path=canonical_invocation.output_path.with_name(
                                f"{item.role}.{suffix}.json"
                            ),
                        )
                    _promote_staged_json(
                        staged_path=invocation.output_path,
                        canonical_path=canonical_invocation.output_path,
                    )
                _validate_task_sources(
                    runtime=runtime,
                    paper=paper,
                    role=item.role,
                    label="Specialist post-commit",
                )
                _discard_task_stage(
                    staging_root=staging_root,
                    staging_parent=staging_parent,
                )
                return value
            canonical_invocation = _final_invocation(
                runtime=runtime,
                output_root=root,
                run_id=options.run_id,
                paper=paper,
                mode="meta",
            )
            review_bindings = _review_file_bindings(
                canonical_invocation.review_paths
            )
            reviews = _paper_reviews(
                runtime=runtime,
                output_root=root,
                run_id=options.run_id,
                paper=paper,
            )
            if len(reviews) != len(ROLES):
                raise CocoReviewInfrastructureFailure(
                    "Meta scheduled without five committed Specialists: "
                    f"{paper.paper_id}"
                )
            _validate_task_sources(
                runtime=runtime,
                paper=paper,
                review_bindings=review_bindings,
                label="Meta preflight",
            )
            with _persistent_task_stage(
                staging_parent=staging_parent,
                kind="meta",
                paper_id=paper.paper_id,
            ) as staging_root:
                invocation = replace(
                    canonical_invocation,
                    output_root=staging_root,
                )
                for attempt in range(1, META_MAX_ATTEMPTS + 1):
                    canonical_raw = canonical_invocation.output_path.with_name(
                        f"{paper.paper_id}.meta.raw.{attempt}.json"
                    )
                    staged_raw = invocation.output_path.with_name(
                        f"{paper.paper_id}.meta.raw.{attempt}.json"
                    )
                    _seed_staged_json(
                        canonical_path=canonical_raw,
                        staged_path=staged_raw,
                    )
                try:
                    value = bounded_adapter.generate_final(invocation)
                except (
                    CocoReviewPaperFailure,
                    CocoReviewInfrastructureFailure,
                ):
                    _validate_task_sources(
                        runtime=runtime,
                        paper=paper,
                        review_bindings=review_bindings,
                        label="failed Meta staging",
                    )
                    raise
                value = _validated_final_payload(
                    value,
                    invocation=invocation,
                    reviews=reviews,
                )
                _validate_staged_value(
                    staged_path=invocation.output_path,
                    returned_value=value,
                    label="Meta",
                )
                _validate_task_sources(
                    runtime=runtime,
                    paper=paper,
                    review_bindings=review_bindings,
                    label="Meta promotion",
                )
                for attempt in range(1, META_MAX_ATTEMPTS + 1):
                    staged_raw = invocation.output_path.with_name(
                        f"{paper.paper_id}.meta.raw.{attempt}.json"
                    )
                    if staged_raw.is_file():
                        _promote_staged_json(
                            staged_path=staged_raw,
                            canonical_path=(
                                canonical_invocation.output_path.with_name(
                                    f"{paper.paper_id}.meta.raw.{attempt}.json"
                                )
                            ),
                        )
                _promote_staged_json(
                    staged_path=invocation.output_path,
                    canonical_path=canonical_invocation.output_path,
                )
            _validate_task_sources(
                runtime=runtime,
                paper=paper,
                review_bindings=review_bindings,
                label="Meta post-commit",
            )
            _discard_task_stage(
                staging_root=staging_root,
                staging_parent=staging_parent,
            )
            return value

        def commit_concat(paper: _CocoPaper) -> None:
            canonical_invocation = _final_invocation(
                runtime=runtime,
                output_root=root,
                run_id=options.run_id,
                paper=paper,
                mode="concat",
            )
            review_bindings = _review_file_bindings(
                canonical_invocation.review_paths
            )
            reviews = _paper_reviews(
                runtime=runtime,
                output_root=root,
                run_id=options.run_id,
                paper=paper,
            )
            if len(reviews) != len(ROLES):
                return
            if concat[paper.index].get("status") in {"completed", "resumed"}:
                return
            _validate_task_sources(
                runtime=runtime,
                paper=paper,
                review_bindings=review_bindings,
                label="concat preflight",
            )
            with _persistent_task_stage(
                staging_parent=staging_parent,
                kind="concat",
                paper_id=paper.paper_id,
            ) as staging_root:
                invocation = replace(
                    canonical_invocation,
                    output_root=staging_root,
                )
                try:
                    value = bounded_adapter.generate_final(invocation)
                except (
                    CocoReviewPaperFailure,
                    CocoReviewInfrastructureFailure,
                ):
                    _validate_task_sources(
                        runtime=runtime,
                        paper=paper,
                        review_bindings=review_bindings,
                        label="failed concat staging",
                    )
                    raise
                value = _validated_final_payload(
                    value,
                    invocation=invocation,
                    reviews=reviews,
                )
                _validate_staged_value(
                    staged_path=invocation.output_path,
                    returned_value=value,
                    label="concat",
                )
                _validate_task_sources(
                    runtime=runtime,
                    paper=paper,
                    review_bindings=review_bindings,
                    label="concat promotion",
                )
                _promote_staged_json(
                    staged_path=invocation.output_path,
                    canonical_path=canonical_invocation.output_path,
                )
            _validate_task_sources(
                runtime=runtime,
                paper=paper,
                review_bindings=review_bindings,
                label="concat post-commit",
            )
            _discard_task_stage(
                staging_root=staging_root,
                staging_parent=staging_parent,
            )
            concat[paper.index] = {
                "status": "completed",
                "final_review_id": value["final_review_id"],
            }
            concat_finished = sum(
                item.get("status") in {"completed", "resumed", "failed", "blocked"}
                for item in concat.values()
            )
            _append_event(
                event_path,
                callback=progress,
                event={
                    "event": "coco_concat_progress",
                    "run_id": options.run_id,
                    "paper_id": paper.paper_id,
                    "disposition": "completed",
                    "finished": concat_finished,
                    "total": COCO_1300_COUNT,
                },
            )

        try:
            for paper in runtime.papers:
                statuses = [
                    specialist.get((paper.index, role), {}).get("status")
                    for role in ROLES
                ]
                if all(status in {"completed", "resumed"} for status in statuses):
                    commit_concat(paper)
        except Exception as exc:
            failure = _save_failure(
                run_dir=run_dir,
                run_id=options.run_id,
                run_spec_sha256=run_spec_sha256,
                stage="concat",
                paper_id=paper.paper_id,
                role="",
                category="infrastructure",
                error=f"{type(exc).__name__}: {exc}",
            )
            infrastructure_failures.append(failure)
            interrupted = _summary(
                options=options,
                run_spec=run_spec,
                run_spec_sha256=run_spec_sha256,
                started_at=started_at,
                specialist=specialist,
                meta=meta,
                concat=concat,
                failures=failures,
                infrastructure_failures=infrastructure_failures,
                status="infrastructure_failed",
            )
            atomic_write_json(summary_path, interrupted, immutable=False)
            raise CocoReviewInfrastructureFailure(
                "CoCo Review batch failed while committing deterministic concat; "
                f"resume with run_id {options.run_id!r}"
            ) from exc

        in_flight: dict[Future[dict[str, Any]], _WorkItem] = {}
        first_infrastructure_error: Exception | None = None
        stop_submitting = False
        finished_since_checkpoint = 0
        executor = ThreadPoolExecutor(
            max_workers=options.max_workers,
            thread_name_prefix="coco-review",
        )
        try:
            while pending or ready_meta or in_flight:
                while not stop_submitting and len(in_flight) < options.max_workers:
                    item = (
                        ready_meta.popleft()
                        if ready_meta
                        else pending.popleft()
                        if pending
                        else None
                    )
                    if item is None:
                        break
                    in_flight[executor.submit(execute, item)] = item
                if not in_flight:
                    break
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in sorted(
                    done,
                    key=lambda candidate: (
                        in_flight[candidate].paper_index,
                        in_flight[candidate].kind,
                        in_flight[candidate].role,
                    ),
                ):
                    item = in_flight.pop(future)
                    paper = runtime.papers[item.paper_index]
                    try:
                        value = future.result()
                    except CocoReviewPaperFailure as exc:
                        failure = _save_failure(
                            run_dir=run_dir,
                            run_id=options.run_id,
                            run_spec_sha256=run_spec_sha256,
                            stage=item.kind,
                            paper_id=paper.paper_id,
                            role=item.role,
                            category=exc.category,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        failures.append(failure)
                        if item.kind == "specialist":
                            specialist[(paper.index, item.role)] = {
                                "status": "failed",
                                "category": exc.category,
                            }
                        else:
                            meta[paper.index] = {
                                "status": "failed",
                                "category": exc.category,
                            }
                        disposition = "failed"
                    except Exception as exc:
                        if first_infrastructure_error is None:
                            first_infrastructure_error = exc
                        stop_submitting = True
                        failure = _save_failure(
                            run_dir=run_dir,
                            run_id=options.run_id,
                            run_spec_sha256=run_spec_sha256,
                            stage=item.kind,
                            paper_id=paper.paper_id,
                            role=item.role,
                            category="infrastructure",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        infrastructure_failures.append(failure)
                        disposition = "infrastructure_failed"
                    else:
                        if item.kind == "specialist":
                            specialist[(paper.index, item.role)] = {
                                "status": "completed",
                                "review_id": value["review_id"],
                            }
                            paper_specialists = [
                                specialist.get((paper.index, role), {}).get("status")
                                for role in ROLES
                            ]
                            if all(
                                status in {"completed", "resumed"}
                                for status in paper_specialists
                            ):
                                try:
                                    commit_concat(paper)
                                except Exception as exc:
                                    if first_infrastructure_error is None:
                                        first_infrastructure_error = exc
                                    stop_submitting = True
                                    failure = _save_failure(
                                        run_dir=run_dir,
                                        run_id=options.run_id,
                                        run_spec_sha256=run_spec_sha256,
                                        stage="concat",
                                        paper_id=paper.paper_id,
                                        role="",
                                        category="infrastructure",
                                        error=f"{type(exc).__name__}: {exc}",
                                    )
                                    infrastructure_failures.append(failure)
                                if (
                                    not stop_submitting
                                    and meta[paper.index].get("status") == "pending"
                                ):
                                    ready_meta.appendleft(
                                        _WorkItem("meta", paper.index)
                                    )
                        else:
                            meta[paper.index] = {
                                "status": "completed",
                                "final_review_id": value["final_review_id"],
                            }
                        disposition = "completed"
                    finished_since_checkpoint += 1
                    if item.kind == "specialist":
                        stage_finished = len(specialist)
                        stage_total = COCO_1300_COUNT * len(ROLES)
                    else:
                        stage_finished = sum(
                            result.get("status")
                            in {"completed", "resumed", "failed", "blocked"}
                            for result in meta.values()
                        )
                        stage_total = COCO_1300_COUNT
                    _append_event(
                        event_path,
                        callback=progress,
                        event={
                            "event": "coco_review_batch_progress",
                            "run_id": options.run_id,
                            "paper_id": paper.paper_id,
                            "stage": item.kind,
                            "role": item.role or None,
                            "disposition": disposition,
                            "finished": stage_finished,
                            "total": stage_total,
                        },
                    )
                if finished_since_checkpoint >= _SUMMARY_CHECKPOINT_INTERVAL:
                    checkpoint = _summary(
                        options=options,
                        run_spec=run_spec,
                        run_spec_sha256=run_spec_sha256,
                        started_at=started_at,
                        specialist=specialist,
                        meta=meta,
                        concat=concat,
                        failures=failures,
                        infrastructure_failures=infrastructure_failures,
                        status=(
                            "infrastructure_failed"
                            if infrastructure_failures
                            else "running"
                        ),
                    )
                    atomic_write_json(summary_path, checkpoint, immutable=False)
                    finished_since_checkpoint = 0
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        for paper in runtime.papers:
            statuses = [
                specialist.get((paper.index, role), {}).get("status") for role in ROLES
            ]
            if any(status == "failed" for status in statuses):
                if meta[paper.index].get("status") == "pending":
                    meta[paper.index] = {"status": "blocked"}
                if concat[paper.index].get("status") == "pending":
                    concat[paper.index] = {"status": "blocked"}
        if not infrastructure_failures:
            try:
                confirmed_runtime = _load_frozen_runtime(
                    frozen_system_path=frozen_system_path,
                    coco_manifest_path=coco_manifest_path,
                )
                if _run_spec(confirmed_runtime, options) != run_spec:
                    raise CocoReviewInfrastructureFailure(
                        "Frozen runtime changed before terminal batch validation"
                    )
                _validate_completed_batch_state(
                    runtime=confirmed_runtime,
                    output_root=root,
                    run_id=options.run_id,
                    specialist=specialist,
                    meta=meta,
                    concat=concat,
                )
            except Exception as exc:
                first_infrastructure_error = exc
                infrastructure_failures.append(
                    _save_failure(
                        run_dir=run_dir,
                        run_id=options.run_id,
                        run_spec_sha256=run_spec_sha256,
                        stage="terminal_validation",
                        paper_id="",
                        role="",
                        category="infrastructure",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        if infrastructure_failures:
            interrupted = _summary(
                options=options,
                run_spec=run_spec,
                run_spec_sha256=run_spec_sha256,
                started_at=started_at,
                specialist=specialist,
                meta=meta,
                concat=concat,
                failures=failures,
                infrastructure_failures=infrastructure_failures,
                status="infrastructure_failed",
            )
            atomic_write_json(summary_path, interrupted, immutable=False)
            _append_event(
                event_path,
                callback=progress,
                event={
                    "event": "coco_review_batch_infrastructure_failure",
                    "run_id": options.run_id,
                    "failure_count": len(infrastructure_failures),
                },
            )
            raise CocoReviewInfrastructureFailure(
                "CoCo Review batch stopped after a shared infrastructure failure; "
                f"resume with run_id {options.run_id!r} after recovery"
            ) from first_infrastructure_error
        final_status = "completed_with_failures" if failures else "complete"
        result = _summary(
            options=options,
            run_spec=run_spec,
            run_spec_sha256=run_spec_sha256,
            started_at=started_at,
            specialist=specialist,
            meta=meta,
            concat=concat,
            failures=failures,
            infrastructure_failures=infrastructure_failures,
            status=final_status,
        )
        atomic_write_json(summary_path, result, immutable=False)
        _append_event(
            event_path,
            callback=progress,
            event={
                "event": "coco_review_batch_complete",
                "run_id": options.run_id,
                "status": final_status,
                "failure_count": len(failures),
            },
        )
        return result


def _specialist_input_identity(
    invocation: SpecialistInvocation,
    *,
    paper: _CocoPaper,
    role_runtime: _RoleRuntime,
) -> dict[str, Any]:
    return {
        "paper_id": paper.paper_id,
        "paper_path": str(paper.paper_path),
        "paper_sha256": paper.paper_sha256,
        "role": invocation.role,
        "program_path": str(role_runtime.program_path),
        "program_sha256": role_runtime.program_sha256,
        "program_version": role_runtime.program_version,
        "model_identity": dict(invocation.model_identity),
        "embedding_identity": dict(invocation.embedding_identity),
        "require_bubblewrap": invocation.require_bubblewrap,
        "snapshot_path": str(role_runtime.snapshot_path),
        "snapshot_sha256": role_runtime.snapshot_sha256,
        "snapshot_id": role_runtime.snapshot_id,
        "frozen_s2_path": str(invocation.frozen_s2_path),
        "frozen_s2_sha256": str(invocation.frozen_s2_store.sha256),
        "s2_intervention": dict(invocation.s2_intervention),
    }


def _s2_id(value: dict[str, Any]) -> str:
    return str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")


def _validated_specialist_payload(
    value: Any,
    *,
    invocation: SpecialistInvocation,
    paper: _CocoPaper,
    role_runtime: _RoleRuntime,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Specialist Review artifact must be an object")
    expected_fields = {
        "schema_version",
        "review_id",
        "paper_id",
        "role",
        "program_version",
        "memory_snapshot_id",
        "review_text",
        "raw_model_output",
        "reviewer_identity",
        "model_identity",
        "generation_config",
        "retrieval_provenance",
        "run_id",
        "created_at",
    }
    if set(value) != expected_fields:
        raise ValueError(
            "Specialist Review fields differ from the frozen artifact schema: "
            f"missing={sorted(expected_fields - set(value))}, "
            f"unexpected={sorted(set(value) - expected_fields)}"
        )
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "paper_id": paper.paper_id,
        "role": invocation.role,
        "program_version": role_runtime.program_version,
        "memory_snapshot_id": role_runtime.snapshot_id,
        "reviewer_identity": load_reviewer_suite().identity(invocation.role),
        "model_identity": invocation.model_identity,
        "run_id": invocation.run_id,
    }
    observed = {name: value.get(name) for name in expected_identity}
    if observed != expected_identity:
        raise ValueError(
            "Specialist Review identity mismatch: "
            f"observed={observed!r}, expected={expected_identity!r}"
        )
    if (
        not _is_nonempty_text(value.get("review_text"))
        or value.get("raw_model_output") != value.get("review_text")
        or not _is_nonempty_text(value.get("created_at"))
    ):
        raise ValueError("Specialist Review output or creation time is invalid")
    provenance = value.get("retrieval_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("protocol") != RETRIEVAL_PROVENANCE_PROTOCOL
    ):
        raise ValueError("Specialist Review retrieval provenance protocol mismatch")
    memory = provenance.get("memory")
    s2 = provenance.get("s2")
    assembly = provenance.get("assembly")
    if not all(isinstance(item, dict) for item in (memory, s2, assembly)):
        raise TypeError("Specialist Review retrieval provenance is incomplete")
    expected_memory = {
        "snapshot_id": role_runtime.snapshot_id,
        "program_version": role_runtime.program_version,
        "program_sha256": role_runtime.program_sha256,
        "seed_kind": role_runtime.seed_kind,
    }
    if {name: memory.get(name) for name in expected_memory} != expected_memory:
        raise ValueError("Specialist Review Memory provenance identity mismatch")
    if not {"query", "context", "logs", "resources"}.issubset(memory):
        raise ValueError("Specialist Review Memory provenance is incomplete")
    if s2.get("intervention") != invocation.s2_intervention:
        raise ValueError("Specialist Review S2 intervention is not autonomous-primary")
    if (
        s2.get("capability_available") is not True
        or s2.get("pool_path") != str(invocation.frozen_s2_path)
        or s2.get("pool_sha256") != invocation.frozen_s2_store.sha256
        or s2.get("pool_version") != paper.s2_pool_version
        or s2.get("capability_manifest") != role_runtime.capability_manifest
    ):
        raise ValueError("Specialist Review S2 capability identity mismatch")
    requested_limits = s2.get("requested_limits")
    candidate_ids = s2.get("candidate_ids")
    selected_ids = s2.get("selected_ids")
    injected_ids = s2.get("injected_ids")
    if not all(
        isinstance(item, list)
        for item in (requested_limits, candidate_ids, selected_ids, injected_ids)
    ):
        raise TypeError("Specialist Review S2 decision lists are invalid")
    if len(requested_limits) > 1 or any(
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5
        for limit in requested_limits
    ):
        raise ValueError("Specialist Review S2 request trace is invalid")
    expected_candidates = (
        [_s2_id(item) for item in paper.s2_values[: requested_limits[0]]]
        if requested_limits
        else []
    )
    if candidate_ids != expected_candidates:
        raise ValueError("Specialist Review S2 candidates differ from the frozen pool")
    if (
        len(selected_ids) != len(set(selected_ids))
        or not set(selected_ids).issubset(candidate_ids)
        or injected_ids != selected_ids
        or s2.get("dropped_ids")
        != [item for item in candidate_ids if item not in set(selected_ids)]
    ):
        raise ValueError("Specialist Review S2 selection trace is invalid")
    evidence_by_id = {_s2_id(item): item for item in paper.s2_values}
    expected_candidate_evidence = [evidence_by_id[item] for item in candidate_ids]
    expected_selected_evidence = [evidence_by_id[item] for item in selected_ids]
    if (
        s2.get("candidate_evidence") != expected_candidate_evidence
        or s2.get("evidence") != expected_selected_evidence
    ):
        raise ValueError(
            "Specialist Review injected S2 evidence differs from the frozen pool"
        )
    lookup_count = len(requested_limits)
    disposition = (
        "not_consulted"
        if lookup_count == 0
        else "use"
        if selected_ids
        else "consulted_no_use"
    )
    if (
        s2.get("lookup_count") != lookup_count
        or s2.get("s2_disposition") != disposition
    ):
        raise ValueError("Specialist Review S2 disposition disagrees with its trace")
    plan = {
        "protocol": REVIEW_PLAN_PROTOCOL,
        "s2_disposition": disposition,
        "selected_s2_ids": selected_ids,
        "decision_label": s2.get("decision_label"),
        "usage_instruction": s2.get("usage_instruction"),
        "context_order": s2.get("context_order"),
        "review_strategy": s2.get("review_strategy"),
    }
    if (
        not _is_nonempty_text(plan["decision_label"])
        or not isinstance(plan["usage_instruction"], str)
        or not isinstance(plan["review_strategy"], str)
        or sorted(plan.get("context_order") or []) != ["memory", "s2", "strategy"]
        or s2.get("plan_sha256") != value_sha256(plan)
    ):
        raise ValueError("Specialist Review evolved plan identity mismatch")
    s2_context = format_s2_evidence(expected_selected_evidence)
    if s2.get("context_sha256") != value_sha256(s2_context):
        raise ValueError("Specialist Review S2 context identity mismatch")
    context_blocks = {
        "memory": "[REVIEW EXPERIENCE]\n"
        + (str(memory.get("context") or "") or "(none)"),
        "s2": "[FROZEN S2 EVIDENCE]\n"
        + (s2_context or "(not selected by the Reviewer Program)"),
        "strategy": "\n".join(
            [
                "[VERSIONED REVIEW STRATEGY]",
                str(plan["review_strategy"] or "(none)"),
                "",
                "[S2 USAGE INSTRUCTION]",
                str(plan["usage_instruction"] or "(none)"),
            ]
        ),
    }
    paper_context = frozen_paper_context(paper.paper, invocation.role)
    user_parts = [
        SPECIALIST_REQUEST_TEMPLATE.format(
            task_id=invocation.role,
            paper_context=paper_context,
        )
    ]
    user_parts.extend(context_blocks[name] for name in plan["context_order"])
    messages = [
        {
            "role": "system",
            "content": load_reviewer_suite().task(invocation.role).system_prompt,
        },
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    request_sha256 = value_sha256(messages)
    if (
        assembly.get("context_order") != plan["context_order"]
        or assembly.get("paper_context_sha256") != value_sha256(paper_context)
        or assembly.get("prompt_sha256") != request_sha256
    ):
        raise ValueError("Specialist Review prompt assembly identity mismatch")
    request_body = {
        "protocol": REVIEW_REQUEST_MATERIALIZATION_PROTOCOL,
        "input_identity": _specialist_input_identity(
            invocation,
            paper=paper,
            role_runtime=role_runtime,
        ),
        "messages": messages,
        "retrieval_provenance": provenance,
        "review_plan": plan,
        "request_sha256": request_sha256,
    }
    preparation_sha256 = value_sha256(request_body)
    request_path = invocation.output_path.with_name(f"{invocation.role}.request.json")
    committed_request = _load_review_request(
        request_path,
        input_identity=request_body["input_identity"],
        allow_legacy_missing_require_bubblewrap=False,
    )
    if committed_request != {
        **request_body,
        "preparation_sha256": preparation_sha256,
    }:
        raise ValueError("Specialist Review request content identity mismatch")
    generation = specialist_generation_identity(invocation.model_identity)
    config = value.get("generation_config")
    attempts = config.get("attempts") if isinstance(config, dict) else None
    expected_config = {
        "temperature": generation["temperature"],
        "enable_thinking": generation["enable_thinking"],
        "max_tokens": generation["max_tokens"],
        "retry": generation["retry"],
        "attempts": attempts,
        "request_sha256": request_sha256,
        "preparation_sha256": preparation_sha256,
    }
    if attempts not in (1, 2) or config != expected_config:
        raise ValueError("Specialist Review generation identity mismatch")
    review_plan_identity = {
        "intervention": dict(invocation.s2_intervention),
        "capability_manifest": role_runtime.capability_manifest,
        "plan_sha256": value_sha256(plan),
        "preparation_sha256": preparation_sha256,
    }
    raw_path = invocation.output_path.with_name(f"{invocation.role}.raw.json")
    raw_artifact = _load_resumable_raw_artifact(
        raw_path,
        paper_id=paper.paper_id,
        role=invocation.role,
        run_id=invocation.run_id,
        request_sha256=request_sha256,
        model_identity=invocation.model_identity,
        generation_identity=generation,
        program_version=role_runtime.program_version,
        snapshot_id=role_runtime.snapshot_id,
        review_plan_identity=review_plan_identity,
    )
    if raw_artifact is None:
        raise ValueError(f"Specialist Review has no committed raw artifact: {raw_path}")
    raw_output = raw_artifact.get("raw_model_output")
    if (
        value.get("review_text") != raw_output
        or value.get("raw_model_output") != raw_output
        or value.get("created_at") != raw_artifact.get("created_at")
        or attempts != raw_artifact.get("attempts")
    ):
        raise ValueError("Specialist Review content identity mismatch")
    expected_review_id = _review_id(
        run_id=invocation.run_id,
        paper_id=paper.paper_id,
        role=invocation.role,
        program_version=role_runtime.program_version,
        snapshot_id=role_runtime.snapshot_id,
        review_plan_identity_sha256=value_sha256(review_plan_identity),
    )
    if value.get("review_id") != expected_review_id:
        raise ValueError("Specialist Review content identity mismatch")
    return value


def _concat_text(reviews: dict[str, dict[str, Any]]) -> str:
    return "\n\n".join(
        f"# {role} Specialist Review\n\n{reviews[role]['review_text']}"
        for role in ROLES
    )


def _validated_final_payload(
    value: Any,
    *,
    invocation: FinalInvocation,
    reviews: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Final Review artifact must be an object")
    source_ids = [str(reviews[role]["review_id"]) for role in ROLES]
    text = value.get("review_text")
    if not _is_nonempty_text(text) or not _is_nonempty_text(value.get("created_at")):
        raise ValueError("Final Review output or creation time is invalid")
    expected_fields = {
        "schema_version",
        "final_review_id",
        "paper_id",
        "mode",
        "review_text",
        "source_review_ids",
        "meta_identity",
        "run_id",
        "created_at",
    }
    expected_identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "final_review_id": _final_review_id(
            run_id=invocation.run_id,
            paper_id=invocation.paper_id,
            mode=invocation.mode,
            source_review_ids=source_ids,
            review_text=str(text),
        ),
        "paper_id": invocation.paper_id,
        "mode": invocation.mode,
        "source_review_ids": source_ids,
        "meta_identity": None,
        "run_id": invocation.run_id,
    }
    if invocation.mode == "meta":
        expected_fields.update({"model_identity", "generation_config"})
        expected_identity.update(
            {
                "meta_identity": load_meta_synthesizer().identity(),
                "model_identity": invocation.model_identity,
                "generation_config": {
                    "temperature": 0.0,
                    "enable_thinking": False,
                    "max_tokens": META_MAX_TOKENS,
                },
            }
        )
    if set(value) != expected_fields:
        raise ValueError(
            "Final Review fields differ from the frozen artifact schema: "
            f"missing={sorted(expected_fields - set(value))}, "
            f"unexpected={sorted(set(value) - expected_fields)}"
        )
    observed = {name: value.get(name) for name in expected_identity}
    if observed != expected_identity:
        raise ValueError(
            f"Final Review identity mismatch: observed={observed!r}, "
            f"expected={expected_identity!r}"
        )
    if invocation.mode == "concat":
        if text != _concat_text(reviews):
            raise ValueError(
                "Final Review content identity is not the deterministic "
                "five-role concat bundle"
            )
    else:
        if re.search(r"(?im)^\s*(final\s+score|confidence)\s*:", str(text)):
            raise ValueError(
                "Meta Final Review contains a forbidden score/confidence field"
            )
        paper = Paper.from_dict(read_json(invocation.paper_path))
        synthesizer = load_meta_synthesizer()
        question = build_meta_review_question(
            paper_id=invocation.paper_id,
            specialist_reviews={
                role: str(reviews[role]["review_text"]) for role in ROLES
            },
            paper_context=frozen_paper_context(paper, "META"),
        )
        messages = [
            {"role": "system", "content": synthesizer.system_prompt},
            {"role": "user", "content": question},
        ]
        raw_identity = {
            "protocol": META_RAW_PROTOCOL,
            "paper_id": invocation.paper_id,
            "run_id": invocation.run_id,
            "source_review_ids": source_ids,
            "meta_identity": synthesizer.identity(),
            "model_identity": dict(invocation.model_identity),
            "generation_config": {
                "temperature": 0.0,
                "enable_thinking": False,
                "max_tokens": META_MAX_TOKENS,
            },
            "request_sha256": value_sha256(messages),
        }
        matching_raw = False
        for attempt in range(1, META_MAX_ATTEMPTS + 1):
            raw_path = invocation.output_path.with_name(
                f"{invocation.paper_id}.meta.raw.{attempt}.json"
            )
            raw = _load_resumable_meta_raw(
                raw_path,
                expected_identity={**raw_identity, "attempt": attempt},
            )
            if raw is not None and (
                raw.get("raw_model_output") == text
                and raw.get("created_at") == value.get("created_at")
            ):
                matching_raw = True
        if not matching_raw:
            raise ValueError("Meta Final Review content identity mismatch")
    return value


def _run_spec(
    runtime: _FrozenRuntime, options: CocoReviewBatchOptions
) -> dict[str, Any]:
    summarizer_roles = [
        role for role in ROLES if runtime.roles[role].seed_kind == "llm_summarizer"
    ]
    specialist_outputs = COCO_1300_COUNT * len(ROLES)
    meta_outputs = COCO_1300_COUNT
    memory_read_outputs = COCO_1300_COUNT * len(summarizer_roles)
    return {
        "protocol": BATCH_PROTOCOL,
        "run_id": options.run_id,
        "system": {
            "path": str(runtime.system_path),
            "sha256": runtime.system_sha256,
            "system_id": runtime.system_id,
        },
        "coco_manifest": {
            "path": str(runtime.manifest_path),
            "sha256": runtime.manifest_sha256,
            "manifest_sha256": runtime.manifest_id,
            "protocol": COCO_1300_PROTOCOL,
            "count": COCO_1300_COUNT,
            "paper_ids_sha256": value_sha256(
                [paper.paper_id for paper in runtime.papers]
            ),
        },
        "roles": {
            role: {
                "program_path": str(runtime.roles[role].program_path),
                "program_sha256": runtime.roles[role].program_sha256,
                "program_version": runtime.roles[role].program_version,
                "capability_manifest": runtime.roles[role].capability_manifest,
                "snapshot_path": str(runtime.roles[role].snapshot_path),
                "snapshot_sha256": runtime.roles[role].snapshot_sha256,
                "snapshot_id": runtime.roles[role].snapshot_id,
                "seed_kind": runtime.roles[role].seed_kind,
            }
            for role in ROLES
        },
        "model_identity": runtime.model_identity,
        "embedding_identity": runtime.embedding_identity,
        "require_bubblewrap": runtime.require_bubblewrap,
        "s2": {
            "path": str(runtime.s2_path),
            "sha256": runtime.s2_sha256,
            "intervention": dict(FORMAL_S2_INTERVENTION),
            "decision_authority": "role-local-evolved-program",
        },
        "run_ids": {
            "specialist": options.run_id,
            "meta": f"{options.run_id}-meta",
            "concat": f"{options.run_id}-concat",
        },
        "model_call_budget": {
            "specialist_outputs": specialist_outputs,
            "meta_outputs": meta_outputs,
            "memory_summarizer_read_outputs": memory_read_outputs,
            "concat_outputs_without_llm": COCO_1300_COUNT,
            "planned_llm_outputs": (
                specialist_outputs + meta_outputs + memory_read_outputs
            ),
            "maximum_request_attempts": (
                specialist_outputs * 2 + meta_outputs * 2 + memory_read_outputs
            ),
            "memory_summarizer_roles": summarizer_roles,
        },
    }


@contextmanager
def _exclusive_run_lock(run_dir: Path):
    lock_path = run_dir / "coco-review-batch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"CoCo Review batch is already active: {run_dir.name}"
            ) from exc
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_existing_summary(
    path: Path,
    *,
    run_id: str,
    run_spec_sha256: str,
) -> str | None:
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"CoCo Review batch summary must be an object: {path}")
    if (
        value.get("protocol") != BATCH_PROTOCOL
        or value.get("run_id") != run_id
        or value.get("run_spec_sha256") != run_spec_sha256
    ):
        raise ValueError(f"run_id {run_id!r} is bound to a different CoCo Review batch")
    started_at = value.get("started_at")
    return str(started_at) if _is_nonempty_text(started_at) else None


def _specialist_invocation(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
    paper: _CocoPaper,
    role: str,
) -> SpecialistInvocation:
    role_runtime = runtime.roles[role]
    return SpecialistInvocation(
        paper_id=paper.paper_id,
        paper_path=paper.paper_path,
        role=role,
        program_path=role_runtime.program_path,
        snapshot_path=role_runtime.snapshot_path,
        run_id=run_id,
        output_root=output_root,
        model_identity=dict(runtime.model_identity),
        embedding_identity=dict(runtime.embedding_identity),
        require_bubblewrap=runtime.require_bubblewrap,
        program_version=role_runtime.program_version,
        frozen_s2_path=runtime.s2_path,
        frozen_s2_store=runtime.s2_store,
        s2_intervention=dict(FORMAL_S2_INTERVENTION),
    )


def _final_invocation(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
    paper: _CocoPaper,
    mode: str,
) -> FinalInvocation:
    return FinalInvocation(
        paper_id=paper.paper_id,
        paper_path=paper.paper_path,
        review_paths=tuple(
            output_root / "reviews" / run_id / paper.paper_id / f"{role}.json"
            for role in ROLES
        ),
        mode=mode,
        run_id=f"{run_id}-{mode}",
        output_root=output_root,
        model_identity=dict(runtime.model_identity),
        embedding_identity=dict(runtime.embedding_identity),
        require_bubblewrap=runtime.require_bubblewrap,
    )


def _save_failure(
    *,
    run_dir: Path,
    run_id: str,
    run_spec_sha256: str,
    stage: str,
    paper_id: str,
    role: str,
    category: str,
    error: str,
) -> dict[str, Any]:
    payload = {
        "protocol": FAILURE_PROTOCOL,
        "run_id": run_id,
        "run_spec_sha256": run_spec_sha256,
        "stage": stage,
        "paper_id": paper_id,
        "role": role or None,
        "category": category,
        "error": error[:4000],
        "created_at": _utc_now(),
    }
    payload["failure_id"] = value_sha256(payload)
    task_key = hashlib.sha256(f"{paper_id}\n{role}".encode("utf-8")).hexdigest()[:20]
    atomic_write_json(
        run_dir / "failures" / stage / task_key / f"{payload['failure_id']}.json",
        payload,
    )
    return payload


def _append_event(
    path: Path,
    *,
    callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    payload = {**event, "recorded_at": _utc_now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
    if callback is not None:
        callback(dict(payload))


def _summary(
    *,
    options: CocoReviewBatchOptions,
    run_spec: dict[str, Any],
    run_spec_sha256: str,
    started_at: str,
    specialist: dict[tuple[int, str], dict[str, Any]],
    meta: dict[int, dict[str, Any]],
    concat: dict[int, dict[str, Any]],
    failures: list[dict[str, Any]],
    infrastructure_failures: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    terminal = status in {
        "complete",
        "completed_with_failures",
        "infrastructure_failed",
    }

    def counts(values: list[dict[str, Any]], planned: int) -> dict[str, int]:
        finished = sum(
            item.get("status") in {"completed", "resumed", "failed", "blocked"}
            for item in values
        )
        return {
            "planned": planned,
            "completed": sum(item.get("status") == "completed" for item in values),
            "resumed": sum(item.get("status") == "resumed" for item in values),
            "failed": sum(item.get("status") == "failed" for item in values),
            "blocked": sum(item.get("status") == "blocked" for item in values),
            "pending": planned - finished,
        }

    now = _utc_now()
    return {
        "protocol": BATCH_PROTOCOL,
        "stage": "generate-coco-1300-reviews",
        "run_id": options.run_id,
        "status": status,
        "max_workers": options.max_workers,
        "run_spec": run_spec,
        "run_spec_sha256": run_spec_sha256,
        "progress": {
            "specialist": counts(
                list(specialist.values()), COCO_1300_COUNT * len(ROLES)
            ),
            "meta": counts(list(meta.values()), COCO_1300_COUNT),
            "concat": counts(list(concat.values()), COCO_1300_COUNT),
        },
        "failure_count": len(failures),
        "failures": failures,
        "infrastructure_failures": infrastructure_failures,
        "started_at": started_at,
        "updated_at": now,
        "completed_at": now if terminal else None,
    }


def _load_existing_artifacts(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
) -> tuple[
    dict[tuple[int, str], dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    deque[_WorkItem],
    deque[_WorkItem],
]:
    specialist: dict[tuple[int, str], dict[str, Any]] = {}
    meta: dict[int, dict[str, Any]] = {}
    concat: dict[int, dict[str, Any]] = {}
    pending: deque[_WorkItem] = deque()
    ready_meta: deque[_WorkItem] = deque()
    for paper in runtime.papers:
        reviews = {}
        for role in ROLES:
            invocation = _specialist_invocation(
                runtime=runtime,
                output_root=output_root,
                run_id=run_id,
                paper=paper,
                role=role,
            )
            if invocation.output_path.is_file():
                value = _validated_specialist_payload(
                    read_json(invocation.output_path),
                    invocation=invocation,
                    paper=paper,
                    role_runtime=runtime.roles[role],
                )
                specialist[(paper.index, role)] = {
                    "status": "resumed",
                    "review_id": value["review_id"],
                }
                reviews[role] = value
            else:
                pending.append(_WorkItem("specialist", paper.index, role))
        if len(reviews) != len(ROLES):
            for mode, destination in (("meta", meta), ("concat", concat)):
                invocation = _final_invocation(
                    runtime=runtime,
                    output_root=output_root,
                    run_id=run_id,
                    paper=paper,
                    mode=mode,
                )
                if invocation.output_path.exists():
                    raise ValueError(
                        f"Orphan {mode} Final Review exists before its five "
                        f"Specialists: {invocation.output_path}"
                    )
                destination[paper.index] = {"status": "pending"}
            continue
        concat_invocation = _final_invocation(
            runtime=runtime,
            output_root=output_root,
            run_id=run_id,
            paper=paper,
            mode="concat",
        )
        if concat_invocation.output_path.is_file():
            value = _validated_final_payload(
                read_json(concat_invocation.output_path),
                invocation=concat_invocation,
                reviews=reviews,
            )
            concat[paper.index] = {
                "status": "resumed",
                "final_review_id": value["final_review_id"],
            }
        else:
            concat[paper.index] = {"status": "pending"}
        meta_invocation = _final_invocation(
            runtime=runtime,
            output_root=output_root,
            run_id=run_id,
            paper=paper,
            mode="meta",
        )
        if meta_invocation.output_path.is_file():
            value = _validated_final_payload(
                read_json(meta_invocation.output_path),
                invocation=meta_invocation,
                reviews=reviews,
            )
            meta[paper.index] = {
                "status": "resumed",
                "final_review_id": value["final_review_id"],
            }
        else:
            meta[paper.index] = {"status": "pending"}
            ready_meta.append(_WorkItem("meta", paper.index))
    return specialist, meta, concat, pending, ready_meta


def _validate_completed_batch_state(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
    specialist: dict[tuple[int, str], dict[str, Any]],
    meta: dict[int, dict[str, Any]],
    concat: dict[int, dict[str, Any]],
) -> None:
    """Reread every completed artifact before declaring the batch terminal."""
    confirmed_specialist, confirmed_meta, confirmed_concat, _, _ = (
        _load_existing_artifacts(
            runtime=runtime,
            output_root=output_root,
            run_id=run_id,
        )
    )
    groups = (
        ("Specialist", specialist, confirmed_specialist, "review_id"),
        ("Meta", meta, confirmed_meta, "final_review_id"),
        ("concat", concat, confirmed_concat, "final_review_id"),
    )
    for label, expected, observed, identity_field in groups:
        for key, state in expected.items():
            if state.get("status") not in {"completed", "resumed"}:
                continue
            confirmed = observed.get(key)
            if (
                not isinstance(confirmed, dict)
                or confirmed.get("status") != "resumed"
                or confirmed.get(identity_field) != state.get(identity_field)
            ):
                raise CocoReviewInfrastructureFailure(
                    f"Completed {label} artifact failed terminal revalidation: {key}"
                )


def _validate_scoring_output_tree(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
) -> None:
    expected_paper_ids = {paper.paper_id for paper in runtime.papers}
    review_root = output_root / "reviews" / run_id
    if not review_root.is_dir():
        raise FileNotFoundError(f"Completed Specialist Review root is missing: {review_root}")
    review_children = list(review_root.iterdir())
    if (
        any(not path.is_dir() for path in review_children)
        or {path.name for path in review_children} != expected_paper_ids
    ):
        raise ValueError("Specialist Review tree differs from the exact frozen paper set")
    expected_specialist_names = {
        filename
        for role in ROLES
        for filename in (f"{role}.json", f"{role}.raw.json", f"{role}.request.json")
    }
    for paper in runtime.papers:
        paper_root = review_root / paper.paper_id
        children = list(paper_root.iterdir())
        if (
            any(not path.is_file() for path in children)
            or {path.name for path in children} != expected_specialist_names
        ):
            raise ValueError(
                "Specialist Review files differ from the exact committed schema: "
                f"{paper.paper_id}"
            )

    for mode in ("meta", "concat"):
        final_root = output_root / "final_reviews" / f"{run_id}-{mode}"
        if not final_root.is_dir():
            raise FileNotFoundError(f"Completed Final Review root is missing: {final_root}")
        children = list(final_root.iterdir())
        if any(not path.is_file() for path in children):
            raise ValueError(f"Final Review root contains a non-file entry: {final_root}")
        expected_final_names = {f"{paper_id}.json" for paper_id in expected_paper_ids}
        allowed_names = set(expected_final_names)
        if mode == "meta":
            allowed_names.update(
                f"{paper_id}.meta.raw.{attempt}.json"
                for paper_id in expected_paper_ids
                for attempt in range(1, META_MAX_ATTEMPTS + 1)
            )
        observed_names = {path.name for path in children}
        if not expected_final_names.issubset(observed_names) or not observed_names.issubset(
            allowed_names
        ):
            raise ValueError(
                f"{mode} Final Review tree differs from the exact committed schema"
            )


def _completed_progress(
    value: Any,
    *,
    planned: int,
    label: str,
) -> None:
    expected_fields = {
        "planned",
        "completed",
        "resumed",
        "failed",
        "blocked",
        "pending",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or any(type(value[name]) is not int for name in expected_fields)
        or value["planned"] != planned
        or value["completed"] + value["resumed"] != planned
        or any(value[name] != 0 for name in ("failed", "blocked", "pending"))
    ):
        raise ValueError(f"Completed CoCo Review {label} progress is not exact")


def validate_completed_coco_review_batch(
    *,
    frozen_system_path: str | Path,
    coco_manifest_path: str | Path,
    output_root: str | Path,
    run_id: str,
) -> dict[str, Any]:
    """Revalidate the complete winner batch before any external scoring call."""
    root = Path(output_root).expanduser().resolve()
    run_dir = root / "runs" / run_id
    summary_path = run_dir / "coco_review_batch.json"
    with _exclusive_run_lock(run_dir):
        runtime = _load_frozen_runtime(
            frozen_system_path=frozen_system_path,
            coco_manifest_path=coco_manifest_path,
        )
        if not summary_path.is_file():
            raise FileNotFoundError(
                f"Completed CoCo Review batch summary is missing: {summary_path}"
            )
        summary_sha256 = file_sha256(summary_path)
        summary = read_json(summary_path)
        expected_summary_fields = {
            "protocol",
            "stage",
            "run_id",
            "status",
            "max_workers",
            "run_spec",
            "run_spec_sha256",
            "progress",
            "failure_count",
            "failures",
            "infrastructure_failures",
            "started_at",
            "updated_at",
            "completed_at",
        }
        max_workers = summary.get("max_workers") if isinstance(summary, dict) else None
        if (
            not isinstance(summary, dict)
            or set(summary) != expected_summary_fields
            or summary.get("protocol") != BATCH_PROTOCOL
            or summary.get("stage") != "generate-coco-1300-reviews"
            or summary.get("run_id") != run_id
            or summary.get("status") != "complete"
            or type(max_workers) is not int
            or not 1 <= max_workers <= 8
            or summary.get("failure_count") != 0
            or summary.get("failures") != []
            or summary.get("infrastructure_failures") != []
            or not _is_nonempty_text(summary.get("started_at"))
            or not _is_nonempty_text(summary.get("updated_at"))
            or not _is_nonempty_text(summary.get("completed_at"))
        ):
            raise ValueError("CoCo Review batch is not a complete failure-free winner batch")
        options = CocoReviewBatchOptions(run_id=run_id, max_workers=max_workers)
        run_spec = _run_spec(runtime, options)
        run_spec_sha256 = value_sha256(run_spec)
        if (
            summary.get("run_spec") != run_spec
            or summary.get("run_spec_sha256") != run_spec_sha256
        ):
            raise ValueError("Completed CoCo Review batch run specification differs")
        progress = summary.get("progress")
        if not isinstance(progress, dict) or set(progress) != {
            "specialist",
            "meta",
            "concat",
        }:
            raise ValueError("Completed CoCo Review batch progress is incomplete")
        _completed_progress(
            progress["specialist"],
            planned=COCO_1300_COUNT * len(ROLES),
            label="Specialist",
        )
        _completed_progress(
            progress["meta"], planned=COCO_1300_COUNT, label="Meta"
        )
        _completed_progress(
            progress["concat"], planned=COCO_1300_COUNT, label="concat"
        )
        specialist, meta, concat, pending, ready_meta = _load_existing_artifacts(
            runtime=runtime,
            output_root=root,
            run_id=run_id,
        )
        if (
            len(specialist) != COCO_1300_COUNT * len(ROLES)
            or len(meta) != COCO_1300_COUNT
            or len(concat) != COCO_1300_COUNT
            or pending
            or ready_meta
            or any(value.get("status") != "resumed" for value in specialist.values())
            or any(value.get("status") != "resumed" for value in meta.values())
            or any(value.get("status") != "resumed" for value in concat.values())
        ):
            raise ValueError("Completed CoCo Review artifacts do not cover the exact batch")
        _validate_completed_batch_state(
            runtime=runtime,
            output_root=root,
            run_id=run_id,
            specialist=specialist,
            meta=meta,
            concat=concat,
        )
        _validate_scoring_output_tree(
            runtime=runtime,
            output_root=root,
            run_id=run_id,
        )

        specialist_records: list[dict[str, Any]] = []
        arm_records: dict[str, list[dict[str, Any]]] = {"meta": [], "concat": []}
        for paper in runtime.papers:
            reviews: dict[str, dict[str, Any]] = {}
            for role in ROLES:
                invocation = _specialist_invocation(
                    runtime=runtime,
                    output_root=root,
                    run_id=run_id,
                    paper=paper,
                    role=role,
                )
                value = _validated_specialist_payload(
                    read_json(invocation.output_path),
                    invocation=invocation,
                    paper=paper,
                    role_runtime=runtime.roles[role],
                )
                reviews[role] = value
                specialist_records.append(
                    {
                        "paper_id": paper.paper_id,
                        "role": role,
                        "review_id": value["review_id"],
                        "path": str(invocation.output_path),
                        "sha256": file_sha256(invocation.output_path),
                    }
                )
            for mode in ("meta", "concat"):
                invocation = _final_invocation(
                    runtime=runtime,
                    output_root=root,
                    run_id=run_id,
                    paper=paper,
                    mode=mode,
                )
                value = _validated_final_payload(
                    read_json(invocation.output_path),
                    invocation=invocation,
                    reviews=reviews,
                )
                arm_records[mode].append(
                    {
                        "paper_id": paper.paper_id,
                        "final_review_id": value["final_review_id"],
                        "source_review_ids": list(value["source_review_ids"]),
                        "path": str(invocation.output_path),
                        "sha256": file_sha256(invocation.output_path),
                    }
                )

        confirmed_runtime = _load_frozen_runtime(
            frozen_system_path=frozen_system_path,
            coco_manifest_path=coco_manifest_path,
        )
        if (
            _run_spec(confirmed_runtime, options) != run_spec
            or file_sha256(summary_path) != summary_sha256
            or read_json(summary_path) != summary
        ):
            raise RuntimeError("CoCo Review scoring sources changed during validation")

        paper_ids = [paper.paper_id for paper in runtime.papers]
        arms: dict[str, Any] = {}
        for mode, records in arm_records.items():
            arms[mode] = {
                "mode": mode,
                "run_id": f"{run_id}-{mode}",
                "count": len(records),
                "paper_ids_sha256": value_sha256(
                    [record["paper_id"] for record in records]
                ),
                "final_review_ids_sha256": value_sha256(
                    [record["final_review_id"] for record in records]
                ),
                "source_review_ids_sha256": value_sha256(
                    [record["source_review_ids"] for record in records]
                ),
                "records_sha256": value_sha256(records),
                "files_sha256": value_sha256(
                    [
                        {"path": record["path"], "sha256": record["sha256"]}
                        for record in records
                    ]
                ),
                "paths": [record["path"] for record in records],
            }
        body = {
            "protocol": SCORING_SOURCE_PROTOCOL,
            "run_id": run_id,
            "system": {
                "path": str(runtime.system_path),
                "sha256": runtime.system_sha256,
                "system_id": runtime.system_id,
                "spec_id": runtime.formal_spec_id,
            },
            "coco_manifest": {
                "path": str(runtime.manifest_path),
                "sha256": runtime.manifest_sha256,
                "manifest_sha256": runtime.manifest_id,
                "count": len(runtime.papers),
                "paper_ids_sha256": value_sha256(paper_ids),
            },
            "summary": {"path": str(summary_path), "sha256": summary_sha256},
            "run_spec_sha256": run_spec_sha256,
            "specialists": {
                "count": len(specialist_records),
                "review_ids_sha256": value_sha256(
                    [record["review_id"] for record in specialist_records]
                ),
                "records_sha256": value_sha256(specialist_records),
                "files_sha256": value_sha256(
                    [
                        {"path": record["path"], "sha256": record["sha256"]}
                        for record in specialist_records
                    ]
                ),
            },
            "arms": arms,
        }
        return {**body, "source_id": value_sha256(body)}


def _paper_reviews(
    *,
    runtime: _FrozenRuntime,
    output_root: Path,
    run_id: str,
    paper: _CocoPaper,
) -> dict[str, dict[str, Any]]:
    reviews = {}
    for role in ROLES:
        invocation = _specialist_invocation(
            runtime=runtime,
            output_root=output_root,
            run_id=run_id,
            paper=paper,
            role=role,
        )
        if not invocation.output_path.is_file():
            return {}
        reviews[role] = _validated_specialist_payload(
            read_json(invocation.output_path),
            invocation=invocation,
            paper=paper,
            role_runtime=runtime.roles[role],
        )
    return reviews
