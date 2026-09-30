import pytest
import pilot_0930 as pilot
from agentreview.research_v2.io import read_json, atomic_write_json


def test_interrupt_records_terminal_run_state(tmp_path, monkeypatch):
    protocol = tmp_path / 'protocol.frozen.json'
    protocol.write_text('{}')
    run = pilot.PilotRun(protocol, tmp_path / 'fresh-run')
    monkeypatch.setattr(pilot.PilotRun, 'check', lambda self: {'status': 'ready'})
    monkeypatch.setattr(pilot.PilotRun, 'report', lambda self: {})
    monkeypatch.setenv('AGENTREVIEW_TELEMETRY_ROOT', '')
    monkeypatch.setenv('HF_HUB_OFFLINE', '')
    def interrupt(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(pilot, 'run_shared_experiment', interrupt)
    with pytest.raises(KeyboardInterrupt):
        run.run()
    assert read_json(run.root / 'pilot-state.json')['status'] == 'stopped'


def test_run_paths_are_independent(tmp_path):
    first = pilot.PilotRun(tmp_path / 'a.json', tmp_path / 'a')
    second = pilot.PilotRun(tmp_path / 'b.json', tmp_path / 'b')
    assert first.status()['output_root'] == str(tmp_path / 'a')
    assert second.status()['protocol_path'] == str(tmp_path / 'b.json')
    assert first.status()['status'] == second.status()['status'] == 'prepared'


def test_status_resolves_an_exited_worker_from_its_recorded_identity(tmp_path):
    run = pilot.PilotRun(tmp_path / 'protocol.json', tmp_path)
    atomic_write_json(tmp_path / 'pilot-state.json', {'status': 'running', 'pid': 99999999,
                                                    'process_start_ticks': '1'})
    status = run.status()
    assert status['status'] == 'interrupted'
    assert status['recorded_status'] == 'running'
    assert read_json(tmp_path / 'pilot-state.json')['status'] == 'running'
