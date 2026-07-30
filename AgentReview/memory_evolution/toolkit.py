import hashlib
import math
import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional


def _cosine(left: List[float], right: List[float]) -> float:
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
        self._records: List[Dict[str, Any]] = []
        self._ids = set()

    def add(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        metadatas = metadatas or [{} for _ in texts]
        ids = ids or [str(len(self._records) + index) for index in range(len(texts))]
        if not (len(texts) == len(embeddings) == len(metadatas) == len(ids)):
            raise ValueError("texts, embeddings, metadatas, and ids must have equal lengths")
        for record_id, text, embedding, metadata in zip(ids, texts, embeddings, metadatas):
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

    def query(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
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
        llm_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        review_splitter=None,
        review_classifier=None,
        max_llm_calls_per_operation: int = 1,
        fallback_embedding_dimensions: int = 256,
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
        self._collections: Dict[str, Any] = {}
        self._operation_state = threading.local()

    @contextmanager
    def operation(self, name: str, max_llm_calls: Optional[int] = None):
        previous = getattr(self._operation_state, "budget", None)
        self._operation_state.budget = {
            "name": name,
            "limit": self.max_llm_calls_per_operation
            if max_llm_calls is None
            else max(0, int(max_llm_calls)),
            "used": 0,
        }
        try:
            yield
        finally:
            self._operation_state.budget = previous

    def _consume_llm_budget(self) -> None:
        budget = getattr(self._operation_state, "budget", None)
        if budget is None:
            return
        if budget["used"] >= budget["limit"]:
            raise RuntimeError(
                f"LLM call limit exceeded for {budget['name']}: {budget['limit']}"
            )
        budget["used"] += 1

    def llm_completion(self, messages: List[Dict[str, str]], **kwargs) -> str:
        if self.llm_client is None:
            raise RuntimeError("No LLM client is configured")
        self._consume_llm_budget()
        client = self.llm_client

        if hasattr(client, "llm_completion"):
            return str(client.llm_completion(messages, **kwargs))
        if hasattr(client, "complete"):
            return str(client.complete(messages=messages, **kwargs))
        if hasattr(client, "_get_response_for_agent"):
            return str(
                client._get_response_for_agent(
                    kwargs.pop("agent_name", "Memory Harness"),
                    messages,
                )
            )
        if hasattr(client, "_get_response"):
            return str(client._get_response(messages))
        if hasattr(client, "query"):
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
                return str(
                    client.query(
                        agent_name="Memory Harness",
                        role_desc="\n".join(system_parts)
                        or "Execute the memory harness request precisely.",
                        history_messages=history,
                    )
                )
            except ImportError:
                pass
        if callable(client):
            return str(client(messages, **kwargs))

        chat = getattr(client, "chat", None)
        completions = getattr(chat, "completions", None)
        create = getattr(completions, "create", None)
        if create is not None:
            request = dict(kwargs)
            if self.llm_model and "model" not in request:
                request["model"] = self.llm_model
            response = create(messages=messages, **request)
            return str(response.choices[0].message.content).strip()

        raise TypeError(f"Unsupported LLM client: {type(client).__name__}")

    def embed(self, texts: List[str]) -> List[List[float]]:
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
                raise TypeError(f"Unsupported embedding client: {type(client).__name__}")
            request = {"input": normalized}
            if self.embedding_model:
                request["model"] = self.embedding_model
            response = create(**request)
            result = [item.embedding for item in response.data]
        return [list(map(float, vector)) for vector in result]

    def _fallback_embed(self, text: str) -> List[float]:
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

    def classify_review(self, review_text: str, task_id: str) -> List[str]:
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
