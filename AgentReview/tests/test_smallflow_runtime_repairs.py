"""Regressions for the real study monitor, admission and finite runtime budgets."""
import asyncio
import json
import time
import sys
from pathlib import Path

import pytest

from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.mstar_prompts import interface_spec
from agentreview.research_v2.program_checks import FORBIDDEN_CALLS, FORBIDDEN_ATTRIBUTES


def test_study_report_reads_bound_telemetry_and_stage(tmp_path):
    from runtime_report import report
    telemetry = tmp_path / 'telemetry'
    telemetry.mkdir()
    (telemetry / 'worker-events.jsonl').write_text(json.dumps({
        'event': 'start', 'span': 'request', 'stage': 'api_request',
        'time': time.time(), 'component': 'specialist_review'}) + '\n')
    result = report(tmp_path / 'runs/seed-0', {
        'status': 'running', 'stage': 'search', 'telemetry_root': str(telemetry)})
    assert result['phase'] == 'search'
    assert len(result['active']) == 1


def test_reflector_receives_actual_static_restrictions():
    prompt = interface_spec({'build_llm_calls': 2, 'read_llm_calls': 1})
    assert 'only top-level assigned constants' in prompt
    assert all(name in prompt for name in FORBIDDEN_CALLS | FORBIDDEN_ATTRIBUTES)


def test_cumulative_budget_reuse_never_resets_usage_or_deadline(tmp_path):
    from agentreview.research_v2.runtime_limits import initialize_run_budget
    spec = {'wall_seconds': 60, 'http_attempts': {'cloud': 2, 'local': 3},
            'request_wall_seconds': 10}
    path = initialize_run_budget(spec, tmp_path)
    value = read_json(path)
    value['used']['cloud'] = 1
    atomic_write_json(path, value, immutable=False)
    assert initialize_run_budget(spec, tmp_path) == path
    assert read_json(path) == value
    with pytest.raises(ValueError, match='budget'):
        initialize_run_budget({**spec, 'http_attempts': {'cloud': 20, 'local': 30}}, tmp_path)


def test_missing_budget_is_rejected_before_launch(tmp_path):
    from agentreview.research_v2.runtime_limits import initialize_run_budget
    with pytest.raises(ValueError, match='budget'):
        initialize_run_budget(None, tmp_path)
    assert not (tmp_path / 'call-budget.json').exists()


def test_progress_detects_exited_worker_without_rewriting_history(tmp_path):
    from runtime_0930_progress import progress
    path = tmp_path / 'study-state.json'
    atomic_write_json(path, {'status': 'running', 'pid': 999999999, 'stage': 'search'})
    original = path.read_bytes()
    result = progress(tmp_path)
    assert result['study-state.json']['status'] == 'interrupted'
    assert result['study-state.json']['recorded_status'] == 'running'
    assert path.read_bytes() == original


def test_busy_admission_has_wall_deadline(tmp_path, monkeypatch):
    from agentreview.research_v2.request_admission import request_slot
    monkeypatch.setenv('AGENTREVIEW_REQUEST_WALL_SECONDS', '.08')
    profile = tmp_path / 'execution.json'
    endpoint = 'http://127.0.0.1:12345/v1'
    atomic_write_json(profile, {'protocol': 'shared-api-execution-v2', 'paper_workers': 1,
                              'admission_root': str(tmp_path / 'slots'), 'limits': {endpoint: 1}})
    from agentreview.research_v2.io import file_sha256
    monkeypatch.setenv('AGENTREVIEW_EXECUTION_PROFILE', str(profile))
    monkeypatch.setenv('AGENTREVIEW_EXECUTION_PROFILE_SHA256', file_sha256(profile))
    with request_slot(endpoint):
        started = time.monotonic()
        with pytest.raises(TimeoutError, match='wall-clock'):
            with request_slot(endpoint):
                pytest.fail('A busy slot must not be entered')
        assert time.monotonic() - started < .5


def test_sync_request_deadline_covers_first_content_wait(monkeypatch):
    from test_streaming_transport import endpoint, event
    from agentreview.research_v2.config import ModelConfig
    from agentreview.research_v2.llm import OpenAICompletionClient
    monkeypatch.setenv('AGENTREVIEW_REQUEST_WALL_SECONDS', '.12')
    def send(handler, attempt):
        if attempt == 1:
            event(handler, content='warmup', finish='stop')
            handler.wfile.write(b'data: [DONE]\n\n')
            return
        time.sleep(.35)
        try:
            event(handler, content='late', finish='stop')
            handler.wfile.write(b'data: [DONE]\n\n')
        except (BrokenPipeError, ConnectionResetError):
            pass
    with endpoint(monkeypatch, send) as (url, received):
        client = OpenAICompletionClient(ModelConfig(base_url=url, model='test-model',
                    response_model='test-model', timeout_seconds=2))
        try:
            client.count_chat_tokens([])  # Local tokenizer warmup is outside the transport contract.
            with monkeypatch.context() as warmup:
                warmup.delenv('AGENTREVIEW_REQUEST_WALL_SECONDS')
                assert client.complete([], max_tokens=20, component='warmup') == 'warmup'
            started = time.monotonic()
            with pytest.raises(TimeoutError, match='wall-clock'):
                client.complete([], max_tokens=20, component='review')
            assert time.monotonic() - started < .3
            assert client.last_completion is None
        finally:
            client.close()
    assert len(received) == 2


def test_async_stream_deadline_cancels_continuous_output(monkeypatch):
    from test_streaming_transport import endpoint, event
    from openai import AsyncOpenAI
    from agentreview.research_v2.streaming import async_stream_completion
    monkeypatch.setenv('AGENTREVIEW_REQUEST_WALL_SECONDS', '.10')
    def send(handler, attempt):
        try:
            for _ in range(20):
                event(handler, content='partial')
                time.sleep(.02)
            event(handler, finish='stop')
            handler.wfile.write(b'data: [DONE]\n\n')
        except (BrokenPipeError, ConnectionResetError):
            pass
    async def request(url):
        async with AsyncOpenAI(base_url=url, api_key='test', max_retries=0, timeout=2) as client:
            with pytest.raises(TimeoutError, match='wall-clock'):
                await async_stream_completion(client, {'model': 'test-model', 'messages': [], 'stream': True})
    with endpoint(monkeypatch, send) as (url, received):
        asyncio.run(request(url))
    assert len(received) == 1


def test_supervisor_hard_deadline_stops_unresponsive_process(tmp_path):
    from run_budgeted_0930 import supervise
    ledger = tmp_path / 'call-budget.json'
    atomic_write_json(ledger, {'limits': {'cloud': 2, 'local': 2}, 'used': {'cloud': 1, 'local': 0},
                              'deadline_at': time.time() + .25})
    code = 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(20)'
    started = time.monotonic()
    result = supervise([sys.executable, '-c', code], root=tmp_path, budget_path=ledger, grace_seconds=.08)
    assert time.monotonic() - started < 1.5
    assert result['status'] == 'budget_exhausted'
    assert result['hard_stop'] and result['child_returncode'] < 0
    assert read_json(ledger)['used'] == {'cloud': 1, 'local': 0}


def test_formal_requires_budget_instead_of_clearing_it(tmp_path):
    from three_search_study import validate
    path = atomic_write_json(tmp_path / 'study.json', {'implementation': {}, 'searches': []})
    with pytest.raises(ValueError, match='budget'):
        validate(path)


def test_late_headers_do_not_rewrite_finished_attempt(tmp_path):
    import httpx
    from contextvars import copy_context
    from agentreview.research_v2.execution_telemetry import RequestTrace
    from agentreview.research_v2.transport_observer import response_headers
    path = tmp_path / 'attempt-1.progress.json'
    with pytest.raises(TimeoutError):
        with RequestTrace(path, request_id='one'):
            late = copy_context()
            raise TimeoutError('wall-clock timeout')
    original = path.read_bytes()
    late.run(response_headers, httpx.Response(200, headers={'x-request-id': 'late'}))
    assert path.read_bytes() == original


def test_expired_request_cannot_dispatch_late_http_attempt(tmp_path):
    import httpx
    from agentreview.research_v2.runtime_limits import request_deadline
    from agentreview.research_v2.transport_observer import dispatch
    from contextvars import copy_context
    from unittest.mock import patch
    with patch.dict('os.environ', {'AGENTREVIEW_REQUEST_WALL_SECONDS': '.01'}):
        with request_deadline():
            late = copy_context()
        time.sleep(.02)
        with pytest.raises(TimeoutError, match='wall-clock'):
            late.run(dispatch, httpx.Request('POST', 'http://127.0.0.1:12345/v1/chat/completions'))


def test_production_worker_preserves_request_deadline_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agentreview.research_v2 import development_coco as coco
    monkeypatch.setenv('AGENTREVIEW_REQUEST_WALL_SECONDS', '600')
    calls = []
    def process(command, **options):
        calls.append(options['env'])
        options['stdout'].write(b'local fixture configuration failure')
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(coco.subprocess, 'run', process)
    request = atomic_write_json(tmp_path / 'request.json', {'request': 'fixture'})
    with pytest.raises(coco.DevelopmentCoCoFailure):
        coco._run_stage(stage='split', request_path=request, output=tmp_path,
            config={'max_stage_attempts': 1, 'timeout_seconds': 1, 'python_executable': sys.executable})
    assert len(calls) == 1 and calls[0]['AGENTREVIEW_REQUEST_WALL_SECONDS'] == '600'


def test_smallflow_completion_rejects_no_evolved_candidate(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from run_smallflow_0930 import verify_completed_run
    search = tmp_path / 'search'
    atomic_write_json(search / 'search.json', {'attempts': {'1': {'status': 'candidate_failed'}}})
    atomic_write_json(search / 'failure-policy.json', {'status': 'ended'})
    atomic_write_json(tmp_path / 'call-budget.json', {'used': {'cloud': 4, 'local': 1}})
    with patch('run_smallflow_0930.load_experiment_protocol', return_value=SimpleNamespace(document={'evolution': {'attempts': 1}})):
        with pytest.raises(AssertionError, match='No valid evolved candidate'):
            verify_completed_run(SimpleNamespace(root=tmp_path, protocol_path=tmp_path / 'protocol.json'))
    assert not (tmp_path / 'completion-check.json').exists()
