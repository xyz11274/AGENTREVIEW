import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.utility.authentication_utils import (
    get_openai_client,
    resolve_openai_base_url,
)
from memory_evolution.cli import (
    _llm_logical_identity,
    _prepare_llm_runtime,
    _record_llm_runtime,
    build_parser,
)


class LocalOpenAIRuntimeTest(unittest.TestCase):
    def _args(self):
        return build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
                "--generation-model-source",
                "Qwen/Qwen3.8-27B-FP8",
                "--generation-model-revision",
                "snapshot-abc",
                "--vllm-version",
                "0.10.0",
            ]
        )

    def test_base_url_priority_is_cli_then_primary_then_legacy_env(self):
        env = {
            "OPENAI_BASE_URL": "http://127.0.0.1:8000/v1/",
            "OPENAI_API_BASE": "http://127.0.0.1:8001/v1",
        }
        self.assertEqual(
            resolve_openai_base_url("http://127.0.0.1:9000/v1", env),
            "http://127.0.0.1:9000/v1",
        )
        self.assertEqual(
            resolve_openai_base_url(None, env),
            "http://127.0.0.1:8000/v1",
        )
        self.assertEqual(
            resolve_openai_base_url(None, {"OPENAI_API_BASE": env["OPENAI_API_BASE"]}),
            "http://127.0.0.1:8001/v1",
        )

    def test_formal_runtime_without_endpoint_fails_closed(self):
        args = self._args()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "dummy"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "endpoint is required"):
                _prepare_llm_runtime(args, formal=True)

    @patch("agentreview.utility.authentication_utils.openai.OpenAI")
    def test_dummy_key_initializes_local_client(self, openai_client):
        get_openai_client(
            "openai",
            base_url="http://127.0.0.1:8000/v1",
            api_key="dummy",
        )
        self.assertEqual(openai_client.call_args.kwargs["api_key"], "dummy")
        self.assertEqual(
            openai_client.call_args.kwargs["base_url"],
            "http://127.0.0.1:8000/v1",
        )

    def test_port_change_preserves_identity_and_secret_is_absent(self):
        first = self._args()
        first.openai_base_url = "http://127.0.0.1:8000/v1"
        first._llm_preflight_result = {"http_status": 200}
        second = copy.deepcopy(first)
        second.openai_base_url = "http://127.0.0.1:8001/v1"
        self.assertEqual(
            _llm_logical_identity(first), _llm_logical_identity(second)
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.dict(
                "os.environ", {"OPENAI_API_KEY": "top-secret-value"}, clear=True
            ):
                _record_llm_runtime(output, first)
                _record_llm_runtime(output, second)
            text = (output / "llm_runtime.json").read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertNotIn("top-secret-value", text)
            self.assertEqual(
                payload["base_url_history"],
                [
                    "http://127.0.0.1:8000/v1",
                    "http://127.0.0.1:8001/v1",
                ],
            )


if __name__ == "__main__":
    unittest.main()
