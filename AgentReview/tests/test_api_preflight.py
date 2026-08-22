import unittest
from types import SimpleNamespace
from unittest.mock import patch

from memory_evolution.api_preflight import run_preflight


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size):
        return b'{"data": [{"id": "model-a"}]}'


class ApiPreflightTest(unittest.TestCase):
    @patch("memory_evolution.api_preflight.urllib.request.urlopen")
    @patch("memory_evolution.api_preflight.socket.getaddrinfo")
    def test_preflight_reports_route_without_exposing_key(
        self, getaddrinfo, urlopen
    ):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        urlopen.return_value = FakeResponse()
        result = run_preflight(
            "https://example.test/v1/",
            "secret-key",
            timeout=2,
        )
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["model_count_in_response"], 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/models")
        self.assertNotIn("secret-key", str(result))

    @patch("memory_evolution.api_preflight.get_openai_client")
    @patch("memory_evolution.api_preflight.urllib.request.urlopen")
    @patch("memory_evolution.api_preflight.socket.getaddrinfo")
    def test_preflight_checks_served_model_and_sdk_chat(
        self, getaddrinfo, urlopen, get_client
    ):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 8000))]
        urlopen.return_value = FakeResponse()
        create = get_client.return_value.chat.completions.create
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )

        result = run_preflight(
            "http://127.0.0.1:8000/v1",
            "dummy",
            model_name="model-a",
            max_tokens=64,
            reasoning_effort="medium",
            reasoning_effort_supported=True,
        )

        self.assertTrue(result["chat_checked"])
        self.assertEqual(result["served_model_name"], "model-a")
        self.assertEqual(create.call_args.kwargs["reasoning_effort"], "medium")
        self.assertEqual(create.call_args.kwargs["max_tokens"], 64)


if __name__ == "__main__":
    unittest.main()
