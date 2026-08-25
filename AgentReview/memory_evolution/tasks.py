"""Fixed task definitions for the six independent M★ searches."""

from dataclasses import dataclass
from typing import Dict, Tuple

from agentreview.single_paper_review_roles import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    REVIEWER_SPECS,
    load_review_request_sections,
)
from agentreview.reviewer_runtime.tasks import (
    ALL_TASKS,
    META_TASK,
    SPECIALIST_TASKS,
)
from agentreview.reviewer_runtime.program_loader import (
    resolve_program_path,
    verify_promoted_program,
)


MSTAR_PAPER_SHA256 = (
    "52f01b5b30582e6b2ecd3004c9861e1350129c0579d1914dea4e7e56e19c49b5"
)
COCOREVIEWBENCH_PAPER_SHA256 = (
    "06c29b7879bb40be15d5611ceca4cf2cde6634e1fc9a3fa89d26ed2e34e00da5"
)
COCOREVIEWBENCH_COMMIT = "2580687ee5d4ad2ac4b9730afb8f1dfda22e40db"


@dataclass(frozen=True)
class ReviewTask:
    task_id: str
    reviewer_name: str
    frozen_prompt: str
    category_prefixes: Tuple[str, ...]


def load_review_tasks() -> Dict[str, ReviewTask]:
    """Load the immutable reviewer prompts after verifying REVIEW_Request.md."""
    sections = load_review_request_sections()
    tasks: Dict[str, ReviewTask] = {}
    for spec in REVIEWER_SPECS:
        task_id = spec["dimension"]
        tasks[task_id] = ReviewTask(
            task_id=task_id,
            reviewer_name=spec["name"],
            frozen_prompt=sections[task_id],
            category_prefixes=(f"{task_id}-",),
        )
    tasks[META_TASK] = ReviewTask(
        task_id=META_TASK,
        reviewer_name="Final Meta-Reviewer",
        frozen_prompt=(
            "Synthesize all five specialist reviews. Resolve disagreements, give an "
            "overall score and confidence, and return one justified decision."
        ),
        category_prefixes=tuple(f"{task_id}-" for task_id in SPECIALIST_TASKS),
    )
    return tasks


def experiment_provenance() -> Dict[str, object]:
    return {
        "architecture": "Mstar executable MemoryProgram",
        "tasks": list(ALL_TASKS),
        "review_request_sha256": EXPECTED_REVIEW_REQUEST_SHA256,
        "mstar_paper_sha256": MSTAR_PAPER_SHA256,
        "cocoreviewbench_paper_sha256": COCOREVIEWBENCH_PAPER_SHA256,
        "cocoreviewbench_reference_commit": COCOREVIEWBENCH_COMMIT,
        "cocoreviewbench_role": "metric/taxonomy reference only",
        "program_scope": ["schema", "write_logic", "read_logic", "instructions"],
    }
