"""Explicit API configuration with one frozen generative model identity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from urllib.parse import urlparse
import math



@dataclass(frozen=True)
class ModelConfig:
    """The single Ark Chat Completions contract used by this version."""
    base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3"
    api_key_env: str = "ARK_API_KEY"
    model: str = "deepseek-v4.1-flash"
    response_model: str = "deepseek-v4-1-flash-260910"
    temperature: float = 0.0
    enable_thinking: bool = False
    reasoning_effort: str = "high"
    max_model_len: int = 1048576
    max_output_tokens: int = 393216
    timeout_seconds: float = 600.0

    def __post_init__(self):
        endpoint = urlparse(self.base_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError("base_url requires an HTTP(S) API endpoint without credentials or query")
        for name in ("model", "response_model", "api_key_env"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} requires a nonempty string")
        for name in ("max_model_len", "max_output_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} requires a positive integer")
        if self.max_output_tokens > self.max_model_len:
            raise ValueError("Output capacity exceeds model context")
        if type(self.temperature) not in (int, float) or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("Ark temperature must be in [0, 2]")
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Request timeout must be positive and finite")
        if type(self.enable_thinking) is not bool or self.reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("Explicit thinking boolean and Ark reasoning effort required")

    def identity(self) -> dict[str, Any]:
        from .token_accounting import tokenizer_identity
        value = asdict(self)
        for name in ("api_key_env", "timeout_seconds"):
            value.pop(name)
        return {**value, "token_accounting": tokenizer_identity()}

    def runtime_config(self) -> dict[str, Any]:
        return {"api_key_env": self.api_key_env,
                "timeout_seconds": self.timeout_seconds}

    def protocol_config(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchConfig:
    embedding_provider: str = "qwen3_embedding"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_revision: str = "1d8ad4ca9b3dd8059ad90a75d4983776a23d44af"
    embedding_cache_path: str = "cache/research_v2_qwen3_embedding.sqlite3"
    embedding_device: str = ""
    model: ModelConfig = field(default_factory=ModelConfig)
