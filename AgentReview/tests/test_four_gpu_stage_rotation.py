"""Deployment leases must never evict a model with live scientific work."""
import copy
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deployment"))
from four_gpu_study import ModeCoordinator, mode_config, install


def test_same_mode_overlaps_but_other_mode_waits_for_both_complete_papers():
    changes = []
    c = ModeCoordinator(changes.append)
    entered = threading.Barrier(3)
    release = threading.Event()
    split_started = threading.Event()
    failures = []
    def paper():
        try:
            with c.lease("reviewer"):
                entered.wait(timeout=5)
                assert release.wait(timeout=5)
        except BaseException as exc: failures.append(exc)
    def split():
        try:
            with c.lease("split"):
                assert release.is_set()
                split_started.set()
        except BaseException as exc: failures.append(exc)
    workers = [threading.Thread(target=paper) for _ in range(2)]
    for t in workers: t.start()
    entered.wait(timeout=5)
    scorer = threading.Thread(target=split); scorer.start()
    assert not split_started.wait(timeout=.05)
    assert changes == ["reviewer"] and c.active == 2
    release.set()
    for t in [*workers,scorer]: t.join(timeout=5)
    assert not failures and all(not t.is_alive() for t in [*workers,scorer])
    assert changes == ["reviewer","split"] and c.active == 0


def test_pending_stage_cannot_be_starved_by_more_generation():
    order=[]
    c=ModeCoordinator(order.append)
    first=c.lease("reviewer"); first.__enter__()
    def acquire(mode):
        with c.lease(mode): pass
    split=threading.Thread(target=acquire,args=("split",)); split.start()
    deadline=time.monotonic()+5
    while not c.queue:
        assert time.monotonic()<deadline
        time.sleep(.001)
    reviewer=threading.Thread(target=acquire,args=("reviewer",)); reviewer.start()
    first.__exit__(None,None,None)
    split.join(timeout=5); reviewer.join(timeout=5)
    assert not split.is_alive() and not reviewer.is_alive()
    assert order==["reviewer","split","reviewer"]


def test_loading_failure_never_enters_stage_or_retries_in_another_waiter():
    calls=[]
    def fail(mode):
        calls.append(mode)
        raise RuntimeError("model load failed")
    c=ModeCoordinator(fail)
    with pytest.raises(RuntimeError,match="model load failed"):
        with c.lease("split"): pytest.fail("must not reserve an original CoCo request")
    with pytest.raises(RuntimeError,match="diagnosis required"):
        with c.lease("reviewer"): pytest.fail("must not dispatch generation")
    assert calls==["split"] and c.active==0 and not c.queue


def test_exception_releases_mode_without_changing_request_result():
    c=ModeCoordinator(lambda mode:None)
    with pytest.raises(ValueError,match="original failure"):
        with c.lease("reviewer"): raise ValueError("original failure")
    with c.lease("judge"): assert c.active==1
    assert c.active==0


def test_exact_gpu_allowlist_and_separate_original_contexts():
    config={"allowed_gpus":[0,1,2,3],"service_config":{"services":{
        "judge":{"gpus":[0,1,2,3],"max_model_len":65536},
        "split":{"gpus":[0,1],"max_model_len":65536},
        "classify":{"gpus":[0,1],"max_model_len":32768}}}}
    before=copy.deepcopy(config)
    for mode,expected in [("reviewer",131072),("split",65536),("classify",32768),("judge",65536)]:
        value,role=mode_config(config,mode)
        assert value['services'][role]['max_model_len']==expected
        assert set(value['services'][role]['gpus'])<=set(range(4))
    assert config==before
    config['service_config']['services']['judge']['gpus']=[4,5,6,7]
    with pytest.raises(ValueError,match="outside"): mode_config(config,"reviewer")


def test_adapter_scopes_whole_clients_and_whole_coco_stages(monkeypatch):
    from agentreview.research_v2 import shared_experiment as shared, development_coco as coco
    c=ModeCoordinator(lambda mode:None)
    sentinel=object(); calls=[]
    @contextmanager
    def client(self,directory):
        assert c.mode=='reviewer' and c.active==1
        calls.append(('client',directory))
        yield sentinel
        assert c.active==1  # Also holds while response artifacts are closed.
    def stage(**kwargs):
        assert c.mode==('judge' if kwargs['stage']=='evaluation' else kwargs['stage']) and c.active==1
        calls.append(copy.deepcopy(kwargs))
        return sentinel
    monkeypatch.setattr(shared.LocalSharedBackend,'client',client)
    monkeypatch.setattr(coco,'_run_stage',stage)
    install(c)
    with shared.LocalSharedBackend.client(None,'exact-directory') as value:
        assert value is sentinel and c.active==1
    for name in ['split','classify','evaluation']:
        kwargs={'stage':name,'request_path':'frozen-request','output':'same-output','config':{'max_stage_attempts':3}}
        assert coco._run_stage(**kwargs) is sentinel
        assert calls[-1]==kwargs and c.active==0
