"""Production HTTP adapter for authoritative Corpus generation."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from .review_corpus import canonical_json_bytes
from .review_corpus_builder import CorpusCompletionResponse


class CorpusOpenAICompatibleAdapter:
    """Send only a frozen request JSON and retain the exact provider response."""

    def __init__(
        self,
        *,
        endpoint: str,
        request_config: Mapping[str, Any],
        expected_prompt_tokens_by_messages_sha256: Mapping[str, int],
        response_formats_by_messages_sha256: Mapping[str, Mapping[str, Any]],
        timeout_seconds: float,
        transport_attempts: int = 2,
    ):
        if int(transport_attempts) < 1:
            raise ValueError("Corpus transport attempts must be positive")
        self.endpoint = endpoint.rstrip("/") + "/chat/completions"
        self.request_config = dict(request_config)
        self.expected_prompt_tokens = {
            str(key): int(value)
            for key, value in expected_prompt_tokens_by_messages_sha256.items()
        }
        self.response_formats = {
            str(key): dict(value)
            for key, value in response_formats_by_messages_sha256.items()
        }
        if set(self.response_formats) != set(self.expected_prompt_tokens):
            raise ValueError(
                "Corpus response-format set must exactly match frozen requests"
            )
        self.timeout_seconds = float(timeout_seconds)
        self.transport_attempts = int(transport_attempts)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(
        self, messages: Sequence[Mapping[str, str]]
    ) -> CorpusCompletionResponse:
        message_values = [dict(message) for message in messages]
        messages_sha = hashlib.sha256(canonical_json_bytes(message_values)).hexdigest()
        if messages_sha not in self.expected_prompt_tokens:
            raise RuntimeError("Corpus live request is absent from frozen materialization")
        body = {
            **self.request_config,
            "messages": message_values,
            "response_format": self.response_formats[messages_sha],
        }
        wire = canonical_json_bytes(body)
        last_error = None
        for attempt in range(self.transport_attempts):
            request = urllib.request.Request(
                self.endpoint,
                data=wire,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self._opener.open(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= self.transport_attempts:
                    raise RuntimeError(
                        "Corpus provider transport failed twice with identical input"
                    ) from exc
                time.sleep(1.0)
        else:
            raise RuntimeError("Corpus provider transport failed") from last_error
        try:
            value = json.loads(raw.decode("utf-8", errors="strict"))
            choice = value["choices"][0]
            message = choice["message"]
            content = message.get("content")
            usage = value["usage"]
            prompt_tokens = usage["prompt_tokens"]
        except (KeyError, IndexError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Corpus provider response schema is invalid") from exc
        expected = self.expected_prompt_tokens[messages_sha]
        if type(prompt_tokens) is not int or prompt_tokens != expected:
            raise RuntimeError(
                "Corpus prompt token accounting mismatch: "
                f"expected={expected}, provider={prompt_tokens!r}"
            )
        if content is None:
            content_bytes = b""
        elif isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            raise RuntimeError("Corpus assistant content is not text")
        return CorpusCompletionResponse(
            assistant_content=content_bytes,
            raw_provider_response=raw,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )
