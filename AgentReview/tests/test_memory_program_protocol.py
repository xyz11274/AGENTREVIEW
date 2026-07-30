import unittest
from dataclasses import fields
from pathlib import Path

from memory_evolution.sandbox import SandboxSession
from memory_evolution.toolkit import MemoryToolkit


class FakeLLM:
    def llm_completion(self, messages, **kwargs):
        return "focused summary"


class RecordingLLM:
    def __init__(self):
        self.prompts = []

    def llm_completion(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return "s" * 5000


def _instance(dataclass_type):
    values = {}
    for item in fields(dataclass_type):
        if item.name == "query_text":
            values[item.name] = "alpha"
        else:
            values[item.name] = f"sample {item.name}"
    return dataclass_type(**values)


class MemoryProgramProtocolTest(unittest.TestCase):
    def test_seed_programs_import_and_smoke(self):
        seed_dir = Path(__file__).parents[1] / "memory_evolution" / "seeds"
        toolkit = MemoryToolkit(llm_client=FakeLLM())
        seed_paths = [
            seed_dir / "vector_search.py",
            seed_dir / "llm_summarizer.py",
            seed_dir / "experience_learner.py",
        ]

        for path in seed_paths:
            with self.subTest(seed=path.name):
                toolkit.reset_vector_collections()
                with SandboxSession(str(path), toolkit) as session:
                    program = session.program_interface()
                    item = _instance(program.KnowledgeItem)
                    query = _instance(program.Query)
                    with toolkit.operation("test_write"):
                        session.write(
                            item,
                            "Alpha is the fact stored in this episode.",
                        )
                    with toolkit.operation("test_read"):
                        result = session.read(query)
                self.assertIsInstance(result, str)
                self.assertLessEqual(len(result), 3000)

    def test_llm_summarizer_has_bounded_balanced_read_context(self):
        seed = (
            Path(__file__).parents[1]
            / "memory_evolution"
            / "seeds"
            / "llm_summarizer.py"
        )
        llm = RecordingLLM()
        toolkit = MemoryToolkit(llm_client=llm)
        first = "FIRST_HEAD-" + ("a" * 8000) + "-FIRST_TAIL"
        second = "SECOND_HEAD-" + ("b" * 8000) + "-SECOND_TAIL"

        with SandboxSession(str(seed), toolkit) as session:
            program = session.program_interface()
            self.assertEqual(
                [item.name for item in fields(program.KnowledgeItem)],
                ["reviewer_dimension"],
            )
            item = program.KnowledgeItem(reviewer_dimension="QUAL")
            with toolkit.operation("test_write"):
                session.write(item, first)
            with toolkit.operation("test_write"):
                session.write(item, second)
            query = program.Query(
                query_text="Which evidence matters?",
                reviewer_dimension="QUAL",
            )
            with toolkit.operation("test_read"):
                result = session.read(query)

        self.assertEqual(len(result), 1500)
        self.assertEqual(len(llm.prompts), 1)
        prompt = llm.prompts[0]
        history = prompt.split("Memory history:\n", 1)[1]
        self.assertLessEqual(len(history), 12000)
        self.assertIn("FIRST_HEAD", history)
        self.assertIn("FIRST_TAIL", history)
        self.assertIn("SECOND_HEAD", history)
        self.assertIn("SECOND_TAIL", history)
        self.assertIn("at most 1500 characters", prompt)


if __name__ == "__main__":
    unittest.main()
