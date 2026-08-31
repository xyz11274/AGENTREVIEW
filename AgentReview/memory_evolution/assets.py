"""Versioned asset identities, path discipline and four-zone isolation audits."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ASSET_MANIFEST_VERSION = "mstar-assets-v2-temporal"
TEMPORAL_SPLIT_V1_VERSION = "coco-temporal-paper-split-v1"
TEMPORAL_SPLIT_VERSION = "coco-temporal-paper-split-v2"
SUPPORTED_TEMPORAL_SPLIT_VERSIONS = (
    TEMPORAL_SPLIT_V1_VERSION,
    TEMPORAL_SPLIT_VERSION,
)
FOUR_ZONES = (
    "historical_experience",
    "evolution_validation",
    "effectiveness_holdout",
    "held_out_benchmark_test",
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def venue_year(record: Mapping[str, Any]) -> tuple[str, str]:
    venue = str(
        record.get("venue")
        or record.get("conference")
        or (record.get("content") or {}).get("venue")
        or ""
    ).strip()
    year = str(
        record.get("year") or (record.get("content") or {}).get("year") or ""
    ).strip()
    if not year:
        match = re.search(r"(?:19|20)\d{2}", venue)
        year = match.group(0) if match else ""
    known = re.search(r"\b(ICLR|NeurIPS)\b", venue, flags=re.IGNORECASE)
    normalized_venue = (
        known.group(1).lower()
        if known
        else re.sub(r"\s+(?:19|20)\d{2}\b", "", venue).strip().lower()
    )
    return normalized_venue, year


def paper_review_cutoff(record: Mapping[str, Any]) -> str:
    """Return an explicit review-time cutoff, never an inferred current date."""
    metadata = record.get("metadata") or {}
    value = record.get("paper_review_cutoff") or metadata.get("paper_review_cutoff")
    if value:
        return str(value)
    return ""


def paper_identity(record: Mapping[str, Any]) -> dict[str, str]:
    metadata = record.get("metadata") or {}
    content = record.get("content") or {}
    paper_id = str(
        record.get("paper_id") or record.get("id") or metadata.get("paper_id") or ""
    ).split("::", 1)[0]
    title = normalize_title(
        record.get("title")
        or record.get("paper_title")
        or content.get("title")
        or metadata.get("paper_title")
        or ""
    )
    family = str(
        record.get("work_family")
        or record.get("paper_family")
        or metadata.get("work_family")
        or content.get("paperhash")
        or title
        or paper_id
    )
    return {"paper_id": paper_id, "normalized_title": title, "paper_family": family}


def audit_four_zone_isolation(
    zones: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Audit paper/title/family disjointness and chronological isolation."""
    unknown = set(zones) - set(FOUR_ZONES)
    if unknown:
        raise ValueError(f"Unknown data zones: {sorted(unknown)}")
    identities = {
        zone: [paper_identity(record) for record in records]
        for zone, records in zones.items()
    }
    overlaps = []
    for index, left in enumerate(FOUR_ZONES):
        for right in FOUR_ZONES[index + 1 :]:
            left_values = identities.get(left, [])
            right_values = identities.get(right, [])
            for identity_field in (
                "paper_id",
                "normalized_title",
                "paper_family",
            ):
                common = sorted(
                    {
                        value[identity_field]
                        for value in left_values
                        if value[identity_field]
                    }
                    & {
                        value[identity_field]
                        for value in right_values
                        if value[identity_field]
                    }
                )
                if common:
                    overlaps.append(
                        {
                            "left": left,
                            "right": right,
                            "field": identity_field,
                            "values": common,
                        }
                    )
    missing_cutoffs = []
    invalid_cutoffs = []
    parsed_cutoffs: dict[str, list[date]] = {zone: [] for zone in FOUR_ZONES}
    for zone in FOUR_ZONES:
        for record in zones.get(zone, []):
            cutoff = paper_review_cutoff(record)
            if not cutoff:
                missing_cutoffs.append(
                    {"zone": zone, "paper_id": paper_identity(record)["paper_id"]}
                )
                continue
            try:
                parsed_cutoffs[zone].append(date.fromisoformat(cutoff[:10]))
            except ValueError:
                invalid_cutoffs.append(
                    {
                        "zone": zone,
                        "paper_id": paper_identity(record)["paper_id"],
                        "paper_review_cutoff": cutoff,
                    }
                )
    development_dates = [
        value for zone in FOUR_ZONES[:3] for value in parsed_cutoffs[zone]
    ]
    test_dates = parsed_cutoffs[FOUR_ZONES[3]]
    development_max = max(development_dates) if development_dates else None
    test_min = min(test_dates) if test_dates else None
    temporal_violations = []
    if development_max and test_min and development_max > test_min:
        temporal_violations.append(
            {
                "rule": "max(A,B,C cutoff) <= min(D cutoff)",
                "development_max": development_max.isoformat(),
                "held_out_test_min": test_min.isoformat(),
            }
        )
    historical_dates = parsed_cutoffs[FOUR_ZONES[0]]
    if historical_dates != sorted(historical_dates):
        temporal_violations.append(
            {"rule": "Historical manifest order must be chronological"}
        )
    report = {
        "protocol": "four-zone-temporal-isolation-v3",
        "zone_counts": {zone: len(zones.get(zone, [])) for zone in FOUR_ZONES},
        "overlaps": overlaps,
        "missing_paper_review_cutoffs": missing_cutoffs,
        "invalid_paper_review_cutoffs": invalid_cutoffs,
        "temporal_violations": temporal_violations,
        "development_max_cutoff": (
            development_max.isoformat() if development_max else None
        ),
        "held_out_test_min_cutoff": test_min.isoformat() if test_min else None,
        "passed": not (
            overlaps or missing_cutoffs or invalid_cutoffs or temporal_violations
        ),
    }
    return report


def audit_family_target_integrity(
    summary: Mapping[str, Any],
    zones: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Audit the persisted v2 family targets from their current content."""
    targets = [(zone, record) for zone in FOUR_ZONES for record in zones.get(zone, ())]
    paper_ids = [str(record.get("paper_id") or "") for _, record in targets]
    family_ids = [str(record.get("family_id") or "") for _, record in targets]
    accounted_source_ids: list[str] = []
    malformed_targets = []
    for zone, record in targets:
        paper_id = str(record.get("paper_id") or "")
        family_id = str(record.get("family_id") or "")
        excluded = record.get("excluded_paper_ids")
        valid = all(
            (
                bool(paper_id),
                bool(family_id),
                record.get("paper_family") == family_id,
                record.get("representative_paper_id") == paper_id,
                record.get("zone") == zone,
                isinstance(excluded, list),
                paper_id not in (excluded if isinstance(excluded, list) else ()),
            )
        )
        if valid:
            accounted_source_ids.extend([paper_id, *map(str, excluded)])
        else:
            malformed_targets.append(paper_id or f"{zone}:<missing-paper-id>")

    duplicate_paper_ids = sorted(
        value for value, count in Counter(paper_ids).items() if count > 1
    )
    duplicate_family_ids = sorted(
        value for value, count in Counter(family_ids).items() if count > 1
    )
    duplicate_source_ids = sorted(
        value for value, count in Counter(accounted_source_ids).items() if count > 1
    )
    target_count = len(targets)
    source_record_count = int(summary.get("source_record_count", -1))
    deduplicated_record_count = int(summary.get("deduplicated_record_count", -1))
    family_index_count = int((summary.get("families") or {}).get("count", -1))
    representative_audit = summary.get("representative_selection_audit") or {}
    temporal_audit = audit_four_zone_isolation(zones)
    report = {
        "protocol": "persisted-family-target-integrity-v2",
        "source_record_count": source_record_count,
        "formal_target_count": target_count,
        "family_index_count": family_index_count,
        "deduplicated_record_count": deduplicated_record_count,
        "accounted_source_record_count": len(accounted_source_ids),
        "unique_accounted_source_record_count": len(set(accounted_source_ids)),
        "duplicate_paper_ids": duplicate_paper_ids,
        "duplicate_family_ids": duplicate_family_ids,
        "duplicate_source_ids": duplicate_source_ids,
        "malformed_targets": malformed_targets,
        "zone_counts": temporal_audit["zone_counts"],
        "cross_zone_isolation_passed": temporal_audit["passed"],
        "representative_selection_passed": representative_audit.get("passed") is True,
    }
    report["passed"] = all(
        (
            summary.get("version") == TEMPORAL_SPLIT_VERSION,
            summary.get("target_unit") == "one representative paper per paper family",
            int(summary.get("unique_paper_family_count", -1)) == target_count,
            int(summary.get("formal_target_count", -1)) == target_count,
            family_index_count == target_count,
            source_record_count == len(accounted_source_ids),
            source_record_count == target_count + deduplicated_record_count,
            len(set(paper_ids)) == target_count,
            len(set(family_ids)) == target_count,
            len(set(accounted_source_ids)) == source_record_count,
            len(malformed_targets) == 0,
            temporal_audit["passed"] is True,
            representative_audit.get("passed") is True,
        )
    )
    return report


def load_temporal_split_manifest(
    path: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Load and checksum the four temporal target-zone manifests."""
    source = Path(path).expanduser().resolve()
    summary = json.loads(source.read_text(encoding="utf-8"))
    if summary.get("version") not in SUPPORTED_TEMPORAL_SPLIT_VERSIONS:
        raise ValueError(
            f"Unsupported temporal split protocol: {summary.get('version')!r}"
        )
    zones: dict[str, list[dict[str, Any]]] = {}
    for zone in FOUR_ZONES:
        entry = (summary.get("zones") or {}).get(zone) or {}
        recorded = str(entry.get("path") or "")
        zone_path = Path(recorded)
        if not zone_path.is_absolute():
            zone_path = source.parent / zone_path
        zone_path = zone_path.resolve()
        if not zone_path.is_file():
            raise FileNotFoundError(f"Temporal zone manifest missing: {zone_path}")
        actual_sha = sha256_file(zone_path)
        if actual_sha != str(entry.get("sha256") or ""):
            raise ValueError(f"Temporal zone manifest SHA256 mismatch: {zone}")
        values = []
        with zone_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid temporal manifest JSONL {zone_path}:{line_number}"
                    ) from exc
        if len(values) != int(entry.get("count", -1)):
            raise ValueError(f"Temporal zone count mismatch: {zone}")
        zones[zone] = values
    expected_total = int(
        summary.get("formal_target_count", -1)
        if summary.get("version") == TEMPORAL_SPLIT_VERSION
        else summary.get("source_pool_count", -1)
    )
    if sum(map(len, zones.values())) != expected_total:
        raise ValueError("Temporal split target count does not match its manifest")
    if summary.get("version") == TEMPORAL_SPLIT_VERSION:
        family_entry = summary.get("families") or {}
        family_recorded = str(family_entry.get("path") or "")
        family_path = Path(family_recorded)
        if not family_path.is_absolute():
            family_path = source.parent / family_path
        family_path = family_path.resolve()
        if not family_path.is_file():
            raise FileNotFoundError(f"Temporal family manifest missing: {family_path}")
        if sha256_file(family_path) != str(family_entry.get("sha256") or ""):
            raise ValueError("Temporal family manifest SHA256 mismatch")
        family_records = []
        with family_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    family_records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid temporal family JSONL {family_path}:{line_number}"
                    ) from exc
        if len(family_records) != int(family_entry.get("count", -1)):
            raise ValueError("Temporal family manifest count mismatch")
        zone_records = [record for zone in FOUR_ZONES for record in zones[zone]]
        zone_by_family = {
            str(record.get("family_id") or ""): record for record in zone_records
        }
        indexed_by_family = {
            str(record.get("family_id") or ""): record for record in family_records
        }
        if (
            "" in zone_by_family
            or "" in indexed_by_family
            or len(zone_by_family) != len(zone_records)
            or len(indexed_by_family) != len(family_records)
            or set(zone_by_family) != set(indexed_by_family)
        ):
            raise ValueError("Temporal split violates one-family-one-target identity")
        for family_id, record in zone_by_family.items():
            family = indexed_by_family[family_id]
            if (
                record.get("paper_family") != family_id
                or record.get("representative_paper_id") != record.get("paper_id")
                or family.get("representative_paper_id") != record.get("paper_id")
                or family.get("excluded_paper_ids") != record.get("excluded_paper_ids")
                or family.get("zone") != record.get("zone")
            ):
                raise ValueError(
                    f"Temporal family representative metadata mismatch: {family_id}"
                )
    return summary, zones


def validate_asset_manifest(
    path: str,
    *,
    required_assets: Sequence[str] = (),
    required_versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve every declared asset and fail closed on any identity mismatch."""
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != ASSET_MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported asset manifest version: {payload.get('manifest_version')!r}"
        )
    external_roots = dict(payload.get("external_roots") or {})
    assets = dict(payload.get("assets") or {})
    missing = sorted(set(required_assets) - set(assets))
    if missing:
        raise ValueError(f"Asset manifest is missing required assets: {missing}")
    expected_versions = dict(required_versions or {})
    missing_versioned = sorted(set(expected_versions) - set(assets))
    if missing_versioned:
        raise ValueError(
            f"Asset manifest is missing versioned assets: {missing_versioned}"
        )
    verified = {}
    for name, entry in assets.items():
        recorded = str(entry.get("path") or "")
        resolved, _ = resolve_asset_path(
            recorded,
            project_root=PROJECT_ROOT,
            external_roots=external_roots,
        )
        if not resolved.is_file():
            raise FileNotFoundError(f"Asset is not a file: {name}: {resolved}")
        actual = sha256_file(resolved)
        expected = str(entry.get("sha256") or "")
        if not expected or actual != expected:
            raise ValueError(f"Asset SHA256 mismatch: {name}")
        actual_version = str(entry.get("version") or "")
        required_version = expected_versions.get(name)
        if required_version is not None and actual_version != required_version:
            raise ValueError(
                f"Asset version mismatch: {name}: expected "
                f"{required_version!r}, got {actual_version!r}"
            )
        verified[name] = {
            "resolved_path": str(resolved),
            "sha256": actual,
            "version": actual_version,
            "split": str(entry.get("split") or ""),
        }
    return {
        "manifest": str(source),
        "manifest_sha256": sha256_file(source),
        "verified_assets": verified,
        "formal_ready": payload.get("formal_ready") is True,
        "formal_blockers": list(payload.get("formal_blockers") or []),
        "passed": True,
    }


def resolve_asset_path(
    recorded_path: str,
    *,
    project_root: Path = PROJECT_ROOT,
    external_roots: Mapping[str, str] | None = None,
) -> tuple[Path, dict[str, str] | None]:
    """Resolve project-relative/external paths and explicitly map legacy snapshots."""
    raw = str(recorded_path or "")
    if raw.startswith("external://"):
        name, _, remainder = raw[len("external://") :].partition("/")
        configured = dict(external_roots or {}).get(name)
        if not configured:
            raise KeyError(f"External data root is not configured: {name}")
        root = Path(configured).expanduser()
        resolved = (root / remainder).resolve()
        return resolved, None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return (project_root / path).resolve(), None
    if path.exists():
        return path.resolve(), None
    parts = path.parts
    if "AgentReview" in parts:
        index = len(parts) - 1 - list(reversed(parts)).index("AgentReview")
        candidate = (project_root / Path(*parts[index + 1 :])).resolve()
        if candidate.exists():
            return candidate, {
                "relation": "legacy_recorded_path_resolved_to_current_snapshot",
                "recorded_path": raw,
                "resolved_path": str(candidate),
            }
    raise FileNotFoundError(f"Asset path cannot be resolved: {recorded_path}")


@dataclass
class AssetManifest:
    project_root: Path = PROJECT_ROOT
    external_roots: dict[str, str] = field(default_factory=dict)
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: str = ASSET_MANIFEST_VERSION

    def add_file(
        self,
        name: str,
        path: str,
        *,
        split: str,
        asset_version: str,
    ) -> dict[str, Any]:
        resolved, relation = resolve_asset_path(
            path,
            project_root=self.project_root,
            external_roots=self.external_roots,
        )
        if not resolved.is_file():
            raise FileNotFoundError(f"Asset is not a file: {resolved}")
        try:
            portable_path = str(resolved.relative_to(self.project_root))
            path_kind = "project_relative"
        except ValueError:
            portable_path = str(resolved)
            path_kind = "external_resolved"
        value = {
            "path": portable_path,
            "resolved_path": str(resolved),
            "path_kind": path_kind,
            "sha256": sha256_file(resolved),
            "version": str(asset_version),
            "split": str(split),
        }
        if relation:
            value["provenance_relation"] = relation
        self.assets[str(name)] = value
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.version,
            "project_root": str(self.project_root.resolve()),
            "external_roots": dict(sorted(self.external_roots.items())),
            "assets": dict(sorted(self.assets.items())),
        }

    def write(self, path: str) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return destination
