import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from memory_evolution.integration import MemoryHarnessAdapter
from memory_evolution.metrics import exact_match
from memory_evolution.models import Episode, EvalItem
from memory_evolution.search import (
    EvolutionConfig,
    run_embedding_closure_smoke,
    run_evolution,
)
from memory_evolution.quality import QualityResult
from memory_evolution.reflector import ChangeLog, CompileFixResult, MutationResult
from memory_evolution.models import EvalResult
from memory_evolution.task_agent import TaskAgent, _parse_json_object
from memory_evolution.toolkit import MemoryToolkit


class FakeLLM:
    def __init__(self, child_code):
        self.child_code = child_code
        self.reflector_calls = 0

    def llm_completion(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        if "Then output one V4A patch" in prompt:
            self.reflector_calls += 1
            return (
                "*** Commit Message\n"
                "Title: Document the selected memory design\n"
                "- Existing behavior already succeeds\n"
                "- Preserve it while recording the mutation\n"
                "*** Begin Patch\n"
                "*** Update File: program.py\n"
                "@@ imports\n"
                " import re\n"
                "+# Population mutation retained this compact design.\n"
                " from dataclasses import dataclass\n"
                "*** End Patch"
            )
        if "KnowledgeItem must conform" in prompt:
            if "lesson_learned" in prompt:
                return json.dumps(
                    {
                        "lesson_learned": "Remember alpha",
                        "fact_to_remember": "alpha",
                    }
                )
            if "reviewer_dimension" in prompt and "content" not in prompt:
                return json.dumps({"reviewer_dimension": "GENERAL"})
            return json.dumps({"topic": "alpha", "content": "alpha"})
        if "Query must conform" in prompt:
            return json.dumps({"query_text": "alpha"})
        if "Provide the final answer only" in prompt:
            return "alpha"
        if "query-focused memory summary" in prompt:
            return "alpha"
        return "alpha"


class SearchLoopTest(unittest.TestCase):
    def test_vector_embedding_closure_smoke_writes_and_reads_real_items(self):
        class RuntimeEmbedding:
            fail_on_cache_miss = False
            cache_namespace = "candidate_runtime_v1"
            embedding_fingerprint = "f" * 64
            revision = "1" * 40
            cache_path = "/tmp/runtime-fixture.sqlite3"

            @staticmethod
            def embed(texts):
                return [[float(len(text)), 1.0, 0.0, 0.0] for text in texts]

        root = Path(__file__).parents[1]
        toolkit = MemoryToolkit(
            llm_client=FakeLLM(""), embedding_client=RuntimeEmbedding()
        )
        result = run_embedding_closure_smoke(
            str(root / "memory_evolution" / "seeds" / "vector_search.py"),
            Episode(id="A::QUAL", text="A QUAL episode about ablations."),
            EvalItem(
                id="B::QUAL",
                question="Produce a QUAL review with ablation evidence.",
                answer="",
            ),
            TaskAgent(toolkit),
            toolkit,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["runtime_namespace"], "candidate_runtime_v1")
        self.assertGreater(result["context_chars"], 0)

    def test_three_iteration_formal_run_executes_embedding_closure(self):
        class RuntimeEmbedding:
            fail_on_cache_miss = False
            cache_namespace = "candidate_runtime_v1"
            embedding_fingerprint = "f" * 64
            revision = "1" * 40
            cache_path = "/tmp/runtime-fixture.sqlite3"

            @staticmethod
            def embed(texts):
                return [[float(len(text)), 1.0, 0.0, 0.0] for text in texts]

        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        child_code = (seed_dir / "experience_learner.py").read_text(
            encoding="utf-8"
        )
        toolkit = MemoryToolkit(
            llm_client=FakeLLM(child_code),
            embedding_client=RuntimeEmbedding(),
        )
        episode = Episode(
            id="A::QUAL",
            text="A QUAL episode whose answer is alpha.",
            metadata={
                "experience_protocol": "self_generated_after_review",
                "experience_protocol_version": "self-generated-experience-v1",
            },
        )
        validation = [
            EvalItem(id="B1::QUAL", question="Return alpha.", answer="alpha"),
            EvalItem(id="B2::QUAL", question="Return alpha.", answer="alpha"),
        ]

        with tempfile.TemporaryDirectory(prefix="mstar_closure_formal_") as directory:
            output = Path(directory) / "run"
            result = run_evolution(
                [
                    str(seed_dir / "vector_search.py"),
                    str(seed_dir / "llm_summarizer.py"),
                    str(seed_dir / "experience_learner.py"),
                ],
                [episode],
                validation,
                TaskAgent(toolkit),
                toolkit,
                exact_match,
                EvolutionConfig(
                    output_dir=str(output),
                    task_description="Return alpha.",
                    iterations=3,
                    static_size=1,
                    rotating_size=1,
                    episode_budget=1,
                    selection_repeats=1,
                    selection_top_k=1,
                    write_timeout=2,
                    read_timeout=2,
                    formal=True,
                    embedding_spec={"provider": "qwen3_embedding"},
                    embedding_fingerprint="f" * 64,
                    historical_manifest_sha256="a" * 64,
                    experiment_config_sha256="b" * 64,
                    semantic_scholar_cache_sha256="c" * 64,
                    s2_asset_version="fixture-v1",
                ),
                selection_embedding_client=RuntimeEmbedding(),
            )

            closure = json.loads(
                (output / "embedding_closure_smoke.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(closure["passed"])
            self.assertEqual(result.pool_size, 6)

    def test_structured_parser_accepts_json_after_qwen_style_braced_prose(self):
        response = (
            "I will use {schema} as requested.\n"
            "```json\n{\"query_text\": \"QUAL ablations\"}\n```"
        )

        self.assertEqual(
            _parse_json_object(response),
            {"query_text": "QUAL ablations"},
        )

    def test_one_iteration_writes_child_and_artifacts(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        child_code = (seed_dir / "experience_learner.py").read_text(encoding="utf-8")
        toolkit = MemoryToolkit(llm_client=FakeLLM(child_code))
        task_agent = TaskAgent(toolkit)
        validation = [
            EvalItem(id="val_1", question="What is remembered?", answer="alpha"),
            EvalItem(id="val_2", question="Which fact?", answer="alpha"),
        ]

        with tempfile.TemporaryDirectory(prefix="mstar_search_test_") as directory:
            output = Path(directory) / "run"
            result = run_evolution(
                [
                    str(seed_dir / "vector_search.py"),
                    str(seed_dir / "llm_summarizer.py"),
                    str(seed_dir / "experience_learner.py"),
                ],
                [Episode(id="ep_1", text="The answer is alpha.")],
                validation,
                task_agent,
                toolkit,
                exact_match,
                EvolutionConfig(
                    output_dir=str(output),
                    task_description="Remember and answer simple facts.",
                    iterations=1,
                    static_size=1,
                    rotating_size=1,
                    episode_budget=1,
                    write_timeout=2,
                    read_timeout=2,
                    formal=False,
                ),
            )

            self.assertEqual(result.pool_size, 4)
            self.assertTrue(list((output / "pool").glob("program_003_child.py")))
            self.assertTrue((output / "scores.jsonl").is_file())
            self.assertTrue((output / "lineage.jsonl").is_file())
            self.assertTrue((output / "candidate_reviews.jsonl").is_file())
            candidate_reviews = [
                json.loads(line)
                for line in (output / "candidate_reviews.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertTrue(candidate_reviews)
            self.assertTrue(
                all("provenance" in value for value in candidate_reviews)
            )
            self.assertTrue((output / "best_program.py").is_file())
            trace_manifest = json.loads(
                (output / "traces" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(trace_manifest["evaluator_only"])
            self.assertTrue(trace_manifest["candidate_forbidden"])
            self.assertFalse(trace_manifest["included_in_promotion_bundle"])
            self.assertFalse(trace_manifest["candidate_mutation_may_reference"])
            score_lines = (
                (output / "scores.jsonl").read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(len(score_lines), 4)
            report = json.loads(
                (output / "best_eval_report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(report["test_set_used"])
            self.assertLessEqual(
                set(report["static_validation_ids"]), {"val_1", "val_2"}
            )
            self.assertIn("final_static_evaluation", report)
            adapter = MemoryHarnessAdapter(
                result.best_program_path,
                toolkit,
                task_agent,
            )
            adapter.ingest_episodes(
                [Episode(id="adapter_ep", text="The remembered answer is alpha.")]
            )
            self.assertEqual(adapter.answer("What is remembered?"), "alpha")

    def test_dry_run_skips_reflector(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        child_code = (seed_dir / "experience_learner.py").read_text(encoding="utf-8")
        fake_llm = FakeLLM(child_code)
        toolkit = MemoryToolkit(llm_client=fake_llm)
        with tempfile.TemporaryDirectory(prefix="mstar_dry_run_") as directory:
            result = run_evolution(
                [
                    str(seed_dir / "vector_search.py"),
                    str(seed_dir / "llm_summarizer.py"),
                    str(seed_dir / "experience_learner.py"),
                ],
                [Episode(id="ep_1", text="The answer is alpha.")],
                [EvalItem(id="val_1", question="What is it?", answer="alpha")],
                TaskAgent(toolkit),
                toolkit,
                exact_match,
                EvolutionConfig(
                    output_dir=str(Path(directory) / "run"),
                    task_description="Remember facts.",
                    iterations=5,
                    static_size=1,
                    episode_budget=1,
                    dry_run=True,
                    write_timeout=2,
                    read_timeout=2,
                    formal=False,
                ),
            )
        self.assertEqual(result.pool_size, 3)
        self.assertEqual(fake_llm.reflector_calls, 0)

    def test_formal_budget_retries_failure_until_lineages_have_equal_successes(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        seed_paths = [
            str(seed_dir / "vector_search.py"),
            str(seed_dir / "llm_summarizer.py"),
            str(seed_dir / "experience_learner.py"),
        ]
        calls = {"mutation": 0}

        def mutate(parent_code, *args, **kwargs):
            calls["mutation"] += 1
            if calls["mutation"] == 1:
                raise ValueError("protocol guard rejected fixture")
            return MutationResult(
                analysis="fixture",
                strategy="fixture",
                program_code=parent_code,
                change_log=ChangeLog(summary="successful fixture mutation"),
            )

        with tempfile.TemporaryDirectory(prefix="mstar_fair_budget_") as directory, patch(
            "memory_evolution.search.check_program_path",
            return_value=QualityResult(passed=True),
        ), patch(
            "memory_evolution.search.evaluate_program",
            return_value=EvalResult(mean_score=0.5),
        ), patch(
            "memory_evolution.search.mutate_program", side_effect=mutate
        ), patch(
            "memory_evolution.search.build_static_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ), patch(
            "memory_evolution.search.select_episode_subset",
            side_effect=lambda values, *args, **kwargs: values,
        ), patch(
            "memory_evolution.search.sample_rotating_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ):
            output = Path(directory) / "run"
            run_evolution(
                seed_paths,
                [
                    Episode(
                        id="A1",
                        text="historical",
                        metadata={
                            "experience_protocol": "self_generated_after_review",
                            "experience_protocol_version": "self-generated-experience-v1",
                        },
                    )
                ],
                [
                    EvalItem(id="B1", question="one", answer="x"),
                    EvalItem(id="B2", question="two", answer="x"),
                ],
                object(),
                MemoryToolkit(),
                exact_match,
                EvolutionConfig(
                    output_dir=str(output),
                    task_description="fixture",
                    iterations=3,
                    static_size=1,
                    rotating_size=1,
                    selection_repeats=1,
                    selection_top_k=1,
                    formal=True,
                    historical_manifest_sha256="a" * 64,
                    experiment_config_sha256="b" * 64,
                    semantic_scholar_cache_sha256="c" * 64,
                    s2_asset_version="frozen-s2-temporal-v1",
                ),
            )
            budget = json.loads((output / "lineage_budget.json").read_text())
            self.assertEqual(
                set(budget["successful_completed_by_lineage"].values()), {1}
            )
            self.assertTrue(budget["equal_completed_budget_passed"])
            self.assertEqual(calls["mutation"], 4)
            self.assertEqual(budget["max_attempts_per_lineage"], 3)
            self.assertEqual(sum(budget["attempts_by_lineage"].values()), 4)

    def test_formal_repeated_reflector_failure_exhausts_attempt_budget(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        seed_paths = [
            str(seed_dir / "vector_search.py"),
            str(seed_dir / "llm_summarizer.py"),
            str(seed_dir / "experience_learner.py"),
        ]
        with tempfile.TemporaryDirectory(prefix="mstar_reflector_cap_") as directory, patch(
            "memory_evolution.search.check_program_path",
            return_value=QualityResult(passed=True),
        ), patch(
            "memory_evolution.search.evaluate_program",
            return_value=EvalResult(mean_score=0.5),
        ), patch(
            "memory_evolution.search.mutate_program",
            side_effect=ValueError("repeated reflector rejection"),
        ), patch(
            "memory_evolution.search.build_static_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ), patch(
            "memory_evolution.search.select_episode_subset",
            side_effect=lambda values, *args, **kwargs: values,
        ), patch(
            "memory_evolution.search.sample_rotating_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ):
            output = Path(directory) / "run"
            with self.assertRaisesRegex(
                RuntimeError,
                r"lineage=evolved_from_vector_search, attempts=1, successful=0, target=1",
            ):
                run_evolution(
                    seed_paths,
                    [Episode(id="A1", text="historical")],
                    [
                        EvalItem(id="B1", question="one", answer="x"),
                        EvalItem(id="B2", question="two", answer="x"),
                    ],
                    object(),
                    MemoryToolkit(),
                    exact_match,
                    EvolutionConfig(
                        output_dir=str(output),
                        task_description="fixture",
                        iterations=3,
                        static_size=1,
                        rotating_size=1,
                        max_attempts_per_lineage=1,
                        selection_repeats=1,
                        selection_top_k=1,
                        formal=True,
                        historical_manifest_sha256="a" * 64,
                        experiment_config_sha256="b" * 64,
                        semantic_scholar_cache_sha256="c" * 64,
                        s2_asset_version="frozen-s2-temporal-v2",
                    ),
                )
            controller = json.loads(
                (output / "search_controller_checkpoint.json").read_text()
            )
            self.assertEqual(controller["attempt"], 3)
            self.assertEqual(set(controller["attempts_by_lineage"].values()), {1})
            self.assertEqual(set(controller["successful_by_lineage"].values()), {0})

    def test_formal_repeated_quality_rejection_exhausts_attempt_budget(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        seed_paths = [
            str(seed_dir / "vector_search.py"),
            str(seed_dir / "llm_summarizer.py"),
            str(seed_dir / "experience_learner.py"),
        ]

        def quality(path, *args, **kwargs):
            if "seed_" in Path(path).stem:
                return QualityResult(passed=True)
            return QualityResult(passed=False, static_errors=["fixture rejection"])

        def mutate(parent_code, *args, **kwargs):
            return MutationResult(
                analysis="fixture",
                strategy="fixture",
                program_code=parent_code,
                change_log=ChangeLog(summary="quality rejection fixture"),
            )

        with tempfile.TemporaryDirectory(prefix="mstar_quality_cap_") as directory, patch(
            "memory_evolution.search.check_program_path", side_effect=quality
        ), patch(
            "memory_evolution.search.evaluate_program",
            return_value=EvalResult(mean_score=0.5),
        ), patch(
            "memory_evolution.search.mutate_program", side_effect=mutate
        ), patch(
            "memory_evolution.search.compile_fix_loop",
            side_effect=lambda code, *args, **kwargs: CompileFixResult(
                code, 0, False, ["fixture rejection"]
            ),
        ), patch(
            "memory_evolution.search.build_static_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ), patch(
            "memory_evolution.search.select_episode_subset",
            side_effect=lambda values, *args, **kwargs: values,
        ), patch(
            "memory_evolution.search.sample_rotating_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ):
            output = Path(directory) / "run"
            with self.assertRaisesRegex(
                RuntimeError,
                r"lineage=evolved_from_vector_search, attempts=1, successful=0, target=1",
            ):
                run_evolution(
                    seed_paths,
                    [Episode(id="A1", text="historical")],
                    [
                        EvalItem(id="B1", question="one", answer="x"),
                        EvalItem(id="B2", question="two", answer="x"),
                    ],
                    object(),
                    MemoryToolkit(),
                    exact_match,
                    EvolutionConfig(
                        output_dir=str(output),
                        task_description="fixture",
                        iterations=3,
                        static_size=1,
                        rotating_size=1,
                        max_attempts_per_lineage=1,
                        selection_repeats=1,
                        selection_top_k=1,
                        formal=True,
                        historical_manifest_sha256="a" * 64,
                        experiment_config_sha256="b" * 64,
                        semantic_scholar_cache_sha256="c" * 64,
                        s2_asset_version="frozen-s2-temporal-v2",
                    ),
                )
            controller = json.loads(
                (output / "search_controller_checkpoint.json").read_text()
            )
            self.assertEqual(controller["attempt"], 3)
            self.assertEqual(set(controller["attempts_by_lineage"].values()), {1})
            self.assertEqual(set(controller["successful_by_lineage"].values()), {0})

    def test_formal_controller_resume_keeps_candidates_and_attempt_counters(self):
        root = Path(__file__).parents[1]
        seed_dir = root / "memory_evolution" / "seeds"
        seed_paths = [
            str(seed_dir / "vector_search.py"),
            str(seed_dir / "llm_summarizer.py"),
            str(seed_dir / "experience_learner.py"),
        ]
        calls = {"mutation": 0, "static_interrupt": 0}
        subset_builder = Mock(side_effect=lambda values, *args, **kwargs: values[:1])

        def mutate(parent_code, *args, **kwargs):
            calls["mutation"] += 1
            return MutationResult(
                analysis="fixture",
                strategy="fixture",
                program_code=parent_code,
                change_log=ChangeLog(summary="resumable fixture mutation"),
            )

        def evaluate(program_path, *args, **kwargs):
            if (
                Path(program_path).stem == "program_004_child"
                and calls["static_interrupt"] == 0
            ):
                calls["static_interrupt"] += 1
                raise KeyboardInterrupt("simulated SSH disconnect")
            return EvalResult(mean_score=0.5)

        with tempfile.TemporaryDirectory(prefix="mstar_controller_resume_") as directory, patch(
            "memory_evolution.search.check_program_path",
            return_value=QualityResult(passed=True),
        ), patch(
            "memory_evolution.search.evaluate_program",
            side_effect=evaluate,
        ), patch(
            "memory_evolution.search.mutate_program", side_effect=mutate
        ), patch(
            "memory_evolution.search.build_static_validation_set", subset_builder
        ), patch(
            "memory_evolution.search.select_episode_subset",
            side_effect=lambda values, *args, **kwargs: values,
        ), patch(
            "memory_evolution.search.sample_rotating_validation_set",
            side_effect=lambda values, *args, **kwargs: values[:1],
        ):
            output = Path(directory) / "run"
            config = EvolutionConfig(
                output_dir=str(output),
                task_description="fixture",
                iterations=3,
                static_size=1,
                rotating_size=1,
                max_attempts_per_lineage=1,
                selection_repeats=1,
                selection_top_k=1,
                formal=True,
                historical_manifest_sha256="a" * 64,
                experiment_config_sha256="b" * 64,
                semantic_scholar_cache_sha256="c" * 64,
                s2_asset_version="frozen-s2-temporal-v1",
            )
            episodes = [
                Episode(
                    id="A1",
                    text="historical",
                    metadata={
                        "experience_protocol": "self_generated_after_review",
                        "experience_protocol_version": "self-generated-experience-v1",
                    },
                )
            ]
            validation = [
                EvalItem(id="B1", question="one", answer="x"),
                EvalItem(id="B2", question="two", answer="x"),
            ]
            with self.assertRaises(KeyboardInterrupt):
                run_evolution(
                    seed_paths,
                    episodes,
                    validation,
                    object(),
                    MemoryToolkit(),
                    exact_match,
                    config,
                )
            interrupted = json.loads(
                (output / "search_controller_checkpoint.json").read_text()
            )
            self.assertEqual(interrupted["phase"], "search_inflight")
            self.assertEqual(interrupted["attempt"], 2)
            self.assertEqual(len(interrupted["candidates"]), 4)
            self.assertEqual(
                interrupted["attempts_by_lineage"],
                {
                    "evolved_from_vector_search": 1,
                    "evolved_from_llm_summarizer": 1,
                    "evolved_from_experience_learner": 0,
                },
            )

            result = run_evolution(
                seed_paths,
                episodes,
                validation,
                object(),
                MemoryToolkit(),
                exact_match,
                config,
            )
            completed = json.loads(
                (output / "search_controller_checkpoint.json").read_text()
            )
            self.assertEqual(completed["phase"], "complete")
            self.assertEqual(completed["attempt"], 3)
            self.assertEqual(set(completed["attempts_by_lineage"].values()), {1})
            self.assertEqual(result.pool_size, 6)
            self.assertEqual(len((output / "scores.jsonl").read_text().splitlines()), 6)
            self.assertEqual(subset_builder.call_count, 1)
            self.assertEqual(calls["mutation"], 3)


if __name__ == "__main__":
    unittest.main()
