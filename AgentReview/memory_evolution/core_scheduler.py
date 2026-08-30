"""Bounded global Qwen scheduling and deterministic paper DAG orchestration."""

from __future__ import annotations

import threading
import time
import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple


ROLE_ORDER = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")


@dataclass(frozen=True)
class LogicalQwenRequest:
    logical_request_id: str
    role: str
    logical_request_sequence: int
    request_class: str
    payload: Any


@dataclass(frozen=True)
class QwenExecutionRecord:
    logical_request_id: str
    role: str
    logical_request_sequence: int
    dispatch_ordinal: int
    started_monotonic: float
    finished_monotonic: float
    result: Any


class GlobalQwenScheduler:
    """One global sequence budget with role round-robin and role-local FIFO."""

    def __init__(self, max_inflight_sequences: int):
        capacity = int(max_inflight_sequences)
        if capacity not in {1, 2, 4}:
            raise ValueError("Core v1 preformal Qwen capacity must be 1, 2, or 4")
        self.capacity = capacity

    def run(
        self,
        requests: Iterable[LogicalQwenRequest],
        execute: Callable[[LogicalQwenRequest], Any],
    ) -> Tuple[QwenExecutionRecord, ...]:
        queues: Dict[str, list[LogicalQwenRequest]] = {role: [] for role in ROLE_ORDER}
        ids = set()
        for request in requests:
            if request.role not in queues:
                raise ValueError("Unknown scheduler role")
            if request.logical_request_id in ids:
                raise ValueError("Duplicate logical Qwen request id")
            ids.add(request.logical_request_id)
            queues[request.role].append(request)
        for role, queue in queues.items():
            queue.sort(key=lambda item: item.logical_request_sequence)
            sequences = [item.logical_request_sequence for item in queue]
            if len(sequences) != len(set(sequences)):
                raise ValueError(f"Duplicate role-local request sequence for {role}")

        cursor = 0
        dispatch_ordinal = 0
        completed: Dict[str, QwenExecutionRecord] = {}
        inflight = {}
        lock = threading.Lock()

        def next_request():
            nonlocal cursor
            for _ in ROLE_ORDER:
                role = ROLE_ORDER[cursor]
                cursor = (cursor + 1) % len(ROLE_ORDER)
                if queues[role]:
                    return queues[role].pop(0)
            return None

        def invoke(request, ordinal):
            started = time.monotonic()
            result = execute(request)
            finished = time.monotonic()
            return QwenExecutionRecord(
                request.logical_request_id,
                request.role,
                request.logical_request_sequence,
                ordinal,
                started,
                finished,
                result,
            )

        with ThreadPoolExecutor(max_workers=self.capacity) as executor:
            while True:
                while len(inflight) < self.capacity:
                    request = next_request()
                    if request is None:
                        break
                    ordinal = dispatch_ordinal
                    dispatch_ordinal += 1
                    future = executor.submit(invoke, request, ordinal)
                    inflight[future] = request.logical_request_id
                if not inflight:
                    break
                done, _ = wait(tuple(inflight), return_when=FIRST_COMPLETED)
                # Multiple simultaneous completions are committed by prior dispatch
                # order, never by arbitrary set iteration order.
                records = sorted((future.result() for future in done), key=lambda x: x.dispatch_ordinal)
                for future in done:
                    inflight.pop(future)
                with lock:
                    for record in records:
                        completed[record.logical_request_id] = record
        return tuple(sorted(completed.values(), key=lambda value: value.dispatch_ordinal))


class GlobalQwenService:
    """Long-lived synchronous gateway over one global Qwen sequence budget."""

    def __init__(self, max_inflight_sequences: int):
        if int(max_inflight_sequences) not in {1, 2, 4}:
            raise ValueError("Core v1 Qwen capacity must be 1, 2, or 4")
        self.capacity = int(max_inflight_sequences)
        self._condition = threading.Condition()
        self._queues = {role: [] for role in ROLE_ORDER}
        self._last_sequence = {role: -1 for role in ROLE_ORDER}
        self._ids = set()
        self._cursor = 0
        self._dispatch_ordinal = 0
        self._inflight = 0
        self._closing = False
        self._records = []
        self._executor = ThreadPoolExecutor(max_workers=self.capacity)
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    @property
    def execution_records(self) -> Tuple[QwenExecutionRecord, ...]:
        with self._condition:
            return tuple(sorted(self._records, key=lambda item: item.dispatch_ordinal))

    def _next_locked(self):
        for _ in ROLE_ORDER:
            role = ROLE_ORDER[self._cursor]
            self._cursor = (self._cursor + 1) % len(ROLE_ORDER)
            if self._queues[role]:
                return self._queues[role].pop(0)
        return None

    def submit(
        self,
        request: LogicalQwenRequest,
        execute: Callable[[LogicalQwenRequest], Any],
    ) -> Future:
        future = Future()
        with self._condition:
            if self._closing:
                raise RuntimeError("Global Qwen service is closing")
            if request.role not in self._queues:
                raise ValueError("Unknown scheduler role")
            if request.logical_request_id in self._ids:
                raise ValueError("Duplicate logical Qwen request id")
            if request.logical_request_sequence <= self._last_sequence[request.role]:
                raise ValueError("Role-local Qwen sequence is not monotonic")
            self._ids.add(request.logical_request_id)
            self._last_sequence[request.role] = request.logical_request_sequence
            self._queues[request.role].append((request, execute, future))
            self._condition.notify_all()
        return future

    def call(
        self,
        request: LogicalQwenRequest,
        execute: Callable[[LogicalQwenRequest], Any],
    ) -> Any:
        return self.submit(request, execute).result()

    def _dispatch_loop(self):
        while True:
            with self._condition:
                while True:
                    pending = any(self._queues.values())
                    if self._closing and not pending and self._inflight == 0:
                        return
                    if pending and self._inflight < self.capacity:
                        item = self._next_locked()
                        break
                    self._condition.wait()
                request, execute, caller_future = item
                ordinal = self._dispatch_ordinal
                self._dispatch_ordinal += 1
                self._inflight += 1

            def invoke(req=request, fn=execute, order=ordinal):
                started = time.monotonic()
                value = fn(req)
                return QwenExecutionRecord(
                    req.logical_request_id,
                    req.role,
                    req.logical_request_sequence,
                    order,
                    started,
                    time.monotonic(),
                    value,
                )

            worker = self._executor.submit(invoke)

            def completed(done, target=caller_future):
                try:
                    record = done.result()
                except BaseException as exc:
                    target.set_exception(exc)
                else:
                    with self._condition:
                        self._records.append(record)
                    target.set_result(record.result)
                finally:
                    with self._condition:
                        self._inflight -= 1
                        self._condition.notify_all()

            worker.add_done_callback(completed)

    def close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._dispatcher.join()
        self._executor.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class ScheduledToolkitProxy:
    """Route every toolkit LLM completion through the shared Qwen service."""

    def __init__(self, base_toolkit: Any, service: GlobalQwenService, role: str):
        if role not in ROLE_ORDER:
            raise ValueError("Scheduled toolkit requires a specialist role")
        self._base = base_toolkit
        self._service = service
        self._role = role
        self._sequence = 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._base, name)

    def llm_completion(self, messages, **kwargs):
        with self._lock:
            sequence = self._sequence
            self._sequence += 1
        payload = {
            "messages": list(messages),
            "options": dict(kwargs),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request_class = str(kwargs.get("agent_name") or "qwen_completion")
        logical_id = "qreq_" + hashlib.sha256(
            (
                self._role
                + "\n"
                + str(sequence)
                + "\n"
                + request_class
                + "\n"
            ).encode("utf-8")
            + encoded
        ).hexdigest()
        request = LogicalQwenRequest(
            logical_id,
            self._role,
            sequence,
            request_class,
            payload,
        )
        return self._service.call(
            request,
            lambda _: self._base.llm_completion(messages, **kwargs),
        )


@dataclass(frozen=True)
class PaperDagInput:
    paper_id: str
    role: str
    paper_order: int
    payload: Any


@dataclass(frozen=True)
class PaperDagResult:
    paper_id: str
    role: str
    paper_order: int
    context: Any
    evidence_availability: Any
    reviewer_output: Any
    applicability: Any
    scoring: Any


def run_bounded_paper_dags(
    papers: Sequence[PaperDagInput],
    *,
    active_paper_window: int,
    build_context: Callable[[PaperDagInput], Any],
    build_evidence_availability: Callable[[PaperDagInput], Any],
    run_reviewer: Callable[[PaperDagInput, Any, Any], Any],
    run_applicability: Callable[[PaperDagInput, Any, Any], Any],
    run_scoring: Callable[[PaperDagInput, Any, Any, Any, Any], Any],
) -> Tuple[PaperDagResult, ...]:
    """Run the frozen per-paper DAG with deterministic aggregate ordering.

    Context and evidence are built first. Reviewer/Memory and Candidate-blind
    applicability then run concurrently. Scoring begins only after both finish.
    """
    window = max(1, int(active_paper_window))

    def one(item: PaperDagInput) -> PaperDagResult:
        with ThreadPoolExecutor(max_workers=2) as first:
            context_future = first.submit(build_context, item)
            evidence_future = first.submit(build_evidence_availability, item)
            context = context_future.result()
            evidence = evidence_future.result()
        with ThreadPoolExecutor(max_workers=2) as middle:
            reviewer_future = middle.submit(run_reviewer, item, context, evidence)
            applicability_future = middle.submit(
                run_applicability, item, context, evidence
            )
            reviewer = reviewer_future.result()
            applicability = applicability_future.result()
        scoring = run_scoring(item, context, evidence, reviewer, applicability)
        return PaperDagResult(
            item.paper_id,
            item.role,
            item.paper_order,
            context,
            evidence,
            reviewer,
            applicability,
            scoring,
        )

    with ThreadPoolExecutor(max_workers=window) as executor:
        futures = [executor.submit(one, item) for item in papers]
        results = [future.result() for future in futures]
    return tuple(sorted(results, key=lambda value: value.paper_order))
