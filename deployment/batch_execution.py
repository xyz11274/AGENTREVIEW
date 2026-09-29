"""Bounded paper generation and stage barriers around original CoCo calls."""
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import functools
import threading
import time

from agentreview.research_v2 import shared_experiment as shared, development_coco as coco
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.execution_telemetry import emit, span
from agentreview.research_v2.constants import ROLES

_LOCAL = threading.local()


class TokenSlots:
    def __init__(self, capacity):
        self.capacity, self.used = capacity, 0
        self.condition = threading.Condition()

    @contextmanager
    def reserve(self, tokens):
        if not 0 < tokens <= self.capacity:
            raise ValueError("Complete request cannot fit the aggregate in-flight token budget")
        started = time.monotonic()
        with self.condition:
            while self.used + tokens > self.capacity:
                self.condition.wait()
            self.used += tokens
            emit(event="admitted", stage="aggregate_token_admission", tokens=tokens,
                 reserved=self.used, capacity=self.capacity, wait_seconds=time.monotonic()-started)
        try:
            yield
        finally:
            with self.condition:
                self.used -= tokens
                self.condition.notify_all()


class BudgetedClient:
    def __init__(self, client, slots):
        self.client, self.slots, self.cache = client, slots, {}
        inner = getattr(client, "client", client)
        self.original_count = inner.count_chat_tokens
        # All callers in this paper's private client reuse the exact token count.
        inner.count_chat_tokens = self.count_chat_tokens

    def __getattr__(self, key):
        return getattr(self.client, key)

    def count_chat_tokens(self, messages):
        key = value_sha256(messages)
        if key not in self.cache:
            self.cache[key] = self.original_count(messages)
        return dict(self.cache[key])

    def complete(self, messages, *, max_tokens, **kwargs):
        tokens = self.count_chat_tokens(messages)["input_tokens"] + max_tokens
        with self.slots.reserve(tokens):
            return self.client.complete(messages, max_tokens=max_tokens, **kwargs)


def batch_failure(exc):
    return coco.DevelopmentCoCoFailure({"stage":"stage_batch", "category":"shared_evaluation",
        "message":f"Batch stopped after an unresolved peer failure: {type(exc).__name__}: {exc}"})


class StageBatch:
    """Every paper keeps its evaluator; only complete stage waves are scheduled."""
    def __init__(self, count, execute):
        self.count, self.execute = count, execute
        self.finished, self.pending, self.failure = 0, [], None
        self.condition = threading.Condition()

    def stage(self, kwargs):
        future = Future()
        with self.condition:
            if self.failure is not None:
                raise batch_failure(self.failure)
            self.pending.append((kwargs, future))
            self.condition.notify_all()
        return future.result()

    def participant(self, call):
        _LOCAL.batch = self
        try:
            return call()
        except BaseException as exc:
            with self.condition:
                if self.failure is None:
                    self.failure = exc
                self.condition.notify_all()
            raise
        finally:
            _LOCAL.batch = None
            with self.condition:
                self.finished += 1
                self.condition.notify_all()

    def abort(self, exc):
        with self.condition:
            self.failure = exc
            pending, self.pending = self.pending, []
            for _, future in pending:
                if not future.done():
                    future.set_exception(batch_failure(exc))
            self.condition.notify_all()

    def drive(self):
        try:
            while True:
                with self.condition:
                    while self.failure is None and self.finished + len(self.pending) < self.count:
                        self.condition.wait()
                    if self.failure is not None:
                        raise self.failure
                    if self.finished == self.count:
                        return
                    pending, self.pending = self.pending, []
                stages = {kwargs["stage"] for kwargs, _ in pending}
                if len(stages) != 1:
                    raise ValueError("Original CoCo pipelines reached different stage barriers")
                try:
                    results = self.execute(next(iter(stages)), [kwargs for kwargs, _ in pending])
                    if len(results) != len(pending):
                        raise ValueError("Every stage job requires its original result or failure")
                    first_error = next((error for _, error in results if error is not None), None)
                    if first_error is not None:
                        # Resolve all waiting callers without letting any next stage start.
                        with self.condition:
                            self.failure = first_error
                    for (_, future), (result, error) in zip(pending, results):
                        future.set_exception(error) if error else future.set_result(result)
                    if first_error is not None:
                        raise first_error
                except BaseException as exc:
                    for _, future in pending:
                        if not future.done():
                            future.set_exception(batch_failure(exc))
                    raise
        except BaseException as exc:
            self.abort(exc)
            raise


def bounded_map(call, values, workers):
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(call, value):index for index, value in enumerate(values)}
        try:
            for future in as_completed(pending):
                results[pending[future]] = future.result()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return [results[index] for index in range(len(values))]


def install(resources, *, paper_workers=4, batch_size=25, token_capacity=262144, deployment_identity=None):
    original_init = shared.LocalSharedBackend.__init__
    original_client = shared.LocalSharedBackend.client
    original_stage = coco._run_stage
    slots = TokenSlots(token_capacity)

    @functools.wraps(original_init)
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if self.execution is None:
            raise ValueError("Batch deployment requires the original global service admission profile")
        # This scheduling override is bound in the deployment appendix. Original
        # endpoint admission limits and all scientific configuration stay frozen.
        self.execution = {**self.execution, "paper_workers":paper_workers}

    @contextmanager
    @functools.wraps(original_client)
    def client(self, directory):
        with resources.reviewer():
            with original_client(self, directory) as instance:
                yield BudgetedClient(instance, slots)

    @functools.wraps(original_stage)
    def stage(**kwargs):
        batch = getattr(_LOCAL, "batch", None)
        if batch is not None:
            return batch.stage(kwargs)
        result, error = resources.execute(kwargs["stage"], [kwargs], original_stage)[0]
        if error:
            raise error
        return result

    def assess_batches(self, *, recipe, epoch, directory, generation_dir, rows, snapshots, embedding, embedding_identity):
        from agentreview.research_v2.cohort_receipt import compose
        references = rows[0]["assets"]["coco_reference"]
        if any(row["assets"]["coco_reference"] != references for row in rows):
            raise ValueError("Batch scoring requires one frozen partition reference manifest")
        reference = shared.bound(references)
        identity = {"experiment_protocol_sha256":self.protocol.protocol_sha256, "candidate":recipe["candidate"],
            "snapshots":{role:shared.binding(path) for role,path in snapshots.items()},
            "memory_enabled":recipe["memory_enabled"], "non_memory_conditions":self.conditions,
            "runtime":shared.runtime_identity(), "generation_identity":shared.generation_identity()}
        if deployment_identity is not None:
            identity["execution_deployment"] = deployment_identity
        all_results = []
        for offset in range(0, len(rows), batch_size):
            batch_rows = rows[offset:offset+batch_size]
            starts = {row["paper_id"]:time.monotonic() for row in batch_rows}
            complete_papers = set()
            for row in batch_rows:
                emit(event="start", stage="paper_full_flow", paper_id=row["paper_id"], candidate=recipe["key"])
            try:
                with span("batch_generation", candidate=recipe["key"], papers=[r["paper_id"] for r in batch_rows]):
                    generated = bounded_map(lambda row:self._generate_paper(recipe=recipe,row=row,generation_dir=generation_dir,
                        snapshots=snapshots,embedding=embedding,embedding_identity=embedding_identity), batch_rows, paper_workers)
                jobs = list(zip(batch_rows, generated))
                group = StageBatch(len(jobs), lambda name, values:resources.execute(name, values, original_stage))
                def score(job):
                    row, (review, roles) = job
                    pid = row["paper_id"]
                    part = directory / "coco" / "parts" / value_sha256(pid)
                    local_identity = {**identity,"role_reviews":roles}
                    manifest = atomic_write_json(part / "reviews.json", {"protocol":coco.REVIEW_MANIFEST_PROTOCOL,
                        "partition":recipe["split"],"paper_ids":[pid],"records":[review]})
                    coco.evaluate_final_reviews(review_manifest_path=manifest,reference_manifest_path=reference,
                        scoring_config_path=shared.bound(self.config["scoring_config"]),output_root=part,partition=recipe["split"],
                        run_id=self.config["experiment_id"],candidate_id=recipe["key"],evaluation_epoch=epoch,
                        candidate_identity=local_identity,purpose="final_test" if recipe["split"]=="sealed_test" else "development")
                    coco.validate_assessment_receipt(part / "assessment.json",expected_paper_ids=[pid],
                        candidate_identity=local_identity,expected_epoch=epoch)
                    complete_papers.add(pid)
                    emit(event="end",stage="paper_full_flow",paper_id=pid,candidate=recipe["key"],
                        status="complete",seconds=time.monotonic()-starts[pid])
                    return review, roles, shared.binding(part / "assessment.json")
                with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
                    futures = [executor.submit(group.participant, lambda job=job:score(job)) for job in jobs]
                    group.drive()
                    all_results.extend(future.result() for future in futures)
                atomic_write_json(directory / "batch-progress.json", {"status":"running" if len(all_results)<len(rows) else "complete",
                    "complete_papers":len(all_results),"expected_papers":len(rows),"batch_size":batch_size},immutable=False)
            except BaseException as exc:
                for row in batch_rows:
                    pid=row["paper_id"]
                    if pid not in complete_papers:
                        emit(event="end",stage="paper_full_flow",paper_id=pid,candidate=recipe["key"],
                            status="failed",error=str(exc),seconds=time.monotonic()-starts[pid])
                atomic_write_json(directory / "pipeline.failure.json", {"status":"failed","error":str(exc),
                    "complete_papers":len(all_results)+len(complete_papers),"admitted":False},immutable=False)
                raise
        identity["role_reviews"] = {role:[ref for _,roles,_ in all_results for ref in roles[role]] for role in ROLES}
        manifest = atomic_write_json(directory / "reviews.json", {"protocol":coco.REVIEW_MANIFEST_PROTOCOL,
            "partition":recipe["split"],"paper_ids":recipe["paper_ids"],"records":[review for review,_,_ in all_results]})
        receipt=compose(children=[ref for _,_,ref in all_results],review_manifest=manifest,candidate_identity=identity,output=directory / "coco")
        return {"status":"complete","evaluation_epoch":epoch,"static_score":receipt["mean_score"],
            "assessment":shared.binding(directory / "coco/assessment.json"),"identity":identity,"role_diagnostics":{}}

    shared.LocalSharedBackend.__init__ = init
    shared.LocalSharedBackend.client = client
    shared.LocalSharedBackend._assess_streaming = assess_batches
    coco._run_stage = stage
