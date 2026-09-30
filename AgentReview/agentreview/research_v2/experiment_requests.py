"""Persist request attempts before dispatch and retain complete raw responses."""

from __future__ import annotations

import fcntl
import time

from .retry import transient_error, retry_delay
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json, value_sha256
from .token_accounting import available_output_tokens
from .execution_telemetry import RequestTrace, emit


class ExperimentRequestFailure(RuntimeError):
    failure_category = "shared_runtime"


class PersistentCompletionClient:
    def __init__(self, client, root, *, identity, max_attempts):
        self.client = client
        self.root = Path(root).resolve()
        self.identity = identity
        self.max_attempts = max_attempts
        self.retry_policy = {"owner": "persistent-logical-request", "max_attempts": max_attempts, "sdk_max_retries": 0}
        if type(max_attempts) is int and max_attempts > 0:
            return
        raise ValueError("Request attempts requires a positive integer")

    def count_chat_tokens(self, messages):
        return self.client.count_chat_tokens(messages)

    def complete(self, messages, *, max_tokens, component, response_format=None):
        identity = {"messages": messages, "max_tokens": max_tokens, "component": component,
                    "completion_contract": "complete-stop-v1",
                    "model": self.identity, "response_format": response_format,
                    "max_attempts": self.max_attempts,
                    "output_policy": "remaining-model-context-v1"}
        directory = self.root / value_sha256(identity)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "request.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            atomic_write_json(directory / "request.json", identity)
            result_path = directory / "response.json"
            failure_path = directory / "failure.json"
            if result_path.is_file():
                result = read_json(result_path)
                body = {k: v for k, v in result.items() if k != "response_sha256"}
                if (result.get("response_sha256") != value_sha256(body)
                        or result.get("request_id") != directory.name
                        or not isinstance(result.get("raw_model_output"), str)
                        or not result["raw_model_output"].strip()):
                    raise ExperimentRequestFailure("Cached response integrity check failed")
                return result["raw_model_output"]
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
                    tokens = self.client.count_chat_tokens(messages)
                    output = available_output_tokens(tokens, max_tokens)
                    with RequestTrace(directory / f'attempt-{attempt}.progress.json',
                                      request_id=directory.name, attempt=attempt, component=component,
                                      input_tokens=tokens['input_tokens'], output_budget=output):
                        response = self.client.complete(messages, max_tokens=output,
                                                       component=component, response_format=response_format)
                    if isinstance(response, str) and response.strip():
                        result = {"request_id": directory.name, "raw_model_output": response,
                                  "attempt": attempt, "tokens": tokens,
                                  "effective_max_tokens": output,
                                  "completion": getattr(self.client, "last_completion", None)}
                        atomic_write_json(result_path, {**result, "response_sha256": value_sha256(result)})
                        return response
                    raise ValueError("Completion requires nonempty text")
                except Exception as exc:
                    transient = transient_error(exc)
                    error = {"type": type(exc).__name__, "message": str(exc), "transient": transient}
                    if getattr(exc, "completion", None) is not None:
                        error["completion"] = exc.completion
                    error["fingerprint"] = value_sha256({"type": error["type"], "message": error["message"]})
                    atomic_write_json(error_path, error)
                    errors.append(error)
                    repeated = previous is not None and previous["fingerprint"] == error["fingerprint"]
                    if transient is False and repeated:
                        break
                    if transient and attempt < self.max_attempts:
                        delay = retry_delay(exc, attempt)
                        atomic_write_json(directory / f"attempt-{attempt}.retry.json", {"delay_seconds": delay})
                        emit(event='retry', stage='api_request', request_id=directory.name,
                             component=component, attempt=attempt, next_attempt=attempt + 1,
                             delay_seconds=delay, error_type=type(exc).__name__, error=str(exc))
                        from .execution_telemetry import span
                        with span('api_retry_wait', component=component, attempt=attempt):
                            time.sleep(delay)
                    previous = error
            failure = {"status": "paused", "reason": "Request exhausted its finite attempts or repeated a deterministic error", "errors": errors}
            atomic_write_json(failure_path, failure)
            raise ExperimentRequestFailure(failure["reason"])
