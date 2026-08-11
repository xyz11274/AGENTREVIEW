from typing import Iterable

from .models import Episode
from .sandbox import SandboxSession


class MemoryHarnessAdapter:
    """Host-facing adapter for an evolved executable memory program."""

    def __init__(self, program_path, toolkit, task_agent):
        self.program_path = str(program_path)
        self.toolkit = toolkit
        self.task_agent = task_agent
        self.reset()

    def reset(self):
        previous = getattr(self, "session", None)
        if previous is not None:
            previous.close()
        self.toolkit.reset_vector_collections()
        self.session = SandboxSession(self.program_path, self.toolkit)
        self.program = self.session.program_interface()

    def ingest_episodes(self, episodes: Iterable):
        for value in episodes:
            episode = Episode.from_value(value)
            item = self.task_agent.extract_knowledge_item(
                self.program, episode.text
            )
            with self.toolkit.operation("memory_write"):
                self.session.write(item, episode.text)

    def ingest_precompiled(self, values: Iterable):
        """Load frozen KnowledgeItems without rerunning write-side LLM extraction."""
        for value in values:
            item_value = dict(value.get("item") or {})
            item = self.program.KnowledgeItem(**item_value)
            raw_text = str(value.get("raw_text") or "")
            with self.toolkit.operation("memory_write"):
                self.session.write(item, raw_text)

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
        context = self.retrieve(question)
        return self.task_agent.generate_response(
            self.program, question, context
        )

    def close(self):
        session = getattr(self, "session", None)
        if session is not None:
            session.close()
            self.session = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
