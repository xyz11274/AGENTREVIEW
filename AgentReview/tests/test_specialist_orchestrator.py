import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from memory_evolution.specialist_orchestrator import (
    SharedEvolutionResources,
    SpecialistRoleJob,
    run_specialist_suite,
)


TASKS = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")


class SpecialistOrchestratorTest(unittest.TestCase):
    @staticmethod
    def resources():
        return SharedEvolutionResources(
            runtime_embedding=object(),
            selection_embedding=object(),
            review_splitter=lambda value: value,
            review_classifier=lambda value: value,
        )

    @staticmethod
    def jobs(root: Path, seeds=(0,)):
        return [
            SpecialistRoleJob(
                task_id=task,
                seeds=tuple(seeds),
                primary_seed=seeds[0],
                output_root=root,
            )
            for task in TASKS
        ]

    def test_role_workers_one_preserves_role_and_seed_order(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            resources = self.resources()

            def execute(job, seed, output, observed_resources):
                calls.append((job.task_id, seed, output))
                self.assertIs(observed_resources, resources)
                return {"task": job.task_id, "seed": seed, "output_dir": str(output)}

            results = run_specialist_suite(
                self.jobs(Path(directory), seeds=(3, 7)),
                role_workers=1,
                resources=resources,
                execute_seed=execute,
            )

            self.assertEqual(list(results), list(TASKS))
            self.assertEqual(
                [(task, seed) for task, seed, _ in calls],
                [(task, seed) for task in TASKS for seed in (3, 7)],
            )
            for task, seed, output in calls:
                self.assertEqual(output, Path(directory) / task / f"seed_{seed}")

    def test_four_workers_start_four_roles_and_queue_the_fifth(self):
        with tempfile.TemporaryDirectory() as directory:
            resources = self.resources()
            release = threading.Event()
            four_started = threading.Event()
            lock = threading.Lock()
            started = []
            active = 0
            max_active = 0

            def execute(job, seed, output, observed_resources):
                nonlocal active, max_active
                self.assertIs(observed_resources.runtime_embedding, resources.runtime_embedding)
                with lock:
                    started.append(job.task_id)
                    active += 1
                    max_active = max(max_active, active)
                    if len(started) == 4:
                        four_started.set()
                self.assertTrue(release.wait(timeout=5))
                with lock:
                    active -= 1
                return {"task": job.task_id, "seed": seed, "output_dir": str(output)}

            with ThreadPoolExecutor(max_workers=1) as caller:
                future = caller.submit(
                    run_specialist_suite,
                    self.jobs(Path(directory)),
                    role_workers=4,
                    resources=resources,
                    execute_seed=execute,
                )
                self.assertTrue(four_started.wait(timeout=5))
                with lock:
                    self.assertEqual(set(started), set(TASKS[:4]))
                    self.assertNotIn("POL", started)
                release.set()
                results = future.result(timeout=5)

            self.assertEqual(max_active, 4)
            self.assertEqual(list(results), list(TASKS))
            self.assertIn("POL", started)

    def test_completion_order_does_not_change_result_order(self):
        with tempfile.TemporaryDirectory() as directory:
            gates = {task: threading.Event() for task in TASKS}

            def execute(job, seed, output, resources):
                self.assertTrue(gates[job.task_id].wait(timeout=5))
                return {"task": job.task_id, "seed": seed, "output_dir": str(output)}

            with ThreadPoolExecutor(max_workers=1) as caller:
                future = caller.submit(
                    run_specialist_suite,
                    self.jobs(Path(directory)),
                    role_workers=4,
                    resources=self.resources(),
                    execute_seed=execute,
                )
                for task in reversed(TASKS):
                    gates[task].set()
                results = future.result(timeout=5)

            self.assertEqual(list(results), list(TASKS))
            self.assertEqual(
                [results[task].winner["task"] for task in TASKS],
                list(TASKS),
            )

    def test_duplicate_role_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = self.jobs(Path(directory))
            jobs[-1] = SpecialistRoleJob(
                task_id="QUAL",
                seeds=(0,),
                primary_seed=0,
                output_root=Path(directory),
            )
            with self.assertRaisesRegex(ValueError, "unique task ids"):
                run_specialist_suite(
                    jobs,
                    role_workers=4,
                    resources=self.resources(),
                    execute_seed=lambda *args: {},
                )


if __name__ == "__main__":
    unittest.main()
