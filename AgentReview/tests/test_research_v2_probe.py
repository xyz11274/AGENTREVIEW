import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2.io import value_sha256
from agentreview.research_v2.probe import (
    COCO_S2_TOTAL,
    FORMAL_ARGUMENTS,
    FORMAL_PYTHON,
    QWEN_ARGUMENTS,
    QWEN_CUDA_VISIBLE_DEVICES,
    QWEN_PYTHON,
    S2_ARGUMENTS,
    PROBE_PROTOCOL,
    _append_lock_owner_issue,
    ProbeControllerBusy,
    _append_s2_stagnation_issues,
    _controller_lock,
    _exact_stage_process,
    _log_snapshot,
    _metric,
    _qwen_process,
    _s2_progress,
    _stagnant_count,
    main,
    run_once,
)
from agentreview.research_v2.s2 import (
    S2_RECORD_BUILDER_SHA256,
    _expected_record_identity,
)


def _contains_pair(values, name, expected):
    return any(
        values[index : index + 2] == (name, expected)
        for index in range(len(values) - 1)
    )


def _write_valid_s2_fixture(artifact_root: Path):
    query_path = artifact_root / "data" / "coco_1300" / "s2_title_queries.jsonl"
    final_path = artifact_root / "s2" / "frozen_s2_coco1300_v1.jsonl"
    query_path.parent.mkdir(parents=True)
    final_path.parent.mkdir(parents=True)
    targets = [
        {
            "paper_id": f"paper-{index:04d}",
            "canonical_query_title": f"Target title {index:04d}",
        }
        for index in range(COCO_S2_TOTAL)
    ]
    records = []
    for target in targets:
        record = _expected_record_identity(
            target=target,
            paper_id=target["paper_id"],
            title=target["canonical_query_title"],
            pool_version="frozen_s2_coco1300_v1",
            raw_limit=50,
            top_k=5,
            builder_sha256=S2_RECORD_BUILDER_SHA256,
        )
        record.update(
            {
                "retrieved_papers": [],
                "result_sha256": value_sha256([]),
                "rejected": [],
            }
        )
        record["record_sha256"] = value_sha256(record)
        records.append(record)
    query_path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in targets),
        encoding="utf-8",
    )
    final_path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
        encoding="utf-8",
    )
    return targets, records, final_path


class ResearchV2ProbeTest(unittest.TestCase):
    def test_metric_sums_labeled_series(self):
        text = "\n".join(
            (
                'vllm:num_requests_running{model_name="a"} 3',
                'vllm:num_requests_running{model_name="b"} 2',
                "vllm:num_requests_waiting 0",
            )
        )
        self.assertEqual(_metric(text, "vllm:num_requests_running"), 5.0)
        self.assertEqual(_metric(text, "vllm:num_requests_waiting"), 0.0)

    @patch("agentreview.research_v2.probe.time.time", return_value=5000.0)
    def test_stagnation_requires_a_real_probe_interval(self, _time):
        previous = {
            "checked_epoch": 1000.0,
            "stagnation": {"formal": 1},
        }
        self.assertEqual(
            _stagnant_count(
                previous,
                key="formal",
                current_signature="same",
                previous_signature="same",
                active=True,
            ),
            2,
        )
        self.assertEqual(
            _stagnant_count(
                previous,
                key="formal",
                current_signature="new",
                previous_signature="same",
                active=True,
            ),
            0,
        )

    @patch("agentreview.research_v2.probe.collect_probe")
    def test_run_once_persists_auditable_state(self, collect):
        collect.return_value = {
            "protocol": PROBE_PROTOCOL,
            "checked_at": "2026-09-01T00:00:00+00:00",
            "checked_epoch": 1.0,
            "status": "healthy",
            "transition_ready": False,
            "issues": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, result = run_once(root, root / "state")
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "healthy")
            self.assertTrue((root / "state" / "latest.json").is_file())
            self.assertTrue((root / "state" / "probes.jsonl").is_file())
            self.assertTrue((root / "state" / "action.json").is_file())

    def test_expected_commands_freeze_services_and_concurrency(self):
        self.assertTrue(
            _contains_pair(
                S2_ARGUMENTS,
                "--min-request-interval-seconds",
                "1.2",
            )
        )
        self.assertIn("--retry-until-success", S2_ARGUMENTS)
        self.assertTrue(_contains_pair(FORMAL_ARGUMENTS, "--action", "run-all-roles"))
        self.assertTrue(
            _contains_pair(FORMAL_ARGUMENTS, "--run-id", "formal-evolution-v1")
        )
        self.assertTrue(_contains_pair(QWEN_ARGUMENTS, "--max-model-len", "65536"))
        self.assertTrue(_contains_pair(QWEN_ARGUMENTS, "--max-num-seqs", "8"))
        self.assertEqual(QWEN_CUDA_VISIBLE_DEVICES, "3,6")

    def test_exact_process_matching_rejects_near_matches(self):
        repo_root = Path("/experiment").resolve()
        formal = {
            "argv": [FORMAL_PYTHON, *FORMAL_ARGUMENTS],
            "cwd": str(repo_root),
            "executable": str(Path(FORMAL_PYTHON).resolve()),
        }
        self.assertTrue(
            _exact_stage_process(
                formal,
                arguments=FORMAL_ARGUMENTS,
                repo_root=repo_root,
            )
        )
        mutated_arguments = list(FORMAL_ARGUMENTS)
        mutated_arguments[mutated_arguments.index("--run-id") + 1] = (
            "formal-evolution-v10"
        )
        self.assertFalse(
            _exact_stage_process(
                {**formal, "argv": [FORMAL_PYTHON, *mutated_arguments]},
                arguments=FORMAL_ARGUMENTS,
                repo_root=repo_root,
            )
        )
        self.assertFalse(
            _exact_stage_process(
                {**formal, "cwd": "/wrong-repository"},
                arguments=FORMAL_ARGUMENTS,
                repo_root=repo_root,
            )
        )
        self.assertFalse(
            _exact_stage_process(
                {**formal, "argv": [FORMAL_PYTHON, *FORMAL_ARGUMENTS, "extra"]},
                arguments=FORMAL_ARGUMENTS,
                repo_root=repo_root,
            )
        )
        s2 = {**formal, "argv": [FORMAL_PYTHON, *S2_ARGUMENTS]}
        self.assertTrue(
            _exact_stage_process(
                s2,
                arguments=S2_ARGUMENTS,
                repo_root=repo_root,
            )
        )

    def test_qwen_process_matching_freezes_full_identity_and_gpu_pair(self):
        repo_root = Path("/experiment").resolve()
        process = {
            "argv": [QWEN_PYTHON, *QWEN_ARGUMENTS],
            "cwd": str(repo_root / "AgentReview"),
            "executable": str(Path(QWEN_PYTHON).resolve()),
            "cuda_visible_devices": QWEN_CUDA_VISIBLE_DEVICES,
        }
        self.assertTrue(_qwen_process(process, repo_root))
        mutated_arguments = list(QWEN_ARGUMENTS)
        mutated_arguments[mutated_arguments.index("--model") + 1] = "/wrong/model"
        self.assertFalse(
            _qwen_process(
                {**process, "argv": [QWEN_PYTHON, *mutated_arguments]},
                repo_root,
            )
        )
        self.assertFalse(
            _qwen_process({**process, "cuda_visible_devices": "3"}, repo_root)
        )

    def test_log_snapshot_baselines_history_and_detects_only_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            path.write_text(
                "Traceback (most recent call last)\n"
                "embedding identity differs from snapshot\n",
                encoding="utf-8",
            )
            baseline = _log_snapshot(path, None)
            self.assertTrue(baseline["baseline_only"])
            self.assertFalse(baseline["new_traceback"])
            self.assertEqual(baseline["new_exact_failures"], [])

            with path.open("a", encoding="utf-8") as handle:
                handle.write("without an embedding client\n")
            appended = _log_snapshot(path, baseline)
            self.assertFalse(appended["baseline_only"])
            self.assertEqual(
                appended["new_exact_failures"],
                ["without an embedding client"],
            )

            replacement = path.with_suffix(".replacement")
            replacement.write_text(
                "Traceback (most recent call last)\n"
                "requires embedding_identity\n",
                encoding="utf-8",
            )
            os.replace(replacement, path)
            rotated = _log_snapshot(path, appended)
            self.assertTrue(rotated["rotated"])
            self.assertFalse(rotated["baseline_only"])
            self.assertTrue(rotated["new_traceback"])
            self.assertEqual(
                rotated["new_exact_failures"],
                ["requires embedding_identity"],
            )

            previous = rotated
            path.write_text("short copytruncate baseline\n", encoding="utf-8")
            truncated = _log_snapshot(path, previous)
            self.assertTrue(truncated["truncated"])
            self.assertFalse(truncated["baseline_only"])

            path.write_text(
                "Traceback (most recent call last)\n" + "regrown\n" * 100,
                encoding="utf-8",
            )
            regrown = _log_snapshot(path, truncated)
            self.assertTrue(regrown["truncated"])
            self.assertFalse(regrown["baseline_only"])
            self.assertTrue(regrown["new_traceback"])

    def test_s2_stagnation_requires_part_and_log_stagnation(self):
        cases = (
            (0, 1, set()),
            (1, 0, {"s2_upstream_degraded"}),
            (1, 1, {"s2_stalled"}),
            (2, 0, {"s2_paper_stalled"}),
            (0, 0, set()),
        )
        for parts_stagnant, log_stagnant, expected in cases:
            with self.subTest(
                parts_stagnant=parts_stagnant,
                log_stagnant=log_stagnant,
            ):
                issues = []
                _append_s2_stagnation_issues(
                    issues,
                    active=True,
                    log_exists=True,
                    parts_stagnant=parts_stagnant,
                    log_stagnant=log_stagnant,
                )
                self.assertEqual({value["code"] for value in issues}, expected)

        missing_log_issues = []
        _append_s2_stagnation_issues(
            missing_log_issues,
            active=True,
            log_exists=False,
            parts_stagnant=0,
            log_stagnant=0,
        )
        self.assertEqual(
            {value["code"] for value in missing_log_issues},
            {"s2_log_missing"},
        )

    def test_active_process_must_own_its_exact_lock(self):
        lock_path = Path("/experiment/formal.lock")
        processes = [{"pid": 17}]
        issues = []
        _append_lock_owner_issue(
            issues,
            code="formal_lock_owner",
            label="Formal",
            active=True,
            processes=processes,
            lock_owners=[{"pid": 17, "path": str(lock_path)}],
            lock_path=lock_path,
        )
        self.assertEqual(issues, [])

        _append_lock_owner_issue(
            issues,
            code="formal_lock_owner",
            label="Formal",
            active=True,
            processes=processes,
            lock_owners=[{"pid": 18, "path": str(lock_path)}],
            lock_path=lock_path,
        )
        self.assertEqual(issues[0]["code"], "formal_lock_owner")
        self.assertEqual(issues[0]["severity"], "critical")

    def test_s2_final_requires_full_identity_and_evidence_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            targets, records, final_path = _write_valid_s2_fixture(artifact_root)
            progress = _s2_progress(artifact_root)
            self.assertTrue(progress["complete"])
            self.assertIsNone(progress["validation_error"])

            records[0]["retrieved_papers"] = [
                {"paperId": "other", "title": targets[0]["canonical_query_title"]}
            ]
            records[0]["result_sha256"] = value_sha256(
                records[0]["retrieved_papers"]
            )
            body = {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
            records[0]["record_sha256"] = value_sha256(body)
            final_path.write_text(
                "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
                encoding="utf-8",
            )
            progress = _s2_progress(artifact_root)
            self.assertFalse(progress["complete"])
            self.assertIn("self match", progress["validation_error"])

            records[0]["retrieved_papers"] = [
                {
                    "paperId": "other",
                    "title": f"{targets[0]['canonical_query_title']} v2",
                }
            ]
            records[0]["result_sha256"] = value_sha256(
                records[0]["retrieved_papers"]
            )
            body = {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
            records[0]["record_sha256"] = value_sha256(body)
            final_path.write_text(
                "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
                encoding="utf-8",
            )
            progress = _s2_progress(artifact_root)
            self.assertFalse(progress["complete"])
            self.assertIn("same-family", progress["validation_error"])

            records[0]["retrieved_papers"] = [{"paperId": "other", "title": ""}]
            records[0]["result_sha256"] = value_sha256(
                records[0]["retrieved_papers"]
            )
            body = {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
            records[0]["record_sha256"] = value_sha256(body)
            final_path.write_text(
                "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
                encoding="utf-8",
            )
            progress = _s2_progress(artifact_root)
            self.assertFalse(progress["complete"])
            self.assertIn("non-empty paper ID and title", progress["validation_error"])

    def test_s2_final_rejects_more_than_top_five_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            _targets, records, final_path = _write_valid_s2_fixture(artifact_root)
            records[0]["retrieved_papers"] = [
                {"paperId": f"evidence-{index}", "title": f"Evidence {index}"}
                for index in range(6)
            ]
            records[0]["result_sha256"] = value_sha256(
                records[0]["retrieved_papers"]
            )
            body = {
                key: value
                for key, value in records[0].items()
                if key != "record_sha256"
            }
            records[0]["record_sha256"] = value_sha256(body)
            final_path.write_text(
                "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
                encoding="utf-8",
            )
            progress = _s2_progress(artifact_root)
            self.assertFalse(progress["complete"])
            self.assertIn("more than top-5", progress["validation_error"])

    def test_s2_final_rejects_malformed_json_even_with_a_final_file(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            query_path = (
                artifact_root / "data" / "coco_1300" / "s2_title_queries.jsonl"
            )
            final_path = artifact_root / "s2" / "frozen_s2_coco1300_v1.jsonl"
            query_path.parent.mkdir(parents=True)
            final_path.parent.mkdir(parents=True)
            query_path.write_text(
                '{"paper_id":"p","canonical_query_title":"title"}\n',
                encoding="utf-8",
            )
            final_path.write_text("{not valid json}\n", encoding="utf-8")
            progress = _s2_progress(artifact_root)
            self.assertFalse(progress["complete"])
            self.assertIn("Invalid JSONL", progress["validation_error"])

    def test_controller_lock_has_process_lifetime_exclusivity(self):
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory)
            with _controller_lock(state_root):
                with self.assertRaises(ProbeControllerBusy):
                    with _controller_lock(state_root):
                        self.fail("A second controller acquired the same state root")

    @patch("agentreview.research_v2.probe.collect_probe")
    def test_main_persists_probe_failure_as_action_required(self, collect):
        collect.side_effect = RuntimeError("probe exploded")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            code = main(
                [
                    "--repo-root",
                    str(root),
                    "--state-root",
                    str(state_root),
                ]
            )
            self.assertEqual(code, 3)
            latest = json.loads((state_root / "latest.json").read_text())
            action = json.loads((state_root / "action.json").read_text())
            self.assertEqual(latest["status"], "action_required")
            self.assertEqual(latest["probe_status"], "probe_failed")
            self.assertEqual(latest["issues"][0]["code"], "probe_failed")
            self.assertTrue(action["required"])

    @patch("agentreview.research_v2.probe.collect_probe")
    def test_busy_controller_does_not_write_shared_state(self, collect):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            with _controller_lock(state_root):
                code = main(
                    [
                        "--repo-root",
                        str(root),
                        "--state-root",
                        str(state_root),
                    ]
                )
            self.assertEqual(code, 4)
            collect.assert_not_called()
            self.assertFalse((state_root / "latest.json").exists())
            self.assertFalse((state_root / "action.json").exists())


if __name__ == "__main__":
    unittest.main()
