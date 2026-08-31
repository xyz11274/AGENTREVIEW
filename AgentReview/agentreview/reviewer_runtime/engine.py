"""Single-task Reviewer Core, independent of every optimizer and benchmark."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from typing import Any

from .models import Episode, ReviewRequest, ReviewResult
from .program_loader import MemoryProgramLoader
from .sandbox import SandboxSession
from .task_agent import build_task_agent
from .tasks import ALL_TASKS, reviewer_identity

EXPERIENCE_PROTOCOL = "self_generated_after_review"
EXPERIENCE_PROTOCOL_VERSION = "self-generated-experience-v1"


def _serializable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serializable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class ReviewerSession:
    """A live MemoryProgram state used for chronological ingestion and reviews."""

    def __init__(
        self,
        *,
        task_id: str,
        memory_program,
        toolkit,
        task_agent=None,
        loader: MemoryProgramLoader | None = None,
        timeout_seconds: float = 60.0,
        memory_mb: int = 1024,
        cpu_seconds: int = 60,
        require_bubblewrap: bool = True,
    ):
        if task_id not in ALL_TASKS:
            raise ValueError(f"Unknown review task: {task_id}")
        self.task_id = task_id
        self.toolkit = toolkit
        self.task_agent = task_agent or build_task_agent(toolkit, task_id)
        self.loader = loader or MemoryProgramLoader()
        self.identity = self.loader.identity(memory_program, task_id)
        self.toolkit.reset_vector_collections()
        self.sandbox = SandboxSession(
            self.identity.path,
            toolkit,
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
            cpu_seconds=cpu_seconds,
            require_bubblewrap=require_bubblewrap,
        )
        self.program = self.sandbox.program_interface()

    def snapshot(self) -> dict[str, Any]:
        return self.sandbox.snapshot()

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.sandbox.restore(snapshot)

    def ingest_text(self, raw_text: str):
        item = self.task_agent.extract_knowledge_item(self.program, str(raw_text))
        with self.toolkit.operation("memory_write"):
            self.sandbox.write(item, str(raw_text))
        return item

    def ingest_precompiled(self, value: dict[str, Any]) -> None:
        item = self.program.KnowledgeItem(**dict(value.get("item") or {}))
        raw_text = str(value.get("raw_text") or "")
        with self.toolkit.operation("memory_write"):
            self.sandbox.write(item, raw_text)

    def ingest_episode(self, value: Any) -> dict[str, Any]:
        """Ingest one episode, preserving the existing review-then-write protocol."""
        episode = Episode.from_value(value)
        raw_text = episode.text
        details: dict[str, Any] = {}
        if episode.metadata.get("experience_protocol") == EXPERIENCE_PROTOCOL:
            if episode.metadata.get("experience_protocol_version") not in (
                None,
                EXPERIENCE_PROTOCOL_VERSION,
            ):
                raise ValueError(
                    f"Experience protocol version mismatch for {episode.id}"
                )
            generated = self.review(
                ReviewRequest(
                    paper_id=episode.id.split("::", 1)[0],
                    task_id=self.task_id,
                    question=episode.text,
                    metadata=dict(episode.metadata),
                    model=str(getattr(self.toolkit, "llm_model", "") or ""),
                    run_identity="historical-replay",
                    read_operation="historical_memory_read",
                )
            )
            raw_text = (
                "HISTORICAL PAPER TASK\n"
                f"{episode.text}\n\n"
                "SELF-GENERATED REVIEW EXPERIENCE\n"
                f"{generated.review_text}"
            )
            details = {
                "experience_protocol": "review_then_write",
                "prior_memory_chars": len(generated.memory_context),
                "generated_review_chars": len(generated.review_text),
            }
        item = self.ingest_text(raw_text)
        return {"item": item, "raw_text": raw_text, **details}

    def review(self, request: ReviewRequest) -> ReviewResult:
        request.validate()
        if request.task_id != self.task_id:
            raise ValueError(
                f"ReviewerSession task mismatch: {self.task_id} != {request.task_id}"
            )
        paper_scope = (
            self.toolkit.paper_scope(request.paper_id)
            if hasattr(self.toolkit, "paper_scope")
            else nullcontext()
        )
        with paper_scope:
            query = self.task_agent.generate_query(self.program, request.question)
            with self.toolkit.operation(request.read_operation):
                context = self.sandbox.read(query)
            if not isinstance(context, str):
                raise TypeError("KnowledgeBase.read() must return str")
            if len(context) > 3000:
                raise ValueError("KnowledgeBase.read() exceeded 3000 characters")
            review_text = self.task_agent.generate_response(
                self.program, request.question, context
            )
        model = request.model or str(getattr(self.toolkit, "llm_model", "") or "")
        return ReviewResult(
            paper_id=request.paper_id,
            task_id=request.task_id,
            review_text=review_text,
            query=_serializable(query),
            memory_context=context,
            reviewer_identity=reviewer_identity(request.task_id),
            memory_program_identity=self.identity.to_dict(),
            memory_program_sha256=self.identity.sha256,
            model=model,
            revision=request.revision,
            run_identity=request.run_identity,
            provenance=dict(request.provenance),
        )

    def close(self, force: bool = False) -> None:
        sandbox = getattr(self, "sandbox", None)
        if sandbox is not None:
            sandbox.close(force=force)
            self.sandbox = None

    def __enter__(self) -> ReviewerSession:
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(force=exc is not None)


class ReviewerEngine:
    """Stable one-shot entrypoint: review(request, memory_program)."""

    def __init__(
        self,
        toolkit,
        *,
        require_bubblewrap: bool = True,
        timeout_seconds: float = 60.0,
        memory_mb: int = 1024,
        task_agent_factory=build_task_agent,
    ):
        self.toolkit = toolkit
        self.require_bubblewrap = require_bubblewrap
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.task_agent_factory = task_agent_factory

    def open_session(self, task_id: str, memory_program) -> ReviewerSession:
        return ReviewerSession(
            task_id=task_id,
            memory_program=memory_program,
            toolkit=self.toolkit,
            task_agent=self.task_agent_factory(self.toolkit, task_id),
            timeout_seconds=self.timeout_seconds,
            memory_mb=self.memory_mb,
            cpu_seconds=max(1, int(self.timeout_seconds)),
            require_bubblewrap=self.require_bubblewrap,
        )

    def review(self, request: ReviewRequest, memory_program) -> ReviewResult:
        with self.open_session(request.task_id, memory_program) as session:
            for episode in request.episodes:
                session.ingest_episode(episode)
            return session.review(request)
