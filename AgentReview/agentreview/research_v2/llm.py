"""One-call generation clients with explicit frozen decoding parameters."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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

    def count_chat_tokens(
        self, messages: list[dict[str, str]]
    ) -> dict[str, int]:
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": messages,
                "add_generation_prompt": True,
                "chat_template_kwargs": {
                    "enable_thinking": self.config.enable_thinking,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/tokenize",
            data=payload,
            headers={
                "Authorization": "Bearer "
                + (os.environ.get(self.config.api_key_env) or "local-not-required"),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30.0) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Frozen model tokenization endpoint failed: {type(exc).__name__}: {exc}"
            ) from exc
        count = value.get("count") if isinstance(value, dict) else None
        max_model_len = value.get("max_model_len") if isinstance(value, dict) else None
        if not isinstance(count, int) or not isinstance(max_model_len, int):
            raise RuntimeError("Frozen model tokenization response is invalid")
        return {"input_tokens": count, "max_model_len": max_model_len}

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
