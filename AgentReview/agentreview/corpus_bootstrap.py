"""Model-free materialization for the authoritative Corpus bootstrap stage.

The module owns A-input validation, exact production request construction,
token accounting and the CORPUS_BUILD_RUNTIME_PROFILE derivation.  It never
calls a model and has no Memory, Evidence, Candidate, Human or CoCo interface.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .review_corpus import ROLE_SUBDIMENSIONS, canonical_json_bytes
from .review_corpus_builder import (
    BUILD_SCHEMA_VERSION,
    ROLE_ORDER,
    CorpusPaper,
    CorpusReviewerRequest,
    build_corpus_reviewer_request,
    default_role_system_prompts,
)


BOOTSTRAP_SCHEMA_VERSION = "research-validity-core-v1-corpus-bootstrap-v1"
CORPUS_REQUEST_CLASS = "CANONICAL_FULL_REVIEW_CORPUS_GENERATION"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(identity_type: str, payload: Any) -> str:
    return _sha_bytes(
        canonical_json_bytes(
            {
                "identity_type": identity_type,
                "schema_version": BOOTSTRAP_SCHEMA_VERSION,
                "payload": payload,
            }
        )
    )


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"JSONL record must be an object at {path}:{line_number}")
            yield value


def selected_paper_ids(selection_path: str | Path) -> Tuple[str, ...]:
    path = Path(selection_path).expanduser().resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    raw_ids = (
        value.get("selected_episode_ids")
        or value.get("episode_ids")
        or value.get("paper_ids")
    )
    if not isinstance(raw_ids, list):
        raise ValueError("A selection lacks selected_episode_ids/episode_ids/paper_ids")
    output = []
    for raw in raw_ids:
        paper_id = str(raw).split("::", 1)[0]
        if paper_id and paper_id not in output:
            output.append(paper_id)
    if len(output) != 125:
        raise ValueError(f"Frozen A selection must contain 125 unique papers, got {len(output)}")
    return tuple(output)


def canonical_context(episode: Mapping[str, Any]) -> str:
    text = str(episode.get("text") or "")
    forbidden = (
        "SELF-GENERATED REVIEW EXPERIENCE",
        "Human Review",
        "OpenReview reference",
        "CoCo score",
    )
    found = [marker for marker in forbidden if marker in text]
    if found:
        raise ValueError(f"A Corpus input contains forbidden material: {found}")
    marker = "Context protocol:"
    start = text.find(marker)
    if start < 0:
        raise ValueError("A episode lacks canonical paper context")
    value = text[start:]
    suffix = "\n\nReturn the complete "
    if suffix in value:
        value = value.split(suffix, 1)[0]
    if not value.strip():
        raise ValueError("A canonical paper context is empty")
    return value


@dataclass(frozen=True)
class CorpusBootstrapRequestRecord:
    request_class: str
    role: str
    paper_id: str
    paper_ordinal: int
    messages: Tuple[Dict[str, str], ...]
    messages_sha256: str
    rendered_prompt_sha256: str
    prompt_token_count: int
    requested_output_token_cap: int
    maximum_request_tokens: int
    expected_response_schema: Mapping[str, Any]
    source_artifact_identities: Mapping[str, str]
    tokenizer_identity: str
    request_identity_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["messages"] = [dict(message) for message in self.messages]
        value["expected_response_schema"] = dict(self.expected_response_schema)
        value["source_artifact_identities"] = dict(self.source_artifact_identities)
        return value


@dataclass(frozen=True)
class CorpusBootstrapMaterialization:
    a_manifest: Mapping[str, Any]
    requests: Tuple[CorpusBootstrapRequestRecord, ...]
    request_manifest: Mapping[str, Any]
    runtime_profile: Mapping[str, Any]

    def write(self, directory: str | Path) -> None:
        target = Path(directory).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        values = {
            "a_manifest.json": canonical_json_bytes(self.a_manifest),
            "corpus_request_materialization.jsonl": b"".join(
                canonical_json_bytes(record.to_dict()) + b"\n"
                for record in self.requests
            ),
            "corpus_request_manifest.json": canonical_json_bytes(self.request_manifest),
            "corpus_build_runtime_profile.json": canonical_json_bytes(
                self.runtime_profile
            ),
        }
        for name, content in values.items():
            path = target / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)


def model_tokenizer_identity(model_path: str | Path) -> Dict[str, Any]:
    root = Path(model_path).expanduser().resolve()
    required = (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "model.safetensors.index.json",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Model/tokenizer assets missing: {missing}")
    files = {
        name: {
            "byte_length": (root / name).stat().st_size,
            "sha256": _sha_file(root / name),
        }
        for name in required
    }
    weight_files = []
    for path in sorted(root.glob("*.safetensors")):
        weight_files.append({"name": path.name, "byte_length": path.stat().st_size})
    weight_manifest = {
        "index_sha256": files["model.safetensors.index.json"]["sha256"],
        "files": weight_files,
    }
    return {
        "model_path": str(root),
        "files": files,
        "weight_manifest_sha256": _identity(
            "corpus_model_weight_manifest", weight_manifest
        ),
        "model_artifact_identity_sha256": _identity(
            "corpus_model_artifact", {"files": files, "weights": weight_manifest}
        ),
        "tokenizer_identity_sha256": _identity(
            "corpus_tokenizer",
            {
                name: files[name]
                for name in (
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "chat_template.jinja",
                )
            },
        ),
    }


def _expected_schema(role: str) -> Dict[str, Any]:
    return {
        "closed_object_fields": [
            "role",
            "paper_id",
            "subdimension_sections",
            "overall_assessment",
        ],
        "role": role,
        "subdimension_order": list(ROLE_SUBDIMENSIONS[role]),
        "section_fields": [
            "subdimension_id",
            "review_text",
            "paper_anchor_ids",
        ],
        "overall_assessment": "string_or_null",
        "parser_schema_version": BUILD_SCHEMA_VERSION,
    }


def materialize_corpus_bootstrap(
    *,
    episodes_root: str | Path,
    selection_path: str | Path,
    model_path: str | Path,
    tokenizer: Any,
    output_token_cap: int,
    model_revision: str,
    served_model_name: str,
    model_context_limit: int,
    gpu_memory_utilization: float = 0.95,
    role_system_prompts: Mapping[str, str] | None = None,
) -> CorpusBootstrapMaterialization:
    """Materialize all 625 exact requests and derive the bootstrap profile."""

    if int(output_token_cap) <= 0:
        raise ValueError("Corpus output token cap must be positive")
    if not 0 < float(gpu_memory_utilization) <= 1:
        raise ValueError("Corpus gpu-memory-utilization must be in (0, 1]")
    identities = model_tokenizer_identity(model_path)
    prompts = dict(role_system_prompts or default_role_system_prompts())
    if set(prompts) != set(ROLE_ORDER):
        raise ValueError("Corpus role system prompt set mismatch")
    selection = Path(selection_path).expanduser().resolve()
    qual_episodes = Path(episodes_root).expanduser().resolve() / "QUAL" / "episodes.jsonl"
    ids = selected_paper_ids(selection)
    requested = set(ids)
    by_id: Dict[str, Dict[str, Any]] = {}
    for episode in _read_jsonl(qual_episodes):
        paper_id = str(episode.get("id") or "").split("::", 1)[0]
        if paper_id in requested:
            by_id[paper_id] = episode
    missing = sorted(requested - set(by_id))
    if missing:
        raise ValueError(f"Selected A papers missing from episodes: {missing}")
    source_identities = {
        "selection_sha256": _sha_file(selection),
        "qual_episodes_sha256": _sha_file(qual_episodes),
    }
    paper_entries = []
    papers = []
    for ordinal, paper_id in enumerate(ids):
        episode = by_id[paper_id]
        metadata = dict(episode.get("metadata") or {})
        if metadata.get("temporal_zone") != "historical_experience":
            raise ValueError(f"Selected Corpus paper is not in frozen A: {paper_id}")
        context = canonical_context(episode)
        paper = CorpusPaper.from_context(
            paper_id=paper_id,
            canonical_paper_context=context,
            structural_context="source anchors embedded in canonical paper context",
        )
        papers.append(paper)
        paper_entries.append(
            {
                "ordinal": ordinal,
                "paper_id": paper_id,
                "paper_family": str(metadata.get("work_family") or ""),
                "paper_review_cutoff": str(metadata.get("paper_review_cutoff") or ""),
                "temporal_zone": str(metadata.get("temporal_zone") or ""),
                "paper_identity_sha256": paper.paper_identity_sha256,
                "structural_context_sha256": paper.structural_context_sha256,
            }
        )
    role_prompt_sha = {
        role: _sha_bytes(prompts[role].encode("utf-8")) for role in ROLE_ORDER
    }
    schema_identity = _identity(
        "canonical_full_review_output_schema",
        {role: _expected_schema(role) for role in ROLE_ORDER},
    )
    a_payload = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "zone": "historical_experience",
        "paper_count": len(paper_entries),
        "role_order": list(ROLE_ORDER),
        "expected_canonical_review_count": len(paper_entries) * len(ROLE_ORDER),
        "papers": paper_entries,
        "source_artifact_identities": source_identities,
        "selection_rule": "pre-registered frozen facility-location selection; preserve source order",
        "selection_fingerprint": json.loads(selection.read_text(encoding="utf-8")).get(
            "selection_fingerprint"
        ),
        "human_openreview_coco_reference_included": False,
    }
    a_manifest_sha = _identity("frozen_a_corpus_input_manifest", a_payload)
    a_manifest = {**a_payload, "a_manifest_sha256": a_manifest_sha}
    generation_payload = {
        "served_model_name": served_model_name,
        "model_artifact_identity_sha256": identities["model_artifact_identity_sha256"],
        "model_revision": model_revision,
        "tokenizer_identity_sha256": identities["tokenizer_identity_sha256"],
        "role_prompt_sha256": role_prompt_sha,
        "output_schema_identity_sha256": schema_identity,
        "request_json": {
            "model": served_model_name,
            "temperature": 0,
            "max_tokens": int(output_token_cap),
            "seed": 0,
            "top_p": 1.0,
            "n": 1,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "sampling_mode": "greedy_temperature_zero",
        "requested_output_token_cap": int(output_token_cap),
        "identical_protocol_retry_count": 1,
        "reasoning_effort": "not_sent_unsupported",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    generation_identity = _identity("corpus_generation_semantics", generation_payload)
    records = []
    for paper_ordinal, paper in enumerate(papers):
        for role in ROLE_ORDER:
            production: CorpusReviewerRequest = build_corpus_reviewer_request(
                paper,
                role,
                generation_identity_sha256=generation_identity,
                role_system_prompts=prompts,
            )
            messages = [dict(message) for message in production.messages]
            rendered = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            ).encode("utf-8")
            tokenized = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            if isinstance(tokenized, Mapping):
                input_ids = tokenized["input_ids"]
            else:
                input_ids = tokenized
            prompt_tokens = len(input_ids)
            records.append(
                CorpusBootstrapRequestRecord(
                    request_class=CORPUS_REQUEST_CLASS,
                    role=role,
                    paper_id=paper.paper_id,
                    paper_ordinal=paper_ordinal,
                    messages=production.messages,
                    messages_sha256=production.prompt_identity_sha256,
                    rendered_prompt_sha256=_sha_bytes(rendered),
                    prompt_token_count=prompt_tokens,
                    requested_output_token_cap=int(output_token_cap),
                    maximum_request_tokens=prompt_tokens + int(output_token_cap),
                    expected_response_schema=_expected_schema(role),
                    source_artifact_identities={
                        **source_identities,
                        "a_manifest_sha256": a_manifest_sha,
                        "paper_identity_sha256": paper.paper_identity_sha256,
                        "role_prompt_sha256": role_prompt_sha[role],
                        "output_schema_identity_sha256": schema_identity,
                        "generation_identity_sha256": generation_identity,
                    },
                    tokenizer_identity=identities["tokenizer_identity_sha256"],
                    request_identity_sha256=production.request_identity_sha256,
                )
            )
    if len(records) != 625:
        raise RuntimeError(f"Corpus request cardinality mismatch: {len(records)}")
    maximum = max(record.maximum_request_tokens for record in records)
    max_model_len = int(math.ceil(maximum / 8192.0) * 8192)
    if max_model_len > int(model_context_limit):
        raise RuntimeError(
            f"Corpus request envelope {maximum} exceeds model context {model_context_limit}"
        )
    worst = min(
        (record for record in records if record.maximum_request_tokens == maximum),
        key=lambda record: (record.paper_ordinal, ROLE_ORDER.index(record.role)),
    )
    distribution = sorted(record.prompt_token_count for record in records)
    request_payload = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "request_count": len(records),
        "request_class": CORPUS_REQUEST_CLASS,
        "role_counts": {
            role: sum(record.role == role for record in records) for role in ROLE_ORDER
        },
        "tokenizer_identity_sha256": identities["tokenizer_identity_sha256"],
        "generation_identity_sha256": generation_identity,
        "requested_output_token_cap": int(output_token_cap),
        "prompt_token_distribution": {
            "min": distribution[0],
            "median": distribution[len(distribution) // 2],
            "p95": distribution[math.ceil(len(distribution) * 0.95) - 1],
            "max": distribution[-1],
        },
        "worst_case_request_identity_sha256": worst.request_identity_sha256,
        "worst_case_paper_id": worst.paper_id,
        "worst_case_role": worst.role,
        "required_context_tokens": maximum,
        "record_identities": [record.request_identity_sha256 for record in records],
    }
    request_manifest_sha = _identity(
        "corpus_request_materialization", request_payload
    )
    request_manifest = {
        **request_payload,
        "corpus_request_materialization_sha256": request_manifest_sha,
    }
    profile_payload = {
        "profile_name": "CORPUS_BUILD_RUNTIME_PROFILE",
        "stage": "CORPUS_BUILD_BOOTSTRAP",
        "tensor_parallel_size": 4,
        "max_num_seqs": 1,
        "model": identities,
        "model_revision": model_revision,
        "generation_semantics": generation_payload,
        "a_manifest_sha256": a_manifest_sha,
        "corpus_request_materialization_sha256": request_manifest_sha,
        "required_context_tokens": maximum,
        "corpus_build_max_model_len": max_model_len,
        "round_up_quantum_tokens": 8192,
        "rounding_safety_reserve_tokens": max_model_len - maximum,
        "model_context_limit": int(model_context_limit),
        "gpu_memory_utilization": format(float(gpu_memory_utilization), ".3f").rstrip("0").rstrip("."),
        "prefix_caching": True,
        "reasoning_parser": "qwen3",
        "gpu_topology": "one four-GPU PCIe/NUMA island",
        "no_memory": True,
        "external_evidence_enabled": False,
        "candidate_enabled": False,
        "protected_reference_access": False,
    }
    profile_sha = _identity("corpus_build_runtime_profile", profile_payload)
    runtime_profile = {
        **profile_payload,
        "corpus_build_runtime_profile_sha256": profile_sha,
    }
    return CorpusBootstrapMaterialization(
        a_manifest=a_manifest,
        requests=tuple(records),
        request_manifest=request_manifest,
        runtime_profile=runtime_profile,
    )
