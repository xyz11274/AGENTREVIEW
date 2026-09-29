"""Execution changes must retain atomic CoCo evidence and bounded resource ownership."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
import copy
import threading
import time

import pytest

import batch_execution as batch
import elastic_resources as elastic
from agentreview.research_v2 import development_coco as coco, shared_experiment as shared
from agentreview.research_v2.io import read_json, atomic_write_json, write_immutable_jsonl
from test_research_v2_shared_coco_integration import backend, recipe, install_model_scores


class FakeResources:
    def __init__(self):
        self.waves = []
    def reviewer(self):
        return nullcontext()
    def execute(self, stage, jobs, original):
        self.waves.append((stage, len(jobs)))
        return [(original(**job), None) for job in jobs]


def patch_install(monkeypatch, resources):
    # Register restoration before the deployment adapter changes class methods.
    for name in ("__init__", "client", "_assess_streaming"):
        monkeypatch.setattr(shared.LocalSharedBackend, name, getattr(shared.LocalSharedBackend, name))
    monkeypatch.setattr(coco, "_run_stage", coco._run_stage)
    batch.install(resources, deployment_identity={"path":"frozen-deployment", "sha256":"test"})


def expand_static(backend, count):
    template = backend.protocol.records("static")[0]
    ids = [f"paper-{n}" for n in range(count)]
    benchmark = write_immutable_jsonl(backend.root / "bench.jsonl", [{"id":pid} for pid in ids])
    human = write_immutable_jsonl(backend.root / "human.jsonl", [{"id":pid} for pid in ids])
    selection = atomic_write_json(backend.root / "select.json", dict.fromkeys(ids, "Reviewer 1"))
    ref = atomic_write_json(backend.root / "references.json", {"protocol":coco.REFERENCE_MANIFEST_PROTOCOL,
        "partition":"static","paper_ids":ids,"benchmark_files":[shared.binding(benchmark)],
        "complete_human_classify_file":shared.binding(human),"reviewer_selection_file":shared.binding(selection)})
    rows = []
    for pid in ids:
        row = copy.deepcopy(template)
        row["paper_id"] = pid
        paper = read_json(template["assets"]["paper"]["path"])
        paper["paper_id"] = pid
        row["assets"].update(paper=shared.binding(atomic_write_json(backend.root / f"{pid}.json", paper)), coco_reference=shared.binding(ref))
        rows.append(row)
    backend.protocol.splits["static"] = rows
    backend.execution = {"paper_workers":4}
    return ids


def test_batch_keeps_original_atomic_receipts_and_independent_role_state(backend, monkeypatch):
    ids = expand_static(backend, 4)
    calls = install_model_scores(monkeypatch)
    owner = FakeResources()
    patch_install(monkeypatch, owner)
    candidate = {**recipe(backend), "paper_ids":ids}
    result = backend.assess(candidate, epoch=0)
    receipt = backend.validate(result, recipe=candidate, epoch=0)
    assert result["static_score"] == 2.0
    assert owner.waves == [("split",4),("classify",4),("evaluation",4)]
    assert len(receipt["children"]) == len(calls) == 4
    for refs in result["identity"]["role_reviews"].values():
        assert [ref["paper_id"] for ref in refs] == ids
        assert len({ref["path"] for ref in refs}) == 4
        assert len({read_json(ref["path"])["memory_snapshot_id"] for ref in refs}) == 1
    # Resuming an already complete cohort does not dispatch work or load models.
    again = backend.assess(candidate, epoch=0)
    assert again == result
    assert len(owner.waves) == 3


def test_batch_failure_cannot_admit_partial_score(backend, monkeypatch):
    ids = expand_static(backend, 2)
    install_model_scores(monkeypatch, fail_role="final")
    owner = FakeResources()
    patch_install(monkeypatch, owner)
    with pytest.raises(coco.DevelopmentCoCoFailure):
        backend.assess({**recipe(backend), "paper_ids":ids}, epoch=0)
    assert not list(backend.root.glob("assessments/**/result.json"))
    failures = list(backend.root.rglob("pipeline.failure.json"))
    assert len(failures) == 1 and read_json(failures[0])["admitted"] is False


def test_preflight_failure_unblocks_waiting_stage_peers():
    group = batch.StageBatch(2, lambda *args:pytest.fail("No incomplete wave may be dispatched"))
    arrived = threading.Event()
    def waiting():
        arrived.set()
        group.stage({"stage":"split"})
    def failed():
        assert arrived.wait(2)
        raise ValueError("invalid original receipt input")
    with ThreadPoolExecutor(2) as pool:
        one, two = pool.submit(group.participant, waiting), pool.submit(group.participant, failed)
        with pytest.raises(ValueError, match="invalid original"):
            group.drive()
        with pytest.raises(coco.DevelopmentCoCoFailure):
            one.result(timeout=2)
        with pytest.raises(ValueError):
            two.result(timeout=2)


def test_token_admission_preserves_budget_and_releases_on_error():
    slots = batch.TokenSlots(100)
    entered, done = threading.Event(), threading.Event()
    def wait():
        entered.set()
        with slots.reserve(60):
            done.set()
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError, match="request failure"):
            with slots.reserve(60):
                pending = pool.submit(wait)
                assert entered.wait(2)
                assert not done.wait(0.05)
                raise ValueError("request failure")
        pending.result(timeout=2)
    assert slots.used == 0 and done.is_set()
    with pytest.raises(ValueError, match="cannot fit"):
        with slots.reserve(101):
            pytest.fail("Oversized request admitted")


def test_client_reuses_exact_counts_without_changing_logical_retry_owner():
    class Client:
        retry_policy = {"owner":"persistent-logical-request", "max_attempts":3}
        calls = 0
        def count_chat_tokens(self, messages):
            self.calls += 1
            return {"input_tokens":12, "max_model_len":131072}
        def complete(self, messages, **kwargs):
            assert kwargs["max_tokens"] == 40
            assert kwargs["response_format"] == {"type":"json_object"}
            assert self.count_chat_tokens(messages)["input_tokens"] == 12
            return "complete response"
    inner = Client()
    client = batch.BudgetedClient(inner, batch.TokenSlots(100))
    messages = [{"role":"user", "content":"Full uncut paper"}]
    client.count_chat_tokens(messages)
    assert client.complete(messages, max_tokens=40, component="review", response_format={"type":"json_object"}) == "complete response"
    assert inner.calls == 1 and client.retry_policy == inner.retry_policy


def test_allowlist_rejects_seven_and_idle_requires_empty_compute():
    with pytest.raises(ValueError, match="permanently excluded"):
        elastic.BorrowPool({"borrowable_gpus":[4,5,6,7],"excluded_gpus":[]}, {}, lambda **_:None)
    assert elastic.is_idle({"pids":[], "memory":18,"utilization":0})
    assert not elastic.is_idle({"pids":[123],"memory":18,"utilization":0})
    assert not elastic.is_idle({"pids":[],"memory":900,"utilization":0})


@pytest.mark.parametrize("fail", [False, True])
def test_temporary_stage_releases_on_success_or_original_failure(tmp_path, fail):
    owner = object.__new__(elastic.BatchResources)
    owner.config = {"stage_workers":2}
    owner.base = {"service_config":{}}
    class Pool:
        released = []
        def acquire(self, mode, count):
            return "lease"
        def release(self, lease):
            self.released.append(lease)
        def revoked(self, lease):
            return False
    owner.pool = Pool()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(elastic, "manage", lambda *args:{"running":False})
        def original(**job):
            if fail:
                raise RuntimeError("original stage error")
            return job["output"]
        jobs = [{"stage":"split","output":tmp_path / str(i)} for i in range(3)]
        if fail:
            with pytest.raises(RuntimeError, match="original stage"):
                owner.execute("split", jobs, original)
        else:
            assert len(owner.execute("split", jobs, original)) == 3
        assert owner.pool.released == ["lease"]


def test_no_idle_pair_falls_back_to_base_cards(tmp_path, monkeypatch):
    owner = object.__new__(elastic.BatchResources)
    owner.config = {"stage_workers":2}
    owner.base = {"service_config":{}}
    class Pool:
        def acquire(self, *args):
            return None
    modes = []
    class Coordinator:
        @contextmanager
        def lease(self, mode):
            modes.append(mode)
            yield
    owner.pool, owner.coordinator = Pool(), Coordinator()
    monkeypatch.setattr(elastic, "manage", lambda *args:{"running":False})
    assert owner.execute("split", [{"output":tmp_path}], lambda **kwargs:"score") == [("score",None)]
    assert modes == ["split"]


def test_cached_stages_do_not_request_any_gpu(tmp_path):
    owner = object.__new__(elastic.BatchResources)
    owner.config = {"stage_workers":2}
    atomic_write_json(tmp_path / "stages/split/state.json", {"status":"complete"})
    assert owner.execute("split", [{"output":tmp_path}], lambda **kwargs:"validated cached result") == [("validated cached result",None)]


def test_idle_interval_resets_when_another_job_appears():
    pool = object.__new__(elastic.BorrowPool)
    pool.mutex, pool.idle_since = threading.Lock(), {}
    quiet = {gpu:{"pids":[], "memory":18,"utilization":0} for gpu in (4,5,6,7)}
    pool.observe(quiet, 100)
    pool.observe(quiet, 129)
    assert pool.idle_since == {4:100,5:100,6:100}
    pool.observe({**quiet,4:{"pids":[999], "memory":18,"utilization":0}},130)
    pool.observe(quiet,131)
    assert pool.idle_since == {4:131,5:100,6:100}


def test_guardian_cleans_owned_service_after_controller_disappears(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    import signal
    controller, service = {"pid":100,"uid":os.getuid(),"start_time":"1"}, None
    stopped = []
    atomic_write_json(tmp_path / "lease.json", {"parent":controller,"role":"split",
        "base_config":{"event_root":str(tmp_path / "events")},
        "service_config":{"services":{"split":{"gpus":[4,5]}}}})
    original_same = elastic.same_process
    monkeypatch.setattr(elastic, "gpu_snapshot", lambda:{g:{"pids":[],"memory":18,"utilization":0} for g in (4,5,6)})
    parent_alive = [True]
    monkeypatch.setattr(elastic,"same_process",lambda ref:parent_alive[0] if ref is controller or ref["pid"]==100 else original_same(ref))
    def manage(config, role, action):
        nonlocal service
        assert action == "start"
        service = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"], start_new_session=True)
        record = {"process":elastic.process_identity(service.pid)}
        parent_alive[0] = False
        return record
    def stop(config, role, record, known):
        assert original_same(record["process"])
        os.kill(record["process"]["pid"],signal.SIGTERM)
        service.wait(timeout=5)
        stopped.append(record["process"]["pid"])
    monkeypatch.setattr(elastic,"manage",manage)
    monkeypatch.setattr(elastic,"stop_owned",stop)
    old_signals = {s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    fd = os.open(tmp_path / "gpu.lock",os.O_CREAT|os.O_RDWR,0o600)
    try:
        elastic.guardian(tmp_path,[fd])
        status = read_json(tmp_path / "status.json")
        assert status["status"] == "released" and status["reason"] == "controller exited"
        assert stopped == [service.pid] and not status["owned_processes_remaining"]
    finally:
        for sig, handler in old_signals.items():
            signal.signal(sig,handler)
        if service and service.poll() is None:
            service.terminate()
            service.wait(timeout=5)
