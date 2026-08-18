"""Fail-closed entry gate for temporal, asset and Human-Reference boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

from agentreview.paper_context import CANONICAL_PAPER_CONTEXT_VERSION

from .assets import (
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
    validate_asset_manifest,
)
from .evaluator import EXPERIENCE_PROTOCOL, EXPERIENCE_PROTOCOL_VERSION
from .agentreview_data import assert_no_human_reference_leak
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
}


def _validate_family_target_protocol(
    split_summary: Dict[str, Any], zones: Dict[str, Any]
) -> None:
    target_count = sum(len(zones[zone]) for zone in FOUR_ZONES)
    family_count = int(split_summary.get("unique_paper_family_count", -1))
    deduplication = split_summary.get("deduplication_audit") or {}
    if (
        split_summary.get("target_unit")
        != "one representative paper per paper family"
        or family_count != target_count
        or int(split_summary.get("formal_target_count", -1)) != target_count
        or int((split_summary.get("families") or {}).get("count", -1))
        != target_count
        or not deduplication.get("passed")
        or deduplication.get("formal_target_count") != target_count
        or deduplication.get("unique_paper_family_count") != target_count
        or deduplication.get("cross_zone_paper_family_overlap_count") != 0
        or deduplication.get("cross_zone_normalized_title_overlap_count") != 0
    ):
        raise RuntimeError("Formal split violates one-paper-family-one-target protocol")


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc


def _base_id(value: Dict[str, Any]) -> str:
    metadata = value.get("metadata") or {}
    return str(metadata.get("paper_id") or value.get("id") or "").split("::", 1)[0]


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _validate_v2_sidecar(
    asset_report: Dict[str, Any],
    *,
    temporal_split_manifest_path: str,
    s2_pool_path: str,
    expected_target_count: int,
) -> Dict[str, Any]:
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
    config_identity: Dict[str, Any],
) -> Dict[str, Any]:
    asset_report = validate_asset_manifest(
        asset_manifest_path,
        required_assets=REQUIRED_ASSETS,
        required_versions=FORMAL_REQUIRED_VERSIONS,
    )
    if not asset_report["formal_ready"]:
        raise RuntimeError(
            f"Asset Manifest is not formal-ready: {asset_report['formal_blockers']}"
        )
    split_summary, zones = load_temporal_split_manifest(
        temporal_split_manifest_path
    )
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise RuntimeError("Formal execution rejects contaminated temporal split v1")
    if not (split_summary.get("v1_v2_comparison_audit") or {}).get("passed"):
        raise RuntimeError("Temporal split v2 lacks a passed v1/v2 comparison audit")
    _validate_family_target_protocol(split_summary, zones)
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
    prepared_manifest = json.loads(
        prepared_manifest_path.read_text(encoding="utf-8")
    )
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
    episodes_path = task_root / "episodes.jsonl"
    validation_path = task_root / "validation.jsonl"
    queries_path = task_root / "queries.jsonl"
    evaluator_references_path = (
        data_path
        / "evaluator_only"
        / task_id
        / "validation_references.jsonl"
    )
    if not episodes_path.is_file() or not validation_path.is_file():
        raise FileNotFoundError("Formal A/B task files are missing")
    if not evaluator_references_path.is_file():
        raise FileNotFoundError(
            "Formal B evaluator-only reference artifact is missing"
        )
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
    human_reference_audit = assert_no_human_reference_leak(
        {
            "historical_tasks": episodes,
            "validation_tasks": validation,
            "queries": queries,
        },
        evaluator_references,
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
        if metadata.get("contains_human_reference") is not False:
            violations.append(f"A Human Reference marker is not false {paper_id}")
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

    program_sha256 = {Path(path).name: sha256_file(Path(path)) for path in program_paths}
    expected_programs = {
        "vector_search.py": asset_report["verified_assets"]["seed_vector_search"]["sha256"],
        "llm_summarizer.py": asset_report["verified_assets"]["seed_llm_summarizer"]["sha256"],
        "experience_learner.py": asset_report["verified_assets"]["seed_experience_learner"]["sha256"],
    }
    if program_sha256 != expected_programs:
        raise ValueError("Formal seed Program SHA256 differs from Asset Manifest")
    config_sha256 = _canonical_hash(config_identity)
    return {
        "protocol": "formal-experiment-gate-v1",
        "passed": True,
        "asset_manifest": asset_report,
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
    }


def run_formal_test_gate(
    *,
    asset_manifest_path: str,
    temporal_split_manifest_path: str,
    test_manifest_path: str,
) -> Dict[str, Any]:
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
    split_summary, zones = load_temporal_split_manifest(temporal_split_manifest_path)
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise RuntimeError("Formal D/Test rejects contaminated temporal split v1")
    if not (split_summary.get("v1_v2_comparison_audit") or {}).get("passed"):
        raise RuntimeError("Formal D/Test requires the passed v1/v2 split audit")
    _validate_family_target_protocol(split_summary, zones)
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
        str(value["paper_id"]): str(value["paper_review_cutoff"])
        for value in held_out
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
        "four_zone_temporal_audit": temporal_audit,
        "s2_future_leak_audit": s2_audit,
        "s2_query_integrity_audit": s2_sidecar["query_integrity_audit"],
        "test_manifest": str(manifest_source),
        "test_manifest_sha256": sha256_file(manifest_source),
        "held_out_paper_count": len(held_out),
        "held_out_paper_ids_sha256": expected_ids_sha,
    }
