#!/usr/bin/env python3
"""Register user-generated formal assets after a read-only protocol audit.

This command never builds S2 data, paper bundles, or experiment runs.  It only
validates assets that already exist and atomically updates Asset Manifest v2
after every specialist A/B gate passes.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory_evolution.assets import (
    ASSET_MANIFEST_VERSION,
    FOUR_ZONES,
    TEMPORAL_SPLIT_VERSION,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
    sha256_file,
)
from memory_evolution.formal_protocol import run_formal_protocol_gate
from memory_evolution.tasks import SPECIALIST_TASKS
from memory_evolution.toolkit import validate_frozen_s2_pool


POOL_VERSION = "frozen-s2-temporal-v2"
DEPRECATION_REASON = "query_title_serialization_contamination_and_duplicate_family_targets"
ZONE_ASSET_NAMES = {
    FOUR_ZONES[0]: "zone_A_historical_experience",
    FOUR_ZONES[1]: "zone_B_evolution_validation",
    FOUR_ZONES[2]: "zone_C_effectiveness_holdout",
    FOUR_ZONES[3]: "zone_D_held_out_benchmark_test",
}


def portable_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def build_candidate_manifest(
    payload: Dict[str, Any],
    *,
    temporal_split_manifest: Path,
    split_summary: Dict[str, Any],
    s2_pool: Path,
    s2_manifest: Path,
) -> Dict[str, Any]:
    candidate = copy.deepcopy(payload)
    if candidate.get("manifest_version") != ASSET_MANIFEST_VERSION:
        raise ValueError("Unsupported Asset Manifest version")
    assets = candidate.setdefault("assets", {})
    previous_temporal = copy.deepcopy(assets.get("temporal_split_manifest") or {})
    deprecated = candidate.setdefault("deprecated_assets", {})
    if previous_temporal:
        previous_temporal.update(
            {
                "deprecated_for_formal_use": True,
                "reason": DEPRECATION_REASON,
            }
        )
        deprecated["coco_temporal_split_v1"] = previous_temporal
    deprecated["frozen_s2_temporal_v1"] = {
        "path": "configs/frozen_s2_temporal_v1.jsonl",
        "work_path": "configs/frozen_s2_temporal_v1.jsonl.work",
        "version": "frozen-s2-temporal-v1",
        "deprecated_for_formal_use": True,
        "reason": DEPRECATION_REASON,
    }
    assets["temporal_split_manifest"] = {
        "path": portable_path(temporal_split_manifest),
        "sha256": sha256_file(temporal_split_manifest),
        "version": TEMPORAL_SPLIT_VERSION,
        "split": "A+B+C+D",
    }
    family_entry = split_summary["families"]
    family_path = temporal_split_manifest.parent / family_entry["path"]
    assets["temporal_family_manifest"] = {
        "path": portable_path(family_path),
        "sha256": sha256_file(family_path),
        "version": TEMPORAL_SPLIT_VERSION,
        "split": "A+B+C+D-family-index",
    }
    for zone in FOUR_ZONES:
        entry = split_summary["zones"][zone]
        zone_path = temporal_split_manifest.parent / entry["path"]
        assets[ZONE_ASSET_NAMES[zone]] = {
            "path": portable_path(zone_path),
            "sha256": sha256_file(zone_path),
            "version": TEMPORAL_SPLIT_VERSION,
            "split": zone,
        }
    generator = PROJECT_ROOT / "scripts/build_coco_temporal_split.py"
    assets["temporal_split_generator"] = {
        "path": portable_path(generator),
        "sha256": sha256_file(generator),
        "version": TEMPORAL_SPLIT_VERSION,
        "split": "infrastructure",
    }
    assets["frozen_s2_pool"] = {
        "path": portable_path(s2_pool),
        "sha256": sha256_file(s2_pool),
        "version": POOL_VERSION,
        "split": "A+B+C+D",
    }
    assets["frozen_s2_pool_manifest"] = {
        "path": portable_path(s2_manifest),
        "sha256": sha256_file(s2_manifest),
        "version": POOL_VERSION,
        "split": "A+B+C+D",
    }
    candidate["formal_ready"] = True
    candidate["formal_readiness_scope"] = "pre-QUAL-evolution assets and A/B task bundles"
    candidate["formal_blockers"] = []
    return candidate


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit existing frozen S2 + formal A/B bundles and register them; "
            "does not generate data or launch Evolution."
        )
    )
    parser.add_argument(
        "--asset-manifest",
        default=str(PROJECT_ROOT / "configs/asset_manifest_v2.json"),
    )
    parser.add_argument(
        "--temporal-split-manifest",
        default=str(PROJECT_ROOT / "configs/coco_temporal_split_v2/manifest.json"),
    )
    parser.add_argument("--s2-pool", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run the same audit without updating Asset Manifest.",
    )
    args = parser.parse_args()

    asset_path = Path(args.asset_manifest).expanduser().resolve()
    split_path = Path(args.temporal_split_manifest).expanduser().resolve()
    s2_path = Path(args.s2_pool).expanduser().resolve()
    s2_manifest_path = s2_path.with_suffix(s2_path.suffix + ".manifest.json")
    data_root = Path(args.data_root).expanduser().resolve()
    for required in (asset_path, split_path, s2_path, s2_manifest_path):
        if not required.is_file():
            raise FileNotFoundError(f"Required existing asset is missing: {required}")
    if not data_root.is_dir():
        raise FileNotFoundError(f"Formal A/B data root is missing: {data_root}")

    split_summary, zones = load_temporal_split_manifest(str(split_path))
    if split_summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise RuntimeError("Formal readiness rejects contaminated temporal split v1")
    if not (split_summary.get("v1_v2_comparison_audit") or {}).get("passed"):
        raise RuntimeError("Temporal split v2 comparison audit is missing or failed")
    temporal_audit = audit_four_zone_isolation(zones)
    if not temporal_audit["passed"]:
        raise RuntimeError(f"Temporal split audit failed: {temporal_audit}")
    all_targets = [value for zone in FOUR_ZONES for value in zones[zone]]
    cutoffs = {
        str(value["paper_id"]): str(value["paper_review_cutoff"])
        for value in all_targets
    }
    queries = {
        str(value["paper_id"]): str(value["title"])
        for value in all_targets
    }
    s2_audit = validate_frozen_s2_pool(
        str(s2_path),
        expected_cutoffs=cutoffs,
        required_ids=set(cutoffs),
        expected_pool_version=POOL_VERSION,
        expected_queries=queries,
    )
    if not s2_audit["passed"] or s2_audit["cached_count"] != len(all_targets):
        raise RuntimeError(f"Frozen S2 pool is not complete/time-safe: {s2_audit}")
    sidecar = json.loads(s2_manifest_path.read_text(encoding="utf-8"))
    query_audit = sidecar.get("query_integrity_audit") or {}
    if (
        sidecar.get("pool_version") != POOL_VERSION
        or int(sidecar.get("paper_count", -1)) != len(all_targets)
        or sidecar.get("asset_sha256") != sha256_file(s2_path)
        or sidecar.get("temporal_split_manifest_sha256") != sha256_file(split_path)
        or not (sidecar.get("temporal_audit") or {}).get("passed")
        or not query_audit.get("passed")
        or query_audit.get("required_count") != len(all_targets)
        or query_audit.get("checked_count") != len(all_targets)
        or query_audit.get("malformed_titles") != []
        or query_audit.get("canonical_mismatches") != []
        or query_audit.get("polluted_query_titles") != []
    ):
        raise RuntimeError("Frozen S2 sidecar manifest does not match audited assets")

    current = json.loads(asset_path.read_text(encoding="utf-8"))
    candidate = build_candidate_manifest(
        current,
        temporal_split_manifest=split_path,
        split_summary=split_summary,
        s2_pool=s2_path,
        s2_manifest=s2_manifest_path,
    )
    seed_root = PROJECT_ROOT / "memory_evolution" / "seeds"
    seed_paths = [
        str(seed_root / "vector_search.py"),
        str(seed_root / "llm_summarizer.py"),
        str(seed_root / "experience_learner.py"),
    ]

    pending_path = None
    gate_reports = {}
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="asset_manifest_v2.pending.",
            dir=asset_path.parent,
            delete=False,
        ) as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            pending_path = Path(handle.name)
        for task_id in SPECIALIST_TASKS:
            gate_reports[task_id] = run_formal_protocol_gate(
                asset_manifest_path=str(pending_path),
                temporal_split_manifest_path=str(split_path),
                data_root=str(data_root),
                task_id=task_id,
                s2_pool_path=str(s2_path),
                program_paths=seed_paths,
                config_identity={
                    "audit_only": True,
                    "task_id": task_id,
                    "temporal_split_version": split_summary["version"],
                    "s2_asset_sha256": s2_audit["asset_sha256"],
                },
            )
    finally:
        if pending_path is not None:
            pending_path.unlink(missing_ok=True)

    if not args.check_only:
        atomic_write_json(asset_path, candidate)
    report = {
        "protocol": "formal-readiness-audit-v1",
        "passed": True,
        "check_only": bool(args.check_only),
        "asset_manifest_updated": not args.check_only,
        "asset_manifest": str(asset_path),
        "asset_manifest_sha256": (
            sha256_file(asset_path) if not args.check_only else None
        ),
        "temporal_zone_counts": temporal_audit["zone_counts"],
        "s2_target_count": s2_audit["cached_count"],
        "s2_asset_sha256": s2_audit["asset_sha256"],
        "query_integrity_audit": sidecar["query_integrity_audit"],
        "specialist_gates": {
            task_id: {
                "passed": gate_reports[task_id]["passed"],
                "historical_manifest_sha256": gate_reports[task_id][
                    "historical_manifest_sha256"
                ],
                "evolution_validation_manifest_sha256": gate_reports[task_id][
                    "evolution_validation_manifest_sha256"
                ],
            }
            for task_id in SPECIALIST_TASKS
        },
        "qual_evolution_may_start": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
