"""Small durable checkpoint runner for the authoritative Corpus bootstrap.

This is intentionally not a general journal.  It makes each paper×role result
durable before the next model request so a long 625-item bootstrap never has to
infer completion from stdout or process memory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .review_corpus import CanonicalFullReviewRecord, canonical_json_bytes
from .review_corpus_builder import (
    BUILD_SCHEMA_VERSION,
    ROLE_ORDER,
    CorpusBuildFailure,
    CorpusBuildResult,
    CorpusPaper,
    NoMemoryCorpusBuilder,
    RawCorpusAttempt,
    build_corpus_reviewer_request,
)


CHECKPOINT_SCHEMA_VERSION = "corpus-build-bootstrap-checkpoint-v1"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Corpus item: {path}")
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


def _item_path(root: Path, paper_ordinal: int, role: str) -> Path:
    return root / "items" / f"{paper_ordinal:04d}_{role}.json"


def _success_payload(
    *,
    paper_ordinal: int,
    role_ordinal: int,
    record: CanonicalFullReviewRecord,
    attempts: Sequence[RawCorpusAttempt],
) -> dict:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "disposition": "CANONICAL_FULL_REVIEW_COMMITTED",
        "paper_ordinal": paper_ordinal,
        "role_ordinal": role_ordinal,
        "paper_id": record.paper_id,
        "role": record.role,
        "request_identity_sha256": record.request_identity_sha256,
        "canonical_record": record.to_dict(),
        "raw_attempts": [attempt.to_dict() for attempt in attempts],
    }
    return {**payload, "item_commit_sha256": _sha(canonical_json_bytes(payload))}


def _load_success(
    path: Path,
    *,
    paper: CorpusPaper,
    paper_ordinal: int,
    role: str,
    role_ordinal: int,
    builder: NoMemoryCorpusBuilder,
):
    value = json.loads(path.read_text(encoding="utf-8"))
    item_sha = value.pop("item_commit_sha256", None)
    if item_sha != _sha(canonical_json_bytes(value)):
        raise ValueError(f"Corpus checkpoint item SHA mismatch: {path}")
    if (
        value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or value.get("disposition") != "CANONICAL_FULL_REVIEW_COMMITTED"
        or value.get("paper_ordinal") != paper_ordinal
        or value.get("role_ordinal") != role_ordinal
        or value.get("paper_id") != paper.paper_id
        or value.get("role") != role
    ):
        raise ValueError(f"Corpus checkpoint identity mismatch: {path}")
    request = build_corpus_reviewer_request(
        paper,
        role,
        generation_identity_sha256=builder.generation_identity_sha256,
        role_system_prompts=builder.role_system_prompts,
    )
    if value.get("request_identity_sha256") != request.request_identity_sha256:
        raise ValueError(f"Corpus checkpoint request identity mismatch: {path}")
    record = CanonicalFullReviewRecord.from_value(value["canonical_record"])
    attempts = tuple(RawCorpusAttempt.from_dict(item) for item in value["raw_attempts"])
    if not attempts or attempts[-1].valid is not True:
        raise ValueError(f"Corpus checkpoint lacks a final legal attempt: {path}")
    if any(attempt.valid for attempt in attempts[:-1]):
        raise ValueError(f"Corpus checkpoint violates first-legal canonical rule: {path}")
    if any(
        attempt.paper_id != paper.paper_id
        or attempt.role != role
        or attempt.request_identity_sha256 != request.request_identity_sha256
        for attempt in attempts
    ):
        raise ValueError(f"Corpus checkpoint raw-attempt identity mismatch: {path}")
    if record.raw_response_sha256 != _sha(attempts[-1].assistant_content):
        raise ValueError(f"Corpus checkpoint canonical/raw response mismatch: {path}")
    return record, attempts


def run_checkpointed_corpus_build(
    *,
    builder: NoMemoryCorpusBuilder,
    papers: Iterable[CorpusPaper],
    checkpoint_directory: str | Path,
    expected_paper_count: int = 125,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> CorpusBuildResult:
    """Build/resume in fixed paper-major, role-minor order."""

    ordered_papers = tuple(papers)
    if len(ordered_papers) != expected_paper_count:
        raise ValueError(
            f"Expected {expected_paper_count} A papers, got {len(ordered_papers)}"
        )
    if len({paper.paper_id for paper in ordered_papers}) != len(ordered_papers):
        raise ValueError("Duplicate A paper id in Corpus input")
    root = Path(checkpoint_directory).expanduser().resolve()
    failure_path = root / "CORPUS_BUILD_FAILED.json"
    if failure_path.exists():
        raise RuntimeError(
            "Corpus bootstrap is sealed fail-fast by an earlier two-output failure: "
            f"{failure_path}"
        )
    total = expected_paper_count * len(ROLE_ORDER)
    records = []
    all_attempts = []
    completed = 0
    for paper_ordinal, paper in enumerate(ordered_papers):
        for role_ordinal, role in enumerate(ROLE_ORDER):
            path = _item_path(root, paper_ordinal, role)
            if path.exists():
                record, attempts = _load_success(
                    path,
                    paper=paper,
                    paper_ordinal=paper_ordinal,
                    role=role,
                    role_ordinal=role_ordinal,
                    builder=builder,
                )
                disposition = "reused"
            else:
                try:
                    record, attempts = builder.build_one(paper, role)
                except CorpusBuildFailure as exc:
                    failure_payload = {
                        "schema_version": CHECKPOINT_SCHEMA_VERSION,
                        "disposition": "CORPUS_BUILD_FAIL_FAST",
                        "paper_ordinal": paper_ordinal,
                        "role_ordinal": role_ordinal,
                        "paper_id": paper.paper_id,
                        "role": role,
                        "raw_attempts": [item.to_dict() for item in exc.attempts],
                    }
                    failure_payload["failure_sha256"] = _sha(
                        canonical_json_bytes(failure_payload)
                    )
                    _write_new(failure_path, canonical_json_bytes(failure_payload))
                    raise
                payload = _success_payload(
                    paper_ordinal=paper_ordinal,
                    role_ordinal=role_ordinal,
                    record=record,
                    attempts=attempts,
                )
                _write_new(path, canonical_json_bytes(payload))
                disposition = "generated"
            records.append(record)
            all_attempts.extend(attempts)
            completed += 1
            if progress is not None:
                progress(completed, total, paper.paper_id, role + ":" + disposition)
    result = CorpusBuildResult(tuple(records), tuple(all_attempts), expected_paper_count)
    if result.manifest["canonical_record_count"] != total:
        raise RuntimeError("Checkpointed Corpus did not reach complete cardinality")
    return result

