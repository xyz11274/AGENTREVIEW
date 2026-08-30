import json
import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CoreV1IsolationTest(unittest.TestCase):
    def test_core_formal_dependency_closure_does_not_load_legacy_coco_evaluator(self):
        command = [
            sys.executable,
            "-c",
            (
                "import json,sys; import memory_evolution.core_formal; "
                "print(json.dumps(sorted(name for name in sys.modules if "
                "name in {'memory_evolution.evaluator','memory_evolution.metrics',"
                "'memory_evolution.benchmark_adapter'})))"
            ),
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), [])

    def test_v2_seeds_have_no_hidden_query_or_knowledge_llm_contract(self):
        for name in ("vector_search_v2.py", "llm_summarizer_v2.py", "experience_learner_v2.py"):
            path = ROOT / "memory_evolution" / "seeds" / name
            source = path.read_text(encoding="utf-8")
            self.assertIn('MEMORY_PROGRAM_PROTOCOL = "research-validity-core-v1"', source)
            tree = ast.parse(source)
            names = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            }
            attributes = {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            classes = {
                node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            }
            assigned = {
                target.id
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
            }
            self.assertNotIn("llm_completion", names | attributes)
            self.assertFalse({"KnowledgeItem", "Query"} & classes)
            self.assertFalse(
                {"INSTRUCTION_KNOWLEDGE_ITEM", "INSTRUCTION_QUERY"} & assigned
            )

    def test_core_modules_do_not_name_human_or_coco_as_input_fields(self):
        core = (ROOT / "memory_evolution" / "core_formal.py").read_text(
            encoding="utf-8"
        )
        for forbidden_field in (
            "human_review=",
            "openreview_reference=",
            "coco_score=",
            "coco_reference=",
        ):
            self.assertNotIn(forbidden_field, core)


if __name__ == "__main__":
    unittest.main()
