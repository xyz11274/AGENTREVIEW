"""Backward-compatible worker entrypoint.

The stable sandbox starts the worker from ``agentreview.reviewer_runtime``.
"""

from agentreview.reviewer_runtime.sandbox_worker import *  # noqa: F401,F403


if __name__ == "__main__":
    import sys

    from agentreview.reviewer_runtime.sandbox_worker import _run

    _run(sys.argv[1])
