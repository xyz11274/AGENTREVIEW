import json

from evolution_round_report import recovery_progress, winner_is_frozen
from runtime_report import coco_attempt_summary


def test_paused_recovery_lists_all_groups_and_rejects_failed_as_complete():
    state = {'champion': {'evaluation_epoch': 0}, 'results': {
        'old': {'status': 'complete', 'evaluation_epoch': 0},
        'failed': {'status': 'candidate_failed', 'evaluation_epoch': 1},
        'done': {'status': 'complete', 'evaluation_epoch': 1}}}
    policy = {'status': 'paused', 'evaluation_epoch': 1, 'pending_candidates': ['old', 'failed', 'done']}
    result = recovery_progress(state, policy)
    assert (result['completed'], result['pending'], result['displayed_epoch']) == (1, 2, 0)
    assert len(result['groups']) == 3


def test_historical_champion_and_unbound_winner_are_not_frozen(tmp_path):
    assert not winner_is_frozen(tmp_path, {'champion': {'evaluation_epoch': 0}}, {})
    assert not winner_is_frozen(tmp_path, {'winner': {'status': 'complete'}}, {})


def test_coco_retries_are_recovered_from_historical_spans(tmp_path):
    path = tmp_path / 'telemetry/worker-events.jsonl'
    path.parent.mkdir()
    rows = []
    for i in (1, 2, 3):
        tags = {'span': str(i), 'component': 'coco:judge', 'stage': 'api_request', 'attempt': i}
        rows.extend([{**tags, 'event': 'progress', 'status': 'failed',
                      'error': {'type': 'ImportError', 'message': 'attempted relative import'}},
                     {**tags, 'event': 'end', 'status': 'failed'}])
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    result = coco_attempt_summary(tmp_path)
    assert result['attempts'] == result['failed_attempts'] == 3
    assert result['repeated_attempts'] == 2
    assert result['failure_kinds'] == {'local_import': 3}
    assert coco_attempt_summary(tmp_path) == result
