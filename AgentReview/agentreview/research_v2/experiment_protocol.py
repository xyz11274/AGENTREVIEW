"""Frozen shared-memory experiment conditions and purpose-scoped data access.

Development validates sealed-test identity metadata. Test paper text, reference
reviews, and retrieval assets stay sealed until an explicit system-freeze receipt
authorizes final evaluation. Experimental sizes and budgets come from JSON.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ModelConfig
from .data import canonical_title
from .io import atomic_write_json, file_sha256, read_json, read_jsonl, value_sha256
from .models import Paper

EXPERIMENT_PROTOCOL = "shared-memory-experiment-v1"
SPLIT_PROTOCOL = "shared-memory-split-v1"
SPLIT_NAMES = ("build", "static", "rotating", "sealed_test")
PURPOSE_SPLITS = {
    "memory_build": "build",
    "selection": "static",
    "reflection": "rotating",
    "final_test": "sealed_test",
}


class ExperimentProtocolError(ValueError):
    """A shared experiment requires complete and consistent frozen conditions."""

    failure_category = "shared_data"


def _object(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ExperimentProtocolError(f"{name} requires an object")


def _text(value: Any, name: str) -> str:
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    raise ExperimentProtocolError(f"{name} requires a nonempty trimmed string")


def _integer(value: Any, name: str, minimum: int = 1) -> int:
    if type(value) is int and value >= minimum:
        return value
    raise ExperimentProtocolError(f"{name} requires an integer >= {minimum}")


def _digest(value: Any, name: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    raise ExperimentProtocolError(f"{name} requires a SHA-256 digest")


def _choice(value: Any, allowed: set[str], name: str) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    raise ExperimentProtocolError(f"{name} requires one of {sorted(allowed)}")


def normalized_content_sha256(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _reference(value: Any, root: Path, name: str, *, read: bool) -> dict[str, str]:
    ref = _object(value, name)
    raw = Path(_text(ref.get("path"), f"{name}.path")).expanduser()
    path = (root / raw).resolve() if raw.is_absolute() is False else raw.resolve()
    expected = _digest(ref.get("sha256"), f"{name}.sha256")
    if read and file_sha256(path) != expected:
        raise ExperimentProtocolError(f"{name} file identity differs from its frozen digest: {path}")
    return {"path": str(path), "sha256": expected}


def _identity(row: Any, name: str) -> dict[str, Any]:
    value = dict(_object(row, name))
    for field in ("paper_id", "title", "work_family"):
        _text(value.get(field), f"{name}.{field}")
    for field in ("content_sha256", "normalized_content_sha256"):
        _digest(value.get(field), f"{name}.{field}")
    versions = value.get("version_ids")
    if isinstance(versions, list) and len(versions) > 0:
        for version in versions:
            _text(version, f"{name}.version_ids")
        if len(set(versions)) == len(versions) and value["paper_id"] in versions:
            return value
    raise ExperimentProtocolError(f"{name}.version_ids requires unique study-version IDs including paper_id")


def _identity_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        ("id", row["paper_id"]),
        ("title", canonical_title(row["title"])),
        ("content", row["content_sha256"]),
        ("normalized_content", row["normalized_content_sha256"]),
        ("family", row["work_family"]),
        *(("id", version) for version in row["version_ids"]),
    }


def _validate_paper_asset(row: dict[str, Any], split: str, name: str) -> Paper:
    asset = row["assets"]["paper"]
    _reference(asset, Path("/"), f"{name}.assets.paper", read=True)
    paper = Paper.from_dict(read_json(asset["path"]))
    actual = {
        "paper_id": paper.paper_id,
        "title": canonical_title(paper.title),
        "content_sha256": hashlib.sha256(paper.text.encode("utf-8")).hexdigest(),
        "normalized_content_sha256": normalized_content_sha256(paper.text),
    }
    expected = {key: row[key] for key in actual}
    expected["title"] = canonical_title(row["title"])
    if actual == expected:
        pass
    else:
        raise ExperimentProtocolError(f"{name} identity differs from its frozen paper asset")
    if paper.source.get("source_partition") == split:
        return paper
    raise ExperimentProtocolError(f"{name} paper source_partition requires {split}")


def _validate_s2_asset(ref: dict[str, str], split: str, splits: dict[str, list[dict[str, Any]]]) -> None:
    _reference(ref, Path("/"), f"frozen_s2.{split}", read=True)
    rows = read_jsonl(ref["path"])
    paper_ids = [row.get("paper_id") for row in rows]
    expected_ids = {row["paper_id"] for row in splits[split]}
    if len(paper_ids) == len(expected_ids) and set(paper_ids) == expected_ids:
        pass
    else:
        raise ExperimentProtocolError(f"frozen_s2.{split} requires exactly its split paper IDs")
    other_partition_keys = {
        key
        for name, identities in splits.items() if name != split
        for row in identities
        for key in _identity_keys(row)
    }
    for row in rows:
        if row.get("source_partition") == split and row.get("contains_human_review_data") is False:
            pass
        else:
            raise ExperimentProtocolError(f"frozen_s2.{split} requires source_partition={split} and contains_human_review_data=false")
        evidence_lists = [row[key] for key in ("retrieved_papers", "papers", "results") if key in row]
        if len(evidence_lists) > 0 and all(isinstance(items, list) for items in evidence_lists):
            pass
        else:
            raise ExperimentProtocolError(f"frozen_s2.{split} requires explicit retrieval result lists")
        for evidence in (value for items in evidence_lists for value in items):
            value = _object(evidence, f"frozen_s2.{split}.evidence")
            keys = set()
            for field in ("paperId", "paper_id", "id"):
                if isinstance(value.get(field), str) and value[field]:
                    keys.add(("id", value[field]))
            for field in ("externalIds", "external_ids"):
                external = value.get(field, {})
                if isinstance(external, dict):
                    for source, identifier in external.items():
                        if isinstance(identifier, str) and identifier:
                            keys.add(("id", identifier))
                            keys.add(("id", f"{str(source).casefold()}:{identifier}"))
            if isinstance(value.get("title"), str):
                keys.add(("title", canonical_title(value["title"])))
            for field, kind in (("work_family", "family"), ("content_sha256", "content"), ("normalized_content_sha256", "normalized_content")):
                if isinstance(value.get(field), str):
                    keys.add((kind, value[field]))
            if keys & other_partition_keys:
                raise ExperimentProtocolError(f"frozen_s2.{split} evidence overlaps another experiment partition")


def _validate_model(value: Any) -> dict[str, Any]:
    model = dict(_object(value, "model"))
    try:
        return ModelConfig(**model).protocol_config()
    except (TypeError, ValueError) as exc:
        raise ExperimentProtocolError(f"Invalid API model configuration: {exc}") from exc


@dataclass(frozen=True)
class FrozenExperimentProtocol:
    path: Path
    document: dict[str, Any]
    protocol_sha256: str
    splits: dict[str, list[dict[str, Any]]]

    def records(self, split: str) -> list[dict[str, Any]]:
        _choice(split, set(SPLIT_NAMES), "split")
        # Callers receive copies so runtime preparation keeps the manifest frozen.
        import copy
        return copy.deepcopy(self.splits[split])

    def paper_paths(
        self,
        split: str,
        *,
        purpose: str,
        frozen_system_sha256: str | None = None,
    ) -> list[Path]:
        expected_split = PURPOSE_SPLITS.get(purpose)
        if expected_split == split:
            pass
        else:
            raise ExperimentProtocolError(f"Purpose {purpose!r} requires split {expected_split!r}")
        if split == "sealed_test":
            _digest(frozen_system_sha256, "final_test.frozen_system_sha256")
        return [Path(row["assets"]["paper"]["path"]) for row in self.records(split)]

    def reference_assets(self, split: str, *, purpose: str, frozen_system_sha256: str | None = None) -> list[dict[str, str]]:
        self.paper_paths(split, purpose=purpose, frozen_system_sha256=frozen_system_sha256)
        if split in {"static", "rotating", "sealed_test"}:
            return [row["assets"]["coco_reference"] for row in self.records(split)]
        raise ExperimentProtocolError("CoCo reference access requires an evaluation split")

    def freeze(self, path: str | Path) -> Path:
        return atomic_write_json(path, {
            "protocol": "shared-memory-experiment-receipt-v1",
            "protocol_sha256": self.protocol_sha256,
            "config_path": str(self.path),
            "document": self.document,
            "split_identities": self.splits,
        })

    def validate_final_test_assets(self, *, frozen_system_sha256: str) -> None:
        """Open final assets after the caller verifies the frozen system receipt."""
        _digest(frozen_system_sha256, "final_test.frozen_system_sha256")
        rows = self.records("sealed_test")
        for index, row in enumerate(rows):
            name = f"splits.sealed_test.records[{index}]"
            for asset_name, asset in row["assets"].items():
                _reference(asset, Path("/"), f"{name}.assets.{asset_name}", read=True)
            _validate_paper_asset(row, "sealed_test", name)
        references = [row["assets"]["coco_reference"] for row in rows]
        reference = read_json(references[0]["path"])
        expected_ids = {row["paper_id"] for row in rows}
        if all(ref == references[0] for ref in references) and reference.get("partition") == "sealed_test" and set(reference.get("paper_ids", [])) == expected_ids:
            pass
        else:
            raise ExperimentProtocolError("Final test requires its exact partition-local CoCo reference manifest")
        conditions = self.document["non_memory_conditions"]
        if conditions["s2_mode"] == "frozen":
            _validate_s2_asset(conditions["frozen_s2"]["sealed_test"], "sealed_test", self.splits)


def load_experiment_protocol(path: str | Path) -> FrozenExperimentProtocol:
    source = Path(path).expanduser().resolve()
    document = dict(_object(read_json(source), "experiment"))
    if document.get("protocol") == EXPERIMENT_PROTOCOL and document.get("status") == "frozen":
        pass
    else:
        raise ExperimentProtocolError("experiment requires shared-memory-experiment-v1 and status=frozen")
    _text(document.get("experiment_id"), "experiment_id")
    model = _validate_model(document.get("model"))
    document["model"] = model
    generation = _object(document.get("generation"), "generation")
    _integer(generation.get("memory_build_llm_calls"), "generation.memory_build_llm_calls")
    if "paper_context_max_chars" in generation and generation["paper_context_max_chars"] is None:
        pass
    else:
        _integer(generation.get("paper_context_max_chars"), "generation.paper_context_max_chars")
    _integer(generation.get("memory_read_llm_calls"), "generation.memory_read_llm_calls")
    _integer(generation.get("native_num_fewshot"), "generation.native_num_fewshot", 0)
    if generation["native_num_fewshot"] == 0:
        pass
    else:
        raise ExperimentProtocolError(
            "generation.native_num_fewshot requires 0 in the current frozen protocol; "
            "positive counts require a partition-audited few-shot study identity inventory"
        )
    _choice(generation.get("memory_query_mode"), {"deterministic", "instruction"}, "generation.memory_query_mode")
    evolution = _object(document.get("evolution"), "evolution")
    for field, minimum in (("attempts", 0), ("rotating_sample_size", 1), ("random_seed", 0)):
        _integer(evolution.get(field), f"evolution.{field}", minimum)
    temperature = evolution.get("parent_temperature")
    if type(temperature) in {int, float} and math.isfinite(temperature) and temperature > 0:
        pass
    else:
        raise ExperimentProtocolError("evolution.parent_temperature requires a finite positive number")
    _choice(evolution.get("mutable_scope"), {"memory"}, "evolution.mutable_scope")
    _choice(evolution.get("reflection_scope"), {"scores_and_traces"}, "evolution.reflection_scope")
    failure = _object(document.get("failure_policy"), "failure_policy")
    for field in ("candidate_repairs", "round_repairs", "candidate_failures"):
        _integer(failure.get(field), f"failure_policy.{field}")
    _integer(failure.get("request_attempts"), "failure_policy.request_attempts")
    if "consecutive_candidate_failures" in failure:
        _integer(failure["consecutive_candidate_failures"], "failure_policy.consecutive_candidate_failures")
    if "random_streams_protocol" in evolution:
        _choice(evolution["random_streams_protocol"], {"sha256-separated-python-random-v1"}, "evolution.random_streams_protocol")
        _integer(evolution.get("detailed_reflection_cases"), "evolution.detailed_reflection_cases")
        if evolution["detailed_reflection_cases"] > evolution["rotating_sample_size"]:
            raise ExperimentProtocolError("Detailed cases cannot exceed rotating sample size")
    document["scoring_config"] = _reference(document.get("scoring_config"), source.parent, "scoring_config", read=True)
    embedding = _object(document.get("embedding"), "embedding")
    for field in ("provider", "model", "revision", "cache_path"):
        _text(embedding.get(field), f"embedding.{field}")
    if isinstance(embedding.get("device"), str):
        pass
    else:
        raise ExperimentProtocolError("embedding.device requires an explicit string")
    non_memory = _object(document.get("non_memory_conditions"), "non_memory_conditions")
    _choice(non_memory.get("final_mode"), {"meta", "concat"}, "non_memory_conditions.final_mode")
    _choice(non_memory.get("s2_mode"), {"frozen", "off"}, "non_memory_conditions.s2_mode")
    non_memory["review_policy_program"] = _reference(non_memory.get("review_policy_program"), source.parent, "non_memory_conditions.review_policy_program", read=True)
    if "initial_memory_programs" in document:
        seeds = _object(document["initial_memory_programs"], "initial_memory_programs")
        if set(seeds) != {"vector_search", "llm_summarizer", "experience_learner"}:
            raise ExperimentProtocolError("Initial programs require exactly the three original memory seed families")
        document["initial_memory_programs"] = {
            name: _reference(ref, source.parent, f"initial_memory_programs.{name}", read=True)
            for name, ref in seeds.items()}
    dedup = _object(document.get("deduplication"), "deduplication")
    _choice(dedup.get("policy"), {"identity-family-normalized-content-v1"}, "deduplication.policy")
    dedup["family_audit"] = _reference(dedup.get("family_audit"), source.parent, "deduplication.family_audit", read=True)
    audit = _object(read_json(dedup["family_audit"]["path"]), "family_audit")
    if audit.get("status") == "complete":
        pass
    else:
        raise ExperimentProtocolError("family_audit requires status=complete")
    _text(audit.get("method"), "family_audit.method")
    split_refs = _object(document.get("splits"), "splits")
    if set(split_refs) == set(SPLIT_NAMES):
        pass
    else:
        raise ExperimentProtocolError(f"splits requires exactly {SPLIT_NAMES}")
    splits: dict[str, list[dict[str, Any]]] = {}
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    design = document.get("evaluation_design", {})
    retain_history = design.get("historical_overlap_policy") == "retain-label-no-test-feedback"
    if retain_history:
        if (design.get("strictly_unseen_claim") is not False or design.get("test_feedback_to_search") is not False
                or design.get("all_arms_frozen_before_final") is not True):
            raise ExperimentProtocolError("Existing-benchmark diagnostics require explicit non-blind and no-test-feedback boundaries")
    for split in SPLIT_NAMES:
        ref = _reference(split_refs[split], source.parent, f"splits.{split}", read=True)
        split_refs[split] = ref
        manifest_path = Path(ref["path"])
        manifest = _object(read_json(manifest_path), f"splits.{split}.manifest")
        if manifest.get("protocol") == SPLIT_PROTOCOL and manifest.get("split") == split:
            pass
        else:
            raise ExperimentProtocolError(f"splits.{split} requires its named {SPLIT_PROTOCOL} manifest")
        rows = manifest.get("records")
        if isinstance(rows, list) and len(rows) > 0:
            pass
        else:
            raise ExperimentProtocolError(f"splits.{split}.records requires an explicit nonempty identity list")
        normalized = []
        split_paper_ids = set()
        for index, raw_row in enumerate(rows):
            name = f"splits.{split}.records[{index}]"
            row = _identity(raw_row, name)
            if row["paper_id"] in split_paper_ids:
                raise ExperimentProtocolError("A split cannot duplicate the same paper ID")
            split_paper_ids.add(row["paper_id"])
            if row.get("source_partition") == split:
                pass
            else:
                raise ExperimentProtocolError(f"{name}.source_partition requires {split}")
            for key in _identity_keys(row):
                if key in seen:
                    previous = seen[key]
                    if not (retain_history and split == previous[0] == "sealed_test"):
                        raise ExperimentProtocolError(f"Data identity overlap ({key[0]}): {previous} and {(split, row['paper_id'])}")
                seen[key] = (split, row["paper_id"])
            assets = _object(row.get("assets"), f"{name}.assets")
            required_assets = {"paper"} if split == "build" else {"paper", "coco_reference"}
            if required_assets <= set(assets):
                pass
            else:
                raise ExperimentProtocolError(f"{name}.assets requires {sorted(required_assets)}")
            row["assets"] = {
                asset_name: _reference(asset, manifest_path.parent, f"{name}.assets.{asset_name}", read=split != "sealed_test")
                for asset_name, asset in assets.items()
            }
            if split != "sealed_test":
                _validate_paper_asset(row, split, name)
            normalized.append(row)
        splits[split] = normalized
    audit_manifest_hashes = audit.get("split_manifest_sha256")
    if audit_manifest_hashes == {name: split_refs[name]["sha256"] for name in SPLIT_NAMES}:
        pass
    else:
        raise ExperimentProtocolError("family_audit requires the exact frozen split_manifest_sha256 mapping")
    history = document.get("historical_exposure_manifests")
    if isinstance(history, list) and len(history) > 0:
        pass
    else:
        raise ExperimentProtocolError("historical_exposure_manifests requires the previously exposed identity lists")
    sealed_keys = {key for row in splits["sealed_test"] for key in _identity_keys(row)}
    history_records = 0
    for index, value in enumerate(history):
        ref = _reference(value, source.parent, f"historical_exposure_manifests[{index}]", read=True)
        history[index] = ref
        manifest = _object(read_json(ref["path"]), f"historical_exposure_manifests[{index}]")
        rows = manifest.get("records")
        if isinstance(rows, list):
            pass
        else:
            raise ExperimentProtocolError("Historical exposure manifests require records")
        for row_index, raw_row in enumerate(rows):
            row = _identity(raw_row, f"history[{index}].records[{row_index}]")
            history_records += 1
            overlap = _identity_keys(row) & sealed_keys
            if overlap and not retain_history:
                raise ExperimentProtocolError(f"Sealed test overlaps historical exposure for {row['paper_id']}: {sorted(overlap)}")
    if retain_history:
        basis = _reference(design.get("original_evaluation_basis"), source.parent, "original_evaluation_basis", read=True)
        original = read_json(basis["path"])
        ids = original.get("paper_ids") or [row["paper_id"] for row in original["records"]]
        if ids != [row["paper_id"] for row in splits["sealed_test"]]:
            raise ExperimentProtocolError("Diagnostic final cohort must retain the entire original ordered evaluation manifest")
        _reference(design.get("historical_use_annotations"), source.parent, "historical_use_annotations", read=True)
    if history_records > 0:
        pass
    else:
        raise ExperimentProtocolError("Historical exposure validation requires a populated identity inventory")
    if evolution["rotating_sample_size"] <= len(splits["rotating"]):
        pass
    else:
        raise ExperimentProtocolError("evolution.rotating_sample_size exceeds the frozen rotating pool")
    s2 = _object(non_memory.get("frozen_s2"), "non_memory_conditions.frozen_s2")
    expected_s2 = set(SPLIT_NAMES) if non_memory["s2_mode"] == "frozen" else set()
    if set(s2) == expected_s2:
        for split in s2:
            s2[split] = _reference(s2[split], source.parent, f"frozen_s2.{split}", read=split != "sealed_test")
            if split != "sealed_test":
                _validate_s2_asset(s2[split], split, splits)
    else:
        raise ExperimentProtocolError("frozen_s2 requires one asset per split for frozen mode, or an empty object for off mode")
    return FrozenExperimentProtocol(source, document, value_sha256(document), splits)


def pending_experiment_template() -> dict[str, Any]:
    """Produce a reviewable template; unresolved fields keep execution pending."""
    return {
        "protocol": EXPERIMENT_PROTOCOL,
        "status": "pending",
        "experiment_id": None,
        "model": {**ModelConfig().protocol_config(), "api_key_env": "ARK_API_KEY", "temperature": None, "enable_thinking": None},
        "generation": {name: None for name in (
            "paper_context_max_chars", "memory_build_llm_calls",
            "memory_read_llm_calls", "memory_query_mode", "native_num_fewshot",
        )},
        "evolution": {
            "attempts": None, "rotating_sample_size": None, "parent_temperature": None,
            "random_seed": None,
            "mutable_scope": "memory", "reflection_scope": "scores_and_traces",
        },
        "failure_policy": {"candidate_repairs": None, "round_repairs": None, "candidate_failures": None, "request_attempts": None},
        "scoring_config": {"path": None, "sha256": None},
        "embedding": {"provider": None, "model": None, "revision": None, "cache_path": None, "device": None},
        "non_memory_conditions": {
            "final_mode": None, "s2_mode": None,
            "review_policy_program": {"path": None, "sha256": None}, "frozen_s2": {},
        },
        "splits": {name: {"path": None, "sha256": None} for name in SPLIT_NAMES},
        "historical_exposure_manifests": [],
        "deduplication": {
            "policy": "identity-family-normalized-content-v1",
            "family_audit": {"path": None, "sha256": None},
        },
    }
