import logging
import os
from pathlib import Path
from typing import List, Union

#from llama_index.readers.file.docs import PDFReader

from agentreview.agent import Player
from .backends import IntelligenceBackend
from .config import BackendConfig
from .message import Message


class AreaChair(Player):

    def __init__(
            self,
            name: str,
            role_desc: str,
            env_type: str,
            backend: Union[BackendConfig, IntelligenceBackend],
            global_prompt: str = None,
            **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)
        self.env_type = env_type
        self.role_desc = role_desc

    def act(self, observation: List[Message]) -> str:

        # The author just finished their rebuttals (so last speaker is Author 1).
        # The AC asks each reviewer to update their reviews.

        if self.env_type == "paper_review":
            if len(observation) > 0 and observation[-1].agent_name.startswith("Author"):
                return "Dear reviewers, please update your reviews based on the author's rebuttals."

            else:
                return super().act(observation)

        elif self.env_type == "paper_decision":
            return super().act(observation)

        else:
            raise ValueError(f"Unknown env_type: {self.env_type}")


class Reviewer(Player):

    def __init__(
            self,
            name: str,
            role_desc: str,
            backend: Union[BackendConfig, IntelligenceBackend],
            global_prompt: str = None,
            **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)

    def act(self, observation: List[Message]) -> str:
        return super().act(observation)


class PaperExtractorPlayer(Player):
    """A player for solely extracting contents from a paper.

    No API calls are made by this player.
    """

    def __init__(
            self,
            name: str,
            role_desc: str,
            paper_id: int,
            paper_decision: str,
            conference: str,
            backend: Union[BackendConfig, IntelligenceBackend],
            paper_md_path: str = None,
            global_prompt: str = None,
            **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)
        self.paper_id = paper_id
        self.paper_decision = paper_decision
        self.conference: str = conference
        self.paper_md_path = paper_md_path

    def act(self, observation: List[Message]) -> str:
        if not self.paper_md_path:
            return "Error: `paper_md_path` is required for Markdown input."

        document_path = Path(self.paper_md_path)
        if not document_path.exists():
            return f"Error: Markdown file not found: {document_path}"

        logging.info(f"Loading paper from {document_path} ...")

        md_text = document_path.read_text(encoding="utf-8", errors="ignore")

        # 按“词数”截断
        words = md_text.split()
        if len(words) > self.args.max_num_words:
            words = words[: self.args.max_num_words]
            md_text = " ".join(words) + "\n\n[Content truncated due to word limit]"

        main_contents = "Contents of this paper:\n\n" + md_text
        return main_contents
