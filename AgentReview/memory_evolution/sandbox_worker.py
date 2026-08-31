"""Backward-compatible worker entrypoint.

The stable sandbox starts the worker from ``agentreview.reviewer_runtime``.
"""

from agentreview.reviewer_runtime.sandbox_worker import (
    CapabilityToolkit,
    ChromaClient,
    VectorCollection,
    _run,
)

__all__ = ["CapabilityToolkit", "ChromaClient", "VectorCollection", "_run"]

if __name__ == "__main__":
    import sys

    _run(sys.argv[1])
