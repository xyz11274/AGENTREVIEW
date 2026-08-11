import unittest

from evolution_viewer.code_analysis import (
    analyze_program,
    changed_sections,
    compare_programs,
    word_diff,
)


BEFORE = '''
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "Store a concise fact."
INSTRUCTION_QUERY = "Build a query."
INSTRUCTION_RESPONSE = "Use retrieved context."
ALWAYS_ON_KNOWLEDGE = "Verify against the current paper."

@dataclass
class KnowledgeItem:
    content: str = ""

@dataclass
class Query:
    query_text: str = ""

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.items = [item.content]

    def read(self, query: Query) -> str:
        return "\\n".join(self.items)
'''


AFTER = '''
import re
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Store a reusable lesson and a concrete fact."
INSTRUCTION_QUERY = "Build a reviewer-specific query."
INSTRUCTION_RESPONSE = "Use relevant context and cite remembered signals."
ALWAYS_ON_KNOWLEDGE = "Verify every judgment against the current paper."

@dataclass
class KnowledgeItem:
    content: str = ""
    signals: str = field(default="")

@dataclass
class Query:
    query_text: str = ""

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.items = [item.content, self._extract(raw_text)]

    @staticmethod
    def _extract(text: str) -> str:
        return re.sub(r"\\s+", " ", text)

    def read(self, query: Query) -> str:
        if not query.query_text:
            return ""
        return "\\n".join(self.items)
'''


class CodeAnalysisTest(unittest.TestCase):
    def test_extracts_facts_without_execution(self):
        value = analyze_program(AFTER)

        self.assertIsNone(value["parse_error"])
        self.assertIn("INSTRUCTION_RESPONSE", value["prompts"])
        self.assertIn("signals", value["schemas"]["KnowledgeItem"])
        self.assertIn("write", value["methods"])
        self.assertIn("KnowledgeBase._extract", value["helpers"])
        self.assertIn("re", value["imports"])

    def test_comparison_is_categorized(self):
        value = compare_programs(BEFORE, AFTER, before_name="a.py", after_name="b.py")

        self.assertEqual(changed_sections(value), ["Prompt", "Schema", "Write", "Read", "辅助代码"])
        self.assertTrue(all(item["status"] == "modified" for item in value["prompts"]))
        self.assertEqual(value["schema"][0]["field"], "signals")
        self.assertIn("self._extract", value["write"][0]["calls"]["added"])
        self.assertEqual(value["read"][0]["branches_after"]["if"], 1)
        self.assertIn("--- a.py", value["raw_diff"])
        self.assertIn("+++ b.py", value["raw_diff"])

    def test_syntax_error_falls_back_to_text_diff(self):
        value = compare_programs("def broken(:\\n", "x = 1\\n")

        self.assertIn("SyntaxError", value["parse_errors"]["before"])
        self.assertIn("-def broken(", value["raw_diff"])

    def test_word_diff_marks_added_and_removed_tokens(self):
        old, new = word_diff("use context", "use relevant context")

        self.assertIn(("insert", "relevant"), new)
        self.assertFalse(any(status == "delete" for status, _ in old))


if __name__ == "__main__":
    unittest.main()
