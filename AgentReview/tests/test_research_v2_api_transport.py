"""Exercise the real SDK against a local mock Ark API."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.llm import OpenAICompletionClient


def test_api_completion_honors_explicit_no_proxy(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({
                "id": "local-test", "object": "chat.completion.chunk", "created": 0,
                "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                    "delta": {"role": "assistant", "content": "local model response"}}],
            })
            body = ('data: ' + body + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1")
    monkeypatch.setenv("ARK_API_KEY", "mock-credential")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenAICompletionClient(ModelConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test-model", response_model="test-model"))
    try:
        assert client.complete([{"role": "user", "content": "test"}], max_tokens=10, component="test") == "local model response"
        assert len(received) == 1
        assert received[0]["thinking"] == {"type": "disabled"}
        assert "chat_template_kwargs" not in received[0]
        assert received[0]["max_tokens"] == 10
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
