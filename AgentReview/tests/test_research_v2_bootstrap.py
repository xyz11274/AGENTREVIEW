import fcntl
import multiprocessing
import threading
import time
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import bootstrap as bootstrap_module
from agentreview.research_v2 import cli as cli_module
from agentreview.research_v2 import review as review_module
from agentreview.research_v2.bootstrap import (
    DEFAULT_FORMAL_RUN_ID,
    BootstrapInfrastructureFailure,
    BootstrapCorpusOptions,
    run_canonical_bootstrap,
)
from agentreview.research_v2.cli import _base_parser
from agentreview.research_v2.config import ModelConfig, ResearchConfig
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.llm import OpenAICompletionClient
from agentreview.research_v2.models import Paper


def _concurrent_immutable_write(path, payload, barrier, outcomes):
    barrier.wait(timeout=10)
    try:
        atomic_write_json(path, payload)
    except Exception as exc:
        outcomes.put(type(exc).__name__)
    else:
        outcomes.put("ok")


class ConcurrentCompletion:
    def __init__(
        self,
        *,
        first_wave=0,
        failing_roles=(),
        infrastructure_error=None,
    ):
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(first_wave) if first_wave else None
        self.failing_roles = set(failing_roles)
        self.infrastructure_error = infrastructure_error
        self.calls = []
        self.active = 0
        self.max_active = 0

    def complete(self, messages, *, max_tokens, component):
        role = component.rsplit(":", 1)[-1]
        with self.lock:
            call_index = len(self.calls)
            self.calls.append(component)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier is not None and call_index < self.barrier.parties:
                self.barrier.wait(timeout=5)
            time.sleep(0.01)
            if self.infrastructure_error is not None:
                raise type(self.infrastructure_error)(str(self.infrastructure_error))
            if role in self.failing_roles:
                return ""
            return f"complete Specialist Review for {role}"
        finally:
            with self.lock:
                self.active -= 1


class BootstrapRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = ResearchConfig(
            artifact_root=str(self.root),
            frozen_s2_path="",
            require_bubblewrap=False,
            model=ModelConfig(max_num_seqs=4),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _prepare_split(self, count=1):
        records = []
        for index in range(count):
            paper_id = f"p{index + 1}"
            path = self.root / "data" / "papers" / f"{paper_id}.json"
            payload = Paper(
                paper_id=paper_id,
                title=f"Test Paper {index + 1}",
                abstract=f"Abstract {index + 1}",
                text=f"# Test Paper {index + 1}\n\nMethod and experiments.",
                source={"conference": "ICLR 2020"},
            ).to_dict()
            payload["paper_asset_id"] = value_sha256(payload)
            atomic_write_json(
                path,
                payload,
            )
            records.append(
                {
                    "paper_id": paper_id,
                    "paper_asset_id": payload["paper_asset_id"],
                    "path": str(path),
                }
            )
        atomic_write_json(
            self.root / "data" / "splits" / "canonical_review_corpus.json",
            {
                "split": "canonical_review_corpus",
                "seed": 0,
                "count": len(records),
                "records": records,
                "split_sha256": value_sha256(records),
            },
        )

    def _run(self, client, *, run_id="bootstrap-test", workers=3, progress=None):
        return run_canonical_bootstrap(
            config=self.config,
            options=BootstrapCorpusOptions(
                run_id=run_id,
                max_workers=workers,
                s2_enabled=False,
            ),
            completion_client=client,
            progress=progress,
        )

    def test_bounded_parallel_run_has_deterministic_order_and_resumes(self):
        self._prepare_split(count=2)
        events = []
        client = ConcurrentCompletion(first_wave=3)
        original_build = bootstrap_module.build_memory_snapshot
        original_review = bootstrap_module.run_specialist_review

        def checked_build(**kwargs):
            self.assertIsNone(kwargs["embedding_client"])
            return original_build(**kwargs)

        def checked_review(**kwargs):
            self.assertIsNone(kwargs["embedding_client"])
            return original_review(**kwargs)

        with patch.object(bootstrap_module, "build_memory_snapshot", checked_build), patch.object(
            bootstrap_module, "run_specialist_review", checked_review
        ):
            first = self._run(client, progress=events.append)

        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["progress"]["completed"], 10)
        self.assertEqual(client.max_active, 3)
        self.assertEqual(len(client.calls), 10)
        self.assertEqual(
            [(value["paper_id"], value["role"]) for value in first["task_results"]],
            [(paper_id, role) for paper_id in ("p1", "p2") for role in ROLES],
        )
        self.assertEqual(
            first["completed_review_ids"],
            [value["review_id"] for value in first["task_results"]],
        )
        self.assertEqual(events[0]["event"], "bootstrap_plan")
        self.assertEqual(events[-1]["event"], "bootstrap_complete")

        self.config = replace(
            self.config,
            model=replace(
                self.config.model,
                base_url="http://127.0.0.1:19999/v1",
                max_num_seqs=3,
            ),
        )
        no_calls = ConcurrentCompletion(failing_roles=ROLES)
        second = self._run(no_calls)
        self.assertEqual(no_calls.calls, [])
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["progress"]["resumed"], 10)
        self.assertEqual(second["completed_review_ids"], first["completed_review_ids"])
        self.assertEqual(second["run_spec_sha256"], first["run_spec_sha256"])
        self.assertEqual(second["runtime_config"]["max_num_seqs"], 3)
        self.assertEqual(
            second["runtime_config"]["base_url"],
            "http://127.0.0.1:19999/v1",
        )
        self.assertEqual(
            read_json(self.root / "runs" / "bootstrap-test" / "run.json"),
            second,
        )

    def test_repeated_failure_is_retryable_and_partial_raw_is_reused(self):
        self._prepare_split()
        first_failure = self._run(
            ConcurrentCompletion(failing_roles={"QUAL"}),
            run_id="retry-test",
        )
        self.assertEqual(first_failure["status"], "completed_with_failures")
        self.assertEqual(first_failure["progress"]["failed"], 1)

        second_failure = self._run(
            ConcurrentCompletion(failing_roles={"QUAL"}),
            run_id="retry-test",
        )
        self.assertEqual(second_failure["status"], "completed_with_failures")
        failures = list(
            (
                self.root
                / "reviews"
                / "retry-test"
                / "p1"
                / "failures"
                / "QUAL"
            ).glob("*.json")
        )
        self.assertEqual(len(failures), 2)

        successful = self._run(ConcurrentCompletion(), run_id="retry-test")
        self.assertEqual(successful["status"], "complete")
        self.assertEqual(successful["progress"]["completed"], 1)
        self.assertEqual(successful["progress"]["resumed"], 4)

        review_path = self.root / "reviews" / "retry-test" / "p1" / "QUAL.json"
        expected = read_json(review_path)
        review_path.unlink()
        no_calls = ConcurrentCompletion(failing_roles=ROLES)
        recovered = self._run(no_calls, run_id="retry-test")
        self.assertEqual(no_calls.calls, [])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(read_json(review_path), expected)
        self.assertEqual(len(failures), 2)

    def test_conflicting_review_fails_before_new_model_calls(self):
        self._prepare_split()
        self._run(ConcurrentCompletion(), run_id="conflict-test")
        review_path = self.root / "reviews" / "conflict-test" / "p1" / "QUAL.json"
        review = read_json(review_path)
        review["model_identity"] = {"model": "different"}
        atomic_write_json(review_path, review, immutable=False)
        no_calls = ConcurrentCompletion(failing_roles=ROLES)
        with self.assertRaisesRegex(ValueError, "conflicting committed artifacts"):
            self._run(no_calls, run_id="conflict-test")
        self.assertEqual(no_calls.calls, [])

    def test_paper_asset_tamper_fails_before_model_calls(self):
        self._prepare_split()
        paper_path = self.root / "data" / "papers" / "p1.json"
        paper = read_json(paper_path)
        paper["text"] = "tampered after the frozen asset ID was committed"
        atomic_write_json(paper_path, paper, immutable=False)
        no_calls = ConcurrentCompletion()
        with self.assertRaisesRegex(ValueError, "asset identity mismatch"):
            self._run(no_calls, run_id="asset-conflict")
        self.assertEqual(no_calls.calls, [])

    def test_run_lock_rejects_a_second_process_before_model_calls(self):
        self._prepare_split()
        run_path = self.root / "runs" / "locked-run" / "run.json"
        lock_path = run_path.with_name("bootstrap.lock")
        lock_path.parent.mkdir(parents=True)
        no_calls = ConcurrentCompletion()
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "already active"):
                self._run(no_calls, run_id="locked-run")
        self.assertEqual(no_calls.calls, [])
        self.assertFalse(run_path.exists())

    def test_infrastructure_failure_stops_refill_and_is_resumable(self):
        self._prepare_split(count=2)
        failing = ConcurrentCompletion(
            infrastructure_error=ConnectionError("endpoint unavailable")
        )
        with self.assertRaisesRegex(
            BootstrapInfrastructureFailure,
            "shared infrastructure failure",
        ):
            self._run(failing, run_id="infrastructure-run", workers=3)
        self.assertLessEqual(len(failing.calls), 6)
        checkpoint = read_json(
            self.root / "runs" / "infrastructure-run" / "run.json"
        )
        self.assertEqual(checkpoint["status"], "infrastructure_failed")
        self.assertEqual(checkpoint["progress"]["failed"], 0)
        self.assertEqual(checkpoint["progress"]["pending"], 10)
        self.assertTrue(checkpoint["infrastructure_failures"])

        recovered = self._run(
            ConcurrentCompletion(),
            run_id="infrastructure-run",
            workers=3,
        )
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["progress"]["completed"], 10)

    def test_generation_policy_change_cannot_reuse_the_same_run(self):
        self._prepare_split()
        first = self._run(ConcurrentCompletion(), run_id="generation-bound")
        no_calls = ConcurrentCompletion()
        with patch.object(review_module, "REVIEW_MAX_TOKENS", 4096):
            with self.assertRaisesRegex(ValueError, "different canonical bootstrap spec"):
                self._run(no_calls, run_id="generation-bound")
        self.assertEqual(no_calls.calls, [])
        self.assertEqual(
            first["run_spec"]["review_generation"]["max_tokens"],
            8192,
        )

    def test_cli_returns_nonzero_for_bootstrap_infrastructure_failure(self):
        with patch.object(
            cli_module,
            "run_canonical_bootstrap",
            side_effect=BootstrapInfrastructureFailure("endpoint unavailable"),
        ), patch.object(cli_module, "OpenAICompletionClient"):
            exit_code = cli_module.main(
                [
                    "bootstrap-corpus",
                    "--run-id",
                    "cli-infrastructure-smoke",
                    "--no-s2",
                    "--max-workers",
                    "1",
                ]
            )
        self.assertEqual(exit_code, 2)

    def test_openai_client_disables_sdk_retry_layer(self):
        with patch("openai.OpenAI") as factory:
            OpenAICompletionClient(ModelConfig())
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_immutable_json_create_is_atomic_across_processes(self):
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        outcomes = context.Queue()
        path = self.root / "immutable-race.json"
        processes = [
            context.Process(
                target=_concurrent_immutable_write,
                args=(path, {"owner": owner, "padding": owner * 8_000_000}, barrier, outcomes),
            )
            for owner in ("A", "B")
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(
            sorted(outcomes.get(timeout=2) for _ in processes),
            ["FileExistsError", "ok"],
        )
        self.assertIn(read_json(path)["owner"], {"A", "B"})

    def test_cli_defaults_and_worker_capacity_validation(self):
        args = _base_parser().parse_args(["bootstrap-corpus", "--no-s2"])
        self.assertEqual(args.max_workers, 8)
        build_s2 = _base_parser().parse_args(
            ["build-s2", "--dataset", "development"]
        )
        self.assertEqual(build_s2.dataset, "development")
        with self.assertRaisesRegex(ValueError, "explicit non-formal"):
            BootstrapCorpusOptions(
                run_id=DEFAULT_FORMAL_RUN_ID,
                limit=1,
                max_workers=1,
            ).validate(self.config)
        with self.assertRaisesRegex(ValueError, "explicit non-formal"):
            BootstrapCorpusOptions(
                run_id=DEFAULT_FORMAL_RUN_ID,
                max_workers=1,
                s2_enabled=False,
            ).validate(self.config)
        with self.assertRaisesRegex(ValueError, "max_num_seqs"):
            BootstrapCorpusOptions(max_workers=5).validate(self.config)


if __name__ == "__main__":
    unittest.main()
