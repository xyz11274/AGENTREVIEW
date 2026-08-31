"""Fixed task identities and provenance for the six independent M★ searches."""

from agentreview.reviewer_identity import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    REVIEWER_IDENTITY_VERSION,
    load_reviewer_suite,
    reviewer_request_protocols,
)
from agentreview.reviewer_runtime.program_loader import (
    resolve_program_path,
    verify_promoted_program,
)
from agentreview.reviewer_runtime.tasks import ALL_TASKS, META_TASK, SPECIALIST_TASKS

__all__ = [
    "ALL_TASKS",
    "META_TASK",
    "SPECIALIST_TASKS",
    "experiment_provenance",
    "resolve_program_path",
    "verify_promoted_program",
]

MSTAR_PAPER_SHA256 = "52f01b5b30582e6b2ecd3004c9861e1350129c0579d1914dea4e7e56e19c49b5"
COCOREVIEWBENCH_PAPER_SHA256 = (
    "06c29b7879bb40be15d5611ceca4cf2cde6634e1fc9a3fa89d26ed2e34e00da5"
)
COCOREVIEWBENCH_COMMIT = "2580687ee5d4ad2ac4b9730afb8f1dfda22e40db"


def experiment_provenance() -> dict[str, object]:
    suite = load_reviewer_suite()
    return {
        "architecture": "Mstar executable MemoryProgram",
        "tasks": list(ALL_TASKS),
        "reviewer_identity_version": REVIEWER_IDENTITY_VERSION,
        "reviewer_identity_source_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        "reviewer_suite_identity_sha256": suite.suite_identity_sha256,
        "reviewer_task_prompt_sha256": {
            task_id: suite.task(task_id).system_prompt_sha256 for task_id in ALL_TASKS
        },
        "reviewer_request_protocols": reviewer_request_protocols(),
        "mstar_paper_sha256": MSTAR_PAPER_SHA256,
        "cocoreviewbench_paper_sha256": COCOREVIEWBENCH_PAPER_SHA256,
        "cocoreviewbench_reference_commit": COCOREVIEWBENCH_COMMIT,
        "cocoreviewbench_role": "metric/taxonomy reference only",
        "program_scope": ["schema", "write_logic", "read_logic", "instructions"],
    }
