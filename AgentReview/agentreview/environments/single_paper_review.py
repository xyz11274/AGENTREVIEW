# AgentReview/agentreview/environments/single_paper_review.py

import logging

from ..message import Message
from ..paper_review_message import PaperReviewMessagePool  # 可复用现有池（最省事）
from .base import TimeStep
from .conversation import Conversation

logger = logging.getLogger(__name__)


class SinglePaperReview(Conversation):
    type_name = "single_paper_review"

    def __init__(
        self,
        player_names: list[str],
        paper_md_path: str,
        experiment_setting: dict,
        args,
        parallel: bool = False,
        **kwargs,
    ):
        super(Conversation, self).__init__(
            player_names=player_names, parallel=parallel, **kwargs
        )
        self.args = args
        self.paper_md_path = paper_md_path
        self.experiment_setting = experiment_setting
        self.parallel = parallel

        self.message_pool = PaperReviewMessagePool(experiment_setting)
        self.phase_index = 0
        self._phases = None

    @property
    def phases(self):
        if self._phases is not None:
            return self._phases

        self._phases = {
            0: {"name": "paper_extraction_md", "speaking_order": ["Paper Extractor"]},
            1: {"name": "review_qual", "speaking_order": ["Reviewer QUAL"]},
            2: {"name": "review_clar", "speaking_order": ["Reviewer CLAR"]},
            3: {"name": "review_sign", "speaking_order": ["Reviewer SIGN"]},
            4: {"name": "review_orig", "speaking_order": ["Reviewer ORIG"]},
            5: {"name": "review_pol", "speaking_order": ["Reviewer POL"]},
            6: {"name": "final_meta_review", "speaking_order": ["Final Meta-Reviewer"]},
        }
        return self._phases

    def reset(self):
        self.phase_index = 0
        return super().reset()

    def step(self, player_name: str, action: str) -> TimeStep:
        msg = Message(agent_name=player_name, content=action, turn=self._current_turn)
        self.message_pool.append_message(msg)

        speaking_order = self.phases[self.phase_index]["speaking_order"]
        terminal = False

        if self._next_player_index == len(speaking_order) - 1:
            self._next_player_index = 0
            if self.phase_index == max(self.phases.keys()):
                terminal = True
            else:
                self.phase_index += 1
                self._current_turn += 1
        else:
            self._next_player_index += 1

        return TimeStep(
            observation=self.get_observation(),
            reward=self.get_zero_rewards(),
            terminal=terminal,
        )

    def get_next_player(self) -> str:
        speaking_order = self.phases[self.phase_index]["speaking_order"]
        return speaking_order[self._next_player_index]

    def get_observation(self, player_name=None) -> list[Message]:
        if player_name is None:
            return self.message_pool.get_all_messages()

        messages = self.message_pool.get_all_messages()

        if self.phase_index == 0:
            return messages

        if 1 <= self.phase_index <= 5:
            return [
                message
                for message in messages
                if message.agent_name == "Paper Extractor"
            ]

        return messages
