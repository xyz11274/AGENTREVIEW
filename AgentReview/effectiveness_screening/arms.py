"""Canonical arm identities for lineage-expanded effectiveness experiments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ARM_SCHEMA_VERSION = "effectiveness-arm-v1"
ARM_KINDS = ("no_memory", "seed", "evolved")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class ArmIdentity:
    """A globally unique experiment arm and its comparison lineage."""

    arm_id: str
    arm_kind: str
    lineage: str | None

    def to_record(self) -> dict[str, Any]:
        return {
            "arm_schema_version": ARM_SCHEMA_VERSION,
            "arm_id": self.arm_id,
            "arm_kind": self.arm_kind,
            "lineage": self.lineage,
        }


def arm_identities(config: Mapping[str, Any]) -> tuple[ArmIdentity, ...]:
    """Expand configured arm kinds into stable, lineage-qualified identities."""

    configured_schema = str(config.get("arm_schema_version") or ARM_SCHEMA_VERSION)
    if configured_schema != ARM_SCHEMA_VERSION:
        raise ValueError(f"Unsupported arm schema version: {configured_schema}")
    experiment = dict(config["experiment"])
    arm_kinds = [str(value) for value in experiment["arms"]]
    lineages = [str(value) for value in experiment.get("lineages") or []]

    duplicate_kinds = sorted(
        {value for value in arm_kinds if arm_kinds.count(value) > 1}
    )
    if duplicate_kinds:
        raise ValueError(f"Duplicate experiment arm kinds: {duplicate_kinds}")
    unknown_kinds = sorted(set(arm_kinds) - set(ARM_KINDS))
    if unknown_kinds:
        raise ValueError(f"Unknown experiment arm kinds: {unknown_kinds}")
    duplicate_lineages = sorted(
        {value for value in lineages if lineages.count(value) > 1}
    )
    if duplicate_lineages:
        raise ValueError(f"Duplicate experiment lineages: {duplicate_lineages}")
    invalid_lineages = sorted(
        value for value in lineages if _IDENTIFIER.fullmatch(value) is None
    )
    if invalid_lineages:
        raise ValueError(
            "Experiment lineages require lowercase identifier syntax: "
            f"{invalid_lineages}"
        )
    lineage_kinds = set(arm_kinds) & {"seed", "evolved"}
    if lineage_kinds and len(lineages) == 0:
        raise ValueError(
            "Seed and evolved arm kinds require explicit experiment lineages"
        )

    identities = []
    for arm_kind in arm_kinds:
        if arm_kind == "no_memory":
            identities.append(ArmIdentity("no_memory", arm_kind, None))
        elif arm_kind == "seed":
            identities.extend(
                ArmIdentity(f"seed_{lineage}", arm_kind, lineage)
                for lineage in lineages
            )
        else:
            identities.extend(
                ArmIdentity(f"evolved_from_{lineage}", arm_kind, lineage)
                for lineage in lineages
            )

    arm_ids = [identity.arm_id for identity in identities]
    duplicate_ids = sorted({value for value in arm_ids if arm_ids.count(value) > 1})
    if duplicate_ids:
        raise ValueError(f"Arm identity collision: {duplicate_ids}")
    return tuple(identities)


def program_arm_identities(
    config: Mapping[str, Any],
) -> tuple[ArmIdentity, ...]:
    """Return the arm identities backed by executable MemoryPrograms."""

    return tuple(
        identity
        for identity in arm_identities(config)
        if identity.arm_kind in {"seed", "evolved"}
    )


def arm_identity(config: Mapping[str, Any], arm_id: str) -> ArmIdentity:
    """Resolve one canonical arm ID from the configured experiment schema."""

    by_id = {identity.arm_id: identity for identity in arm_identities(config)}
    if arm_id in by_id:
        return by_id[arm_id]
    raise ValueError(f"Unknown arm ID: {arm_id!r}; configured IDs: {sorted(by_id)}")


def lineage_arm_pairs(
    config: Mapping[str, Any],
) -> tuple[tuple[str, ArmIdentity, ArmIdentity], ...]:
    """Pair each lineage's seed and evolved identities for causal comparisons."""

    identities = arm_identities(config)
    by_kind_lineage = {
        (identity.arm_kind, identity.lineage): identity for identity in identities
    }
    lineages = [str(value) for value in config["experiment"].get("lineages") or []]
    pairs = []
    for lineage in lineages:
        seed = by_kind_lineage.get(("seed", lineage))
        evolved = by_kind_lineage.get(("evolved", lineage))
        if seed is None or evolved is None:
            raise ValueError(
                "Each effectiveness comparison requires seed and evolved arms "
                f"for lineage {lineage!r}"
            )
        pairs.append((lineage, seed, evolved))
    return tuple(pairs)
