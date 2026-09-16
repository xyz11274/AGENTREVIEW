"""Local model traffic must reach the local server despite shell proxy settings."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.llm import OpenAICompletionClient


def test_local_completion_bypasses_environment_proxy(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({
                "id": "local-test", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "local model response"}}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenAICompletionClient(ModelConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test-model"))
    try:
        assert client.complete([{"role": "user", "content": "test"}], max_tokens=10, component="test") == "local model response"
        assert len(received) == 1
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
