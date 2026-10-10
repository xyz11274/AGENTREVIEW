"""Candidate admission exercises persisted state in a fresh sandbox."""
import pytest

from agentreview.research_v2 import program_validation as validation
from agentreview.research_v2.memory import seed_program_path


def test_unsupported_nested_state_is_rejected_before_review(tmp_path):
    source = seed_program_path('vector_search').read_text().replace(
        'self.seen = set()', 'self.seen = set()\n        self.bad_state = {"nested": [frozenset(("x",))]}')
    path = tmp_path / 'program.py'
    path.write_text(source)
    errors = validation.validate_program(path, require_bubblewrap=True)
    assert any('frozenset' in error and "bad_state['nested'][0]" in error
               and 'snapshot' in error for error in errors), errors


@pytest.mark.parametrize('seed', ['vector_search', 'llm_summarizer', 'experience_learner'])
def test_initial_seeds_survive_fresh_process_restore(seed, monkeypatch):
    real = validation.SandboxSession
    sessions, restores, toolkits = [], [], []

    class Observed(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)
            toolkits.append(args[1])

        def restore(self, snapshot):
            restores.append(self)
            return super().restore(snapshot)

    monkeypatch.setattr(validation, 'SandboxSession', Observed)
    assert validation.validate_program(seed_program_path(seed), require_bubblewrap=True,
        memory_runtime={'build_llm_calls': 2, 'read_llm_calls': 2}, seed_kind=seed) == []
    assert len(sessions) == 2 and restores == [sessions[1]]
    assert toolkits[0] is not toolkits[1]


def test_restored_read_mismatch_is_not_silently_accepted(monkeypatch):
    real = validation.SandboxSession

    class WrongRestore(real):
        restored = False

        def restore(self, snapshot):
            super().restore(snapshot)
            self.restored = True

        def read(self, query):
            return 'lost retrieval' if self.restored else super().read(query)

    monkeypatch.setattr(validation, 'SandboxSession', WrongRestore)
    errors = validation.validate_program(seed_program_path('vector_search'), require_bubblewrap=True)
    assert any('restored read' in error for error in errors), errors
