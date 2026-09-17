"""Component-safe Reviewer Program composition for post-winner diagnostics."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .constants import ROLES
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .memory import validate_memory_snapshot_artifact
from .program_checks import (
    REQUIRED_CLASSES,
    REQUIRED_INSTRUCTIONS,
    REVIEW_PLAN_FUNCTION,
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
    static_check,
)

COMPONENT_ASSEMBLY_PROTOCOL = "research-v2-program-component-assembly-v1"
FACTORIAL_COMPONENTS_PROTOCOL = "research-v2-factorial-components-v1"
MEMORY_STATE_PROTOCOL = "research-v2-frozen-memory-state-v1"
FACTORIAL_CELL_SOURCES = {
    "baseline_memory__baseline_s2_policy": ("baseline", "baseline"),
    "evolved_memory__baseline_s2_policy": ("evolved", "baseline"),
    "baseline_memory__evolved_s2_policy": ("baseline", "evolved"),
    "evolved_memory__evolved_s2_policy": ("evolved", "evolved"),
}
REQUIRED_SHARED_FACTORIAL_IDENTITY = {
    "development_manifest_sha256",
    "paper_ids_sha256",
    "model_identity",
    "reviewer_identity",
    "embedding_identity",
    "evaluator_identity",
    "frozen_s2_sha256",
    "generation_identity",
}


def _load_verified_program(path: str | Path) -> tuple[Path, ast.Module, dict[str, str]]:
    source = Path(path).expanduser().resolve()
    code = source.read_text(encoding="utf-8")
    errors = static_check(code)
    if errors:
        raise ValueError(f"Invalid component Program at {source}: {errors}")
    return source, ast.parse(code), program_capability_manifest(code=code)


def _import_bindings(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    return {alias.asname or alias.name for alias in node.names}


def _component_imports(
    tree: ast.Module,
    *,
    policy: bool,
) -> list[ast.Import | ast.ImportFrom]:
    imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if policy:
        roots = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == REVIEW_PLAN_FUNCTION
        ]
    else:
        roots = [
            node
            for node in tree.body
            if (isinstance(node, ast.ClassDef) and node.name in REQUIRED_CLASSES)
            or (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id in REQUIRED_INSTRUCTIONS
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            )
        ]
    loaded_names = {
        node.id
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return [
        node for node in imports if _import_bindings(node).intersection(loaded_names)
    ]


def _merged_imports(
    memory_tree: ast.Module,
    policy_tree: ast.Module,
) -> list[ast.Import | ast.ImportFrom]:
    selected: list[ast.Import | ast.ImportFrom] = []
    binding_sources: dict[str, str] = {}
    seen_nodes: set[str] = set()
    selected_imports = [
        *_component_imports(memory_tree, policy=False),
        *_component_imports(policy_tree, policy=True),
    ]
    for node in selected_imports:
        rendered = ast.dump(node, annotate_fields=True, include_attributes=False)
        for binding in _import_bindings(node):
            previous = binding_sources.get(binding)
            if previous is not None and previous != rendered:
                raise ValueError(
                    "Memory and policy Programs bind an import name differently: "
                    f"{binding}"
                )
            binding_sources[binding] = rendered
        if rendered not in seen_nodes:
            seen_nodes.add(rendered)
            selected.append(node)
    return selected


def _composed_source(memory_tree: ast.Module, policy_tree: ast.Module) -> str:
    policy_plans = [
        node
        for node in policy_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "plan_review"
    ]
    if len(policy_plans) != 1:
        raise ValueError("Policy Program must contain exactly one plan_review")
    merged_imports = _merged_imports(memory_tree, policy_tree)
    body: list[ast.stmt] = []
    imports_inserted = False
    for index, node in enumerate(memory_tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if is_docstring:
            body.append(node)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not imports_inserted:
                body.extend(merged_imports)
                imports_inserted = True
            continue
        if isinstance(node, ast.FunctionDef) and node.name == "plan_review":
            if not imports_inserted:
                body.extend(merged_imports)
                imports_inserted = True
            body.append(policy_plans[0])
            continue
        if not imports_inserted:
            body.extend(merged_imports)
            imports_inserted = True
        body.append(node)
    if not imports_inserted:
        body.extend(merged_imports)
    tree = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(tree)
    return ast.unparse(tree).strip() + "\n"


def _write_immutable_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_text(encoding="utf-8") == value:
            return
        raise FileExistsError(f"Component Program already differs: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def compose_program_components(
    *,
    memory_program_path: str | Path,
    policy_program_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Materialize one Program with Memory and S2-policy components crossed."""
    memory_path, memory_tree, memory_manifest = _load_verified_program(
        memory_program_path
    )
    policy_path, policy_tree, policy_manifest = _load_verified_program(
        policy_program_path
    )
    code = _composed_source(memory_tree, policy_tree)
    errors = static_check(code)
    if errors:
        raise ValueError(f"Composed Program is invalid: {errors}")
    composed_manifest = program_capability_manifest(code=code)
    if composed_manifest["memory_ast_sha256"] != memory_manifest["memory_ast_sha256"]:
        raise ValueError("Composed Program changed the selected Memory component")
    if composed_manifest["policy_ast_sha256"] != policy_manifest["policy_ast_sha256"]:
        raise ValueError("Composed Program changed the selected S2 policy component")

    destination = Path(output_path).expanduser().resolve()
    _write_immutable_text(destination, code)
    if file_sha256(destination) != composed_manifest["program_sha256"]:
        raise ValueError("Composed Program file hash differs from its manifest")
    assembly = {
        "protocol": COMPONENT_ASSEMBLY_PROTOCOL,
        "memory_source": {
            "path": str(memory_path),
            "manifest": memory_manifest,
        },
        "policy_source": {
            "path": str(policy_path),
            "manifest": policy_manifest,
        },
        "program_path": str(destination),
        "capability_manifest": composed_manifest,
    }
    assembly["assembly_id"] = value_sha256(assembly)
    atomic_write_json(destination.with_suffix(".components.json"), assembly)
    return assembly


def _memory_state_sha256(
    snapshot: dict[str, Any],
    capability_manifest: dict[str, str],
) -> str:
    return value_sha256(
        {
            "protocol": MEMORY_STATE_PROTOCOL,
            "role": snapshot.get("role"),
            "seed_kind": snapshot.get("seed_kind"),
            "memory_ast_sha256": capability_manifest["memory_ast_sha256"],
            "source_review_ids": snapshot.get("source_review_ids"),
            "source_evaluation_ids": snapshot.get("source_evaluation_ids"),
            "embedding_identity": snapshot.get("embedding_identity"),
            "build_identity": snapshot.get("build_identity"),
            "items": snapshot.get("items"),
            "snapshot": snapshot.get("snapshot"),
        }
    )


def _verified_source_snapshot(
    path: str | Path,
    *,
    expected_role: str,
    expected_program_path: str | Path | None = None,
) -> tuple[Path, dict[str, Any], dict[str, str], str]:
    source_path = Path(path).expanduser().resolve()
    value = validate_memory_snapshot_artifact(
        read_json(source_path),
        path=source_path,
    )
    if value.get("snapshot_protocol") != "research-v2-memory-snapshot-v1":
        raise ValueError(f"Unsupported Memory snapshot protocol: {source_path}")
    if expected_role not in ROLES or value.get("role") != expected_role:
        raise ValueError(
            f"Memory snapshot role mismatch: expected={expected_role!r}, "
            f"actual={value.get('role')!r}"
        )
    for field_name in (
        "source_review_ids",
        "source_evaluation_ids",
        "items",
    ):
        if not isinstance(value.get(field_name), list):
            raise TypeError(
                f"Memory snapshot {field_name} must be a list: {source_path}"
            )
    if not isinstance(value.get("snapshot"), dict):
        raise TypeError(f"Memory snapshot state must be an object: {source_path}")

    recorded_program_path = value.get("program_path")
    if not isinstance(recorded_program_path, str) or not recorded_program_path:
        raise ValueError(f"Memory snapshot lacks program_path: {source_path}")
    program_path = Path(recorded_program_path).expanduser().resolve()
    if (
        expected_program_path is not None
        and program_path != Path(expected_program_path).expanduser().resolve()
    ):
        raise ValueError(
            "Memory snapshot Program path differs from the selected source Program"
        )
    manifest = program_capability_manifest(path=program_path)
    if value.get("program_sha256") != manifest["program_sha256"]:
        raise ValueError(f"Memory snapshot Program hash mismatch: {source_path}")
    if value.get("capability_manifest") != manifest:
        raise ValueError(f"Memory snapshot capability manifest mismatch: {source_path}")
    if manifest.get("interface_version") != REVIEW_PLAN_PROTOCOL:
        raise ValueError(f"Memory snapshot Program interface mismatch: {source_path}")

    build_identity = value.get("build_identity")
    embedding_identity = value.get("embedding_identity")
    if not isinstance(build_identity, dict) or not isinstance(embedding_identity, dict):
        raise ValueError(f"Memory snapshot lacks build lineage: {source_path}")
    build_program_path = (
        Path(str(build_identity.get("program_path") or "")).expanduser().resolve()
    )
    build_manifest = program_capability_manifest(path=build_program_path)
    expected_build_fields = {
        "protocol": "research-v2-memory-build-input-v1",
        "role": expected_role,
        "program_path": str(build_program_path),
        "program_sha256": build_manifest["program_sha256"],
        "capability_manifest": build_manifest,
        "seed_kind": value.get("seed_kind"),
        "embedding_identity": embedding_identity,
    }
    for field_name, expected in expected_build_fields.items():
        if build_identity.get(field_name) != expected:
            raise ValueError(
                f"Memory snapshot build lineage mismatch for {field_name}: "
                f"{source_path}"
            )
    rebind = value.get("component_rebind")
    if rebind is None:
        if build_program_path != program_path or build_identity.get(
            "program_version"
        ) != value.get("program_version"):
            raise ValueError(
                f"Memory snapshot original build Program mismatch: {source_path}"
            )
    elif (
        not isinstance(rebind, dict)
        or build_manifest["memory_ast_sha256"] != manifest["memory_ast_sha256"]
    ):
        raise ValueError(
            f"Memory snapshot rebound build lineage mismatch: {source_path}"
        )
    build_sources = build_identity.get("sources")
    if not isinstance(build_sources, list) or len(build_sources) != len(value["items"]):
        raise ValueError(f"Memory snapshot build sources mismatch: {source_path}")
    if [item.get("review_id") for item in build_sources] != value["source_review_ids"]:
        raise ValueError(f"Memory snapshot Review lineage mismatch: {source_path}")
    if sorted(
        str(item.get("evaluation_id"))
        for item in build_sources
        if item.get("evaluation_id")
    ) != sorted(str(item) for item in value["source_evaluation_ids"]):
        raise ValueError(f"Memory snapshot Evaluation lineage mismatch: {source_path}")

    review_ids = set(value["source_review_ids"])
    evaluation_ids = set(value["source_evaluation_ids"])
    for index, item in enumerate(value["items"]):
        if not isinstance(item, dict):
            raise TypeError(f"Memory snapshot item {index} must be an object")
        if item.get("role") != expected_role:
            raise ValueError(f"Memory snapshot item {index} crosses role scope")
        if item.get("derivation_version") != build_identity.get("program_version"):
            raise ValueError(
                f"Memory snapshot item {index} derivation version mismatch"
            )
        if not set(item.get("source_review_ids") or []).issubset(review_ids):
            raise ValueError(
                f"Memory snapshot item {index} has an unknown source Review"
            )
        if not set(item.get("source_evaluation_ids") or []).issubset(evaluation_ids):
            raise ValueError(
                f"Memory snapshot item {index} has an unknown source Evaluation"
            )
    state_sha256 = _memory_state_sha256(value, manifest)
    recorded_state_sha256 = value.get("memory_state_sha256")
    if recorded_state_sha256 is not None and recorded_state_sha256 != state_sha256:
        raise ValueError(f"Memory state identity mismatch: {source_path}")
    return source_path, value, manifest, state_sha256


def rebind_memory_snapshot(
    *,
    source_snapshot_path: str | Path,
    composed_program_path: str | Path,
    program_version: str,
    expected_role: str,
    expected_source_program_path: str | Path | None = None,
    output_path: str | Path,
) -> dict[str, Any]:
    """Reuse frozen Memory state after proving its component is unchanged."""
    source_path, source, source_manifest, memory_state_sha256 = (
        _verified_source_snapshot(
            source_snapshot_path,
            expected_role=expected_role,
            expected_program_path=expected_source_program_path,
        )
    )
    composed_path = Path(composed_program_path).expanduser().resolve()
    composed_manifest = program_capability_manifest(path=composed_path)
    if (
        source_manifest.get("memory_ast_sha256")
        != composed_manifest["memory_ast_sha256"]
    ):
        raise ValueError("Cannot rebind snapshot to a different Memory component")
    payload = {
        name: value
        for name, value in source.items()
        if name
        not in {"snapshot_id", "program_path", "program_sha256", "program_version"}
    }
    payload.update(
        {
            "program_path": str(composed_path),
            "program_sha256": composed_manifest["program_sha256"],
            "program_version": str(program_version),
            "capability_manifest": composed_manifest,
            "memory_state_sha256": memory_state_sha256,
            "component_rebind": {
                "protocol": COMPONENT_ASSEMBLY_PROTOCOL,
                "source_snapshot_path": str(source_path),
                "source_snapshot_sha256": file_sha256(source_path),
                "source_snapshot_id": source.get("snapshot_id"),
                "memory_ast_sha256": composed_manifest["memory_ast_sha256"],
                "memory_state_sha256": memory_state_sha256,
            },
        }
    )
    payload["snapshot_id"] = value_sha256(payload)
    atomic_write_json(output_path, payload)
    return payload


def materialize_factorial_components(
    *,
    role: str,
    baseline_program_path: str | Path,
    evolved_program_path: str | Path,
    baseline_snapshot_path: str | Path,
    evolved_snapshot_path: str | Path,
    shared_identity: dict[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    """Build the four verified Program/snapshot cells for a 2x2 diagnostic."""
    if role not in ROLES:
        raise ValueError(f"Unknown Specialist role: {role}")
    missing = sorted(REQUIRED_SHARED_FACTORIAL_IDENTITY - set(shared_identity))
    unexpected = sorted(set(shared_identity) - REQUIRED_SHARED_FACTORIAL_IDENTITY)
    if missing or unexpected:
        raise ValueError(
            "Factorial shared identity fields differ from protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
    programs = {
        "baseline": Path(baseline_program_path).expanduser().resolve(),
        "evolved": Path(evolved_program_path).expanduser().resolve(),
    }
    snapshots = {
        "baseline": Path(baseline_snapshot_path).expanduser().resolve(),
        "evolved": Path(evolved_snapshot_path).expanduser().resolve(),
    }
    source_snapshots = {
        name: _verified_source_snapshot(
            snapshots[name],
            expected_role=role,
            expected_program_path=programs[name],
        )
        for name in ("baseline", "evolved")
    }
    destination = Path(output_root).expanduser().resolve()
    cells = {}
    for cell, (memory_name, policy_name) in FACTORIAL_CELL_SOURCES.items():
        cell_root = destination / cell
        program_path = cell_root / "program.py"
        assembly = compose_program_components(
            memory_program_path=programs[memory_name],
            policy_program_path=programs[policy_name],
            output_path=program_path,
        )
        version = f"{role.lower()}-{cell}-v1"
        snapshot_path = cell_root / "snapshot.json"
        snapshot = rebind_memory_snapshot(
            source_snapshot_path=snapshots[memory_name],
            composed_program_path=program_path,
            program_version=version,
            expected_role=role,
            expected_source_program_path=programs[memory_name],
            output_path=snapshot_path,
        )
        source_path, source_snapshot, _, memory_state_sha256 = source_snapshots[
            memory_name
        ]
        cells[cell] = {
            "memory_source": memory_name,
            "s2_policy_source": policy_name,
            "program_path": str(program_path),
            "program_sha256": assembly["capability_manifest"]["program_sha256"],
            "program_version": version,
            "assembly_id": assembly["assembly_id"],
            "capability_manifest": assembly["capability_manifest"],
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": file_sha256(snapshot_path),
            "snapshot_id": snapshot["snapshot_id"],
            "source_snapshot_path": str(source_path),
            "source_snapshot_sha256": file_sha256(source_path),
            "source_snapshot_id": source_snapshot["snapshot_id"],
            "memory_state_sha256": memory_state_sha256,
        }
    payload = {
        "protocol": FACTORIAL_COMPONENTS_PROTOCOL,
        "role": role,
        "shared_identity": dict(shared_identity),
        "cells": cells,
    }
    payload["factorial_components_id"] = value_sha256(payload)
    atomic_write_json(destination / "factorial_components.json", payload)
    return payload


def validate_factorial_components(
    artifact: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    """Validate the materialized four-cell runtime through one deep interface."""
    if isinstance(artifact, (str, Path)):
        value = read_json(Path(artifact).expanduser().resolve())
    else:
        value = dict(artifact)
    if not isinstance(value, dict):
        raise TypeError("Factorial components artifact must be an object")
    if value.get("protocol") != FACTORIAL_COMPONENTS_PROTOCOL:
        raise ValueError("Unsupported factorial components protocol")
    recorded_id = value.get("factorial_components_id")
    unhashed = {
        name: item for name, item in value.items() if name != "factorial_components_id"
    }
    if not isinstance(recorded_id, str) or recorded_id != value_sha256(unhashed):
        raise ValueError("Factorial components identity mismatch")
    role = value.get("role")
    if role not in ROLES:
        raise ValueError(f"Unknown factorial Specialist role: {role!r}")
    shared_identity = value.get("shared_identity")
    if not isinstance(shared_identity, dict):
        raise TypeError("Factorial shared identity must be an object")
    missing = sorted(REQUIRED_SHARED_FACTORIAL_IDENTITY - set(shared_identity))
    unexpected = sorted(set(shared_identity) - REQUIRED_SHARED_FACTORIAL_IDENTITY)
    if missing or unexpected:
        raise ValueError(
            "Factorial shared identity fields differ from protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
    cells = value.get("cells")
    if not isinstance(cells, dict) or set(cells) != set(FACTORIAL_CELL_SOURCES):
        raise ValueError("Factorial components require exactly the registered cells")

    normalized: dict[str, dict[str, Any]] = {}
    for cell, (memory_source, policy_source) in FACTORIAL_CELL_SOURCES.items():
        spec = cells[cell]
        if not isinstance(spec, dict):
            raise TypeError(f"Factorial cell {cell} must be an object")
        if spec.get("memory_source") != memory_source:
            raise ValueError(f"Factorial cell {cell} Memory source mismatch")
        if spec.get("s2_policy_source") != policy_source:
            raise ValueError(f"Factorial cell {cell} S2 policy source mismatch")

        program_path = Path(str(spec.get("program_path") or "")).expanduser().resolve()
        manifest = program_capability_manifest(path=program_path)
        if spec.get("program_sha256") != file_sha256(program_path):
            raise ValueError(f"Factorial cell {cell} Program hash mismatch")
        if spec.get("capability_manifest") != manifest:
            raise ValueError(f"Factorial cell {cell} capability manifest mismatch")

        assembly_path = program_path.with_suffix(".components.json")
        assembly = read_json(assembly_path)
        if not isinstance(assembly, dict):
            raise TypeError(f"Factorial cell {cell} assembly must be an object")
        assembly_id = assembly.get("assembly_id")
        assembly_unhashed = {
            name: item for name, item in assembly.items() if name != "assembly_id"
        }
        if (
            assembly.get("protocol") != COMPONENT_ASSEMBLY_PROTOCOL
            or not isinstance(assembly_id, str)
            or assembly_id != value_sha256(assembly_unhashed)
            or spec.get("assembly_id") != assembly_id
            or assembly.get("program_path") != str(program_path)
            or assembly.get("capability_manifest") != manifest
        ):
            raise ValueError(f"Factorial cell {cell} assembly identity mismatch")

        snapshot_path = (
            Path(str(spec.get("snapshot_path") or "")).expanduser().resolve()
        )
        if spec.get("snapshot_sha256") != file_sha256(snapshot_path):
            raise ValueError(f"Factorial cell {cell} snapshot file hash mismatch")
        _, snapshot, _, memory_state_sha256 = _verified_source_snapshot(
            snapshot_path,
            expected_role=str(role),
            expected_program_path=program_path,
        )
        if spec.get("snapshot_id") != snapshot.get("snapshot_id"):
            raise ValueError(f"Factorial cell {cell} snapshot identity mismatch")
        if spec.get("memory_state_sha256") != memory_state_sha256:
            raise ValueError(f"Factorial cell {cell} Memory state mismatch")

        rebind = snapshot.get("component_rebind")
        if not isinstance(rebind, dict):
            raise ValueError(f"Factorial cell {cell} lacks rebind provenance")
        source_snapshot_path = (
            Path(str(spec.get("source_snapshot_path") or "")).expanduser().resolve()
        )
        memory_program_source = (assembly.get("memory_source") or {}).get("path")
        verified_source_path, source_snapshot, _, source_state_sha256 = (
            _verified_source_snapshot(
                source_snapshot_path,
                expected_role=str(role),
                expected_program_path=str(memory_program_source or ""),
            )
        )
        source_sha256 = file_sha256(verified_source_path)
        if (
            spec.get("source_snapshot_sha256") != source_sha256
            or spec.get("source_snapshot_id") != source_snapshot.get("snapshot_id")
            or rebind.get("source_snapshot_path") != str(verified_source_path)
            or rebind.get("source_snapshot_sha256") != source_sha256
            or rebind.get("source_snapshot_id") != source_snapshot.get("snapshot_id")
            or rebind.get("memory_state_sha256") != source_state_sha256
            or source_state_sha256 != memory_state_sha256
        ):
            raise ValueError(f"Factorial cell {cell} source snapshot identity mismatch")
        normalized[cell] = {
            "memory_ast_sha256": manifest["memory_ast_sha256"],
            "policy_ast_sha256": manifest["policy_ast_sha256"],
            "memory_state_sha256": memory_state_sha256,
        }

    y00 = normalized["baseline_memory__baseline_s2_policy"]
    y10 = normalized["evolved_memory__baseline_s2_policy"]
    y01 = normalized["baseline_memory__evolved_s2_policy"]
    y11 = normalized["evolved_memory__evolved_s2_policy"]
    if not (
        y00["memory_ast_sha256"] == y01["memory_ast_sha256"]
        and y10["memory_ast_sha256"] == y11["memory_ast_sha256"]
        and y00["memory_state_sha256"] == y01["memory_state_sha256"]
        and y10["memory_state_sha256"] == y11["memory_state_sha256"]
    ):
        raise ValueError("Factorial cells do not preserve paired Memory state")
    if not (
        y00["policy_ast_sha256"] == y10["policy_ast_sha256"]
        and y01["policy_ast_sha256"] == y11["policy_ast_sha256"]
    ):
        raise ValueError("Factorial cells do not preserve paired S2 policy")
    return value
