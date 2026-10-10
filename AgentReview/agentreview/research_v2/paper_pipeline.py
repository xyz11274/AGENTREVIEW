"""Bounded durable handoff of complete reviews to independent score workers.

Only host-owned immutable tickets cross the boundary. A cohort lock excludes
duplicate schedulers; successful external receipts remain the recovery authority
when a process dies before committing its local completed index.
"""
from __future__ import annotations

import fcntl
import queue
import threading
import time
from pathlib import Path

from .io import atomic_write_json, read_json, value_sha256
from .execution_telemetry import span


class PipelineFailed(RuntimeError):
    failure_category = "shared_runtime"


class PaperPipeline:
    def __init__(self, root, *, identity, task_ids, review_workers=2,
                 score_workers=2, ready_capacity=4):
        self.root = Path(root)
        self.task_ids = list(task_ids)
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("Pipeline requires unique paper identities")
        for value in (review_workers, score_workers, ready_capacity):
            if type(value) is not int or value <= 0:
                raise ValueError("Pipeline capacities require positive integers")
        self.review_workers, self.score_workers = review_workers, score_workers
        self.capacity = ready_capacity
        self.binding = {"protocol": "durable-paper-pipeline-v1", "identity": identity,
                        "paper_ids": self.task_ids, "review_workers": review_workers,
                        "score_workers": score_workers, "ready_capacity": ready_capacity}
        self.lock = threading.RLock()
        self.cancel = threading.Event()

    def _path(self, paper_id, suffix="state"):
        return self.root / "tasks" / value_sha256(paper_id) / f"{suffix}.json"

    def run(self, *, generate, score, verify_ticket, verify_result):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "controller.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            atomic_write_json(self.root / "binding.json", self.binding)
            return self._run(generate, score, verify_ticket, verify_result)

    def _run(self, generate, score, verify_ticket, verify_result):
        self.cancel.clear()
        ready = queue.Queue(maxsize=self.capacity)
        permits = threading.BoundedSemaphore(self.capacity)
        pending, recovered = queue.Queue(), queue.Queue()
        states, results, errors = {}, {}, []
        stats = {"ready_peak": 0, "review_active": 0, "review_peak": 0,
                 "score_active": 0, "score_peak": 0, "reused_results": 0}

        def save(pid, **changes):
            with self.lock:
                states[pid].update(changes, updated_at=time.time())
                atomic_write_json(self._path(pid), states[pid], immutable=False)

        def ticket(pid):
            from .shared_experiment import bound
            value = read_json(bound(states[pid]["ticket"]))
            if value["paper_id"] != pid or value["cohort_sha256"] != value_sha256(self.binding):
                raise ValueError("Pipeline ticket belongs to another paper or cohort")
            verify_ticket(pid, value["payload"])
            return value["payload"]

        # Reconstruct immutable results first. No dispatch happens on a completed
        # task or for a cohort whose recorded failure needs explicit repair.
        for pid in self.task_ids:
            path = self._path(pid)
            state = read_json(path) if path.exists() else {
                "paper_id": pid, "cohort_sha256": value_sha256(self.binding), "phase": "pending"}
            if (state.get("paper_id") != pid or
                    state.get("cohort_sha256") != value_sha256(self.binding)):
                raise ValueError("Pipeline state identity differs from frozen cohort")
            states[pid] = state
            phase = state["phase"]
            if phase == "failed":
                raise PipelineFailed(f"Recorded paper failure requires repair: {pid}: {state['error']}")
            if phase == "scored":
                from .shared_experiment import bound
                value = read_json(bound(state["result"]))
                payload = ticket(pid)
                verify_result(pid, payload, value)
                results[pid] = value
                stats["reused_results"] += 1
            elif phase == "scoring":
                ticket(pid)
                recovered.put(pid)
            elif phase == "review_ready":
                ticket(pid)
                if not permits.acquire(blocking=False):
                    raise ValueError("Persisted ready queue exceeds its frozen capacity")
                ready.put_nowait(pid)
            elif phase in {"pending", "generating"}:
                pending.put(pid)
            else:
                raise ValueError(f"Unknown pipeline task phase: {phase}")
        if recovered.qsize() > self.score_workers:
            raise ValueError("Persisted in-flight scoring exceeds its frozen capacity")
        stats["ready_peak"] = ready.qsize()
        generation_finished = threading.Event()

        def active(kind, delta):
            with self.lock:
                key = kind + "_active"
                stats[key] += delta
                stats[kind + "_peak"] = max(stats[kind + "_peak"], stats[key])

        def fail(pid, exc):
            error = {"type": type(exc).__name__, "message": str(exc), "paper_id": pid}
            save(pid, phase="failed", error=error)
            with self.lock:
                errors.append(error)
            self.cancel.set()

        def review_worker():
            while not self.cancel.is_set():
                try:
                    pid = pending.get_nowait()
                except queue.Empty:
                    return
                acquired = False
                active("review", 1)
                try:
                    save(pid, phase="generating")
                    with span("paper_review_generation", paper_id=pid):
                        payload = generate(pid)
                        verify_ticket(pid, payload)
                    # Backpressure holds this review worker until there is space
                    # for a durable handoff. No unbounded executor submission.
                    while not self.cancel.is_set():
                        if permits.acquire(timeout=.05):
                            acquired = True
                            break
                    if self.cancel.is_set():
                        if acquired:
                            permits.release()
                        return
                    from .shared_experiment import binding
                    value = {"paper_id": pid, "cohort_sha256": value_sha256(self.binding), "payload": payload}
                    ref = binding(atomic_write_json(self._path(pid, "review-ready"), value))
                    # Commit-before-enqueue makes a crash between these operations
                    # replayable from the task index.
                    with self.lock:
                        save(pid, phase="review_ready", ticket=ref)
                        ready.put_nowait(pid)
                        stats["ready_peak"] = max(stats["ready_peak"], ready.qsize())
                    acquired = False  # Ownership transfers to the score worker.
                except Exception as exc:
                    if acquired:
                        permits.release()
                    fail(pid, exc)
                    return
                finally:
                    active("review", -1)

        def score_worker():
            while not self.cancel.is_set():
                try:
                    pid = recovered.get_nowait()
                except queue.Empty:
                    try:
                        pid = ready.get(timeout=.05)
                    except queue.Empty:
                        if generation_finished.is_set():
                            return
                        continue
                    # Persist the claim before releasing queue capacity. A crash
                    # must never reconstruct five READY rows for a capacity of 4.
                    with self.lock:
                        save(pid, phase="scoring")
                        permits.release()
                active("score", 1)
                try:
                    payload = ticket(pid)
                    save(pid, phase="scoring")
                    with span("paper_coco_scoring", paper_id=pid):
                        value = score(pid, payload)
                        verify_result(pid, payload, value)
                    from .shared_experiment import binding
                    ref = binding(atomic_write_json(self._path(pid, "completed"), value))
                    save(pid, phase="scored", result=ref)
                    with self.lock:
                        results[pid] = value
                except Exception as exc:
                    fail(pid, exc)
                    return
                finally:
                    active("score", -1)

        reviewers = [threading.Thread(target=review_worker, name=f"review-{i}")
                     for i in range(self.review_workers)]
        scorers = [threading.Thread(target=score_worker, name=f"score-{i}")
                   for i in range(self.score_workers)]
        began = time.monotonic()
        try:
            for worker in scorers + reviewers:
                worker.start()
            for worker in reviewers:
                worker.join()
            generation_finished.set()
            for worker in scorers:
                worker.join()
        finally:
            self.cancel.set()
            generation_finished.set()
            for worker in scorers + reviewers:
                if worker.ident is not None:
                    worker.join()
            atomic_write_json(self.root / "progress.json", {
                "status": "complete" if len(results) == len(self.task_ids) and not errors else "incomplete",
                "seconds": time.monotonic() - began, "paper_ids": self.task_ids,
                "scored_papers": [p for p in self.task_ids if p in results],
                "errors": errors, **stats}, immutable=False)
        if errors or len(results) != len(self.task_ids):
            raise PipelineFailed(f"Incomplete cohort; no candidate admission: {errors}")
        return {pid: results[pid] for pid in self.task_ids}
