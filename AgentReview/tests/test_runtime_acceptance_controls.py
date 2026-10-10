"""HTTP budget and score/checkpoint interruption contracts, without real models."""
import time
import httpx
import pytest
from openai import OpenAI
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.transport_observer import observe_client, RunCallBudgetExhausted
from test_research_v2_shared_evolution import Backend, protocol
from agentreview.research_v2.shared_evolution import SharedEvolution


def test_sdk_retries_each_consume_http_budget(tmp_path, monkeypatch):
    path = tmp_path / 'budget.json'
    atomic_write_json(path, {'limits': {'cloud': 2, 'local': 2}, 'used': {'cloud': 0, 'local': 0},
                            'deadline_at': time.time() + 60})
    monkeypatch.setenv('AGENTREVIEW_CALL_BUDGET', str(path))
    dispatched = []
    def send(request):
        dispatched.append(request)
        return httpx.Response(500, json={'error': 'temporary'}, request=request)
    with OpenAI(base_url='http://127.0.0.1:12345/v1', api_key='test', max_retries=2,
                http_client=httpx.Client(transport=httpx.MockTransport(send))) as client:
        observe_client(client)
        with pytest.raises(Exception):
            client.chat.completions.create(model='test', messages=[])
    assert len(dispatched) == 2
    assert read_json(path)['used'] == {'cloud': 0, 'local': 2}


def test_score_before_checkpoint_replays_without_double_admission_or_failure_budget(tmp_path):
    host = Backend()
    cached = {}
    original_assess = host.assess
    def assess(recipe, *, epoch):
        key = (recipe['key'], epoch)
        if key not in cached:
            cached[key] = original_assess(recipe, epoch=epoch)
        return cached[key]
    host.assess = assess
    config = protocol(attempts=1)
    evolution = SharedEvolution(protocol=config, output_root=tmp_path, backend=host)
    original_record = evolution._record_result
    def interrupt(recipe, result, **kwargs):
        if recipe['key'] == 'candidate-0001':
            raise KeyboardInterrupt('score succeeded, checkpoint pending')
        return original_record(recipe, result, **kwargs)
    evolution._record_result = interrupt
    with pytest.raises(KeyboardInterrupt):
        evolution.run()
    calls = list(host.calls)
    budgets = read_json(tmp_path / 'failure-policy.json')
    state = SharedEvolution(protocol=config, output_root=tmp_path, backend=host).run()
    assert state['status'] == 'complete' and state['pool'].count('candidate-0001') == 1
    assert host.calls == calls and len(host.proposals) == 1
    assert read_json(tmp_path / 'failure-policy.json') == budgets
