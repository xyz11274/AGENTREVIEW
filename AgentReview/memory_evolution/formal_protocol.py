"""Fail-closed entry gate for temporal, asset and Human-Reference boundaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agentreview.paper_context import CANONICAL_PAPER_CONTEXT_VERSION
from agentreview.reviewer_identity import (
    ALL_TASKS,
    META_REQUEST_PROTOCOL_VERSION,
    REVIEWER_IDENTITY_VERSION,
    validate_reviewer_identity_manifest,
)
from agentreview.reviewer_runtime.program_loader import resolve_manifest_path

from .agentreview_data import assert_no_human_reference_leak
from .assets import (
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    audit_family_target_integrity,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
    validate_asset_manifest,
)
from .evaluator import EXPERIENCE_PROTOCOL, EXPERIENCE_PROTOCOL_VERSION
from .toolkit import validate_frozen_s2_pool

REQUIRED_ASSETS = (
    "temporal_split_manifest",
    "temporal_family_manifest",
    "seed_vector_search",
    "seed_llm_summarizer",
    "seed_experience_learner",
    "frozen_reviewer_identity",
    "frozen_s2_pool",
    "frozen_s2_pool_manifest",
)
FROZEN_S2_POOL_VERSION = "frozen-s2-temporal-v2"
FORMAL_REQUIRED_VERSIONS = {
    "temporal_split_manifest": TEMPORAL_SPLIT_VERSION,
    "temporal_family_manifest": TEMPORAL_SPLIT_VERSION,
    "frozen_s2_pool": FROZEN_S2_POOL_VERSION,
    "frozen_s2_pool_manifest": FROZEN_S2_POOL_VERSION,
    "frozen_reviewer_identity": REVIEWER_IDENTITY_VERSION,
}
FROZEN_COCO_ROOT = Path("/data0/xyj/shengao/CoCoReviewBench")


def _remap_frozen_coco_path(source: str, coco_root: str) -> tuple[Path, Path]:
    """Map one frozen CoCo path to the same relative path under ``coco_root``.

    The frozen source identity is intentionally narrow: callers cannot use this
    mechanism to remap arbitrary absolute paths or paths that escape through
    ``..`` components or symlinks.
    """
    source_text = str(source or "")
    source_path = Path(source_text)
    if not source_path.is_absolute():
        raise ValueError(
            f"Formal CoCo provenance path is not absolute: {source_text!r}"
        )
    try:
        relative = source_path.relative_to(FROZEN_COCO_ROOT)
    except ValueError as exc:
        raise ValueError(
            "Formal CoCo provenance path is outside the frozen CoCo root: "
            f"{source_text}"
        ) from exc
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError(
            f"Formal CoCo provenance path has unsafe relative identity: {source_text}"
        )

    configured_root = Path(str(coco_root or "")).expanduser()
    if not str(coco_root or "") or not configured_root.is_absolute():
        raise ValueError(
            "Formal CoCo provenance requires an absolute current coco_root"
        )
    resolved_root = configured_root.resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(
            f"Configured current CoCo root is missing: {resolved_root}"
        )
    lexical_mapped = resolved_root / relative
    mapped = lexical_mapped.resolve(strict=False)
    try:
        mapped.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            "Mapped Formal CoCo provenance path escapes the current CoCo root: "
            f"{source_text}"
        ) from exc
    if mapped != lexical_mapped:
        raise ValueError(
            "Mapped Formal CoCo provenance path does not preserve relative path "
            f"identity: {source_text}"
        )
    return mapped, relative


def _verify_remapped_coco_file(
    source: str,
    expected_sha256: str,
    *,
    coco_root: str,
    label: str,
) -> dict[str, str]:
    mapped, relative = _remap_frozen_coco_path(source, coco_root)
    if not mapped.is_file():
        raise RuntimeError(f"Formal CoCo {label} is missing after root remap: {mapped}")
    actual_sha256 = sha256_file(mapped)
    if actual_sha256 != str(expected_sha256 or ""):
        raise RuntimeError(
            f"Formal CoCo {label} SHA256 mismatch after root remap: {mapped}"
        )
    return {
        "frozen_path": str(source),
        "relative_path": relative.as_posix(),
        "resolved_path": str(mapped),
        "sha256": actual_sha256,
    }


def _validate_frozen_coco_provenance(
    reference_source: dict[str, Any], *, coco_root: str
) -> dict[str, Any]:
    verified = {}
    for path_key, sha_key in (
        ("implementation_path", "implementation_sha256"),
        ("selection_loader_path", "selection_loader_sha256"),
        ("left_out_selection", "left_out_selection_sha256"),
    ):
        verified[path_key] = _verify_remapped_coco_file(
            str(reference_source.get(path_key) or ""),
            str(reference_source.get(sha_key) or ""),
            coco_root=coco_root,
            label=path_key,
        )

    benchmark_sha256 = reference_source.get("benchmark_file_sha256") or {}
    if not benchmark_sha256:
        raise RuntimeError("Formal CoCo reference benchmark provenance is empty")
    verified_benchmarks = []
    for source, expected_sha in benchmark_sha256.items():
        verified_benchmarks.append(
            _verify_remapped_coco_file(
                str(source),
                str(expected_sha),
                coco_root=coco_root,
                label="benchmark provenance",
            )
        )
    return {
        "frozen_root": str(FROZEN_COCO_ROOT),
        "current_root": str(Path(coco_root).expanduser().resolve()),
        "verified_reference_files": verified,
        "verified_benchmark_files": verified_benchmarks,
    }


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc


def _base_id(value: dict[str, Any]) -> str:
    metadata = value.get("metadata") or {}
    return str(metadata.get("paper_id") or value.get("id") or "").split("::", 1)[0]


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _validate_v2_sidecar(
    asset_report: dict[str, Any],
    *,
    temporal_split_manifest_path: str,
    s2_pool_path: str,
    expected_target_count: int,
) -> dict[str, Any]:
    sidecar_path = Path(
        asset_report["verified_assets"]["frozen_s2_pool_manifest"]["resolved_path"]
    )
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if sidecar.get("pool_version") != FROZEN_S2_POOL_VERSION:
        raise RuntimeError("Formal S2 sidecar is not Frozen S2 v2")
    if sidecar.get("asset_sha256") != sha256_file(Path(s2_pool_path).resolve()):
        raise RuntimeError("Formal S2 sidecar asset SHA256 mismatch")
    if sidecar.get("temporal_split_manifest_sha256") != sha256_file(
        Path(temporal_split_manifest_path).resolve()
    ):
        raise RuntimeError("Formal S2 sidecar temporal split SHA256 mismatch")
    if not (sidecar.get("temporal_audit") or {}).get("passed"):
        raise RuntimeError("Formal S2 sidecar temporal audit did not pass")
    query_audit = sidecar.get("query_integrity_audit") or {}
    if (
        not query_audit.get("passed")
        or query_audit.get("required_count") != expected_target_count
        or query_audit.get("checked_count") != expected_target_count
        or query_audit.get("malformed_titles") != []
        or query_audit.get("canonical_mismatches") != []
        or query_audit.get("polluted_query_titles") != []
    ):
        raise RuntimeError("Formal S2 sidecar query integrity audit did not pass")
    return sidecar


def run_formal_protocol_gate(
    *,
    asset_manifest_path: str,
    temporal_split_manifest_path: str,
    data_root: str,
    task_id: str,
    s2_pool_path: str,
    program_paths: Sequence[str],
    config_identity: dict[str, Any],
    coco_root: str = "",
) -> dict[str, Any]:
    asset_report = validate_asset_manifest(
        asset_manifest_path,
        required_assets=REQUIRED_ASSETS,
        required_versions=FORMAL_REQUIRED_VERSIONS,
    )
    if not asset_report["formal_ready"]:
        raise RuntimeError(
            f"Asset Manifest is not formal-ready: {asset_report['formal_blockers']}"
        )
    reviewer_identity = validate_reviewer_identity_manifest(
        asset_report["verified_assets"]["frozen_reviewer_identity"]["resolved_path"]
    )
    split_summary, zones = load_temporal_split_manifest(temporal_split_manifest_path)
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise RuntimeError("Formal execution rejects contaminated temporal split v1")
    family_audit = audit_family_target_integrity(split_summary, zones)
    if family_audit["passed"] is not True:
        raise RuntimeError(
            f"Temporal split v2 family-target integrity failed: {family_audit}"
        )
    isolation = audit_four_zone_isolation(zones)
    if not isolation["passed"]:
        raise RuntimeError(f"Four-zone temporal isolation failed: {isolation}")

    temporal_asset = asset_report["verified_assets"]["temporal_split_manifest"]
    if temporal_asset["resolved_path"] != str(
        Path(temporal_split_manifest_path).expanduser().resolve()
    ):
        raise ValueError("CLI temporal split differs from Asset Manifest")
    s2_asset = asset_report["verified_assets"]["frozen_s2_pool"]
    if s2_asset["resolved_path"] != str(Path(s2_pool_path).expanduser().resolve()):
        raise ValueError("CLI S2 pool differs from Asset Manifest")
    s2_sidecar = _validate_v2_sidecar(
        asset_report,
        temporal_split_manifest_path=temporal_split_manifest_path,
        s2_pool_path=s2_pool_path,
        expected_target_count=sum(len(zones[zone]) for zone in FOUR_ZONES),
    )

    zone_by_id = {
        zone: {str(value["paper_id"]): value for value in values}
        for zone, values in zones.items()
    }
    data_path = Path(data_root).expanduser().resolve()
    task_root = data_path / task_id
    prepared_manifest_path = (
        task_root / "manifest.json"
        if task_id == "META"
        else data_path / "manifest.json"
    )
    if not prepared_manifest_path.is_file():
        raise FileNotFoundError("Formal prepared-data manifest is missing")
    prepared_manifest = json.loads(prepared_manifest_path.read_text(encoding="utf-8"))
    task_manifest = (
        prepared_manifest
        if task_id == "META"
        else (prepared_manifest.get("tasks") or {}).get(task_id) or {}
    )
    reference_access = task_manifest.get("evaluator_reference_access") or {}
    if not (
        reference_access.get("evaluator_only") is True
        and reference_access.get("candidate_forbidden") is True
    ):
        raise RuntimeError(
            "Formal prepared-data manifest does not forbid Candidate access "
            "to evaluator references"
        )
    prepared_leak_audit = prepared_manifest.get("human_reference_leak_audit") or {}
    if not prepared_leak_audit.get("passed"):
        raise RuntimeError("Formal prepared-data Human Reference audit did not pass")
    coco_provenance = None
    if task_id != "META":
        reference_source = prepared_manifest.get("evaluator_reference_source") or {}
        if reference_source.get("implementation") != (
            "CoCoReviewBench.src.evaluation._4_evaluation.load_human_review_data"
        ):
            raise RuntimeError("Formal evaluator references were not built by CoCo")
        coco_provenance = _validate_frozen_coco_provenance(
            reference_source, coco_root=coco_root
        )
    episodes_path = task_root / "episodes.jsonl"
    validation_path = task_root / "validation.jsonl"
    queries_path = task_root / "queries.jsonl"
    evaluator_references_path = (
        data_path / "evaluator_only" / task_id / "validation_references.jsonl"
    )
    if not episodes_path.is_file() or not validation_path.is_file():
        raise FileNotFoundError("Formal A/B task files are missing")
    if not evaluator_references_path.is_file():
        raise FileNotFoundError("Formal B evaluator-only reference artifact is missing")
    episodes = list(_iter_jsonl(episodes_path))
    validation = list(_iter_jsonl(validation_path))
    queries = list(_iter_jsonl(queries_path)) if queries_path.is_file() else []
    evaluator_references = list(_iter_jsonl(evaluator_references_path))
    if not episodes or not validation:
        raise ValueError("Formal A and B must both be non-empty")
    reference_ids = [str(value.get("id") or "") for value in evaluator_references]
    validation_ids = [str(value.get("id") or "") for value in validation]
    if (
        len(set(reference_ids)) != len(reference_ids)
        or len(set(validation_ids)) != len(validation_ids)
        or set(reference_ids) != set(validation_ids)
    ):
        raise RuntimeError("Formal B evaluator reference coverage mismatch")
    for value in evaluator_references:
        access = value.get("access") or {}
        if not (
            access.get("evaluator_only") is True
            and access.get("candidate_forbidden") is True
        ):
            raise RuntimeError(
                "Formal B evaluator reference lacks candidate-forbidden access"
            )
    # Reapply the same-paper boundary assertion used at preparation time.
    authorized_fragments_by_paper = {}
    for value in [*episodes, *validation, *queries]:
        paper_id = _base_id(value)
        md_path = str(
            ((value.get("metadata") or {}).get("internal_provenance") or {}).get(
                "paper_md_path"
            )
            or ""
        )
        if not md_path:
            raise RuntimeError(
                f"Formal Candidate task lacks Markdown source: {paper_id}"
            )
        path, _ = _remap_frozen_coco_path(md_path, coco_root)
        if not path.is_file():
            raise FileNotFoundError(f"Formal Candidate Markdown is missing: {path}")
        authorized_fragments_by_paper.setdefault(paper_id, set()).update(
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if len(line.strip()) >= 20
        )
    human_reference_audit = assert_no_human_reference_leak(
        {
            "historical_tasks": episodes,
            "validation_tasks": validation,
            "queries": queries,
        },
        evaluator_references,
        authorized_fragments_by_paper=authorized_fragments_by_paper,
    )
    violations = []
    for value in episodes:
        paper_id = _base_id(value)
        metadata = value.get("metadata") or {}
        expected = zone_by_id[FOUR_ZONES[0]].get(paper_id)
        if not expected:
            violations.append(f"A contains non-A paper {paper_id}")
            continue
        if metadata.get("experience_protocol") != EXPERIENCE_PROTOCOL:
            violations.append(f"A protocol mismatch {paper_id}")
        if metadata.get("experience_protocol_version") != EXPERIENCE_PROTOCOL_VERSION:
            violations.append(f"A protocol version mismatch {paper_id}")
        human_reference_marker = metadata.get("contains_human_reference")
        if human_reference_marker is False:
            marker_violation = ""
        else:
            marker_violation = (
                f"A Human Reference marker requires false for paper {paper_id}"
            )
        if marker_violation:
            violations.append(marker_violation)
        if metadata.get("paper_review_cutoff") != expected["paper_review_cutoff"]:
            violations.append(f"A cutoff mismatch {paper_id}")
        if metadata.get("paper_context_version") != CANONICAL_PAPER_CONTEXT_VERSION:
            violations.append(f"A canonical preprocessing mismatch {paper_id}")
    for value in validation:
        paper_id = _base_id(value)
        metadata = value.get("metadata") or {}
        expected = zone_by_id[FOUR_ZONES[1]].get(paper_id)
        if not expected:
            violations.append(f"B contains non-B paper {paper_id}")
            continue
        if metadata.get("paper_review_cutoff") != expected["paper_review_cutoff"]:
            violations.append(f"B cutoff mismatch {paper_id}")
        if metadata.get("paper_context_version") != CANONICAL_PAPER_CONTEXT_VERSION:
            violations.append(f"B canonical preprocessing mismatch {paper_id}")
    if violations:
        raise RuntimeError("Formal data gate failed: " + "; ".join(violations[:20]))

    required_ids = {_base_id(value) for value in [*episodes, *validation]}
    cutoff_by_id = {
        paper_id: zone_by_id[zone][paper_id]["paper_review_cutoff"]
        for zone in FOUR_ZONES[:2]
        for paper_id in required_ids & set(zone_by_id[zone])
    }
    query_by_id = {
        paper_id: str(zone_by_id[zone][paper_id]["title"])
        for zone in FOUR_ZONES[:2]
        for paper_id in required_ids & set(zone_by_id[zone])
    }
    s2_audit = validate_frozen_s2_pool(
        s2_pool_path,
        expected_cutoffs=cutoff_by_id,
        required_ids=required_ids,
        expected_pool_version=FROZEN_S2_POOL_VERSION,
        expected_queries=query_by_id,
    )
    if not s2_audit["passed"]:
        raise RuntimeError(f"Frozen S2 temporal audit failed: {s2_audit}")

    program_sha256 = {
        Path(path).name: sha256_file(Path(path)) for path in program_paths
    }
    expected_programs = {
        "vector_search.py": asset_report["verified_assets"]["seed_vector_search"][
            "sha256"
        ],
        "llm_summarizer.py": asset_report["verified_assets"]["seed_llm_summarizer"][
            "sha256"
        ],
        "experience_learner.py": asset_report["verified_assets"][
            "seed_experience_learner"
        ]["sha256"],
    }
    if program_sha256 != expected_programs:
        raise ValueError("Formal seed Program SHA256 differs from Asset Manifest")
    config_sha256 = _canonical_hash(config_identity)
    return {
        "protocol": "formal-experiment-gate-v1",
        "passed": True,
        "asset_manifest": asset_report,
        "reviewer_identity": reviewer_identity,
        "temporal_split_manifest": str(Path(temporal_split_manifest_path).resolve()),
        "temporal_split_manifest_sha256": sha256_file(
            Path(temporal_split_manifest_path).resolve()
        ),
        "four_zone_temporal_audit": isolation,
        "s2_future_leak_audit": s2_audit,
        "s2_query_integrity_audit": s2_sidecar["query_integrity_audit"],
        "canonical_paper_preprocessing_version": CANONICAL_PAPER_CONTEXT_VERSION,
        "historical_manifest": str(episodes_path),
        "historical_manifest_sha256": sha256_file(episodes_path),
        "evolution_validation_manifest": str(validation_path),
        "evolution_validation_manifest_sha256": sha256_file(validation_path),
        "evaluator_only_validation_references": str(evaluator_references_path),
        "evaluator_only_validation_references_sha256": sha256_file(
            evaluator_references_path
        ),
        "human_reference_leak_audit": human_reference_audit,
        "program_sha256": program_sha256,
        "task_id": task_id,
        "lineage_names": [
            "evolved_from_vector_search",
            "evolved_from_llm_summarizer",
            "evolved_from_experience_learner",
        ],
        "experiment_config_sha256": config_sha256,
        "source_asset_identity": split_summary.get("source_sha256"),
        "coco_provenance": coco_provenance,
    }


def run_formal_test_gate(
    *,
    asset_manifest_path: str,
    temporal_split_manifest_path: str,
    test_manifest_path: str,
) -> dict[str, Any]:
    """Fail-closed gate used exclusively by ``final-evaluate-suite``."""
    asset_report = validate_asset_manifest(
        asset_manifest_path,
        required_assets=(
            "temporal_split_manifest",
            "temporal_family_manifest",
            "frozen_s2_pool",
            "frozen_s2_pool_manifest",
            "frozen_reviewer_identity",
        ),
        required_versions=FORMAL_REQUIRED_VERSIONS,
    )
    if not asset_report["formal_ready"]:
        raise RuntimeError(
            f"D Asset Manifest is not formal-ready: {asset_report['formal_blockers']}"
        )
    reviewer_identity = validate_reviewer_identity_manifest(
        asset_report["verified_assets"]["frozen_reviewer_identity"]["resolved_path"]
    )
    split_summary, zones = load_temporal_split_manifest(temporal_split_manifest_path)
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise RuntimeError("Formal D/Test rejects contaminated temporal split v1")
    family_audit = audit_family_target_integrity(split_summary, zones)
    if family_audit["passed"] is not True:
        raise RuntimeError(
            f"Formal D/Test family-target integrity failed: {family_audit}"
        )
    temporal_audit = audit_four_zone_isolation(zones)
    if not temporal_audit["passed"]:
        raise RuntimeError("D gate failed four-zone temporal audit")
    temporal_asset = asset_report["verified_assets"]["temporal_split_manifest"]
    if temporal_asset["resolved_path"] != str(
        Path(temporal_split_manifest_path).expanduser().resolve()
    ):
        raise RuntimeError("D temporal split differs from the Asset Manifest")

    manifest_source = Path(test_manifest_path).expanduser().resolve()
    test_manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    if (
        test_manifest.get("reviewer_suite_identity_sha256")
        == reviewer_identity["suite_identity_sha256"]
        and test_manifest.get("meta_request_protocol_version")
        == META_REQUEST_PROTOCOL_VERSION
    ):
        reviewer_materialization = {
            "suite_identity_sha256": reviewer_identity["suite_identity_sha256"],
            "meta_request_protocol_version": META_REQUEST_PROTOCOL_VERSION,
        }
    else:
        raise RuntimeError("D materialization uses a different Reviewer Identity")
    task_assets = dict(test_manifest.get("tasks") or {})
    if set(task_assets) == set(ALL_TASKS):
        verified_task_assets: dict[str, dict[str, Any]] = {}
    else:
        raise RuntimeError("D materialization requires all six reviewer task assets")
    for task_id in ALL_TASKS:
        entry = dict(task_assets[task_id])
        path = resolve_manifest_path(manifest_source, str(entry.get("path") or ""))
        expected_path = (
            manifest_source.parent
            / task_id
            / ("references.jsonl" if task_id == "META" else "test.jsonl")
        ).resolve()
        expected_sha256 = str(entry.get("sha256") or "")
        values = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if (
            path == expected_path
            and path.is_file()
            and sha256_file(path) == expected_sha256
            and len(values) == int(entry.get("count", -1))
        ):
            verified_task_assets[task_id] = {
                "path": str(path),
                "sha256": expected_sha256,
                "count": len(values),
            }
        else:
            raise RuntimeError(f"D {task_id} task asset identity mismatch")
    meta_entry = task_assets["META"]
    meta_context_path = resolve_manifest_path(
        manifest_source,
        str(meta_entry.get("context_path") or ""),
    )
    expected_meta_context_path = (
        manifest_source.parent / "META" / "contexts.jsonl"
    ).resolve()
    meta_context_values = [
        line
        for line in meta_context_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if (
        meta_context_path == expected_meta_context_path
        and meta_context_path.is_file()
        and sha256_file(meta_context_path)
        == str(meta_entry.get("context_sha256") or "")
        and len(meta_context_values) == int(meta_entry.get("context_count", -1))
    ):
        verified_task_assets["META"]["context_path"] = str(meta_context_path)
        verified_task_assets["META"]["context_sha256"] = str(
            meta_entry["context_sha256"]
        )
        verified_task_assets["META"]["context_count"] = len(meta_context_values)
    else:
        raise RuntimeError("D META context asset identity mismatch")
    s2_path = str(test_manifest.get("semantic_scholar_cache") or "")
    if not s2_path:
        raise RuntimeError("D materialization lacks its frozen S2 pool path")
    s2_asset = asset_report["verified_assets"]["frozen_s2_pool"]
    if s2_asset["resolved_path"] != str(Path(s2_path).expanduser().resolve()):
        raise RuntimeError("D S2 pool differs from the Asset Manifest")
    s2_sidecar = _validate_v2_sidecar(
        asset_report,
        temporal_split_manifest_path=temporal_split_manifest_path,
        s2_pool_path=s2_path,
        expected_target_count=sum(len(zones[zone]) for zone in FOUR_ZONES),
    )

    held_out = zones[FOUR_ZONES[3]]
    if int(test_manifest.get("paper_count", -1)) != len(held_out):
        raise RuntimeError("D materialization does not match held-out manifest")
    expected_ids_sha = hashlib.sha256(
        "\n".join(str(value["paper_id"]) for value in held_out).encode("utf-8")
    ).hexdigest()
    if test_manifest.get("paper_ids_sha256") != expected_ids_sha:
        raise RuntimeError("D paper identities differ from held-out manifest")
    cutoffs = {
        str(value["paper_id"]): str(value["paper_review_cutoff"]) for value in held_out
    }
    s2_audit = validate_frozen_s2_pool(
        s2_path,
        expected_cutoffs=cutoffs,
        required_ids=set(cutoffs),
        expected_pool_version=FROZEN_S2_POOL_VERSION,
        expected_queries={
            str(value["paper_id"]): str(value["title"]) for value in held_out
        },
    )
    if not s2_audit["passed"]:
        raise RuntimeError(f"D frozen S2 temporal audit failed: {s2_audit}")
    return {
        "protocol": "formal-held-out-test-gate-v1",
        "passed": True,
        "asset_manifest": asset_report,
        "reviewer_identity": reviewer_identity,
        "reviewer_materialization": reviewer_materialization,
        "verified_task_assets": verified_task_assets,
        "four_zone_temporal_audit": temporal_audit,
        "s2_future_leak_audit": s2_audit,
        "s2_query_integrity_audit": s2_sidecar["query_integrity_audit"],
        "test_manifest": str(manifest_source),
        "test_manifest_sha256": sha256_file(manifest_source),
        "held_out_paper_count": len(held_out),
        "held_out_paper_ids_sha256": expected_ids_sha,
    }
