"""Structured activation gates for effectiveness-screening stages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ACTIVATION_SCHEMA_VERSION = "effectiveness-activation-v1"


class ActivationPending(RuntimeError):
    """Carry a machine-readable activation report to CLI callers."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        super().__init__(json.dumps(self.report, ensure_ascii=False, sort_keys=True))


def activation_report(
    config: Mapping[str, Any],
    stage: str,
    required_assets: Mapping[str, Mapping[str, Any] | str | Path] | None = None,
) -> dict[str, Any]:
    """Describe whether the configured inputs authorize a stage."""
    configured_status = str(config.get("status") or "active")
    requirements = dict(config.get("activation_requirements") or {})
    pending = []
    for name, raw_requirement in sorted(requirements.items()):
        requirement = dict(raw_requirement or {})
        required_for = tuple(requirement.get("required_for") or ())
        applies = len(required_for) == 0 or stage in required_for
        ready = requirement.get("active") is True
        if applies and ready is False:
            pending.append(
                {
                    "requirement": str(name),
                    "configured_path": str(requirement.get("configured_path") or ""),
                    "current_state": str(requirement.get("current_state") or "pending"),
                    "required_state": str(
                        requirement.get("required_state") or "active"
                    ),
                }
            )
    pending_assets = []
    if "status" in config:
        for name, raw_asset in sorted(dict(required_assets or {}).items()):
            if isinstance(raw_asset, Mapping):
                asset = dict(raw_asset)
                path = Path(str(asset.get("path") or "")).expanduser().resolve()
                asset_type = str(asset.get("type") or "file")
            else:
                path = Path(raw_asset).expanduser().resolve()
                asset_type = "file"
            present = path.is_dir() if asset_type == "directory" else path.is_file()
            if present:
                continue
            pending_assets.append(
                {
                    "asset": str(name),
                    "path": str(path),
                    "required_type": asset_type,
                    "current_state": "pending_asset",
                    "required_state": "present",
                }
            )
    active = (
        configured_status == "active" and len(pending) == 0 and len(pending_assets) == 0
    )
    return {
        "activation_schema_version": ACTIVATION_SCHEMA_VERSION,
        "stage": str(stage),
        "active": active,
        "configured_status": configured_status,
        "pending_requirements": pending,
        "pending_assets": pending_assets,
        "activation_message": (
            "stage assets are active"
            if active
            else "stage awaits the listed activation requirements"
        ),
    }


def require_activation(
    config: Mapping[str, Any],
    stage: str,
    required_assets: Mapping[str, Mapping[str, Any] | str | Path] | None = None,
) -> dict[str, Any]:
    """Return the active report or raise it as structured pending state."""
    report = activation_report(config, stage, required_assets)
    if report["active"]:
        return report
    raise ActivationPending(report)
