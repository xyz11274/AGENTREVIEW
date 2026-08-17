"""Versioned asset identities, path discipline and four-zone isolation audits."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ASSET_MANIFEST_VERSION = "mstar-assets-v2-temporal"
TEMPORAL_SPLIT_VERSION = "coco-temporal-paper-split-v1"
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


def venue_year(record: Mapping[str, Any]) -> Tuple[str, str]:
    venue = str(
        record.get("venue")
        or record.get("conference")
        or (record.get("content") or {}).get("venue")
        or ""
    ).strip()
    year = str(
        record.get("year")
        or (record.get("content") or {}).get("year")
        or ""
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
    value = record.get("paper_review_cutoff") or metadata.get(
        "paper_review_cutoff"
    )
    if value:
        return str(value)
    return ""


def paper_identity(record: Mapping[str, Any]) -> Dict[str, str]:
    metadata = record.get("metadata") or {}
    content = record.get("content") or {}
    paper_id = str(
        record.get("paper_id")
        or record.get("id")
        or metadata.get("paper_id")
        or ""
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
) -> Dict[str, Any]:
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
            for field in ("paper_id", "normalized_title", "paper_family"):
                common = sorted(
                    {value[field] for value in left_values if value[field]}
                    & {value[field] for value in right_values if value[field]}
                )
                if common:
                    overlaps.append(
                        {"left": left, "right": right, "field": field, "values": common}
                    )
    missing_cutoffs = []
    invalid_cutoffs = []
    parsed_cutoffs: Dict[str, List[date]] = {zone: [] for zone in FOUR_ZONES}
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
        "protocol": "four-zone-temporal-isolation-v2",
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


def load_temporal_split_manifest(path: str) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    """Load and checksum the four paper-level zone manifests."""
    source = Path(path).expanduser().resolve()
    summary = json.loads(source.read_text(encoding="utf-8"))
    if summary.get("version") != TEMPORAL_SPLIT_VERSION:
        raise ValueError(
            f"Unsupported temporal split protocol: {summary.get('version')!r}"
        )
    zones: Dict[str, List[Dict[str, Any]]] = {}
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
    expected_total = int(summary.get("source_pool_count", -1))
    if sum(map(len, zones.values())) != expected_total:
        raise ValueError("Temporal split does not account for the frozen source pool")
    return summary, zones


def validate_asset_manifest(path: str, *, required_assets: Sequence[str] = ()) -> Dict[str, Any]:
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
        verified[name] = {"resolved_path": str(resolved), "sha256": actual}
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
    external_roots: Optional[Mapping[str, str]] = None,
) -> Tuple[Path, Optional[Dict[str, str]]]:
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
    external_roots: Dict[str, str] = field(default_factory=dict)
    assets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    version: str = ASSET_MANIFEST_VERSION

    def add_file(
        self,
        name: str,
        path: str,
        *,
        split: str,
        asset_version: str,
    ) -> Dict[str, Any]:
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

    def to_dict(self) -> Dict[str, Any]:
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
