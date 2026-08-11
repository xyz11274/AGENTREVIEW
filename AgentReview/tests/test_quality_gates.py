import unittest
from pathlib import Path

from memory_evolution.quality import check_program_code, check_program_path


VALID_PREFIX = """
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "extract"
INSTRUCTION_QUERY = "query"
INSTRUCTION_RESPONSE = "answer"
ALWAYS_ON_KNOWLEDGE = "verify current evidence"

@dataclass
class KnowledgeItem:
    content: str = ""

@dataclass
class Query:
    query_text: str = ""
"""


class QualityGatesTest(unittest.TestCase):
    def test_non_whitelisted_import_is_rejected(self):
        result = check_program_code("import os\n" + VALID_PREFIX)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("Import is not allowed: os" in error for error in result.errors)
        )

    def test_missing_knowledge_base_is_rejected(self):
        result = check_program_code(VALID_PREFIX)
        self.assertFalse(result.passed)
        self.assertTrue(any("KnowledgeBase" in error for error in result.errors))

    def test_builtins_import_escape_is_rejected(self):
        code = (
            VALID_PREFIX
            + """
class KnowledgeBase:
    def __init__(self, toolkit):
        globals()["__builtins__"]["__import__"]("os")
    def write(self, item, raw_text):
        pass
    def read(self, query):
        return ""
"""
        )
        result = check_program_code(code)
        self.assertFalse(result.passed)
        self.assertTrue(any("globals" in error for error in result.errors))

    def test_overlong_read_is_rejected(self):
        code = (
            VALID_PREFIX
            + """
class KnowledgeBase:
    def __init__(self, toolkit):
        pass
    def write(self, item, raw_text):
        pass
    def read(self, query):
        return "x" * 3001
"""
        )
        result = check_program_code(code, write_timeout=2, read_timeout=2)
        self.assertFalse(result.passed)
        self.assertTrue(any("limit is 3000" in error for error in result.errors))

    def test_normal_seed_passes(self):
        seed = (
            Path(__file__).parents[1]
            / "memory_evolution"
            / "seeds"
            / "experience_learner.py"
        )
        result = check_program_path(str(seed), write_timeout=2, read_timeout=2)
        self.assertTrue(result.passed, result.errors)


if __name__ == "__main__":
    unittest.main()
