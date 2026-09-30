"""Real SDK SSE tests for progress, idle timeouts and complete-response admission."""
import json
import asyncio
import time
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.experiment_requests import PersistentCompletionClient, ExperimentRequestFailure
from agentreview.research_v2.io import read_json
from agentreview.research_v2.llm import OpenAICompletionClient


@contextmanager
def endpoint(monkeypatch, send):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            send(self, len(received))
        def log_message(self, *args):
            pass
    monkeypatch.setenv('ARK_API_KEY', 'test-stream-secret')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def event(handler, *, content=None, reasoning=None, finish=None, usage=None):
    delta = {}
    if content is not None:
        delta['content'] = content
    if reasoning is not None:
        delta['reasoning_content'] = reasoning
    value = {'id': 'stream-test', 'object': 'chat.completion.chunk', 'created': 0,
             'model': 'test-model', 'choices': [] if usage else [
                 {'index': 0, 'delta': delta, 'finish_reason': finish}]}
    if usage:
        value['usage'] = usage
    handler.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
    handler.wfile.flush()


def test_receiving_stream_survives_total_time_above_idle_timeout(tmp_path, monkeypatch):
    observations = []
    def send(handler, attempt):
        event(handler, reasoning='reasoning')
        for _ in range(8):
            time.sleep(.04)
            event(handler, content='word ')
            observations.extend(read_json(p) for p in tmp_path.rglob('attempt-1.progress.json'))
        event(handler, finish='stop')
        event(handler, usage={'prompt_tokens': 2, 'completion_tokens': 8, 'total_tokens': 10})
        handler.wfile.write(b'data: [DONE]\n\n')
    with endpoint(monkeypatch, send) as (url, requests):
        client = OpenAICompletionClient(ModelConfig(base_url=url, model='test-model',
            response_model='test-model', timeout_seconds=.15))
        wrapped = PersistentCompletionClient(client, tmp_path, identity={}, max_attempts=1)
        try:
            assert wrapped.complete([], max_tokens=100, component='reflection') == ('word ' * 8).strip()
        finally:
            client.close()
    assert requests[0]['stream'] is True
    assert requests[0]['stream_options']['include_usage'] is True
    assert any(row['status'] == 'streaming' for row in observations)
    progress = read_json(next(tmp_path.rglob('attempt-1.progress.json')))
    assert progress['status'] == 'complete'
    assert progress['elapsed_seconds'] > .15
    assert progress['first_chunk_seconds'] < progress['elapsed_seconds']
    response = read_json(next(tmp_path.rglob('response.json')))['completion']
    assert response['choices'][0]['message']['reasoning_content'] == 'reasoning'
    assert response['usage']['completion_tokens'] == 8
    assert all('test-stream-secret' not in p.read_text() for p in tmp_path.rglob('*.json'))


def test_interrupted_stream_is_recorded_and_retry_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr('agentreview.research_v2.experiment_requests.retry_delay', lambda *a: 0)
    def send(handler, attempt):
        event(handler, content='partial' if attempt == 1 else 'complete')
        if attempt == 2:
            event(handler, finish='stop')
            handler.wfile.write(b'data: [DONE]\n\n')
    with endpoint(monkeypatch, send) as (url, _):
        client = OpenAICompletionClient(ModelConfig(base_url=url, model='test-model', response_model='test-model'))
        try:
            wrapped = PersistentCompletionClient(client, tmp_path, identity={}, max_attempts=2)
            assert wrapped.complete([], max_tokens=100, component='review') == 'complete'
        finally:
            client.close()
    assert read_json(next(tmp_path.rglob('response.json')))['attempt'] == 2
    assert read_json(next(tmp_path.rglob('attempt-1.error.json')))['transient'] is True
    assert read_json(next(tmp_path.rglob('attempt-1.progress.json')))['status'] == 'failed'
    assert 'partial' in next(tmp_path.rglob('attempt-1.stream.jsonl')).read_text()


def test_length_finish_remains_a_failed_generation(tmp_path, monkeypatch):
    def send(handler, attempt):
        event(handler, content='truncated', finish='length')
        handler.wfile.write(b'data: [DONE]\n\n')
    with endpoint(monkeypatch, send) as (url, _):
        client = OpenAICompletionClient(ModelConfig(base_url=url, model='test-model', response_model='test-model'))
        try:
            with pytest.raises(ExperimentRequestFailure):
                PersistentCompletionClient(client, tmp_path, identity={}, max_attempts=1).complete(
                    [], max_tokens=100, component='review')
        finally:
            client.close()
    assert not list(tmp_path.rglob('response.json'))


@pytest.mark.parametrize('finish,content', [('stop', 'judge result'), ('length', 'partial judge'), ('stop', '')])
def test_official_coco_judge_receives_assembled_stream_with_original_semantics(tmp_path, monkeypatch, finish, content):
    from agentreview.research_v2.development_coco_worker import _load_standard, _client
    def send(handler, attempt):
        event(handler, reasoning='judge thinking')
        event(handler, content=content, finish=finish)
        event(handler, usage={'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7})
        handler.wfile.write(b'data: [DONE]\n\n')
    _load_standard(Path(__file__).resolve().parents[3] / 'CoCoReviewBench_official_2580687', '_4_evaluation')
    with endpoint(monkeypatch, send) as (url, requests):
        config = {'services': {'judge': ModelConfig(base_url=url, model='test-model',
                  response_model='test-model', enable_thinking=True).protocol_config()},
                  'request_settings': {'judge': {'max_workers': 1}}}
        client = _client(config, 'judge', {'output_root': str(tmp_path)})
        try:
            key, messages, payload = client._prepare_request('judge the complete review', max_completion_tokens=100)
            future = asyncio.run_coroutine_threadsafe(client.get_response(key, messages, payload), client._loop)
            assert future.result(timeout=5)[0] == content
        finally:
            client.shutdown()
    assert requests[0]['stream'] is True
    assert requests[0]['max_tokens'] == 100
    receipt = read_json(next(tmp_path.rglob('attempt-1.result.json')))
    assert receipt['completion']['choices'][0]['finish_reason'] == finish
    assert receipt['completion']['usage']['total_tokens'] == 7
    assert read_json(next(tmp_path.rglob('attempt-1.progress.json')))['status'] == 'complete'
