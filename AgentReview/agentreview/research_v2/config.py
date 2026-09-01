"""Explicit local configuration with one frozen generative model identity."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_ARTIFACT_ROOT,
    ENABLE_THINKING,
    MODEL_ALIAS,
    MODEL_PRECISION,
    MODEL_REVISION,
    MODEL_SOURCE,
    TEMPERATURE,
)


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = "http://127.0.0.1:18001/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = MODEL_ALIAS
    source_model: str = MODEL_SOURCE
    revision: str = MODEL_REVISION
    precision: str = MODEL_PRECISION
    temperature: float = TEMPERATURE
    enable_thinking: bool = ENABLE_THINKING
    vllm_version: str = "0.20.1+cu129"
    transformers_version: str = "5.8.1"
    tokenizer_sha256: str = "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3"
    chat_template_sha256: str = "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041"
    max_model_len: int = 65536
    max_num_seqs: int = 8

    def identity(self) -> dict[str, Any]:
        """Return generation identity without endpoint or scheduling capacity."""
        value = asdict(self)
        for name in ("base_url", "api_key_env", "max_num_seqs"):
            value.pop(name)
        return value

    def runtime_config(self) -> dict[str, Any]:
        """Return connection and capacity settings that may change across resumes."""
        return {
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "max_num_seqs": self.max_num_seqs,
        }


@dataclass(frozen=True)
class ResearchConfig:
    artifact_root: str = str(DEFAULT_ARTIFACT_ROOT)
    train_clean_path: str = "/data0/xyj/shengao/OpenReview/train_clean.jsonl"
    paper_asset_root: str = "/data0/xyj/shengao/OpenReview/home/hexuan/AIReviewBenchmark"
    coco_root: str = "/data0/xyj/shengao/CoCoReviewBench"
    frozen_s2_path: str = ""
    frozen_coco_s2_path: str = ""
    embedding_provider: str = "qwen3_embedding"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_revision: str = "1d8ad4ca9b3dd8059ad90a75d4983776a23d44af"
    embedding_cache_path: str = "cache/research_v2_qwen3_embedding.sqlite3"
    embedding_device: str = ""
    split_seed: int = 0
    require_bubblewrap: bool = True
    model: ModelConfig = field(default_factory=ModelConfig)

    @classmethod
    def load(cls, path: str | Path | None) -> "ResearchConfig":
        if path is None:
            return cls()
        value = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Research config must be a JSON object")
        model = ModelConfig(**dict(value.pop("model", {}) or {}))
        return cls(model=model, **value)

    def artifact_path(self) -> Path:
        return Path(self.artifact_root).expanduser().resolve()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def embedding_identity(self) -> dict[str, Any]:
        return {
            "provider": self.embedding_provider,
            "model": self.embedding_model,
            "revision": self.embedding_revision,
            "cache_path": str(Path(self.embedding_cache_path).expanduser().resolve()),
        }
