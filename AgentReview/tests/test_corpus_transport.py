import json
import unittest
from unittest import mock

from agentreview.corpus_transport import CorpusOpenAICompatibleAdapter
from agentreview.review_corpus import canonical_json_bytes


class FakeHTTPResponse:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.raw


class CorpusTransportTest(unittest.TestCase):
    def test_retains_raw_response_and_enforces_prompt_count(self):
        messages = [{"role": "user", "content": "frozen"}]
        import hashlib

        message_sha = hashlib.sha256(canonical_json_bytes(messages)).hexdigest()
        raw = json.dumps(
            {
                "id": "response",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "{}"}}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 1},
            }
        ).encode()
        adapter = CorpusOpenAICompatibleAdapter(
            endpoint="http://127.0.0.1:8000/v1",
            request_config={"model": "qwen", "max_tokens": 10},
            expected_prompt_tokens_by_messages_sha256={message_sha: 7},
            timeout_seconds=1,
        )
        adapter._opener = mock.Mock()
        adapter._opener.open.return_value = FakeHTTPResponse(raw)
        result = adapter(messages)
        self.assertEqual(result.raw_provider_response, raw)
        self.assertEqual(result.assistant_content, b"{}")
        self.assertEqual(result.finish_reason, "stop")

    def test_rejects_token_accounting_mismatch(self):
        messages = [{"role": "user", "content": "frozen"}]
        import hashlib

        message_sha = hashlib.sha256(canonical_json_bytes(messages)).hexdigest()
        raw = json.dumps(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "{}"}}
                ],
                "usage": {"prompt_tokens": 8},
            }
        ).encode()
        adapter = CorpusOpenAICompatibleAdapter(
            endpoint="http://127.0.0.1:8000/v1",
            request_config={"model": "qwen", "max_tokens": 10},
            expected_prompt_tokens_by_messages_sha256={message_sha: 7},
            timeout_seconds=1,
        )
        adapter._opener = mock.Mock()
        adapter._opener.open.return_value = FakeHTTPResponse(raw)
        with self.assertRaisesRegex(RuntimeError, "token accounting mismatch"):
            adapter(messages)


if __name__ == "__main__":
    unittest.main()
