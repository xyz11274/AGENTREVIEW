"""Persist request attempts before dispatch and retain complete raw responses."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json, value_sha256
from .llm import complete_with_response_format


class ExperimentRequestFailure(RuntimeError):
    failure_category = "shared_runtime"


class PersistentCompletionClient:
    def __init__(self, client, root, *, identity, max_attempts, input_max_tokens):
        self.client = client
        self.root = Path(root).resolve()
        self.identity = identity
        self.max_attempts = max_attempts
        self.input_max_tokens = input_max_tokens
        if type(max_attempts) is int and max_attempts > 0:
            return
        raise ValueError("Request attempts requires a positive integer")

    def count_chat_tokens(self, messages):
        return self.client.count_chat_tokens(messages)

    def complete(self, messages, *, max_tokens, component, response_format=None):
        identity = {"messages": messages, "max_tokens": max_tokens, "component": component,
                    "completion_contract": "complete-stop-v1",
                    "model": self.identity, "response_format": response_format,
                    "max_attempts": self.max_attempts, "input_max_tokens": self.input_max_tokens}
        directory = self.root / value_sha256(identity)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "request.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            atomic_write_json(directory / "request.json", identity)
            result_path = directory / "response.json"
            failure_path = directory / "failure.json"
            if result_path.is_file():
                return read_json(result_path)["raw_model_output"]
            if failure_path.is_file():
                raise ExperimentRequestFailure(read_json(failure_path)["reason"])
            previous = None
            errors = []
            for attempt in range(1, self.max_attempts + 1):
                reserve = directory / f"attempt-{attempt}.reserved.json"
                error_path = directory / f"attempt-{attempt}.error.json"
                if reserve.is_file():
                    recorded = read_json(error_path) if error_path.is_file() else {"fingerprint": "interrupted", "transient": True}
                    errors.append(recorded)
                    if recorded["transient"] is False and previous is not None and previous["fingerprint"] == recorded["fingerprint"]:
                        break
                    previous = recorded
                    continue
                atomic_write_json(reserve, {"attempt": attempt, "request_id": directory.name})
                try:
                    if type(max_tokens) is not int or max_tokens <= 0:
                        raise ValueError("Output budget requires a positive integer")
                    tokens = self.client.count_chat_tokens(messages)
                    if tokens["input_tokens"] <= self.input_max_tokens and tokens["input_tokens"] + max_tokens <= tokens["max_model_len"]:
                        pass
                    else:
                        raise ValueError(f"Complete request exceeds the frozen input or model token budget: input={tokens['input_tokens']}, output={max_tokens}, input_limit={self.input_max_tokens}, context={tokens['max_model_len']}")
                    response = complete_with_response_format(self.client, messages, max_tokens=max_tokens,
                                                             component=component, response_format=response_format)
                    if isinstance(response, str) and response.strip():
                        atomic_write_json(result_path, {"raw_model_output": response, "attempt": attempt, "tokens": tokens,
                                                       "completion": getattr(self.client, "last_completion", None)})
                        return response
                    raise ValueError("Completion requires nonempty text")
                except Exception as exc:
                    status = getattr(exc, "status_code", None)
                    transient = isinstance(exc, (TimeoutError, ConnectionError)) or status in {408, 429, 500, 502, 503, 504} or type(exc).__name__ in {"APITimeoutError", "APIConnectionError"}
                    error = {"type": type(exc).__name__, "message": str(exc), "transient": transient}
                    if getattr(exc, "completion", None) is not None:
                        error["completion"] = exc.completion
                    error["fingerprint"] = value_sha256({"type": error["type"], "message": error["message"]})
                    atomic_write_json(error_path, error)
                    errors.append(error)
                    repeated = previous is not None and previous["fingerprint"] == error["fingerprint"]
                    if transient is False and repeated:
                        break
                    previous = error
            failure = {"status": "paused", "reason": "Request exhausted its finite attempts or repeated a deterministic error", "errors": errors}
            atomic_write_json(failure_path, failure)
            raise ExperimentRequestFailure(failure["reason"])
