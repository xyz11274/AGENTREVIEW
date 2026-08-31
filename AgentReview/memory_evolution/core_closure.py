"""Three-stage Core v1 closure audit.

G1 is code/composition evidence and is intentionally independent of real G2/G3
artifact counts.  G2 and G3 are conservative presence checks; their contents
are validated by the stage-specific loaders/preflights before formal use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping

from agentreview.review_corpus import canonical_json_bytes


G2_REQUIRED_ARTIFACTS = {
    "corpus_success_manifest": "corpus/manifest.json",
    "full_reviews": "corpus/full_reviews.jsonl",
    "primary_projections": "corpus/projections.jsonl",
    "projection_fragments": "corpus/fragments.jsonl",
    "qual_review_memory_index": "review_memory/QUAL.json",
    "clar_review_memory_index": "review_memory/CLAR.json",
    "sign_review_memory_index": "review_memory/SIGN.json",
    "orig_review_memory_index": "review_memory/ORIG.json",
    "pol_review_memory_index": "review_memory/POL.json",
    "b_local_manifest": "b_local/manifest.json",
    "frozen_evidence_pool": "evidence/pool.json",
    "evaluator_production_smoke": "preflight/evaluator_smoke.json",
    "capacity_results": "capacity/results.json",
    "capacity_selection": "capacity/selection.json",
    "main_formal_runtime_profile": "runtime/main_formal_runtime_profile.json",
    "final_preflight": "preflight/final_preflight.json",
}

G3_REQUIRED_ARTIFACTS = {
    "qual_winner": "formal/role_winners/QUAL.json",
    "clar_winner": "formal/role_winners/CLAR.json",
    "sign_winner": "formal/role_winners/SIGN.json",
    "orig_winner": "formal/role_winners/ORIG.json",
    "pol_winner": "formal/role_winners/POL.json",
    "final_suite": "formal/final_suite.json",
    "official_authorization": "official/authorization.json",
    "official_report": "official/official_result.json",
    "immutable_final_report": "official/final_report.json",
}


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _artifact_checks(root: Path, required: Mapping[str, str]) -> Dict[str, bool]:
    return {key: (root / relative).is_file() for key, relative in required.items()}


def audit_core_v1_closure(
    *,
    smoke_report_path: str | Path,
    formal_artifact_root: str | Path,
    output_path: str | Path | None = None,
) -> Dict[str, Any]:
    repo = Path(__file__).resolve().parents[1]
    application_path = repo / "memory_evolution" / "core_application.py"
    cli_path = repo / "memory_evolution" / "core_cli.py"
    application_source = application_path.read_text(encoding="utf-8")
    smoke_path = Path(smoke_report_path).expanduser().resolve()
    smoke = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path.is_file() else {}
    g1_checks = {
        "core_production_cli_exists": cli_path.is_file(),
        "production_composition_root_exists": application_path.is_file(),
        "core_role_runner_has_production_caller": "CoreRoleEvolutionRunner(" in application_source,
        "core_reviewer_session_has_production_caller": "CoreV1ReviewerSession(" in application_source,
        "reference_free_evaluator_has_production_caller": "ReferenceFreeRoleEvaluator(" in application_source,
        "protected_official_access_has_production_caller": "ProtectedOfficialAccess(" in application_source,
        "production_integration_smoke_pass": smoke.get("status") == "PASS",
        "five_roles_traversed": tuple(smoke.get("role_order", ()))
        == ("QUAL", "CLAR", "SIGN", "ORIG", "POL"),
        "protected_access_zero_before_suite": smoke.get(
            "protected_loader_calls_before_authorization"
        )
        == 0
        and smoke.get("protected_loader_calls_after_suite_freeze") == 0,
        "protected_access_after_official_once": smoke.get(
            "protected_loader_calls_after_official"
        )
        == 1,
    }
    root = Path(formal_artifact_root).expanduser().resolve()
    g2_checks = _artifact_checks(root, G2_REQUIRED_ARTIFACTS)
    # Cardinality is a G2 fact, not a G1 condition.
    corpus_path = root / G2_REQUIRED_ARTIFACTS["full_reviews"]
    g2_checks["canonical_full_review_count_625"] = (
        corpus_path.is_file()
        and sum(1 for line in corpus_path.read_bytes().splitlines() if line.strip()) == 625
    )
    g3_checks = _artifact_checks(root, G3_REQUIRED_ARTIFACTS)
    report = {
        "schema_version": "core-v1-three-gate-closure-audit-v1",
        "G1_PRODUCTION_WIRING": {
            "status": "PASS" if all(g1_checks.values()) else "FAIL",
            "checks": g1_checks,
        },
        "G2_PREFORMAL_ARTIFACTS": {
            "status": "PASS" if all(g2_checks.values()) else "PENDING",
            "checks": g2_checks,
            "missing": sorted(key for key, value in g2_checks.items() if not value),
        },
        "G3_FORMAL_CLOSURE": {
            "status": "PASS" if all(g3_checks.values()) else "PENDING",
            "checks": g3_checks,
            "missing": sorted(key for key, value in g3_checks.items() if not value),
        },
        "smoke_report_path": str(smoke_path),
        "formal_artifact_root": str(root),
    }
    if output_path is not None:
        _atomic_write(Path(output_path).expanduser().resolve(), report)
    return report

