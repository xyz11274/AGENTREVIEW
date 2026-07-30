import unittest
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


if __name__ == "__main__":
    unittest.main()
