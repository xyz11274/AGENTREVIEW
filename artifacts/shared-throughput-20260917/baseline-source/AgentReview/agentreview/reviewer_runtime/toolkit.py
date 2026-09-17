"""Capabilities supplied to optimizer-produced MemoryPrograms."""

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any


def _load_s2_entries(path: str | None):
    """Load a JSON/JSONL paper-id keyed S2 pool and return it with its hash."""
    if not path:
        return [], ""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Frozen S2 retrieval pool not found: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    values = []
    if source.suffix.lower() == ".jsonl":
        values = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            values = [
                {"paper_id": key, "retrieved_papers": value}
                if isinstance(value, list)
                else {"paper_id": key, **value}
                for key, value in payload.items()
                if isinstance(value, (list, dict))
            ]
        elif isinstance(payload, list):
            values = payload
    return [value for value in values if isinstance(value, dict)], digest


def load_frozen_s2_pool(path: str | None):
    """Load a frozen pool after preserving per-target provenance for audits."""
    values, digest = _load_s2_entries(path)
    pool = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        paper_id = str(
            value.get("paper_id") or value.get("id") or value.get("query_id") or ""
        )
        papers = (
            value.get("retrieved_papers")
            or value.get("papers")
            or value.get("results")
            or []
        )
        if paper_id and isinstance(papers, list):
            cutoff_text = str(value.get("paper_review_cutoff") or "")
            safe = []
            if cutoff_text:
                try:
                    cutoff = date.fromisoformat(cutoff_text[:10])
                except ValueError:
                    cutoff = None
                if cutoff is not None:
                    for item in papers:
                        if not isinstance(item, dict):
                            continue
                        try:
                            if _publication_cutoff(item) <= cutoff:
                                safe.append(item)
                        except (TypeError, ValueError):
                            continue
            pool[paper_id] = safe
    return pool, digest


def frozen_s2_pool_version(path: str | Path) -> str:
    """Read the declared pool identity without guessing it from the filename."""
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid Frozen S2 JSONL at {source}:{line_number}"
                ) from exc
            version = record.get("pool_version") if isinstance(record, dict) else None
            if not isinstance(version, str) or not version.strip():
                raise RuntimeError(
                    f"Frozen S2 record lacks pool_version at {source}:{line_number}"
                )
            return version.strip()
    raise RuntimeError(f"Frozen S2 pool is empty: {source}")


def _publication_cutoff(value: dict[str, Any]) -> date:
    raw = value.get("publication_date") or value.get("publicationDate")
    if raw:
        return date.fromisoformat(str(raw)[:10])
    year = value.get("year") or value.get("publication_year")
    if year:
        return date(int(year), 12, 31)
    raise ValueError("retrieved paper lacks publication_date/year")


def validate_frozen_s2_pool(
    path: str,
    *,
    expected_cutoffs: dict[str, str],
    required_ids: Iterable[str],
    expected_pool_version: str = "frozen-s2-temporal-v1",
    expected_queries: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prove that every frozen result existed no later than target review time."""
    entries, digest = _load_s2_entries(path)
    required_id_set = {str(value) for value in required_ids}
    by_id = {}
    future = []
    provenance_errors = []
    canonical_mismatches = []
    polluted_query_titles = []
    for entry in entries:
        paper_id = str(
            entry.get("paper_id") or entry.get("id") or entry.get("query_id") or ""
        )
        if not paper_id or paper_id in by_id:
            provenance_errors.append(f"missing/duplicate target identity: {paper_id!r}")
            continue
        by_id[paper_id] = entry
        cutoff_text = str(entry.get("paper_review_cutoff") or "")
        required = (
            "target_identity",
            "retrieval_method",
            "retrieval_version",
            "top_k",
            "retrieval_config",
            "result_sha256",
            "pool_version",
        )
        missing = [name for name in required if entry.get(name) in (None, "")]
        if not cutoff_text:
            missing.append("paper_review_cutoff")
        expected = str(expected_cutoffs.get(paper_id) or "")
        if expected and cutoff_text != expected:
            provenance_errors.append(
                f"{paper_id}: cutoff mismatch expected={expected} actual={cutoff_text}"
            )
        if missing:
            provenance_errors.append(
                f"{paper_id}: missing provenance {sorted(set(missing))}"
            )
            continue
        target_identity = entry.get("target_identity")
        target_identity_id = (
            str(target_identity.get("paper_id") or "")
            if isinstance(target_identity, dict)
            else str(target_identity or "")
        )
        if target_identity_id != paper_id:
            provenance_errors.append(f"{paper_id}: target identity mismatch")
            continue
        if entry.get("pool_version") != expected_pool_version:
            provenance_errors.append(f"{paper_id}: unsupported pool version")
            continue
        papers = entry.get("retrieved_papers")
        if papers is None:
            papers = entry.get("papers")
        if papers is None:
            papers = entry.get("results")
        if papers is None:
            papers = []
        if not isinstance(papers, list):
            provenance_errors.append(f"{paper_id}: retrieved results are not a list")
            continue
        result_sha = hashlib.sha256(
            json.dumps(
                papers, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if result_sha != str(entry.get("result_sha256")):
            provenance_errors.append(f"{paper_id}: result SHA256 mismatch")
            continue
        if expected_queries is not None:
            canonical_query = str(entry.get("canonical_query_title") or "").strip()
            mismatch_fields = []
            # Result-count and whole-record hashes are pool-wide provenance
            # checks. They remain active for C/D even when this invocation only
            # requires A/B.
            if entry.get("result_count") != len(papers):
                provenance_errors.append(f"{paper_id}: result_count mismatch")
            record_payload = dict(entry)
            recorded_record_sha = str(record_payload.pop("record_sha256", ""))
            actual_record_sha = hashlib.sha256(
                json.dumps(
                    record_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if recorded_record_sha != actual_record_sha:
                provenance_errors.append(f"{paper_id}: record SHA256 mismatch")

            # Canonical target/query identity belongs to the caller's required
            # scope. A full A+B+C+D pool must not fail an A+B preparation merely
            # because C/D have no entry in the caller's expected-query map.
            if paper_id in required_id_set:
                expected_query = str(expected_queries.get(paper_id) or "").strip()
                actual_query = str(entry.get("query") or "").strip()
                query_sha = hashlib.sha256(canonical_query.encode("utf-8")).hexdigest()
                if not expected_query or canonical_query != expected_query:
                    mismatch_fields.append("canonical_query_title")
                if actual_query != canonical_query:
                    mismatch_fields.append("query")
                if (
                    not isinstance(target_identity, dict)
                    or str(target_identity.get("title") or "").strip() != expected_query
                ):
                    mismatch_fields.append("target_identity.title")
                if entry.get("query_sha256") != query_sha:
                    mismatch_fields.append("query_sha256")
                if (
                    canonical_query.startswith("{")
                    and "value" in canonical_query
                    and canonical_query.endswith("}")
                ):
                    polluted_query_titles.append(
                        {"paper_id": paper_id, "query": canonical_query}
                    )
            if mismatch_fields:
                canonical_mismatches.append(
                    {"paper_id": paper_id, "fields": mismatch_fields}
                )
        cutoff = date.fromisoformat(cutoff_text[:10])
        for result in papers:
            try:
                published = _publication_cutoff(result)
            except Exception as exc:
                provenance_errors.append(f"{paper_id}: {exc}")
                continue
            if published > cutoff:
                future.append(
                    {
                        "paper_id": paper_id,
                        "retrieved_id": str(
                            result.get("paperId") or result.get("id") or ""
                        ),
                        "publication_date": published.isoformat(),
                        "paper_review_cutoff": cutoff.isoformat(),
                    }
                )
    missing_ids = sorted(required_id_set - set(by_id))
    return {
        "protocol": expected_pool_version,
        "asset_sha256": digest,
        "required_count": len(required_id_set),
        "cached_count": len(by_id),
        "missing_target_ids": missing_ids,
        "future_results": future,
        "provenance_errors": provenance_errors,
        "canonical_mismatches": canonical_mismatches,
        "polluted_query_titles": polluted_query_titles,
        "passed": not (
            missing_ids
            or future
            or provenance_errors
            or canonical_mismatches
            or polluted_query_titles
        ),
    }


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size]))
    right_norm = math.sqrt(sum(value * value for value in right[:size]))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class InMemoryVectorCollection:
    """Small vector collection used when the host project has no vector DB."""

    def __init__(self, name: str):
        self.name = name
        self._records: list[dict[str, Any]] = []
        self._ids = set()

    def add(
        self,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        metadatas = metadatas or [{} for _ in texts]
        ids = ids or [str(len(self._records) + index) for index in range(len(texts))]
        if not (len(texts) == len(embeddings) == len(metadatas) == len(ids)):
            raise ValueError(
                "texts, embeddings, metadatas, and ids must have equal lengths"
            )
        for record_id, text, embedding, metadata in zip(
            ids, texts, embeddings, metadatas
        ):
            if record_id in self._ids:
                continue
            self._ids.add(record_id)
            self._records.append(
                {
                    "id": record_id,
                    "text": text,
                    "embedding": list(embedding),
                    "metadata": dict(metadata),
                }
            )

    def query(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[dict[str, Any]]:
        scored = []
        for record in self._records:
            result = dict(record)
            result.pop("embedding", None)
            result["score"] = _cosine(query_embedding, record["embedding"])
            scored.append(result)
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: max(0, int(top_k))]

    def __len__(self) -> int:
        return len(self._records)


class MemoryToolkit:
    """Model and storage capabilities exposed to sandboxed memory programs."""

    def __init__(
        self,
        llm_client=None,
        embedding_client=None,
        vector_store_factory=None,
        llm_model: str | None = None,
        embedding_model: str | None = None,
        review_splitter=None,
        review_classifier=None,
        max_llm_calls_per_operation: int = 1,
        fallback_embedding_dimensions: int = 256,
        s2_retrieval_pool: dict[str, list[dict[str, Any]]] | None = None,
        s2_retrieval_pool_sha256: str = "",
    ):
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.vector_store_factory = vector_store_factory
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self.review_splitter = review_splitter
        self.review_classifier = review_classifier
        self.max_llm_calls_per_operation = max(0, int(max_llm_calls_per_operation))
        self.fallback_embedding_dimensions = max(8, int(fallback_embedding_dimensions))
        self.s2_retrieval_pool = {
            str(key): [dict(item) for item in values if isinstance(item, dict)]
            for key, values in dict(s2_retrieval_pool or {}).items()
            if isinstance(values, list)
        }
        self.s2_retrieval_pool_sha256 = str(s2_retrieval_pool_sha256 or "")
        self._collections: dict[str, Any] = {}
        self._operation_state = threading.local()
        self._resource_lock = threading.Lock()
        self._resources = {
            "llm_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_chars": 0,
            "completion_chars": 0,
            "llm_latency_seconds": 0.0,
            "s2_call_count": 0,
            "s2_result_count": 0,
        }

    @contextmanager
    def operation(
        self,
        name: str,
        max_llm_calls: int | None = None,
        allowed_capabilities: set[str] | None = None,
    ):
        previous = getattr(self._operation_state, "budget", None)
        self._operation_state.budget = {
            "name": name,
            "limit": self.max_llm_calls_per_operation
            if max_llm_calls is None
            else max(0, int(max_llm_calls)),
            "used": 0,
            "allowed_capabilities": (
                None
                if allowed_capabilities is None
                else frozenset(str(value) for value in allowed_capabilities)
            ),
        }
        try:
            yield
        finally:
            self._operation_state.budget = previous

    def require_capability(self, name: str) -> None:
        """Enforce the active operation's candidate-visible capability set."""
        budget = getattr(self._operation_state, "budget", None)
        allowed = None if budget is None else budget.get("allowed_capabilities")
        if allowed is not None and name not in allowed:
            raise PermissionError(
                f"Capability {name!r} is unavailable during {budget['name']!r}"
            )

    @contextmanager
    def paper_scope(self, paper_id: str):
        previous = getattr(self._operation_state, "paper_id", None)
        previous_called = getattr(self._operation_state, "current_s2_called", None)
        previous_trace = getattr(self._operation_state, "current_s2_trace", None)
        self._operation_state.paper_id = str(paper_id or "").split("::", 1)[0]
        self._operation_state.current_s2_called = False
        self._operation_state.current_s2_trace = {
            "paper_id": self._operation_state.paper_id,
            "calls": [],
        }
        try:
            yield
        finally:
            self._operation_state.last_s2_trace = dict(
                self._operation_state.current_s2_trace
            )
            self._operation_state.paper_id = previous
            if previous_called is None:
                try:
                    del self._operation_state.current_s2_called
                except AttributeError:
                    pass
            else:
                self._operation_state.current_s2_called = previous_called
            if previous_trace is None:
                try:
                    del self._operation_state.current_s2_trace
                except AttributeError:
                    pass
            else:
                self._operation_state.current_s2_trace = previous_trace

    def _consume_llm_budget(self) -> None:
        budget = getattr(self._operation_state, "budget", None)
        if budget is None:
            return
        if budget["used"] >= budget["limit"]:
            raise RuntimeError(
                f"LLM call limit exceeded for {budget['name']}: {budget['limit']}"
            )
        budget["used"] += 1

    def llm_completion(self, messages: list[dict[str, str]], **kwargs) -> str:
        if self.llm_client is None:
            raise RuntimeError("No LLM client is configured")
        self._consume_llm_budget()
        client = self.llm_client
        prompt_chars = sum(
            len(str(item.get("content", "")))
            for item in messages
            if isinstance(item, dict)
        )
        started = time.monotonic()

        if hasattr(client, "llm_completion"):
            result = str(client.llm_completion(messages, **kwargs))
        elif hasattr(client, "complete"):
            result = str(client.complete(messages=messages, **kwargs))
        elif hasattr(client, "_get_response_for_agent"):
            agent_name = kwargs.pop("agent_name", "Memory Harness")
            result = str(
                client._get_response_for_agent(
                    agent_name,
                    messages,
                    **kwargs,
                )
            )
        elif hasattr(client, "_get_response"):
            result = str(client._get_response(messages, **kwargs))
        elif hasattr(client, "query"):
            try:
                from agentreview.message import Message

                system_parts = [
                    item.get("content", "")
                    for item in messages
                    if item.get("role") == "system"
                ]
                history = [
                    Message(
                        agent_name="Memory Harness User",
                        content=item.get("content", ""),
                        turn=index,
                    )
                    for index, item in enumerate(messages)
                    if item.get("role") != "system"
                ]
                result = str(
                    client.query(
                        agent_name="Memory Harness",
                        role_desc="\n".join(system_parts)
                        or "Execute the memory harness request precisely.",
                        history_messages=history,
                    )
                )
            except ImportError:
                result = None
        elif callable(client):
            result = str(client(messages, **kwargs))
        else:
            chat = getattr(client, "chat", None)
            completions = getattr(chat, "completions", None)
            create = getattr(completions, "create", None)
            if create is None:
                raise TypeError(f"Unsupported LLM client: {type(client).__name__}")
            request = dict(kwargs)
            if self.llm_model and "model" not in request:
                request["model"] = self.llm_model
            response = create(messages=messages, **request)
            result = str(response.choices[0].message.content).strip()
        if result is None:
            raise TypeError(f"Unsupported LLM client: {type(client).__name__}")
        info = getattr(client, "last_response_info", {}) or {}
        with self._resource_lock:
            self._resources["llm_call_count"] += 1
            self._resources["prompt_chars"] += prompt_chars
            self._resources["completion_chars"] += len(result)
            self._resources["prompt_tokens"] += int(info.get("prompt_tokens") or 0)
            self._resources["completion_tokens"] += int(
                info.get("completion_tokens") or 0
            )
            self._resources["llm_latency_seconds"] += time.monotonic() - started
        return result

    def retrieve_s2(self, paper_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Read only from the experiment-frozen, paper-specific S2 pool."""
        key = str(paper_id or "")
        active = getattr(self._operation_state, "paper_id", None)
        if active is not None and key != active:
            raise PermissionError(
                "S2 retrieval is restricted to the currently reviewed paper"
            )
        selected = [
            dict(value)
            for value in self.s2_retrieval_pool.get(key, [])[: max(0, int(limit))]
        ]
        with self._resource_lock:
            self._resources["s2_call_count"] += 1
            self._resources["s2_result_count"] += len(selected)
        return selected

    def retrieve_current_s2(self, limit: int = 5) -> list[dict[str, Any]]:
        """Read the current paper's frozen S2 candidates at most once per scope.

        Candidate Programs receive this paper-id-free capability. The host owns
        ``paper_scope`` and therefore the Program cannot select or probe another
        paper's retrieval record.
        """
        active = getattr(self._operation_state, "paper_id", None)
        if active is None:
            raise RuntimeError("Current-paper S2 retrieval requires paper_scope")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Current-paper S2 limit must be an integer")
        if not 1 <= limit <= 5:
            raise ValueError("Current-paper S2 limit must be between 1 and 5")
        if getattr(self._operation_state, "current_s2_called", False):
            raise RuntimeError(
                "Current-paper S2 retrieval is limited to one call per paper scope"
            )
        self._operation_state.current_s2_called = True
        selected = self.retrieve_s2(active, limit=limit)
        result_ids = []
        for value in selected:
            result_id = str(
                value.get("paperId") or value.get("paper_id") or value.get("id") or ""
            )
            if result_id:
                result_ids.append(result_id)
        trace = getattr(self._operation_state, "current_s2_trace", None)
        if not isinstance(trace, dict):
            trace = {"paper_id": active, "calls": []}
            self._operation_state.current_s2_trace = trace
        trace.setdefault("calls", []).append(
            {"requested_limit": limit, "result_ids": result_ids}
        )
        return selected

    def s2_trace_snapshot(self) -> dict[str, Any]:
        """Return the active, or most recently completed, current-paper trace."""
        trace = getattr(self._operation_state, "current_s2_trace", None)
        if trace is None:
            trace = getattr(self._operation_state, "last_s2_trace", None)
        if not isinstance(trace, dict):
            return {"paper_id": "", "calls": []}
        return {
            "paper_id": str(trace.get("paper_id") or ""),
            "calls": [dict(value) for value in trace.get("calls", [])],
        }

    def resource_snapshot(self) -> dict[str, Any]:
        with self._resource_lock:
            return dict(self._resources)

    def resource_delta(self, before: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.resource_snapshot()
        baseline = dict(before or {})
        return {key: current.get(key, 0) - baseline.get(key, 0) for key in current}

    def embed(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text) for text in texts]
        if self.embedding_client is None:
            return [self._fallback_embed(text) for text in normalized]

        client = self.embedding_client
        if hasattr(client, "embed"):
            result = client.embed(normalized)
        elif hasattr(client, "embed_documents"):
            result = client.embed_documents(normalized)
        elif callable(client):
            result = client(normalized)
        else:
            embeddings = getattr(client, "embeddings", None)
            create = getattr(embeddings, "create", None)
            if create is None:
                raise TypeError(
                    f"Unsupported embedding client: {type(client).__name__}"
                )
            request = {"input": normalized}
            if self.embedding_model:
                request["model"] = self.embedding_model
            response = create(**request)
            result = [item.embedding for item in response.data]
        return [list(map(float, vector)) for vector in result]

    def _fallback_embed(self, text: str) -> list[float]:
        vector = [0.0] * self.fallback_embedding_dimensions
        tokens = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % len(vector)
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def create_vector_collection(self, name: str):
        if name in self._collections:
            return self._collections[name]
        if self.vector_store_factory is None:
            collection = InMemoryVectorCollection(name)
        elif hasattr(self.vector_store_factory, "create_collection"):
            collection = self.vector_store_factory.create_collection(name)
        else:
            collection = self.vector_store_factory(name)
        self._collections[name] = collection
        return collection

    def reset_vector_collections(self) -> None:
        self._collections.clear()

    def classify_review(self, review_text: str, task_id: str) -> list[str]:
        """Run the frozen ReviewSplit/ReviewClassify adapter used by formal fitness."""
        if self.review_splitter is None or self.review_classifier is None:
            raise RuntimeError(
                "Formal review fitness requires frozen ReviewSplit and "
                "ReviewClassify adapters"
            )
        opinions = self.review_splitter(str(review_text))
        labels = self.review_classifier(opinions)
        if isinstance(labels, str) or not isinstance(labels, Iterable):
            raise TypeError("ReviewClassify adapter must return an iterable of labels")
        flattened = []
        for value in labels:
            if isinstance(value, str):
                flattened.append(value)
            else:
                flattened.extend(str(item) for item in value)
        prefix = f"{task_id}-"
        return sorted(
            {
                label
                for label in flattened
                if task_id == "META" or label.startswith(prefix)
            }
        )
