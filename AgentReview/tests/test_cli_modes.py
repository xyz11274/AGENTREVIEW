import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_evolution.cli import (
    _deterministic_eval_subset,
    _final_evaluate_suite,
    _experiment_kind,
    _is_nonformal,
    _parse_experiment_seeds,
    _promote_lineage_champions,
    _shared_evolution_resources,
    _task_toolkit,
    build_parser,
    evolution_protocol_config_identity,
    main,
)
from memory_evolution.models import EvalItem
from memory_evolution.specialist_orchestrator import SharedEvolutionResources


class CliModeTest(unittest.TestCase):
    def test_parallel_specialist_main_uses_isolated_args_and_atomic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_root = root / "suite"
            resources = SharedEvolutionResources(
                runtime_embedding=object(),
                selection_embedding=object(),
                review_splitter=object(),
                review_classifier=object(),
            )
            observed_args = {}
            observed_resources = []
            lock = threading.Lock()

            def run_task(args, task_id, output, seed, shared_resources=None):
                output.mkdir(parents=True, exist_ok=True)
                best_program = output / "best_program.py"
                best_program.write_text(
                    f"VALUE = {task_id!r}\n",
                    encoding="utf-8",
                )
                program_sha256 = hashlib.sha256(best_program.read_bytes()).hexdigest()
                (output / "lineage_champions.json").write_text(
                    json.dumps(
                        {
                            "vector_search": {
                                "program": "best_program.py",
                                "program_sha256": program_sha256,
                                "fitness": 0.5,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                with lock:
                    observed_args[task_id] = id(args)
                    observed_resources.append(shared_resources)
                payload = {
                    "best_program_path": str(best_program),
                    "best_score": 0.5,
                    "output_dir": str(output),
                }
                return SimpleNamespace(to_dict=lambda: dict(payload))

            argv = [
                "memory_evolution.cli",
                "evolve-specialists",
                "--data-root",
                str(root / "data"),
                "--output",
                str(suite_root),
                "--pilot",
                "--role-workers",
                "4",
            ]
            with (
                patch("sys.argv", argv),
                patch(
                    "memory_evolution.cli._shared_evolution_resources",
                    return_value=resources,
                ),
                patch("memory_evolution.cli._run_task", side_effect=run_task),
                patch("builtins.print"),
            ):
                result = main()

            manifest_path = suite_root / "suite_manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["orchestration"]["role_workers"], 4)
            self.assertEqual(
                manifest["orchestration"]["role_order"],
                ["QUAL", "CLAR", "SIGN", "ORIG", "POL"],
            )
            self.assertEqual(list(manifest["results"]), manifest["orchestration"]["role_order"])
            self.assertEqual(len(set(observed_args.values())), 5)
            self.assertTrue(all(value is resources for value in observed_resources))
            self.assertIsNone(result)

    def test_shared_evolution_resources_are_constructed_once_on_caller(self):
        args = SimpleNamespace(
            classifier_adapter="fixture:factory",
            review_split_revision="split",
            review_classify_revision="classify",
            coco_root="coco",
        )
        runtime = object()
        selection = object()
        splitter = object()
        classifier = object()
        with (
            patch("memory_evolution.cli._prepare_llm_runtime") as preflight,
            patch(
                "memory_evolution.cli._classifier_adapter",
                return_value=(splitter, classifier),
            ) as coco,
            patch(
                "memory_evolution.cli._runtime_embedding_client",
                return_value=runtime,
            ) as runtime_factory,
            patch(
                "memory_evolution.cli._selection_embedding_client",
                return_value=selection,
            ) as selection_factory,
            patch("memory_evolution.cli._is_nonformal", return_value=False),
        ):
            resources = _shared_evolution_resources(args)

        self.assertIs(resources.runtime_embedding, runtime)
        self.assertIs(resources.selection_embedding, selection)
        self.assertIs(resources.review_splitter, splitter)
        self.assertIs(resources.review_classifier, classifier)
        preflight.assert_called_once_with(args, formal=True)
        coco.assert_called_once()
        runtime_factory.assert_called_once_with(args)
        selection_factory.assert_called_once_with(args)

    def test_task_toolkit_uses_injected_runtime_and_coco_adapters(self):
        runtime = object()
        splitter = object()
        classifier = object()
        resources = SharedEvolutionResources(
            runtime_embedding=runtime,
            selection_embedding=object(),
            review_splitter=splitter,
            review_classifier=classifier,
        )
        args = SimpleNamespace(
            disable_embedding_cache=False,
            embedding_cache_read_only=False,
            task_model_name="task-model",
            embedding_model_name="embedding-model",
            s2_retrieval_pool="pool.jsonl",
        )

        def toolkit(**kwargs):
            return SimpleNamespace(**kwargs)

        with (
            patch("memory_evolution.cli._prepare_llm_runtime"),
            patch(
                "memory_evolution.cli._classifier_adapter",
                side_effect=AssertionError("must use injected CoCo adapters"),
            ),
            patch(
                "memory_evolution.cli._runtime_embedding_client",
                side_effect=AssertionError("must use injected runtime embedding"),
            ),
            patch("memory_evolution.cli._backend", return_value=object()),
            patch(
                "memory_evolution.cli.load_frozen_s2_pool",
                return_value=([], "sha256"),
            ),
            patch("memory_evolution.cli.MemoryToolkit", side_effect=toolkit),
            patch("memory_evolution.cli._is_nonformal", return_value=False),
        ):
            observed = _task_toolkit(args, resources)

        self.assertIs(observed.embedding_client, runtime)
        self.assertIs(observed.review_splitter, splitter)
        self.assertIs(observed.review_classifier, classifier)

    def test_final_suite_specialists_only_has_distinct_scope(self):
        args = build_parser().parse_args(
            [
                "final-evaluate-suite",
                "--data-root",
                "data/formal",
                "--test-data-root",
                "data/test",
                "--program-root",
                "runs/formal",
                "--output",
                "runs/formal/d.json",
                "--specialists-only",
            ]
        )
        self.assertTrue(args.specialists_only)

    def test_specialist_d_subset_flags_are_parsed(self):
        args = build_parser().parse_args(
            [
                "final-evaluate-suite",
                "--data-root",
                "data/formal",
                "--test-data-root",
                "data/test",
                "--program-root",
                "runs/formal",
                "--output",
                "runs/formal/d-subset.json",
                "--specialists-only",
                "--max-specialist-test-items",
                "2",
                "--specialist-test-subset-seed",
                "7",
            ]
        )
        self.assertEqual(args.max_specialist_test_items, 2)
        self.assertEqual(args.specialist_test_subset_seed, 7)

    def test_specialist_d_failure_retry_manifest_is_parsed(self):
        args = build_parser().parse_args(
            [
                "final-evaluate-suite",
                "--data-root",
                "data/formal",
                "--test-data-root",
                "data/test",
                "--program-root",
                "runs/formal",
                "--output",
                "runs/formal/d-retry.json",
                "--specialists-only",
                "--specialist-test-item-ids",
                "failed-ids.json",
            ]
        )
        self.assertEqual(args.specialist_test_item_ids, "failed-ids.json")

    def test_specialist_d_selection_manifest_is_parsed(self):
        args = build_parser().parse_args(
            [
                "final-evaluate-suite",
                "--data-root",
                "data/formal",
                "--test-data-root",
                "data/test",
                "--program-root",
                "runs/formal",
                "--output",
                "runs/formal/d-selected.json",
                "--specialists-only",
                "--specialist-test-selection-manifest",
                "selected-ids.json",
            ]
        )
        self.assertEqual(
            args.specialist_test_selection_manifest, "selected-ids.json"
        )

    def test_final_suite_evaluation_pipeline_depth_is_parsed(self):
        args = build_parser().parse_args(
            [
                "final-evaluate-suite",
                "--data-root",
                "data/formal",
                "--test-data-root",
                "data/test",
                "--program-root",
                "runs/formal",
                "--output",
                "runs/formal/d-pipelined.json",
                "--evaluation-pipeline-depth",
                "2",
            ]
        )
        self.assertEqual(args.evaluation_pipeline_depth, 2)

    def test_specialist_d_subset_is_stable_and_order_independent(self):
        items = [
            EvalItem(id=f"paper-{index}", question="q", answer="a")
            for index in range(10)
        ]
        selected = _deterministic_eval_subset(items, "QUAL", 2, 0)
        reversed_selected = _deterministic_eval_subset(
            list(reversed(items)), "QUAL", 2, 0
        )
        self.assertEqual(
            [item.id for item in selected],
            [item.id for item in reversed_selected],
        )
        self.assertEqual(len(selected), 2)

    def test_specialists_only_final_suite_does_not_require_meta_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            test_root = root / "test"
            program_root = root / "programs"
            output = root / "report.json"
            data_root.mkdir()
            test_root.mkdir()
            program_root.mkdir()
            (test_root / "manifest.json").write_text("{}", encoding="utf-8")
            promoted = {
                task: {"source_run": f"{task}/seed_0", "lineage": "fixture"}
                for task in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
            }
            for task in promoted:
                task_root = test_root / task
                task_root.mkdir()
                (task_root / "test.jsonl").write_text(
                    json.dumps(
                        {"id": f"paper::{task}", "question": "paper", "answer": "gold"}
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (program_root / "suite_manifest.json").write_text(
                json.dumps({"promoted": promoted, "provenance": {"kind": "smoke"}}),
                encoding="utf-8",
            )
            program = root / "best_program.py"
            program.write_text("VALUE = 1\n", encoding="utf-8")
            selection = root / "selection.json"
            selection.write_text("{}", encoding="utf-8")
            args = SimpleNamespace(
                data_root=str(data_root),
                test_data_root=str(test_root),
                program_root=str(program_root),
                output=str(output),
                specialists_only=True,
                asset_manifest="asset.json",
                temporal_split_manifest="split.json",
                task_model_name="task-model",
                judge_model_name="judge-model",
                max_tokens=1024,
                retry_max_tokens=2048,
                reasoning_effort="medium",
                embedding_model_name="embedding-model",
                embedding_revision="a" * 40,
                write_timeout=60,
                read_timeout=60,
                classifier_adapter="fixture:classifier",
                review_split_revision="b" * 40,
                review_classify_revision="c" * 40,
                embedding_adapter="fixture:embedding",
            )
            toolkit = SimpleNamespace(
                embedding_client=SimpleNamespace(embedding_fingerprint="fingerprint"),
                s2_retrieval_pool_sha256="d" * 64,
            )
            quality = SimpleNamespace(passed=True, errors=[])
            evaluation = SimpleNamespace(
                mean_score=0.75,
                runtime_stats={"eval_item_count": 1},
                error="",
                traces=[
                    {
                        "phase": "read",
                        "id": "paper::QUAL",
                        "score": 0.75,
                        "success": True,
                    }
                ],
            )
            gate = {
                "asset_manifest": {"passed": True},
                "four_zone_temporal_audit": {"passed": True},
            }
            with (
                patch("memory_evolution.cli._validate_formal_runtime"),
                patch("memory_evolution.cli.run_formal_test_gate", return_value=gate),
                patch(
                    "memory_evolution.cli._toolkits",
                    return_value=(toolkit, SimpleNamespace(), SimpleNamespace()),
                ),
                patch("memory_evolution.cli.resolve_program_path", return_value=program),
                patch("memory_evolution.cli.verify_promoted_program"),
                patch("memory_evolution.cli.check_program_path", return_value=quality),
                patch(
                    "memory_evolution.cli._selected_episodes",
                    return_value=([SimpleNamespace(id="history")], selection),
                ),
                patch(
                    "memory_evolution.cli._load_jsonl",
                    return_value=[
                        {"id": "paper::QUAL", "question": "paper", "answer": "gold"}
                    ],
                ),
                patch("memory_evolution.cli.evaluate_program", return_value=evaluation),
                patch("memory_evolution.cli.get_metric", return_value=SimpleNamespace()),
                patch("memory_evolution.cli.build_task_agent", return_value=SimpleNamespace()),
                patch("memory_evolution.cli._adapter_sha256", return_value="e" * 64),
            ):
                result = _final_evaluate_suite(args)
                subset_output = root / "subset-report.json"
                args.output = str(subset_output)
                args.max_specialist_test_items = 1
                args.specialist_test_subset_seed = 7
                subset_result = _final_evaluate_suite(args)
                retry_ids = root / "retry-ids.json"
                retry_ids.write_text(
                    json.dumps({"QUAL": ["paper::QUAL"]}), encoding="utf-8"
                )
                retry_output = root / "retry-report.json"
                args.output = str(retry_output)
                args.max_specialist_test_items = None
                args.specialist_test_item_ids = str(retry_ids)
                retry_result = _final_evaluate_suite(args)
                selected_ids = root / "selected-ids.json"
                selected_ids.write_text(
                    json.dumps(
                        {
                            "protocol": "reduced-fullflow-d-selection-v1",
                            "seed_label": "fixed",
                            "tasks": {
                                task: ["paper::QUAL"] for task in promoted
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                selected_output = root / "selected-report.json"
                args.output = str(selected_output)
                args.specialist_test_item_ids = ""
                args.specialist_test_selection_manifest = str(selected_ids)
                selected_result = _final_evaluate_suite(args)

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                report["evaluation_identity"], "sealed_d_specialists_coco_style"
            )
            self.assertEqual(set(report["specialists"]), set(promoted))
            self.assertFalse(report["meta"]["enabled"])
            self.assertFalse(result["meta_enabled"])

            subset_report = json.loads(subset_output.read_text(encoding="utf-8"))
            self.assertEqual(
                subset_report["evaluation_identity"],
                "d_subset_specialists_coco_style",
            )
            self.assertFalse(subset_report["final_test"])
            self.assertTrue(subset_report["non_reportable"])
            self.assertEqual(subset_report["subset_protocol"]["seed"], 7)
            self.assertEqual(
                {
                    task: values["selected_item_count"]
                    for task, values in subset_report["subset_protocol"]["tasks"].items()
                },
                {task: 1 for task in promoted},
            )
            self.assertFalse(subset_result["final_test"])
            self.assertTrue(subset_result["non_reportable"])

            retry_report = json.loads(retry_output.read_text(encoding="utf-8"))
            self.assertEqual(
                retry_report["evaluation_identity"],
                "d_retry_specialists_coco_style",
            )
            self.assertEqual(
                retry_report["evaluation_scope"],
                "selected_specialist_failures_d_retry",
            )
            self.assertEqual(set(retry_report["specialists"]), {"QUAL"})
            self.assertEqual(
                retry_report["subset_protocol"]["tasks"]["QUAL"]["selected_ids"],
                ["paper::QUAL"],
            )
            self.assertFalse(retry_result["final_test"])
            self.assertTrue(retry_result["non_reportable"])

            selected_report = json.loads(selected_output.read_text(encoding="utf-8"))
            self.assertEqual(
                selected_report["evaluation_identity"],
                "d_selected_specialists_coco_style",
            )
            self.assertEqual(
                selected_report["evaluation_scope"],
                "five_specialist_champions_d_selected",
            )
            self.assertEqual(
                selected_report["subset_protocol"]["seed_label"], "fixed"
            )
            self.assertFalse(selected_result["final_test"])
            self.assertTrue(selected_result["non_reportable"])

    def test_lineage_promotion_contract_is_shared_by_specialists_and_meta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_root = root / "suite"
            source_run = suite_root / "META" / "seed_3"
            source_run.mkdir(parents=True)
            source_program = source_run / "best_program.py"
            source_program.write_text("VALUE = 'program'\n", encoding="utf-8")
            source_sha256 = hashlib.sha256(source_program.read_bytes()).hexdigest()
            (source_run / "lineage_champions.json").write_text(
                json.dumps(
                    {
                        "vector_search": {
                            "program": "best_program.py",
                            "program_sha256": source_sha256,
                            "fitness": 0.75,
                        }
                    }
                ),
                encoding="utf-8",
            )

            promoted = _promote_lineage_champions(
                {"output_dir": str(source_run)}, suite_root, "META", 3
            )

            entry = promoted["vector_search"]
            self.assertEqual(entry["program_sha256"], source_sha256)
            self.assertEqual(entry["experiment_seed"], 3)
            self.assertTrue(
                entry["program"].endswith("lineages/vector_search/META/best_program.py")
            )
            self.assertFalse(Path(entry["program"]).is_absolute())
            self.assertEqual(entry["source_run"], "META/seed_3")

    def test_all_evolution_llm_roles_default_to_local_qwen(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/pilot",
                "--output",
                "runs/pilot",
                "--pilot",
            ]
        )
        self.assertEqual(args.task_model_name, "qwen3.8-27b-fp8")
        self.assertEqual(args.reflector_model_name, "qwen3.8-27b-fp8")
        self.assertEqual(args.judge_model_name, "qwen3.8-27b-fp8")

    def test_formal_cli_defaults_to_the_paper_iteration_budget(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
            ]
        )
        self.assertEqual(args.iterations, 20)
        self.assertEqual(args.static_size, 32)
        self.assertEqual(args.rotating_size, 5)
        self.assertEqual(args.episode_budget, 64)
        self.assertEqual(args.selection_repeats, 0)
        self.assertEqual(args.selection_top_k, 1)
        self.assertFalse(args.reference_programs)
        self.assertFalse(hasattr(args, "max_attempts_per_lineage"))
        self.assertEqual(_experiment_kind(args), "formal")

    def test_evolution_sandbox_memory_limit_is_explicitly_configurable(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
                "--sandbox-memory-mb",
                "8192",
            ]
        )

        self.assertEqual(args.sandbox_memory_mb, 8192)

    def test_selection_and_runtime_embedding_caches_have_distinct_defaults(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/formal",
                "--output",
                "runs/formal",
            ]
        )

        self.assertNotEqual(args.embedding_cache, args.runtime_embedding_cache)
        self.assertTrue(
            args.embedding_cache.endswith("qwen3_embedding_8b_cache.sqlite3")
        )
        self.assertTrue(
            args.runtime_embedding_cache.endswith("qwen3_embedding_runtime.sqlite3")
        )

    def test_pilot_preserves_requested_search_size(self):
        args = build_parser().parse_args(
            [
                "evolve-task",
                "--task-id",
                "QUAL",
                "--data-root",
                "data/pilot",
                "--output",
                "runs/pilot",
                "--pilot",
                "--iterations",
                "4",
                "--static-size",
                "3",
                "--rotating-size",
                "2",
            ]
        )
        self.assertTrue(args.pilot)
        self.assertFalse(args.smoke)
        self.assertTrue(_is_nonformal(args))
        self.assertEqual(args.iterations, 4)
        self.assertEqual(args.static_size, 3)
        self.assertEqual(args.rotating_size, 2)
        self.assertEqual(_experiment_kind(args), "pilot_non_reportable")

    def test_formal_suite_defaults_to_one_outer_experiment_seed(self):
        for command in ("evolve-specialists", "evolve-meta"):
            args = build_parser().parse_args(
                [
                    command,
                    "--data-root",
                    "data/formal",
                    "--output",
                    "runs/formal",
                ]
            )
            self.assertEqual(args.experiment_seeds, "0")
            self.assertEqual(_parse_experiment_seeds(args.experiment_seeds), [0])

    def test_specialist_role_workers_default_to_serial_and_accept_four(self):
        base = [
            "evolve-specialists",
            "--data-root",
            "data/formal",
            "--output",
            "runs/formal",
        ]
        self.assertEqual(build_parser().parse_args(base).role_workers, 1)
        self.assertEqual(
            build_parser().parse_args([*base, "--role-workers", "4"]).role_workers,
            4,
        )

    def test_specialist_role_workers_reject_values_outside_role_count(self):
        base = [
            "evolve-specialists",
            "--data-root",
            "data/formal",
            "--output",
            "runs/formal",
            "--role-workers",
        ]
        for value in ("0", "6"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                build_parser().parse_args([*base, value])

    def test_role_workers_do_not_change_evolution_protocol_identity(self):
        base = [
            "evolve-specialists",
            "--data-root",
            "data/formal",
            "--output",
            "runs/formal",
        ]
        serial = build_parser().parse_args([*base, "--role-workers", "1"])
        parallel = build_parser().parse_args([*base, "--role-workers", "4"])
        self.assertEqual(
            evolution_protocol_config_identity(serial, "QUAL", 0),
            evolution_protocol_config_identity(parallel, "QUAL", 0),
        )

    def test_outer_experiment_seed_count_is_parameterized_but_nonempty(self):
        self.assertEqual(_parse_experiment_seeds("0"), [0])
        self.assertEqual(_parse_experiment_seeds("0,1,2,3,4"), [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(SystemExit, "at least one experiment seed"):
            _parse_experiment_seeds("")
        with self.assertRaisesRegex(SystemExit, "must not contain duplicates"):
            _parse_experiment_seeds("0,0")

    def test_smoke_and_pilot_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "evolve-task",
                    "--task-id",
                    "QUAL",
                    "--data-root",
                    "data/pilot",
                    "--output",
                    "runs/pilot",
                    "--smoke",
                    "--pilot",
                ]
            )

    def test_evaluate_final_test_is_always_forbidden(self):
        argv = [
            "memory_evolution.cli",
            "evaluate",
            "--task-id",
            "QUAL",
            "--program",
            "program.py",
            "--episodes",
            "episodes.jsonl",
            "--eval",
            "arbitrary.jsonl",
            "--output",
            "report.json",
            "--final-test",
        ]
        with (
            patch("sys.argv", argv),
            self.assertRaisesRegex(
                SystemExit, "final-evaluate-suite is the sealed D/Test entry point"
            ),
        ):
            main()


if __name__ == "__main__":
    unittest.main()
