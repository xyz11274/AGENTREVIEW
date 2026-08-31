"""Complete offline freeze gate for an authoritative no-memory Corpus build."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .corpus_semantic_audit import (
    SEMANTIC_AUDIT_VERSION,
    audit_semantic_boundaries,
)
from .review_corpus import CanonicalFullReviewRecord, canonical_json_bytes
from .review_corpus_builder import BUILD_SCHEMA_VERSION, ROLE_ORDER, RawCorpusAttempt


FREEZE_AUDIT_SCHEMA_VERSION = "core-v1-corpus-freeze-audit-v1"
SUCCESS_MANIFEST_SCHEMA_VERSION = "frozen-full-review-corpus-success-v1"


class CorpusFreezeAuditFailure(RuntimeError):
    pass


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    output = []
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8", errors="strict"))
            except Exception as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"JSONL item is not an object: {path}:{line_number}")
            output.append(value)
    return tuple(output)


def _write_immutable_or_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"Refusing to replace frozen Corpus artifact: {path}")
        return
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def audit_and_freeze_corpus(
    *,
    corpus_build_directory: str | Path,
    bootstrap_directory: str | Path,
    expected_paper_count: int = 125,
) -> Mapping[str, Any]:
    """Audit the complete set and write success only after every gate passes."""

    build = Path(corpus_build_directory).expanduser().resolve()
    bootstrap = Path(bootstrap_directory).expanduser().resolve()
    expected_records = expected_paper_count * len(ROLE_ORDER)
    errors = []

    build_manifest_path = build / "build_manifest.json"
    records_path = build / "canonical_full_reviews.jsonl"
    attempts_path = build / "raw_attempts.jsonl"
    structural_path = bootstrap / "structural_contexts.jsonl"
    request_path = bootstrap / "corpus_request_materialization.jsonl"
    a_manifest_path = bootstrap / "a_manifest.json"
    runtime_profile_path = bootstrap / "corpus_build_runtime_profile.json"
    required_paths = (
        build_manifest_path,
        records_path,
        attempts_path,
        structural_path,
        request_path,
        a_manifest_path,
        runtime_profile_path,
    )
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise CorpusFreezeAuditFailure(f"Corpus freeze inputs are missing: {missing}")

    build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    a_manifest = json.loads(a_manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(runtime_profile_path.read_text(encoding="utf-8"))
    record_values = _read_jsonl(records_path)
    attempt_values = _read_jsonl(attempts_path)
    structural_values = _read_jsonl(structural_path)
    request_values = _read_jsonl(request_path)

    if build_manifest.get("schema_version") != BUILD_SCHEMA_VERSION:
        errors.append("BUILD_SCHEMA_IDENTITY_MISMATCH")
    if build_manifest.get("canonical_record_count") != expected_records:
        errors.append("INCOMPLETE_BUILD_MANIFEST")
    if a_manifest.get("paper_count") != expected_paper_count:
        errors.append("A_MANIFEST_PAPER_COUNT_MISMATCH")
    if len(record_values) != expected_records:
        errors.append("CANONICAL_RECORD_COUNT_MISMATCH")
    if len(structural_values) != expected_paper_count:
        errors.append("STRUCTURAL_CONTEXT_COUNT_MISMATCH")
    if len(request_values) != expected_records:
        errors.append("REQUEST_MATERIALIZATION_COUNT_MISMATCH")
    if (
        profile.get("profile_name") != "CORPUS_BUILD_RUNTIME_PROFILE"
        or profile.get("profile_version") != "v4"
        or profile.get("no_memory") is not True
        or profile.get("external_evidence_enabled") is not False
        or profile.get("candidate_enabled") is not False
        or profile.get("protected_reference_access") is not False
    ):
        errors.append("CORPUS_RUNTIME_ISOLATION_MISMATCH")

    structural_by_paper = {}
    for item in structural_values:
        paper_id = item.get("paper_id")
        payload = item.get("structural_context")
        if (
            not isinstance(paper_id, str)
            or not isinstance(payload, dict)
            or item.get("structural_context_sha256")
            != _sha(canonical_json_bytes(payload))
        ):
            errors.append("STRUCTURAL_CONTEXT_IDENTITY_MISMATCH")
            continue
        if paper_id in structural_by_paper:
            errors.append("DUPLICATE_STRUCTURAL_CONTEXT_PAPER")
        structural_by_paper[paper_id] = item

    requests_by_key = {}
    for item in request_values:
        key = (item.get("paper_id"), item.get("role"))
        if key in requests_by_key:
            errors.append("DUPLICATE_REQUEST_MATERIALIZATION")
        requests_by_key[key] = item
        response_format = item.get("response_format")
        if (
            not isinstance(response_format, dict)
            or response_format.get("type") != "json_schema"
            or response_format.get("json_schema", {}).get("strict") is not True
            or item.get("response_format_sha256")
            != _sha(canonical_json_bytes(response_format))
        ):
            errors.append("STRICT_RESPONSE_FORMAT_IDENTITY_MISMATCH")

    records = []
    for value in record_values:
        try:
            record = CanonicalFullReviewRecord.from_value(value)
        except Exception:
            errors.append("CANONICAL_RECORD_SCHEMA_OR_IDENTITY_FAILURE")
            continue
        records.append(record)
        structural = structural_by_paper.get(record.paper_id)
        if structural is None:
            errors.append("MISSING_PAPER_STRUCTURAL_CONTEXT")
            continue
        if record.structural_context_sha256 != structural["structural_context_sha256"]:
            errors.append("RECORD_STRUCTURAL_CONTEXT_MISMATCH")
        allowed = {
            item["anchor_id"]
            for item in structural["structural_context"].get("source_anchors", [])
        }
        used = {
            anchor
            for section in record.subdimension_sections
            for anchor in section.paper_anchor_ids
        }
        if not used <= allowed:
            errors.append("RECORD_FOREIGN_SOURCE_ANCHOR")
        request = requests_by_key.get((record.paper_id, record.role))
        if request is None or request.get("request_identity_sha256") != record.request_identity_sha256:
            errors.append("RECORD_REQUEST_IDENTITY_MISMATCH")

    expected_order = [
        (paper["paper_id"], role)
        for paper in a_manifest.get("papers", [])
        for role in ROLE_ORDER
    ]
    actual_order = [(record.paper_id, record.role) for record in records]
    if actual_order != expected_order:
        errors.append("CANONICAL_RECORD_ORDER_MISMATCH")
    if len(set(actual_order)) != len(actual_order):
        errors.append("DUPLICATE_CANONICAL_PAPER_ROLE")

    attempts = []
    for value in attempt_values:
        try:
            attempts.append(RawCorpusAttempt.from_dict(value))
        except Exception:
            errors.append("RAW_ATTEMPT_SCHEMA_OR_IDENTITY_FAILURE")
    attempts_by_key = defaultdict(list)
    for attempt in attempts:
        attempts_by_key[(attempt.paper_id, attempt.role)].append(attempt)
    record_by_key = {(record.paper_id, record.role): record for record in records}
    for key in expected_order:
        group = attempts_by_key.get(key, [])
        record = record_by_key.get(key)
        if (
            record is None
            or len(group) not in (1, 2)
            or group[-1].valid is not True
            or any(item.valid for item in group[:-1])
            or [item.attempt_index for item in group] != list(range(len(group)))
            or group[-1].request_identity_sha256 != record.request_identity_sha256
            or _sha(group[-1].assistant_content) != record.raw_response_sha256
        ):
            errors.append("FIRST_LEGAL_RAW_ATTEMPT_RULE_FAILURE")

    semantic_violations = audit_semantic_boundaries(records)
    errors.extend(item.code for item in semantic_violations)
    role_counts = Counter(record.role for record in records)
    if role_counts != Counter({role: expected_paper_count for role in ROLE_ORDER}):
        errors.append("ROLE_CARDINALITY_MISMATCH")

    error_counts = Counter(errors)
    report_payload = {
        "schema_version": FREEZE_AUDIT_SCHEMA_VERSION,
        "status": "PASS" if not errors else "FAIL",
        "expected_paper_count": expected_paper_count,
        "expected_record_count": expected_records,
        "canonical_record_count": len(records),
        "raw_attempt_count": len(attempts),
        "structural_context_count": len(structural_by_paper),
        "request_materialization_count": len(requests_by_key),
        "role_counts": {role: role_counts[role] for role in ROLE_ORDER},
        "semantic_audit_version": SEMANTIC_AUDIT_VERSION,
        "semantic_violation_count": len(semantic_violations),
        "error_counts": dict(sorted(error_counts.items())),
        "input_artifact_sha256": {
            path.name: _file_sha(path) for path in required_paths
        },
    }
    report = {
        **report_payload,
        "corpus_freeze_audit_sha256": _sha(canonical_json_bytes(report_payload)),
    }
    _write_immutable_or_identical(
        build / "corpus_freeze_audit.json", canonical_json_bytes(report)
    )
    if errors:
        raise CorpusFreezeAuditFailure(
            "Corpus freeze audit failed: "
            + ", ".join(f"{key}={value}" for key, value in sorted(error_counts.items()))
        )

    success_payload = {
        "schema_version": SUCCESS_MANIFEST_SCHEMA_VERSION,
        "status": "FROZEN_CORPUS_SUCCESS",
        "canonical_record_count": len(records),
        "paper_count": expected_paper_count,
        "role_order": list(ROLE_ORDER),
        "build_manifest_sha256": build_manifest["build_manifest_sha256"],
        "a_manifest_sha256": a_manifest["a_manifest_sha256"],
        "corpus_build_runtime_profile_sha256": profile[
            "corpus_build_runtime_profile_sha256"
        ],
        "corpus_freeze_audit_sha256": report["corpus_freeze_audit_sha256"],
        "canonical_full_reviews_sha256": _file_sha(records_path),
        "raw_attempts_sha256": _file_sha(attempts_path),
        "no_memory": True,
        "external_evidence_enabled": False,
        "protected_reference_access": False,
    }
    success = {
        **success_payload,
        "frozen_corpus_success_manifest_sha256": _sha(
            canonical_json_bytes(success_payload)
        ),
    }
    _write_immutable_or_identical(
        build / "frozen_corpus_success_manifest.json", canonical_json_bytes(success)
    )
    return success
