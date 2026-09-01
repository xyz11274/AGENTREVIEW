"""Bounded role-level orchestration for independent Specialist evolution runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SharedEvolutionResources:
    """Process-scoped adapters constructed before role workers start.

    Runtime and selection embeddings intentionally remain separate cache views.
    Every role receives the same runtime object, whose adapter-level lock
    serializes model inference and runtime-cache writes.
    """

    runtime_embedding: Any
    selection_embedding: Any
    review_splitter: Callable[..., Any] | None
    review_classifier: Callable[..., Any] | None


@dataclass(frozen=True)
class SpecialistRoleJob:
    """One role's serial outer-seed schedule and isolated output root."""

    task_id: str
    seeds: tuple[int, ...]
    primary_seed: int
    output_root: Path


@dataclass(frozen=True)
class SpecialistRoleResult:
    """Ordered seed results for one role."""

    task_id: str
    seeds: tuple[int, ...]
    primary_seed: int
    runs: tuple[Mapping[str, Any], ...]

    @property
    def winner(self) -> Mapping[str, Any]:
        return self.runs[self.seeds.index(self.primary_seed)]


SeedRunner = Callable[
    [SpecialistRoleJob, int, Path, SharedEvolutionResources],
    Mapping[str, Any],
]


def _run_role(
    job: SpecialistRoleJob,
    resources: SharedEvolutionResources,
    execute_seed: SeedRunner,
) -> SpecialistRoleResult:
    print(f"[ROLE START] task={job.task_id}", flush=True)
    runs = []
    for seed in job.seeds:
        output = job.output_root / job.task_id / f"seed_{seed}"
        runs.append(dict(execute_seed(job, seed, output, resources)))
    print(f"[ROLE DONE] task={job.task_id}", flush=True)
    return SpecialistRoleResult(
        task_id=job.task_id,
        seeds=job.seeds,
        primary_seed=job.primary_seed,
        runs=tuple(runs),
    )


def run_specialist_suite(
    jobs: Sequence[SpecialistRoleJob],
    *,
    role_workers: int,
    resources: SharedEvolutionResources,
    execute_seed: SeedRunner,
) -> dict[str, SpecialistRoleResult]:
    """Run independent roles with bounded concurrency and canonical ordering.

    Seeds inside one role remain serial.  A worker never writes another role's
    output root.  Results are returned in ``jobs`` order regardless of finish
    order, leaving promotion and suite-manifest publication to the caller.

    ``role_workers=1`` deliberately avoids creating worker threads and retains
    the legacy serial execution path.
    """

    ordered_jobs = tuple(jobs)
    if not ordered_jobs:
        raise ValueError("Specialist suite requires at least one role job")
    workers = int(role_workers)
    if workers < 1 or workers > len(ordered_jobs):
        raise ValueError(
            f"role_workers must be between 1 and {len(ordered_jobs)}, got {workers}"
        )
    task_ids = [job.task_id for job in ordered_jobs]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Specialist role jobs must have unique task ids")
    output_roots = [
        (job.output_root / job.task_id).expanduser().resolve()
        for job in ordered_jobs
    ]
    if len(output_roots) != len(set(output_roots)):
        raise ValueError("Specialist role jobs must have isolated output roots")
    for job in ordered_jobs:
        if not job.seeds or job.primary_seed not in job.seeds:
            raise ValueError(
                f"Role {job.task_id} must include its primary seed in seeds"
            )

    if workers == 1:
        ordered_results = [
            _run_role(job, resources, execute_seed) for job in ordered_jobs
        ]
    else:
        futures: dict[str, Future[SpecialistRoleResult]] = {}
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="specialist-role",
        ) as executor:
            for job in ordered_jobs:
                futures[job.task_id] = executor.submit(
                    _run_role,
                    job,
                    resources,
                    execute_seed,
                )
            ordered_results = [
                futures[job.task_id].result() for job in ordered_jobs
            ]

    return {result.task_id: result for result in ordered_results}
