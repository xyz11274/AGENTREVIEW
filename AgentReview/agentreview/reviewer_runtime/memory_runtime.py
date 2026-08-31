from collections.abc import Iterable

from .engine import ReviewerSession
from .models import Episode, ReviewRequest


class MemoryHarnessAdapter:
    """Compatibility adapter around the stable MemoryProgram runtime."""

    def __init__(self, program_path, toolkit, task_agent):
        self.program_path = str(program_path)
        self.toolkit = toolkit
        self.task_agent = task_agent
        self.reset()

    def reset(self):
        previous = getattr(self, "runtime", None)
        if previous is not None:
            previous.close()
        self.runtime = ReviewerSession(
            task_id=self.task_agent.task_id or "QUAL",
            memory_program=self.program_path,
            toolkit=self.toolkit,
            task_agent=self.task_agent,
        )
        self.session = self.runtime.sandbox
        self.program = self.runtime.program

    def ingest_episodes(self, episodes: Iterable):
        for value in episodes:
            episode = Episode.from_value(value)
            # Preserve the legacy adapter contract: its inputs are already
            # complete memory texts, even if their metadata resembles a formal
            # history record. ReviewerEngine/ReviewerSession own review-then-write.
            self.runtime.ingest_text(episode.text)

    def ingest_precompiled(self, values: Iterable):
        """Load frozen KnowledgeItems without rerunning write-side LLM extraction."""
        for value in values:
            item_value = dict(value.get("item") or {})
            self.runtime.ingest_precompiled(
                {"item": item_value, "raw_text": str(value.get("raw_text") or "")}
            )

    def retrieve(self, question: str) -> str:
        query = self.task_agent.generate_query(self.program, question)
        with self.toolkit.operation("memory_read"):
            context = self.session.read(query)
        if not isinstance(context, str):
            raise TypeError("KnowledgeBase.read() must return str")
        if len(context) > 3000:
            raise ValueError("KnowledgeBase.read() exceeded 3000 characters")
        return context

    def answer(self, question: str) -> str:
        task_id = self.task_agent.task_id or "QUAL"
        return self.runtime.review(
            ReviewRequest(
                paper_id="legacy-memory-adapter",
                task_id=task_id,
                question=question,
                model=str(getattr(self.toolkit, "llm_model", "") or "legacy"),
                revision="legacy",
                run_identity="legacy-memory-adapter",
            )
        ).review_text

    def close(self):
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.close()
            self.runtime = None
            self.session = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
