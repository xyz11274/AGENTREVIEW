"""Concurrency, durable handoff and crash recovery at the real host boundary."""
import multiprocessing
import os
import threading
import time

import pytest

from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json
from agentreview.research_v2.paper_pipeline import PaperPipeline, PipelineFailed


def test_service_capacity_cannot_be_silently_changed(tmp_path):
    from agentreview.research_v2.request_admission import Slot
    with Slot(tmp_path, 'classify', 2):
        with pytest.raises(ValueError, match='capacity'):
            Slot(tmp_path, 'classify', 4)


def test_v3_execution_profile_binds_distinct_workers_and_global_caps(tmp_path, monkeypatch):
    from agentreview.research_v2.request_admission import profile
    p = atomic_write_json(tmp_path / 'execution.json', {
        'protocol': 'shared-api-execution-v3', 'review_paper_workers': 2,
        'coco_paper_workers': 2, 'review_ready_capacity': 4, 'role_workers': 2,
        'admission_root': str(tmp_path / 'shared-service-slots'),
        'limits': {'http://127.0.0.1:14298/v1': 32, 'https://cloud/v1': 8}})
    monkeypatch.setenv('AGENTREVIEW_EXECUTION_PROFILE', str(p))
    monkeypatch.setenv('AGENTREVIEW_EXECUTION_PROFILE_SHA256', file_sha256(p))
    assert profile()['coco_paper_workers'] == 2


def test_review_handoff_releases_slot_before_scoring_and_keeps_order(tmp_path):
    first_score = threading.Event()
    third_review = threading.Event()
    def generate(pid):
        if pid == '2':
            third_review.set()
        return {'review': pid}
    def score(pid, ticket):
        first_score.set()
        if pid == '0':
            assert third_review.wait(2), 'third paper cannot wait for a scoring slot to finish'
        time.sleep(.01 if pid == '0' else .025)
        return {'paper_id': pid, 'review': ticket['review'], 'score': float(pid)}
    def verify(pid, payload):
        assert payload['review'] == pid
    def verify_result(pid, payload, value):
        assert value['paper_id'] == value['review'] == payload['review'] == pid
    flow = PaperPipeline(tmp_path, identity={'candidate': 'a'}, task_ids=list('0123456789'))
    result = flow.run(generate=generate, score=score, verify_ticket=verify, verify_result=verify_result)
    assert list(result) == list('0123456789')
    stats = read_json(tmp_path / 'progress.json')
    assert stats['ready_peak'] <= 4 and stats['score_peak'] <= 2 and stats['review_peak'] <= 2
    assert stats['score_peak'] == 2
    def forbidden(*args):
        pytest.fail('Completed cohort must not generate or score again')
    assert flow.run(generate=forbidden, score=forbidden, verify_ticket=verify,
                    verify_result=verify_result) == result
    assert read_json(tmp_path / 'progress.json')['reused_results'] == 10


def _crash_child(root):
    def generate(pid):
        return {'review': pid}
    def score(pid, payload):
        atomic_write_json(root / 'external-score.json', {'paper_id': pid, 'score': 3.14})
        os._exit(17)  # successful external receipt, before pipeline index commit
    PaperPipeline(root / 'queue', identity={'candidate': 'a'}, task_ids=['p']).run(
        generate=generate, score=score, verify_ticket=lambda *args: None,
        verify_result=lambda *args: None)


def test_process_crash_after_score_receipt_recovers_without_duplicate_call(tmp_path):
    process = multiprocessing.get_context('fork').Process(target=_crash_child, args=(tmp_path,))
    process.start(); process.join(5)
    assert process.exitcode == 17
    assert (tmp_path / 'external-score.json').exists()
    def no_generate(*args):
        pytest.fail('Durable ready review must survive a scoring crash')
    def score(pid, payload):
        # The production scorer likewise validates and reuses the receipt.
        return read_json(tmp_path / 'external-score.json')
    result = PaperPipeline(tmp_path / 'queue', identity={'candidate': 'a'}, task_ids=['p']).run(
        generate=no_generate, score=score, verify_ticket=lambda *args: None,
        verify_result=lambda pid, payload, value: value['paper_id'] == pid)
    assert result['p']['score'] == 3.14


def test_failed_score_closes_cohort_and_never_becomes_zero(tmp_path):
    def score(pid, payload):
        raise TimeoutError('incomplete Judge response')
    def run():
        return PaperPipeline(tmp_path, identity={'candidate': 'a'}, task_ids=['p']).run(
            generate=lambda pid: {'review': pid}, score=score,
            verify_ticket=lambda *args: None, verify_result=lambda *args: None)
    with pytest.raises(PipelineFailed, match='Incomplete cohort'):
        run()
    assert read_json(tmp_path / 'progress.json')['scored_papers'] == []
    with pytest.raises(PipelineFailed, match='Recorded paper failure'):
        run()


def test_changed_candidate_or_ticket_is_rejected_before_dispatch(tmp_path):
    def run(identity):
        return PaperPipeline(tmp_path, identity=identity, task_ids=['p']).run(
            generate=lambda pid: {'review': pid}, score=lambda pid, payload: {'score': 3},
            verify_ticket=lambda *args: None, verify_result=lambda *args: None)
    run({'candidate': 'a'})
    with pytest.raises(FileExistsError):
        run({'candidate': 'b'})
    ticket = next((tmp_path / 'tasks').glob('*/review-ready.json'))
    value = read_json(ticket); value['payload']['review'] = 'other-paper'
    atomic_write_json(ticket, value, immutable=False)
    with pytest.raises(ValueError, match='identity'):
        run({'candidate': 'a'})
