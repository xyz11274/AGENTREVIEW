"""One-call generation clients with explicit frozen decoding parameters."""

from __future__ import annotations

import os
from typing import Any, Protocol

from .config import ModelConfig


class EmptyCompletionOutput(RuntimeError):
    """The endpoint returned no usable assistant text for one request."""


class CompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        component: str,
    ) -> str: ...


class OpenAICompletionClient:
    def __init__(self, config: ModelConfig):
        from openai import OpenAI

        self.config = config
        api_key = os.environ.get(config.api_key_env) or "local-not-required"
        self.client = OpenAI(
            base_url=config.base_url,
            api_key=api_key,
            max_retries=0,
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        component: str,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=max_tokens,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": self.config.enable_thinking,
                }
            },
        )
        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise EmptyCompletionOutput(f"{component} returned an empty response")
        return text.strip()


class ToolkitCompletionAdapter:
    """Expose a Research v2 client through M★ Toolkit's completion method."""

    def __init__(self, client: CompletionClient, max_tokens: int, component: str):
        self.client = client
        self.max_tokens = max_tokens
        self.component = component

    def llm_completion(self, messages: list[dict[str, str]], **_: Any) -> str:
        return self.client.complete(
            messages,
            max_tokens=self.max_tokens,
            component=self.component,
        )
