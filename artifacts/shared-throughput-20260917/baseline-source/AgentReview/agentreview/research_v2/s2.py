"""Frozen Semantic Scholar assets and exact paper-id runtime lookup."""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol

from .data import canonical_title
from .io import (
    atomic_write_json,
    file_sha256,
    read_json,
    read_jsonl,
    value_sha256,
    write_derived_jsonl,
)

S2_BUILDER_PROTOCOL = "frozen-s2-resumable-v2"
S2_QUERY_METHOD = "semantic_scholar_exact_title_v1"
# Frozen identity of the v2 record construction/filtering protocol.  Transport-only
# changes (for example, handling an empty API result) preserve resumable checkpoints.
S2_RECORD_BUILDER_SHA256 = (
    "b76ac49d0a82469aa452deadfa4a710fcf859da19cf8c6d5ecfe1e89138f8e6e"
)


class S2SearchClient(Protocol):
    def search_title(self, title: str, *, limit: int) -> list[dict[str, Any]]: ...


class SemanticScholarSearchClient:
    """Data-preparation-only Semantic Scholar Graph API client."""

    def __init__(
        self,
        api_key_env: str = "S2_API_KEY",
        *,
        max_attempts: int | None = 6,
        retry_base_seconds: float = 2.0,
        min_request_interval_seconds: float = 1.2,
    ):
        import requests

        self.requests = requests
        self.api_key = os.environ.get(api_key_env, "")
        self.max_attempts = (
            None if max_attempts is None else max(1, int(max_attempts))
        )
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.min_request_interval_seconds = max(
            0.0,
            float(min_request_interval_seconds),
        )
        self._last_request_started = 0.0

    def search_title(self, title: str, *, limit: int) -> list[dict[str, Any]]:
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        retry_statuses = {408, 425, 429, 500, 502, 503, 504}
        attempt = 0
        while True:
            attempt += 1
            remaining = self.min_request_interval_seconds - (
                time.monotonic() - self._last_request_started
            )
            if remaining > 0.0:
                time.sleep(remaining)
            self._last_request_started = time.monotonic()
            response = None
            try:
                response = self.requests.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={
                        "query": title,
                        "limit": int(limit),
                        "fields": "paperId,externalIds,title,abstract,year,publicationDate,venue,authors,url",
                    },
                    headers=headers,
                    timeout=60,
                )
            except self.requests.RequestException:
                if self.max_attempts is not None and attempt >= self.max_attempts:
                    raise
            else:
                if response.ok:
                    break
                if response.status_code not in retry_statuses:
                    response.raise_for_status()
            if self.max_attempts is not None and attempt >= self.max_attempts:
                assert response is not None
                response.raise_for_status()
            retry_after = "" if response is None else response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = self.retry_base_seconds * (2 ** min(attempt - 1, 5))
            delay = min(max(0.0, delay), 60.0)
            print(
                json.dumps(
                    {
                        "event": "s2_transport_retry",
                        "attempt": attempt,
                        "status_code": (
                            None if response is None else response.status_code
                        ),
                        "delay_seconds": delay,
                        "query": title,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(delay)
        assert response is not None
        payload = response.json()
        values = payload.get("data") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and values is None
            and payload.get("total") == 0
        ):
            return []
        if not isinstance(values, list):
            raise ValueError("Semantic Scholar search response lacks data list")
        return [dict(value) for value in values if isinstance(value, dict)]


def _family_title(value: str) -> str:
    text = canonical_title(value)
    text = re.sub(r"\bv\d+\b$", "", text)
    return " ".join(text.split())


def _external_ids(value: dict[str, Any]) -> set[str]:
    external = value.get("externalIds") or value.get("external_ids") or {}
    if not isinstance(external, dict):
        return set()
    return {
        f"{str(key).casefold()}:{str(item).casefold()}"
        for key, item in external.items()
        if item not in (None, "")
    }


def _expected_record_identity(
    *,
    target: dict[str, Any],
    paper_id: str,
    title: str,
    pool_version: str,
    raw_limit: int,
    top_k: int,
    builder_sha256: str,
) -> dict[str, Any]:
    return {
        "builder_protocol": S2_BUILDER_PROTOCOL,
        "builder_sha256": builder_sha256,
        "target_sha256": value_sha256(target),
        "pool_version": pool_version,
        "paper_id": paper_id,
        "canonical_query_title": title,
        "query_method": S2_QUERY_METHOD,
        "raw_limit": raw_limit,
        "top_k": top_k,
    }


def _validate_record_identity(
    record: dict[str, Any],
    expected: dict[str, Any],
    *,
    source: Path,
) -> None:
    observed = {key: record.get(key) for key in expected}
    if observed != expected:
        raise ValueError(
            f"Frozen S2 record identity mismatch at {source}: "
            f"observed={observed!r}, expected={expected!r}"
        )
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    if record.get("record_sha256") != value_sha256(body):
        raise ValueError(f"Frozen S2 record hash mismatch at {source}")


def _pool_manifest(
    *,
    destination: Path,
    pool_version: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "pool_version": pool_version,
        "record_count": len(records),
        "output_path": str(destination),
        "sha256": file_sha256(destination),
        "rejection_counts": {
            reason: sum(
                1
                for record in records
                for value in record["rejected"]
                if value["reason"] == reason
            )
            for reason in sorted(
                {
                    value["reason"]
                    for record in records
                    for value in record["rejected"]
                }
            )
        },
    }


def _build_frozen_s2_pool_unlocked(
    *,
    targets: list[dict[str, Any]],
    output_path: str | Path,
    client: S2SearchClient,
    pool_version: str,
    raw_limit: int = 50,
    top_k: int = 5,
) -> dict[str, Any]:
    """Build one pool while the caller holds the artifact-root S2 lock."""
    destination = Path(output_path).expanduser().resolve()
    checkpoint_root = destination.with_name(f"{destination.name}.parts")
    builder_sha256 = S2_RECORD_BUILDER_SHA256
    target_specs = []
    for target in targets:
        paper_id = str(target.get("paper_id") or "")
        title = str(
            target.get("canonical_query_title") or target.get("title") or ""
        ).strip()
        if not paper_id or not title:
            raise ValueError("Frozen S2 target requires paper_id and title")
        target_specs.append(
            (
                target,
                paper_id,
                title,
                _expected_record_identity(
                    target=target,
                    paper_id=paper_id,
                    title=title,
                    pool_version=pool_version,
                    raw_limit=raw_limit,
                    top_k=top_k,
                    builder_sha256=builder_sha256,
                ),
            )
        )
    target_ids = [paper_id for _, paper_id, _, _ in target_specs]
    if "" in target_ids or len(set(target_ids)) != len(target_ids):
        raise ValueError("Frozen S2 targets require unique non-empty paper IDs")
    if destination.is_file():
        frozen_records = read_jsonl(destination)
        if len(frozen_records) != len(target_specs):
            raise ValueError(
                f"Frozen S2 output count differs from requested targets: {destination}"
            )
        for record, (_, _, _, expected_identity) in zip(
            frozen_records, target_specs, strict=True
        ):
            _validate_record_identity(
                record,
                expected_identity,
                source=destination,
            )
        return _pool_manifest(
            destination=destination,
            pool_version=pool_version,
            records=frozen_records,
        )
    records = []
    for target_index, (target, paper_id, title, expected_identity) in enumerate(
        target_specs
    ):
        checkpoint_id = value_sha256(expected_identity)
        checkpoint_path = checkpoint_root / f"{target_index:06d}-{checkpoint_id}.json"
        record = read_json(checkpoint_path) if checkpoint_path.is_file() else None
        if record is not None:
            _validate_record_identity(
                record,
                expected_identity,
                source=checkpoint_path,
            )
            records.append(record)
            print(
                json.dumps(
                    {
                        "completed": target_index + 1,
                        "total": len(targets),
                        "paper_id": paper_id,
                        "disposition": "resumed",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue

        raw = client.search_title(title, limit=raw_limit)
        target_title = canonical_title(title)
        target_family = _family_title(title)
        target_external = _external_ids(target)
        target_s2_id = str(target.get("s2_paper_id") or "")
        rejected = []
        selected = []
        seen_ids: set[str] = set()
        seen_titles: set[str] = set()
        for index, value in enumerate(raw):
            result_id = str(value.get("paperId") or value.get("paper_id") or "")
            result_title = str(value.get("title") or "")
            result_title_key = canonical_title(result_title)
            reason = ""
            if target_s2_id and result_id == target_s2_id:
                reason = "target_s2_id"
            elif result_title_key and result_title_key == target_title:
                reason = "exact_canonical_title"
            elif result_title_key and _family_title(result_title) == target_family:
                reason = "same_family_title"
            elif target_external and target_external & _external_ids(value):
                reason = "shared_external_id"
            elif result_id and result_id in seen_ids:
                reason = "duplicate_s2_id"
            elif result_title_key and result_title_key in seen_titles:
                reason = "duplicate_canonical_title"
            if reason:
                rejected.append(
                    {"raw_rank": index, "paper_id": result_id, "reason": reason}
                )
                continue
            if result_id:
                seen_ids.add(result_id)
            if result_title_key:
                seen_titles.add(result_title_key)
            selected.append(value)
            if len(selected) >= top_k:
                break
        record = {
            **expected_identity,
            "raw_result_count": len(raw),
            "raw_response_sha256": value_sha256(raw),
            "rejected": rejected,
            "retrieved_papers": selected,
            "result_sha256": value_sha256(selected),
        }
        record["record_sha256"] = value_sha256(record)
        atomic_write_json(checkpoint_path, record)
        records.append(record)
        print(
            json.dumps(
                {
                    "completed": target_index + 1,
                    "total": len(targets),
                    "paper_id": paper_id,
                    "disposition": "queried",
                },
                sort_keys=True,
            ),
            flush=True,
        )
    write_derived_jsonl(destination, records)
    return _pool_manifest(
        destination=destination,
        pool_version=pool_version,
        records=records,
    )


def build_frozen_s2_pool(
    *,
    targets: list[dict[str, Any]],
    output_path: str | Path,
    client: S2SearchClient,
    pool_version: str,
    raw_limit: int = 50,
    top_k: int = 5,
) -> dict[str, Any]:
    """Freeze one serial S2 pool under a non-blocking cross-process lock."""
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / ".s2-build.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another S2 build is already running for this artifact root: "
                f"{lock_path}"
            ) from exc
        return _build_frozen_s2_pool_unlocked(
            targets=targets,
            output_path=destination,
            client=client,
            pool_version=pool_version,
            raw_limit=raw_limit,
            top_k=top_k,
        )


class FrozenS2Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Frozen S2 Pool not found: {self.path}")
        self.sha256 = file_sha256(self.path)
        self.pool: dict[str, dict[str, Any]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                paper_id = str(
                    value.get("paper_id") or value.get("id") or value.get("query_id") or ""
                )
                if not paper_id or paper_id in self.pool:
                    raise ValueError(
                        f"Frozen S2 Pool missing/duplicate paper_id at line {line_number}"
                    )
                self.pool[paper_id] = value

    def retrieve(
        self,
        *,
        paper_id: str,
        target_title: str,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if paper_id not in self.pool:
            raise KeyError(f"Frozen S2 Pool has no exact paper_id entry: {paper_id}")
        record = self.pool[paper_id]
        values = (
            record.get("retrieved_papers")
            or record.get("papers")
            or record.get("results")
            or []
        )
        if not isinstance(values, list):
            raise ValueError(f"Frozen S2 results are not a list for {paper_id}")
        target_key = canonical_title(target_title)
        selected = []
        seen_ids: set[str] = set()
        seen_titles: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            result_id = str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
            result_title = str(value.get("title") or "")
            title_key = canonical_title(result_title)
            if target_key and title_key == target_key:
                raise ValueError(
                    f"Frozen S2 Pool contains an exact-title self match for {paper_id}: {result_title}"
                )
            if (result_id and result_id in seen_ids) or (title_key and title_key in seen_titles):
                raise ValueError(f"Frozen S2 Pool contains duplicate evidence for {paper_id}")
            if result_id:
                seen_ids.add(result_id)
            if title_key:
                seen_titles.add(title_key)
            selected.append(dict(value))
            if len(selected) >= max(0, int(limit)):
                break
        provenance = {
            "pool_path": str(self.path),
            "pool_sha256": self.sha256,
            "pool_version": record.get("pool_version"),
            "paper_id": paper_id,
            "result_ids": [
                str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
                for value in selected
            ],
        }
        return selected, provenance


def validate_frozen_s2_pool(
    *,
    pool_path: str | Path,
    expected_count: int,
    pool_version: str,
    raw_limit: int,
    top_k: int,
    builder_sha256: str,
    targets: list[dict[str, Any]] | None = None,
    query_path: str | Path | None = None,
    label: str = "Frozen S2",
) -> dict[str, Any]:
    """Validate one completed Frozen S2 pool against its exact build inputs.

    Exactly one of ``targets`` and ``query_path`` must be supplied.  Validation
    is deliberately stricter than runtime retrieval: it verifies build and
    content identities, target/record ordering, self-match exclusion, and the
    complete evidence list rather than only the prefix requested at runtime.
    """
    if (targets is None) == (query_path is None):
        raise ValueError("Exactly one of targets and query_path is required")
    target_values = (
        read_jsonl(query_path) if query_path is not None else list(targets or [])
    )
    records = read_jsonl(pool_path)
    if len(target_values) != expected_count:
        raise ValueError(
            f"{label} target count is {len(target_values)}, expected {expected_count}"
        )
    if len(records) != expected_count:
        raise ValueError(
            f"{label} record count is {len(records)}, expected {expected_count}"
        )
    if not all(isinstance(target, dict) for target in target_values):
        raise ValueError(f"{label} targets must be objects")
    target_ids = [str(target.get("paper_id") or "") for target in target_values]
    if "" in target_ids or len(set(target_ids)) != len(target_ids):
        raise ValueError(
            f"{label} targets require {expected_count} unique non-empty paper IDs"
        )

    store = FrozenS2Store(pool_path)
    if len(store.pool) != expected_count:
        raise ValueError(
            f"{label} indexed record count is {len(store.pool)}, "
            f"expected {expected_count}"
        )
    for target, record in zip(target_values, records, strict=True):
        paper_id = str(target.get("paper_id") or "")
        title = str(
            target.get("canonical_query_title") or target.get("title") or ""
        ).strip()
        if not title:
            raise ValueError(f"{label} target {paper_id} has no query title")
        expected = _expected_record_identity(
            target=target,
            paper_id=paper_id,
            title=title,
            pool_version=pool_version,
            raw_limit=raw_limit,
            top_k=top_k,
            builder_sha256=builder_sha256,
        )
        _validate_record_identity(record, expected, source=store.path)
        raw_evidence = record.get("retrieved_papers")
        if not isinstance(raw_evidence, list) or not all(
            isinstance(value, dict) for value in raw_evidence
        ):
            raise ValueError(
                f"{label} record {paper_id} retrieved_papers must be a list of objects"
            )
        if len(raw_evidence) > top_k:
            raise ValueError(
                f"{label} record {paper_id} contains more than top-{top_k} evidence"
            )
        if record.get("result_sha256") != value_sha256(raw_evidence):
            raise ValueError(f"{label} record {paper_id} result hash mismatch")
        target_title_key = canonical_title(title)
        target_family = _family_title(title)
        target_s2_id = str(target.get("s2_paper_id") or "")
        target_external_ids = _external_ids(target)
        seen_evidence_ids: set[str] = set()
        seen_evidence_titles: set[str] = set()
        for evidence_index, value in enumerate(raw_evidence):
            evidence_id = str(
                value.get("paperId")
                or value.get("paper_id")
                or value.get("id")
                or ""
            )
            evidence_title = str(value.get("title") or "").strip()
            if not evidence_id or not evidence_title:
                raise ValueError(
                    f"{label} record {paper_id} evidence {evidence_index} "
                    "requires a non-empty paper ID and title"
                )
            evidence_title_key = canonical_title(evidence_title)
            if target_s2_id and evidence_id == target_s2_id:
                raise ValueError(
                    f"{label} record {paper_id} contains target S2 ID {evidence_id}"
                )
            if evidence_title_key == target_title_key:
                raise ValueError(
                    f"{label} record {paper_id} contains an exact-title self match"
                )
            if _family_title(evidence_title) == target_family:
                raise ValueError(
                    f"{label} record {paper_id} contains a same-family self match"
                )
            if target_external_ids & _external_ids(value):
                raise ValueError(
                    f"{label} record {paper_id} contains a shared-external-ID "
                    "self match"
                )
            if evidence_id in seen_evidence_ids:
                raise ValueError(
                    f"{label} record {paper_id} contains duplicate evidence ID "
                    f"{evidence_id}"
                )
            if evidence_title_key in seen_evidence_titles:
                raise ValueError(
                    f"{label} record {paper_id} contains a duplicate evidence title"
                )
            seen_evidence_ids.add(evidence_id)
            seen_evidence_titles.add(evidence_title_key)
        evidence, _ = store.retrieve(
            paper_id=paper_id,
            target_title=title,
            limit=top_k,
        )
        if len(evidence) != len(raw_evidence):
            raise ValueError(f"{label} record {paper_id} evidence schema is incomplete")
    return {
        "validated": True,
        "target_count": len(target_values),
        "record_count": len(records),
        "pool_sha256": store.sha256,
    }


def format_s2_evidence(values: list[dict[str, Any]]) -> str:
    sections = []
    for index, value in enumerate(values, start=1):
        title = str(value.get("title") or "(untitled)")
        paper_id = str(value.get("paperId") or value.get("paper_id") or value.get("id") or "")
        year = str(value.get("year") or value.get("publication_year") or "")
        abstract = str(value.get("abstract") or "").strip()
        sections.append(
            "\n".join(
                [
                    f"[FROZEN S2 EVIDENCE {index}]",
                    f"paper_id: {paper_id}",
                    f"title: {title}",
                    f"year: {year}",
                    f"abstract: {abstract}",
                ]
            )
        )
    return "\n\n".join(sections)
