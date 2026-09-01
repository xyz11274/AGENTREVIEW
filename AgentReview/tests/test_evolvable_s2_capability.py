import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
    static_check,
)
from agentreview.reviewer_runtime.sandbox import SandboxError, SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit


SEED_ROOT = (
    Path(__file__).resolve().parents[1] / "agentreview" / "research_v2" / "seeds"
)
EVOLUTION_SEEDS = (
    "vector_search.py",
    "llm_summarizer.py",
    "experience_learner.py",
)


class CurrentPaperS2CapabilityTest(unittest.TestCase):
    def test_current_paper_capability_is_scoped_traced_and_single_use(self):
        toolkit = MemoryToolkit(
            s2_retrieval_pool={
                "p1": [
                    {"paperId": "s1", "title": "First"},
                    {"paper_id": "s2", "title": "Second"},
                ],
                "p2": [{"paperId": "other", "title": "Other paper"}],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "requires paper_scope"):
            toolkit.retrieve_current_s2()

        with toolkit.paper_scope("p1::QUAL"):
            with self.assertRaisesRegex(ValueError, "between 1 and 5"):
                toolkit.retrieve_current_s2(limit=0)
            selected = toolkit.retrieve_current_s2(limit=2)
            self.assertEqual(
                [value.get("paperId") or value.get("paper_id") for value in selected],
                ["s1", "s2"],
            )
            self.assertEqual(
                toolkit.s2_trace_snapshot(),
                {
                    "paper_id": "p1",
                    "calls": [{"requested_limit": 2, "result_ids": ["s1", "s2"]}],
                },
            )
            with self.assertRaisesRegex(RuntimeError, "one call per paper scope"):
                toolkit.retrieve_current_s2(limit=1)

        self.assertEqual(toolkit.s2_trace_snapshot()["paper_id"], "p1")
        with toolkit.paper_scope("p2"):
            self.assertEqual(
                toolkit.retrieve_current_s2(limit=1)[0]["paperId"], "other"
            )
        self.assertEqual(toolkit.resource_snapshot()["s2_call_count"], 2)

    def test_all_evolution_seeds_return_a_strict_autonomous_plan(self):
        for seed_name in EVOLUTION_SEEDS:
            with self.subTest(seed=seed_name):
                toolkit = MemoryToolkit(
                    s2_retrieval_pool={
                        "p1": [
                            {"paperId": "s1", "title": "First"},
                            {"paperId": "s2", "title": "Second"},
                        ]
                    }
                )
                with SandboxSession(
                    str(SEED_ROOT / seed_name),
                    toolkit,
                    require_bubblewrap=False,
                ) as sandbox:
                    program = sandbox.program_interface()
                    query = program.Query(
                        query_text="A robust evaluation paper",
                        reviewer_dimension="QUAL",
                    )
                    with toolkit.paper_scope("p1"):
                        plan = sandbox.plan_review(query, "frozen Memory context")
                self.assertEqual(plan["protocol"], REVIEW_PLAN_PROTOCOL)
                self.assertEqual(plan["s2_disposition"], "use")
                self.assertEqual(plan["selected_s2_ids"], ["s1", "s2"])
                self.assertEqual(
                    set(plan["context_order"]), {"memory", "s2", "strategy"}
                )
                self.assertEqual(toolkit.resource_snapshot()["s2_call_count"], 1)
                self.assertEqual(
                    toolkit.s2_trace_snapshot()["calls"][0]["result_ids"],
                    ["s1", "s2"],
                )

    def test_seed_records_consulted_no_use_when_pool_has_no_identified_result(self):
        toolkit = MemoryToolkit(s2_retrieval_pool={"p1": [{"title": "No ID"}]})
        with SandboxSession(
            str(SEED_ROOT / "vector_search.py"),
            toolkit,
            require_bubblewrap=False,
        ) as sandbox:
            program = sandbox.program_interface()
            query = program.Query(query_text="paper", reviewer_dimension="ORIG")
            with toolkit.paper_scope("p1"):
                plan = sandbox.plan_review(query, "")
        self.assertEqual(plan["s2_disposition"], "consulted_no_use")
        self.assertEqual(plan["selected_s2_ids"], [])

    def test_sandbox_rejects_non_dict_plan(self):
        source = """
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "item"
INSTRUCTION_QUERY = "query"
INSTRUCTION_RESPONSE = "response"
ALWAYS_ON_KNOWLEDGE = "always"

@dataclass
class KnowledgeItem:
    content: str = ""

@dataclass
class Query:
    query_text: str = ""

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
    def write(self, item, raw_text):
        return None
    def read(self, query):
        return ""

def plan_review(toolkit, query, memory_context):
    return []
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.py"
            path.write_text(source, encoding="utf-8")
            toolkit = MemoryToolkit()
            with SandboxSession(
                str(path), toolkit, require_bubblewrap=False
            ) as sandbox:
                query = sandbox.program_interface().Query(query_text="paper")
                with toolkit.paper_scope("p1"):
                    with self.assertRaisesRegex(SandboxError, "must return dict"):
                        sandbox.plan_review(query, "")

    def test_review_plan_operation_exposes_only_current_s2(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        source = source.replace(
            "evidence = toolkit.retrieve_current_s2(limit=5)",
            'toolkit.embed(["forbidden during planning"])\n    evidence = []',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.py"
            path.write_text(source, encoding="utf-8")
            toolkit = MemoryToolkit()
            with SandboxSession(
                str(path), toolkit, require_bubblewrap=False
            ) as sandbox:
                query = sandbox.program_interface().Query(
                    query_text="paper", reviewer_dimension="QUAL"
                )
                with toolkit.paper_scope("p1"):
                    with toolkit.operation(
                        "review_plan",
                        max_llm_calls=0,
                        allowed_capabilities={"retrieve_current_s2"},
                    ):
                        with self.assertRaisesRegex(
                            SandboxError, "has no attribute 'embed'"
                        ):
                            sandbox.plan_review(query, "")

    def test_review_plan_runtime_facade_does_not_expose_memory_state(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        source = source.replace(
            "evidence = toolkit.retrieve_current_s2(limit=5)",
            "alias = toolkit\n    _ = alias.db\n    evidence = []",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.py"
            path.write_text(source, encoding="utf-8")
            toolkit = MemoryToolkit()
            with SandboxSession(
                str(path), toolkit, require_bubblewrap=False
            ) as sandbox:
                query = sandbox.program_interface().Query(
                    query_text="paper", reviewer_dimension="QUAL"
                )
                with toolkit.paper_scope("p1"):
                    with self.assertRaisesRegex(SandboxError, "has no attribute 'db'"):
                        sandbox.plan_review(query, "")


class ProgramCapabilityManifestTest(unittest.TestCase):
    def test_evolution_seeds_pass_static_check_and_have_stable_manifests(self):
        for seed_name in EVOLUTION_SEEDS:
            with self.subTest(seed=seed_name):
                path = SEED_ROOT / seed_name
                source = path.read_text(encoding="utf-8")
                self.assertEqual(static_check(source), [])
                self.assertEqual(
                    program_capability_manifest(path=path),
                    program_capability_manifest(code=source),
                )

    def test_static_check_requires_plan_and_rejects_host_only_s2_lookup(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        missing_plan = source.replace("def plan_review(", "def choose_review(", 1)
        self.assertTrue(
            any(
                "exactly one top-level plan_review" in error
                for error in static_check(missing_plan)
            )
        )
        host_lookup = source.replace(
            "toolkit.retrieve_current_s2(limit=5)",
            'toolkit.retrieve_s2("p1", limit=5)',
            1,
        )
        self.assertTrue(
            any("host-only capability" in error for error in static_check(host_lookup))
        )

    def test_manifest_separates_policy_and_memory_ast_identities(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        baseline = program_capability_manifest(code=source)
        policy_change = program_capability_manifest(
            code=source.replace(
                "seed_always_consult_top5", "seed_consult_for_novelty", 1
            )
        )
        self.assertNotEqual(
            baseline["policy_ast_sha256"], policy_change["policy_ast_sha256"]
        )
        self.assertEqual(
            baseline["memory_ast_sha256"], policy_change["memory_ast_sha256"]
        )
        self.assertNotEqual(baseline["program_sha256"], policy_change["program_sha256"])

        memory_change = program_capability_manifest(
            code=source.replace("research_v2_vector", "research_v2_vector_v2", 1)
        )
        self.assertEqual(
            baseline["policy_ast_sha256"], memory_change["policy_ast_sha256"]
        )
        self.assertNotEqual(
            baseline["memory_ast_sha256"], memory_change["memory_ast_sha256"]
        )

        instruction_change = program_capability_manifest(
            code=source.replace(
                "Store traceable Review Experience without paper prose.",
                "Store role-local Review Experience without paper prose.",
                1,
            )
        )
        self.assertEqual(
            baseline["policy_ast_sha256"], instruction_change["policy_ast_sha256"]
        )
        self.assertNotEqual(
            baseline["memory_ast_sha256"], instruction_change["memory_ast_sha256"]
        )

    def test_static_check_keeps_policy_component_self_contained_and_deterministic(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        helper = source.replace(
            "def plan_review(toolkit, query, memory_context):",
            "def policy_threshold():\n    return 2\n\n\n"
            "def plan_review(toolkit, query, memory_context):",
            1,
        )
        self.assertTrue(
            any("helper functions" in error for error in static_check(helper))
        )
        nondeterministic = source.replace(
            "from hashlib import sha256",
            "from hashlib import sha256\nimport time",
            1,
        )
        self.assertTrue(
            any(
                "Import is not allowed: time" in error
                for error in static_check(nondeterministic)
            )
        )

    def test_static_check_rejects_cross_component_symbol_dependencies(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        policy_reads_memory = source.replace(
            "def plan_review(toolkit, query, memory_context):",
            "def plan_review(toolkit, query, memory_context):\n    _ = KnowledgeBase",
            1,
        )
        self.assertTrue(
            any(
                "independent from Memory classes" in error
                for error in static_check(policy_reads_memory)
            )
        )
        memory_reads_policy = source.replace(
            "def read(self, query: Query) -> str:",
            "def read(self, query: Query) -> str:\n        _ = plan_review",
            1,
        )
        self.assertTrue(
            any(
                "independent from plan_review" in error
                for error in static_check(memory_reads_policy)
            )
        )

    def test_static_check_rejects_toolkit_and_query_aliases(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        toolkit_alias = source.replace(
            "def plan_review(toolkit, query, memory_context):",
            "def plan_review(toolkit, query, memory_context):\n    alias = toolkit",
            1,
        )
        self.assertTrue(
            any(
                "direct retrieve_current_s2" in error
                for error in static_check(toolkit_alias)
            )
        )
        query_alias = source.replace(
            "def plan_review(toolkit, query, memory_context):",
            "def plan_review(toolkit, query, memory_context):\n    alias = query",
            1,
        )
        self.assertTrue(
            any("canonical query_text" in error for error in static_check(query_alias))
        )

    def test_static_check_rejects_untracked_import_execution(self):
        source = (SEED_ROOT / "vector_search.py").read_text(encoding="utf-8")
        unused_import = source.replace(
            "from dataclasses import dataclass",
            "from dataclasses import dataclass\nimport math",
            1,
        )
        self.assertTrue(
            any(
                "Every import must be referenced" in error
                for error in static_check(unused_import)
            )
        )


if __name__ == "__main__":
    unittest.main()
