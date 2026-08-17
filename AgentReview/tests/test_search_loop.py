import json
import tempfile
import unittest
from pathlib import Path

from memory_evolution.integration import MemoryHarnessAdapter
from memory_evolution.metrics import exact_match
from memory_evolution.models import Episode, EvalItem
from memory_evolution.search import EvolutionConfig, run_evolution
from memory_evolution.task_agent import TaskAgent
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
            self.assertTrue((output / "best_program.py").is_file())
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


if __name__ == "__main__":
    unittest.main()
