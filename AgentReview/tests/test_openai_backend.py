import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from agentreview.backends.openai import (
    EmptyModelResponseError,
    OpenAIChat,
    _exception_chain_summary,
)


class FakeCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=""),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(
                completion_tokens=1200,
                completion_tokens_details=SimpleNamespace(
                    reasoning_tokens=1200
                ),
            ),
        )


class OpenAIBackendTest(unittest.TestCase):
    def test_exception_chain_includes_transport_root_cause(self):
        try:
            try:
                raise OSError("connection reset by peer")
            except OSError as cause:
                raise RuntimeError("connection error") from cause
        except RuntimeError as error:
            summary = _exception_chain_summary(error)

        self.assertIn("RuntimeError: connection error", summary)
        self.assertIn("OSError: connection reset by peer", summary)

    def test_gpt5_uses_max_completion_tokens(self):
        backend = object.__new__(OpenAIChat)
        backend.model = "gpt-5"
        backend.temperature = 0.3
        backend.max_tokens = 4096
        backend.request_timeout_seconds = 10

        kwargs = backend._completion_kwargs(
            [{"role": "user", "content": "test"}]
        )

        self.assertEqual(kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("stop", kwargs)
        self.assertNotIn("reasoning_effort", kwargs)

    def test_length_limited_empty_response_increases_budget_and_lowers_effort(self):
        backend = object.__new__(OpenAIChat)
        backend.model = "gpt-5"
        backend.base_url = "https://example.test/v1"
        backend.max_tokens = 8192
        backend.retry_max_tokens = 16384
        backend.reasoning_effort = "low"
        backend.connect_timeout_seconds = 30
        backend.request_timeout_seconds = 600
        backend.empty_response_attempts = 2
        backend.empty_response_retry_seconds = 0
        backend.last_response_info = {}
        calls = []

        def fake_get_response(
            messages,
            agent_name=None,
            max_tokens=None,
            reasoning_effort=None,
        ):
            calls.append((max_tokens, reasoning_effort))
            if len(calls) == 1:
                raise EmptyModelResponseError(
                    "length exhausted",
                    finish_reason="length",
                    completion_tokens=8192,
                    reasoning_tokens=8192,
                    requested_tokens=8192,
                )
            backend.last_response_info = {
                "response_chars": 6,
                "finish_reason": "stop",
                "completion_tokens": 100,
                "reasoning_tokens": 10,
                "requested_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            }
            return "review"

        backend._get_response = fake_get_response
        output = io.StringIO()
        with redirect_stdout(output):
            response = backend._get_response_for_agent(
                "Reviewer CLAR",
                [{"role": "user", "content": "paper"}],
            )

        self.assertEqual(response, "review")
        self.assertEqual(calls, [(8192, "low"), (16384, "minimal")])
        self.assertIn("REASONING TOKEN EXHAUSTED", output.getvalue())
        self.assertIn("MAX_COMPLETION_TOKENS EXHAUSTED", output.getvalue())

    def test_unsupported_reasoning_effort_retries_without_parameter(self):
        calls = []

        class RejectReasoningEffort:
            def create(self, **kwargs):
                calls.append(dict(kwargs))
                if "reasoning_effort" in kwargs:
                    raise TypeError(
                        "unexpected keyword argument 'reasoning_effort'"
                    )
                return SimpleNamespace(
                    id="response-1",
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="review"),
                            finish_reason="stop",
                        )
                    ],
                    usage=SimpleNamespace(
                        completion_tokens=10,
                        completion_tokens_details=SimpleNamespace(
                            reasoning_tokens=0
                        ),
                    ),
                )

        backend = object.__new__(OpenAIChat)
        backend.client_type = "openai"
        backend.client = SimpleNamespace(
            chat=SimpleNamespace(completions=RejectReasoningEffort())
        )
        backend.model = "gpt-5"
        backend.temperature = 0.3
        backend.max_tokens = 8192
        backend.reasoning_effort = "low"
        backend.reasoning_effort_supported = None
        backend.min_interval_seconds = 0
        backend.request_timeout_seconds = 10

        response = backend._get_response(
            [{"role": "user", "content": "test"}],
            "Reviewer SIGN",
            max_tokens=8192,
            reasoning_effort="low",
        )

        self.assertEqual(response, "review")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", calls[1])
        self.assertFalse(backend.reasoning_effort_supported)

    def test_empty_success_response_is_rejected_with_diagnostics(self):
        backend = object.__new__(OpenAIChat)
        backend.client_type = "openai"
        backend.client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        backend.model = "gpt-5"
        backend.temperature = 0.3
        backend.max_tokens = 1200
        backend.min_interval_seconds = 0
        backend.request_timeout_seconds = 10

        with self.assertRaisesRegex(
            EmptyModelResponseError,
            "finish_reason=length.*reasoning_tokens=1200",
        ):
            backend._get_response([{"role": "user", "content": "test"}])


if __name__ == "__main__":
    unittest.main()
