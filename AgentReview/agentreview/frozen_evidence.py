"""Minimal immutable Frozen External Evidence Pool v1.

The module owns item/set identity, deterministic text projection, cutoff
validation, strict load-time validation and the model-free read interface.  It
does not search, rerank, summarize, embed, or mutate evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from agentreview.review_corpus import canonical_json_bytes


EVIDENCE_SCHEMA_VERSION = "frozen-external-evidence-pool-v1"
PARTITIONS = ("PRIOR_WORK", "POLICY", "ARTIFACT_LICENSE")
AVAILABILITY = {"available", "unavailable", "not_archived_before_cutoff"}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(prefix: str, identity_type: str, payload: Mapping[str, Any]) -> str:
    return prefix + _sha256(
        canonical_json_bytes(
            {
                "identity_type": identity_type,
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "payload": dict(payload),
            }
        )
    )


def _project_utf8(raw: bytes) -> str:
    """Frozen v1 projection: strict UTF-8 decode and line-ending normalization."""
    return raw.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class EvidenceRecord:
    pool_version: str
    partition: str
    target_paper_id: str
    canonical_source_identity: str
    source_url: str
    source_id: str
    venue: str
    year: str
    cutoff: str
    retrieval_snapshot_time: str
    mime_type: str
    provenance: str
    publication_or_version_date: str
    raw_content: bytes
    raw_content_sha256: str
    canonical_text: str
    canonical_text_sha256: str
    projection_identity: str
    evidence_id: str

    @property
    def identity_payload(self) -> Dict[str, Any]:
        return {
            "pool_version": self.pool_version,
            "partition": self.partition,
            "target_paper_id": self.target_paper_id,
            "canonical_source_identity": self.canonical_source_identity,
            "cutoff": self.cutoff,
            "raw_content_sha256": self.raw_content_sha256,
            "canonical_text_sha256": self.canonical_text_sha256,
            "projection_identity": self.projection_identity,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pool_version": self.pool_version,
            "partition": self.partition,
            "target_paper_id": self.target_paper_id,
            "canonical_source_identity": self.canonical_source_identity,
            "source_url": self.source_url,
            "source_id": self.source_id,
            "venue": self.venue,
            "year": self.year,
            "cutoff": self.cutoff,
            "retrieval_snapshot_time": self.retrieval_snapshot_time,
            "mime_type": self.mime_type,
            "provenance": self.provenance,
            "publication_or_version_date": self.publication_or_version_date,
            "raw_content_base64": base64.b64encode(self.raw_content).decode("ascii"),
            "raw_content_byte_length": len(self.raw_content),
            "raw_content_sha256": self.raw_content_sha256,
            "canonical_text": self.canonical_text,
            "canonical_text_sha256": self.canonical_text_sha256,
            "projection_identity": self.projection_identity,
            "evidence_id": self.evidence_id,
        }


@dataclass(frozen=True)
class EvidenceSetManifest:
    pool_version: str
    target_paper_id: str
    target_family_id: str
    cutoff: str
    partition_availability_items: Tuple[Tuple[str, str], ...]
    partition_evidence_id_items: Tuple[Tuple[str, Tuple[str, ...]], ...]
    evidence_set_sha256: str

    @property
    def partition_availability(self) -> Dict[str, str]:
        return dict(self.partition_availability_items)

    @property
    def partition_evidence_ids(self) -> Dict[str, Tuple[str, ...]]:
        return dict(self.partition_evidence_id_items)

    @property
    def result_count(self) -> int:
        return sum(len(values) for _, values in self.partition_evidence_id_items)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pool_version": self.pool_version,
            "target_paper_id": self.target_paper_id,
            "target_family_id": self.target_family_id,
            "cutoff": self.cutoff,
            "partition_availability": self.partition_availability,
            "partition_evidence_ids": {
                key: list(values) for key, values in self.partition_evidence_id_items
            },
            "result_count": self.result_count,
            "evidence_set_sha256": self.evidence_set_sha256,
        }


@dataclass(frozen=True)
class FrozenEvidencePool:
    pool_version: str
    projection_identity: str
    records: Tuple[EvidenceRecord, ...]
    manifests: Tuple[EvidenceSetManifest, ...]
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        paper_ids = tuple(item.target_paper_id for item in self.manifests)
        evidence_ids = tuple(item.evidence_id for item in self.records)
        if not self.pool_version or not self.projection_identity or not paper_ids:
            raise ValueError("Frozen Evidence Pool identity and paper sets are required")
        if len(set(paper_ids)) != len(paper_ids) or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Frozen Evidence paper/evidence IDs must be unique")

    @property
    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pool_version": self.pool_version,
            "projection_identity": self.projection_identity,
            "records": [item.to_dict() for item in self.records],
            "manifests": [item.to_dict() for item in self.manifests],
        }

    @property
    def pool_sha256(self) -> str:
        return _identity("evidencepool_", "frozen_external_evidence_pool", self.canonical_payload)

    def manifest(self, target_paper_id: str) -> EvidenceSetManifest:
        for item in self.manifests:
            if item.target_paper_id == target_paper_id:
                return item
        raise KeyError(f"Unknown Evidence target paper: {target_paper_id}")

    def get_availability(self, target_paper_id: str) -> Dict[str, str]:
        return self.manifest(target_paper_id).partition_availability

    def list_evidence_metadata(
        self, target_paper_id: str, partition: str
    ) -> Tuple[Dict[str, Any], ...]:
        if partition not in PARTITIONS:
            raise ValueError("Unknown Frozen Evidence partition")
        ids = self.manifest(target_paper_id).partition_evidence_ids[partition]
        by_id = {item.evidence_id: item for item in self.records}
        return tuple(
            {
                "evidence_id": by_id[evidence_id].evidence_id,
                "partition": by_id[evidence_id].partition,
                "canonical_source_identity": by_id[evidence_id].canonical_source_identity,
                "source_url": by_id[evidence_id].source_url,
                "source_id": by_id[evidence_id].source_id,
                "cutoff": by_id[evidence_id].cutoff,
                "provenance": by_id[evidence_id].provenance,
                "canonical_text_sha256": by_id[evidence_id].canonical_text_sha256,
            }
            for evidence_id in ids
        )

    def validate_evidence_ids(
        self, target_paper_id: str, evidence_ids: Sequence[str]
    ) -> bool:
        allowed = {
            evidence_id
            for values in self.manifest(target_paper_id).partition_evidence_ids.values()
            for evidence_id in values
        }
        unknown = set(evidence_ids) - allowed
        if unknown:
            raise ValueError(
                "Evidence ID is not in the target paper Frozen Evidence Pool: "
                + ", ".join(sorted(unknown))
            )
        return True

    def read_evidence(
        self, target_paper_id: str, evidence_ids: Sequence[str]
    ) -> Tuple[EvidenceRecord, ...]:
        self.validate_evidence_ids(target_paper_id, evidence_ids)
        by_id = {item.evidence_id: item for item in self.records}
        return tuple(by_id[evidence_id] for evidence_id in evidence_ids)

    def read_partition(
        self,
        target_paper_id: str,
        partition: str,
        *,
        cursor: int = 0,
        limit: int | None = None,
    ) -> Tuple[EvidenceRecord, ...]:
        if partition not in PARTITIONS or type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid Frozen Evidence partition/cursor")
        ids = self.manifest(target_paper_id).partition_evidence_ids[partition]
        if limit is None:
            selected = ids[cursor:]
        elif type(limit) is not int or limit < 0:
            raise ValueError("Invalid Frozen Evidence limit")
        else:
            selected = ids[cursor : cursor + limit]
        return self.read_evidence(target_paper_id, selected)

    def render_full_context(self, target_paper_id: str) -> str:
        """Render the complete frozen delivered set in protocol partition order."""
        manifest = self.manifest(target_paper_id)
        blocks = []
        for partition in PARTITIONS:
            blocks.append(
                "FROZEN_EVIDENCE_PARTITION "
                + canonical_json_bytes(
                    {
                        "partition": partition,
                        "availability": manifest.partition_availability[partition],
                        "result_count": len(manifest.partition_evidence_ids[partition]),
                    }
                ).decode("utf-8")
            )
            for record in self.read_partition(target_paper_id, partition):
                encoded = record.canonical_text.encode("utf-8")
                header = canonical_json_bytes(
                    {
                        "evidence_id": record.evidence_id,
                        "partition": record.partition,
                        "canonical_source_identity": record.canonical_source_identity,
                        "cutoff": record.cutoff,
                        "provenance": record.provenance,
                        "utf8_byte_length": len(encoded),
                    }
                ).decode("utf-8")
                blocks.append(f"FROZEN_EVIDENCE_ITEM {header}\n{record.canonical_text}")
        return "\n\n".join(blocks)

    def write(self, path: str | Path) -> None:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "identity_type": "frozen_external_evidence_pool_envelope",
            "schema_version": self.schema_version,
            "pool_sha256": self.pool_sha256,
            "payload": self.canonical_payload,
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(canonical_json_bytes(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    @classmethod
    def load(cls, path: str | Path) -> "FrozenEvidencePool":
        value = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "identity_type", "schema_version", "pool_sha256", "payload"
        }:
            raise ValueError("Frozen Evidence envelope fields mismatch")
        if (
            value["identity_type"] != "frozen_external_evidence_pool_envelope"
            or value["schema_version"] != EVIDENCE_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported Frozen Evidence envelope")
        payload = value["payload"]
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "pool_version", "projection_identity", "records", "manifests"
        }:
            raise ValueError("Frozen Evidence payload fields mismatch")
        records = tuple(_record_from_dict(item) for item in payload["records"])
        manifests = tuple(_manifest_from_dict(item) for item in payload["manifests"])
        result = cls(
            pool_version=str(payload["pool_version"]),
            projection_identity=str(payload["projection_identity"]),
            records=records,
            manifests=manifests,
        )
        if result.canonical_payload != payload or result.pool_sha256 != value["pool_sha256"]:
            raise ValueError("Frozen Evidence canonical payload/SHA mismatch")
        # Referential integrity is checked again on every load.
        record_ids = {item.evidence_id for item in records}
        for manifest in manifests:
            referenced = {
                item for values in manifest.partition_evidence_ids.values() for item in values
            }
            if not referenced <= record_ids:
                raise ValueError("EvidenceSetManifest references an unknown evidence ID")
        return result


def _record_from_dict(value: Mapping[str, Any]) -> EvidenceRecord:
    fields = {
        "pool_version", "partition", "target_paper_id", "canonical_source_identity",
        "source_url", "source_id", "venue", "year", "cutoff",
        "retrieval_snapshot_time", "mime_type", "provenance",
        "publication_or_version_date", "raw_content_base64", "raw_content_byte_length",
        "raw_content_sha256", "canonical_text", "canonical_text_sha256",
        "projection_identity", "evidence_id",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("EvidenceRecord fields mismatch")
    try:
        raw = base64.b64decode(value["raw_content_base64"], validate=True)
    except Exception as exc:
        raise ValueError("EvidenceRecord raw Base64 is invalid") from exc
    if len(raw) != value["raw_content_byte_length"] or _sha256(raw) != value["raw_content_sha256"]:
        raise ValueError("EvidenceRecord raw length/SHA mismatch")
    record = EvidenceRecord(
        pool_version=str(value["pool_version"]),
        partition=str(value["partition"]),
        target_paper_id=str(value["target_paper_id"]),
        canonical_source_identity=str(value["canonical_source_identity"]),
        source_url=str(value["source_url"]),
        source_id=str(value["source_id"]),
        venue=str(value["venue"]),
        year=str(value["year"]),
        cutoff=str(value["cutoff"]),
        retrieval_snapshot_time=str(value["retrieval_snapshot_time"]),
        mime_type=str(value["mime_type"]),
        provenance=str(value["provenance"]),
        publication_or_version_date=str(value["publication_or_version_date"]),
        raw_content=raw,
        raw_content_sha256=str(value["raw_content_sha256"]),
        canonical_text=str(value["canonical_text"]),
        canonical_text_sha256=str(value["canonical_text_sha256"]),
        projection_identity=str(value["projection_identity"]),
        evidence_id=str(value["evidence_id"]),
    )
    if _sha256(record.canonical_text.encode("utf-8")) != record.canonical_text_sha256:
        raise ValueError("EvidenceRecord canonical text SHA mismatch")
    if _identity("evidence_", "frozen_external_evidence_item", record.identity_payload) != record.evidence_id:
        raise ValueError("EvidenceRecord identity mismatch")
    return record


def _manifest_from_dict(value: Mapping[str, Any]) -> EvidenceSetManifest:
    fields = {
        "pool_version", "target_paper_id", "target_family_id", "cutoff",
        "partition_availability", "partition_evidence_ids", "result_count",
        "evidence_set_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("EvidenceSetManifest fields mismatch")
    availability = value["partition_availability"]
    evidence_ids = value["partition_evidence_ids"]
    if set(availability) != set(PARTITIONS) or set(evidence_ids) != set(PARTITIONS):
        raise ValueError("EvidenceSetManifest partition coverage mismatch")
    payload = {
        "pool_version": str(value["pool_version"]),
        "target_paper_id": str(value["target_paper_id"]),
        "target_family_id": str(value["target_family_id"]),
        "cutoff": str(value["cutoff"]),
        "partition_availability": {key: str(availability[key]) for key in PARTITIONS},
        "partition_evidence_ids": {key: list(map(str, evidence_ids[key])) for key in PARTITIONS},
    }
    expected_id = _identity("evidenceset_", "frozen_external_evidence_set", payload)
    result = EvidenceSetManifest(
        pool_version=payload["pool_version"],
        target_paper_id=payload["target_paper_id"],
        target_family_id=payload["target_family_id"],
        cutoff=payload["cutoff"],
        partition_availability_items=tuple(
            (key, payload["partition_availability"][key]) for key in PARTITIONS
        ),
        partition_evidence_id_items=tuple(
            (key, tuple(payload["partition_evidence_ids"][key])) for key in PARTITIONS
        ),
        evidence_set_sha256=expected_id,
    )
    if result.result_count != value["result_count"] or expected_id != value["evidence_set_sha256"]:
        raise ValueError("EvidenceSetManifest count/identity mismatch")
    return result


def materialize_frozen_evidence_pool(
    *,
    pool_version: str,
    paper_sets: Iterable[Mapping[str, Any]],
    items: Iterable[Mapping[str, Any]],
    projection_identity: str,
) -> FrozenEvidencePool:
    if not pool_version or not projection_identity:
        raise ValueError("Frozen Evidence pool/projection identity are required")
    set_specs = {}
    for value in paper_sets:
        expected = {
            "target_paper_id", "target_family_id", "cutoff", "partition_availability"
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("Frozen Evidence paper-set fields mismatch")
        paper_id = str(value["target_paper_id"])
        availability = dict(value["partition_availability"])
        if set(availability) != set(PARTITIONS) or any(
            status not in AVAILABILITY for status in availability.values()
        ):
            raise ValueError("Frozen Evidence partition availability is invalid")
        if paper_id in set_specs:
            raise ValueError("Duplicate Frozen Evidence target paper")
        set_specs[paper_id] = {
            "target_family_id": str(value["target_family_id"]),
            "cutoff": str(value["cutoff"]),
            "partition_availability": {key: availability[key] for key in PARTITIONS},
        }
    if not set_specs:
        raise ValueError("Frozen Evidence Pool requires target paper manifests")

    records = []
    for value in items:
        expected = {
            "partition", "target_paper_id", "canonical_source_identity", "source_url",
            "source_id", "venue", "year", "cutoff", "retrieval_snapshot_time",
            "mime_type", "provenance", "raw_content", "publication_or_version_date",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("Frozen Evidence source item fields mismatch")
        partition = str(value["partition"])
        paper_id = str(value["target_paper_id"])
        if partition not in PARTITIONS or paper_id not in set_specs:
            raise ValueError("Frozen Evidence item partition/paper is out of scope")
        cutoff = str(value["cutoff"])
        if cutoff != set_specs[paper_id]["cutoff"]:
            raise ValueError("Frozen Evidence item cutoff differs from its set")
        version_date = str(value["publication_or_version_date"])
        if not version_date or version_date > cutoff:
            raise ValueError("Frozen Evidence item is not proven available before cutoff")
        if set_specs[paper_id]["partition_availability"][partition] != "available":
            raise ValueError("Unavailable Frozen Evidence partition cannot contain records")
        raw = value["raw_content"]
        if not isinstance(raw, bytes):
            raise TypeError("Frozen Evidence raw_content must be immutable bytes")
        canonical_text = _project_utf8(raw)
        raw_sha = _sha256(raw)
        canonical_sha = _sha256(canonical_text.encode("utf-8"))
        identity_payload = {
            "pool_version": pool_version,
            "partition": partition,
            "target_paper_id": paper_id,
            "canonical_source_identity": str(value["canonical_source_identity"]),
            "cutoff": cutoff,
            "raw_content_sha256": raw_sha,
            "canonical_text_sha256": canonical_sha,
            "projection_identity": projection_identity,
        }
        records.append(
            EvidenceRecord(
                pool_version=pool_version,
                partition=partition,
                target_paper_id=paper_id,
                canonical_source_identity=identity_payload["canonical_source_identity"],
                source_url=str(value["source_url"]),
                source_id=str(value["source_id"]),
                venue=str(value["venue"]),
                year=str(value["year"]),
                cutoff=cutoff,
                retrieval_snapshot_time=str(value["retrieval_snapshot_time"]),
                mime_type=str(value["mime_type"]),
                provenance=str(value["provenance"]),
                publication_or_version_date=version_date,
                raw_content=raw,
                raw_content_sha256=raw_sha,
                canonical_text=canonical_text,
                canonical_text_sha256=canonical_sha,
                projection_identity=projection_identity,
                evidence_id=_identity(
                    "evidence_", "frozen_external_evidence_item", identity_payload
                ),
            )
        )
    records = sorted(records, key=lambda item: (item.target_paper_id, PARTITIONS.index(item.partition), item.evidence_id))
    if len({item.evidence_id for item in records}) != len(records):
        raise ValueError("Duplicate Frozen Evidence content identity")

    manifests = []
    for paper_id in sorted(set_specs):
        spec = set_specs[paper_id]
        ids = {
            partition: [
                item.evidence_id
                for item in records
                if item.target_paper_id == paper_id and item.partition == partition
            ]
            for partition in PARTITIONS
        }
        payload = {
            "pool_version": pool_version,
            "target_paper_id": paper_id,
            "target_family_id": spec["target_family_id"],
            "cutoff": spec["cutoff"],
            "partition_availability": spec["partition_availability"],
            "partition_evidence_ids": ids,
        }
        manifests.append(
            EvidenceSetManifest(
                pool_version=pool_version,
                target_paper_id=paper_id,
                target_family_id=spec["target_family_id"],
                cutoff=spec["cutoff"],
                partition_availability_items=tuple(
                    (partition, spec["partition_availability"][partition])
                    for partition in PARTITIONS
                ),
                partition_evidence_id_items=tuple(
                    (partition, tuple(ids[partition])) for partition in PARTITIONS
                ),
                evidence_set_sha256=_identity(
                    "evidenceset_", "frozen_external_evidence_set", payload
                ),
            )
        )
    return FrozenEvidencePool(
        pool_version=pool_version,
        projection_identity=projection_identity,
        records=tuple(records),
        manifests=tuple(manifests),
    )
