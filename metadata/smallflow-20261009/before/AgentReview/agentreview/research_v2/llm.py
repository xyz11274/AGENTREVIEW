"""One-call generation clients with explicit frozen decoding parameters."""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import Any, Protocol

from .config import ModelConfig
from .request_admission import request_slot
from .token_accounting import count_chat_tokens, available_output_tokens
from .streaming import stream_completion
from .execution_telemetry import span, current_request


class EmptyCompletionOutput(RuntimeError):
    """The endpoint returned no usable assistant text for one request."""


class IncompleteCompletionOutput(RuntimeError):
    """A raw response is evidence, but is not usable downstream text."""

    def __init__(self, message: str, completion: dict[str, Any]):
        super().__init__(message)
        self.completion = completion


def validate_completion(response: Any, *, component: str, expected_model: str) -> dict[str, Any]:
    receipt = response.model_dump(mode="json")
    if receipt.get("model") != expected_model:
        raise IncompleteCompletionOutput(
            f"{component} returned model {receipt.get('model')!r}; expected {expected_model!r}", receipt)
    choices = receipt.get("choices", [])
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise IncompleteCompletionOutput(
            f"{component} requires one complete response; finish_reason="
            f"{[choice.get('finish_reason') for choice in choices]}", receipt)
    content = choices[0]["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        raise IncompleteCompletionOutput(f"{component} returned an empty response", receipt)
    return receipt


class CompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        component: str,
        response_format: dict[str, Any] | None = None,
    ) -> str: ...


def require_api_key(config: ModelConfig) -> str:
    key = os.environ.get(config.api_key_env, "").strip()
    if not key:
        raise ValueError(f"Missing API credential: set {config.api_key_env}")
    return key


def completion_payload(config, messages, *, max_tokens, response_format=None):
    if type(max_tokens) is not int or not 0 < max_tokens <= config.max_output_tokens:
        raise ValueError("Output budget exceeds the configured API output capacity")
    output = available_output_tokens(count_chat_tokens(messages, config), max_tokens)
    payload = {"model": config.model, "messages": messages,
               "temperature": config.temperature, "max_tokens": output,
               "stream": True, "stream_options": {"include_usage": True},
               "extra_body": {"thinking": {"type": "enabled" if config.enable_thinking else "disabled"}}}
    if config.enable_thinking:
        payload["reasoning_effort"] = config.reasoning_effort
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


class OpenAICompletionClient:
    def __init__(self, config: ModelConfig):
        from openai import OpenAI
        self.config = config
        self.client = OpenAI(base_url=config.base_url, api_key=require_api_key(config),
                             max_retries=0, timeout=config.timeout_seconds)
        from .transport_observer import observe_client
        observe_client(self.client)

    def close(self) -> None:
        self.client.close()

    def count_chat_tokens(self, messages):
        return count_chat_tokens(messages, self.config)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        component: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        self.last_completion = None
        request = completion_payload(self.config, messages, max_tokens=max_tokens,
                                     response_format=response_format)
        trace = current_request()
        if trace is not None:
            trace.update(model=self.config.model, read_timeout_seconds=self.config.timeout_seconds,
                         sdk_max_retries=getattr(self.client, "max_retries", None))
        with ExitStack() as resources:
            with span('api_admission', component=component):
                resources.enter_context(request_slot(self.config.base_url))
            response = stream_completion(self.client, request)
        self.last_completion = validate_completion(response, component=component,
                                                   expected_model=self.config.response_model)
        return response.choices[0].message.content.strip()


class ToolkitCompletionAdapter:
    """Expose a Research v2 client through M★ Toolkit's completion method."""

    def __init__(self, client: CompletionClient, max_tokens: int, component: str):
        self.client = client
        self.max_tokens = max_tokens
        self.component = component

    def llm_completion(self, messages: list[dict[str, str]], **_: Any) -> str:
        requested = _.get("max_tokens", self.max_tokens)
        if type(requested) is not int or not 0 < requested <= self.max_tokens:
            raise ValueError("Memory max_tokens must be positive and within the frozen host output budget")
        return self.client.complete(
            messages,
            max_tokens=requested,
            component=self.component,
            response_format=_.get("response_format"),
        )
