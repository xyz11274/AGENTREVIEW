"""Fail-closed entry gate for temporal, asset and Human-Reference boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

from agentreview.paper_context import CANONICAL_PAPER_CONTEXT_VERSION

from .assets import (
    FOUR_ZONES,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
    validate_asset_manifest,
)
from .evaluator import EXPERIENCE_PROTOCOL, EXPERIENCE_PROTOCOL_VERSION
from .toolkit import validate_frozen_s2_pool


REQUIRED_ASSETS = (
    "temporal_split_manifest",
    "seed_vector_search",
    "seed_llm_summarizer",
    "seed_experience_learner",
    "frozen_reviewer_identity",
    "frozen_s2_pool",
)


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
        asset_manifest_path, required_assets=REQUIRED_ASSETS
    )
    if not asset_report["formal_ready"]:
        raise RuntimeError(
            f"Asset Manifest is not formal-ready: {asset_report['formal_blockers']}"
        )
    split_summary, zones = load_temporal_split_manifest(
        temporal_split_manifest_path
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

    zone_by_id = {
        zone: {str(value["paper_id"]): value for value in values}
        for zone, values in zones.items()
    }
    task_root = Path(data_root).expanduser().resolve() / task_id
    episodes_path = task_root / "episodes.jsonl"
    validation_path = task_root / "validation.jsonl"
    if not episodes_path.is_file() or not validation_path.is_file():
        raise FileNotFoundError("Formal A/B task files are missing")
    episodes = list(_iter_jsonl(episodes_path))
    validation = list(_iter_jsonl(validation_path))
    if not episodes or not validation:
        raise ValueError("Formal A and B must both be non-empty")
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
    s2_audit = validate_frozen_s2_pool(
        s2_pool_path,
        expected_cutoffs=cutoff_by_id,
        required_ids=required_ids,
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
        "canonical_paper_preprocessing_version": CANONICAL_PAPER_CONTEXT_VERSION,
        "historical_manifest": str(episodes_path),
        "historical_manifest_sha256": sha256_file(episodes_path),
        "evolution_validation_manifest": str(validation_path),
        "evolution_validation_manifest_sha256": sha256_file(validation_path),
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
