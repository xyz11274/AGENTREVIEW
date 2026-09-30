import json
import time

import pytest

from agentreview.research_v2.execution_telemetry import span, summarize, RequestTrace, emit, timed
from agentreview.research_v2.io import read_json
from runtime_report import report


def test_live_request_progress_and_retries_are_visible_without_search_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTREVIEW_TELEMETRY_ROOT', str(tmp_path / 'telemetry'))
    with RequestTrace(tmp_path / 'request/attempt-1.progress.json', component='reflection', attempt=1, request_id='request'):
        live = report(tmp_path, {'status': 'running', 'phase': 'search'})
        assert live['active'][0]['status'] == 'queued'
        assert live['active'][0]['elapsed_seconds'] >= 0
    emit(event='retry', stage='api_request', component='reflection', attempt=1,
         next_attempt=2, delay_seconds=2, error_type='APITimeoutError')
    value = report(tmp_path, {'status': 'running', 'phase': 'search'})
    assert value['retries'][0]['next_attempt'] == 2
    assert 'APITimeoutError' in (tmp_path / 'report/流程耗时报告.md').read_text()
    assert not (tmp_path / 'search/search.json').exists()


def test_stage_exception_and_operator_interrupt_have_distinct_terminal_timings(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTREVIEW_TELEMETRY_ROOT', str(tmp_path))
    @timed('memory_build', 'role')
    def build(*, role):
        raise ValueError('candidate failure')
    with pytest.raises(ValueError):
        build(role='QUAL')
    with pytest.raises(KeyboardInterrupt):
        with RequestTrace(tmp_path / 'attempt-1.progress.json', component='reflection', attempt=1):
            raise KeyboardInterrupt()
    value = summarize(tmp_path)
    assert value['active'] == []
    assert value['stages']['memory_build']['failed'] == 1
    assert value['stages']['api_request']['interrupted'] == 1
    assert read_json(tmp_path / 'attempt-1.progress.json')['status'] == 'interrupted'


def test_simultaneous_spans_preserve_independent_durations(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('AGENTREVIEW_TELEMETRY_ROOT', str(tmp_path))
    def work(role):
        with span('specialist_review', role=role):
            time.sleep(.01)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(work, ['QUAL', 'CLAR', 'SIGN', 'ORIG', 'POL']))
    value = summarize(tmp_path)
    assert value['active'] == []
    assert value['stages']['specialist_review']['complete'] == 5
    assert value['stages']['specialist_review']['seconds'] >= .05
