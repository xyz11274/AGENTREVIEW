"""Report screening readiness without exposing credentials."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from memory_evolution.assets import (
    FOUR_ZONES,
    audit_four_zone_isolation,
    load_temporal_split_manifest,
)

from .activation import activation_report
from .arms import program_arm_identities
from .prepare import audit_selected_s2_pool, iter_jsonl, load_config, sha256_file


def _model_complete(path: Path):
    index_path = path / "model.safetensors.index.json"
    if not index_path.is_file():
        return False, 0, []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    shards = sorted(set((index.get("weight_map") or {}).values()))
    missing = [
        shard
        for shard in shards
        if not (path / shard).is_file() or (path / shard).stat().st_size <= 0
    ]
    return not missing, len(shards), missing


def _endpoint_ready(url: str):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) == 200
    except Exception:
        return False


def check(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(config["output_root"]).expanduser().resolve()
    related_path = Path(config["frozen_s2_pool"]).expanduser().resolve()
    related_manifest_path = (
        Path(config["frozen_s2_pool_manifest"]).expanduser().resolve()
    )
    coco_root = Path(config["external_roots"]["cocoreviewbench"])
    split_root = coco_root / "model" / "split_model"
    classify_root = coco_root / "model" / "classify_model"
    split_complete, split_shards, split_missing = _model_complete(split_root)
    classify_complete, classify_shards, classify_missing = _model_complete(
        classify_root
    )
    _, temporal_zones = load_temporal_split_manifest(config["temporal_split_manifest"])
    temporal_audit = audit_four_zone_isolation(temporal_zones)
    selected_values = []
    for filename in ("memory_corpus.jsonl", "effectiveness_validation.jsonl"):
        path = output_root / filename
        if path.is_file():
            selected_values.extend(iter_jsonl(path))
    if related_path.is_file() and selected_values:
        s2_temporal_audit = audit_selected_s2_pool(config, selected_values)
    else:
        s2_temporal_audit = {
            "passed": False,
            "reason": "selected A/C records and the Frozen S2 v2 asset are required",
        }
    if related_manifest_path.is_file():
        related_manifest = json.loads(related_manifest_path.read_text(encoding="utf-8"))
    else:
        related_manifest = {}
    s2_manifest_checks = {
        "pool_version": related_manifest.get("pool_version")
        == config["frozen_s2_pool_version"],
        "asset_sha256": related_path.is_file()
        and related_manifest.get("asset_sha256") == sha256_file(related_path),
        "query_integrity": (related_manifest.get("query_integrity_audit") or {}).get(
            "passed"
        )
        is True,
        "temporal_integrity": (related_manifest.get("temporal_audit") or {}).get(
            "passed"
        )
        is True,
    }
    allowed_by_file = {
        "memory_corpus.jsonl": {
            str(value["paper_id"]) for value in temporal_zones[FOUR_ZONES[0]]
        },
        "effectiveness_validation.jsonl": {
            str(value["paper_id"]) for value in temporal_zones[FOUR_ZONES[2]]
        },
    }
    zone_membership_violations = {}
    for filename, allowed in allowed_by_file.items():
        path = output_root / filename
        zone_membership_violations[filename] = (
            sum(
                str(value.get("paper_id") or "") not in allowed
                for value in iter_jsonl(path)
            )
            if path.is_file()
            else None
        )
    checks = {
        "selection_audit": (output_root / "selection_audit.json").is_file(),
        "effectiveness_selection_20": (
            (output_root / "effectiveness_validation.jsonl").is_file()
            and sum(
                1 for _ in iter_jsonl(output_root / "effectiveness_validation.jsonl")
            )
            == 20
        ),
        "memory_corpus_selection_20": (
            (output_root / "memory_corpus.jsonl").is_file()
            and sum(1 for _ in iter_jsonl(output_root / "memory_corpus.jsonl")) == 20
        ),
        "programs_frozen": (
            output_root / "programs" / "program_manifest.json"
        ).is_file(),
        "development_assets_match_frozen_temporal_zones": temporal_audit["passed"]
        and all(value == 0 for value in zone_membership_violations.values()),
        "frozen_s2_v2_manifest": all(s2_manifest_checks.values()),
        "frozen_s2_v2_selected_query_cutoff_audit": bool(
            s2_temporal_audit.get("passed")
        ),
        "paper_context_bundles_40": (
            output_root / "paper_context_manifest.json"
        ).is_file(),
        "OpenAI_route_set": bool(
            os.environ.get("OPENAI_API_KEY")
            and (os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL"))
        ),
        "program_specific_historical_replays": all(
            (
                output_root
                / "runs"
                / f"replicate_{replicate}"
                / identity.arm_id
                / "history"
                / "single_paper_review"
                / "memory_library.jsonl"
            ).is_file()
            for replicate in config["experiment"]["replicates"]
            for identity in program_arm_identities(config)
        ),
        "ReviewSplit_download_complete": split_complete,
        "ReviewClassify_download_complete": classify_complete,
        "ReviewSplit_service_13099": _endpoint_ready(
            "http://127.0.0.1:13099/v1/models"
        ),
        "ReviewClassify_service_13098": _endpoint_ready(
            "http://127.0.0.1:13098/v1/models"
        ),
    }
    evolved_root = Path(config["evolved_program_root"]).expanduser().resolve()
    stage_assets = {
        "program_freeze": {
            "specialist_promotion_manifest": evolved_root / "suite_manifest.json",
            "meta_promotion_manifest": evolved_root / "meta_manifest.json",
        },
        "generation": {
            "frozen_s2_pool": related_path,
            "frozen_s2_pool_manifest": related_manifest_path,
            "paper_context_manifest": output_root / "paper_context_manifest.json",
            "program_manifest": output_root / "programs" / "program_manifest.json",
        },
        "evaluation": {
            "run_audit": output_root / "run_audit.json",
            "effectiveness_selection": output_root / "effectiveness_validation.jsonl",
        },
        "summary": {
            "judged_outputs": {
                "path": output_root / "evaluation" / "judged",
                "type": "directory",
            },
            "blind_judge_results": output_root
            / "evaluation"
            / "blind_judge_results.jsonl",
        },
    }
    activation = {
        stage: activation_report(config, stage, assets)
        for stage, assets in stage_assets.items()
    }
    return {
        "checks": checks,
        "activation": activation,
        "pending_activation": {
            stage: {
                "requirements": report["pending_requirements"],
                "assets": report["pending_assets"],
            }
            for stage, report in activation.items()
            if report["active"] is False
        },
        "details": {
            "frozen_s2_pool": str(related_path),
            "frozen_s2_pool_version": config["frozen_s2_pool_version"],
            "frozen_s2_manifest_checks": s2_manifest_checks,
            "ReviewSplit_expected_shards": split_shards,
            "ReviewSplit_missing_shards": split_missing,
            "ReviewClassify_expected_shards": classify_shards,
            "ReviewClassify_missing_shards": classify_missing,
            "zone_membership_violations": zone_membership_violations,
            "four_zone_temporal_audit": temporal_audit,
            "s2_future_leak_audit": s2_temporal_audit,
        },
        "credentials_exposed": False,
        "ready_for_history_generation": all(
            checks[name]
            for name in (
                "paper_context_bundles_40",
                "OpenAI_route_set",
                "programs_frozen",
                "development_assets_match_frozen_temporal_zones",
                "frozen_s2_v2_manifest",
                "frozen_s2_v2_selected_query_cutoff_audit",
            )
        )
        and activation["generation"]["active"],
        "ready_for_target_matrix": all(
            checks[name]
            for name in (
                "paper_context_bundles_40",
                "OpenAI_route_set",
                "program_specific_historical_replays",
                "development_assets_match_frozen_temporal_zones",
                "frozen_s2_v2_manifest",
                "frozen_s2_v2_selected_query_cutoff_audit",
            )
        )
        and activation["generation"]["active"],
        "ready_for_proxy_classification": all(
            checks[name]
            for name in (
                "ReviewSplit_download_complete",
                "ReviewClassify_download_complete",
                "ReviewSplit_service_13099",
                "ReviewClassify_service_13098",
            )
        )
        and activation["evaluation"]["active"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    print(json.dumps(check(config), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
