import unittest

from memory_evolution.core_reflector import CoreReflector, validate_core_reflector_feedback
from memory_evolution.core_search import CoreCandidate


class CoreReflectorTest(unittest.TestCase):
    def parent(self):
        source = (
            'MEMORY_PROGRAM_PROTOCOL = "research-validity-core-v1"\n'
            'MEMORY_READ_MODE = "DIRECT_RETRIEVAL"\n'
            'INSTRUCTION_RESPONSE = "old"\n'
            'ALWAYS_ON_KNOWLEDGE = "grounded"\n'
            'def build_retrieval_plan(paper_text, role):\n'
            '    return {"actions": []}\n'
        ).encode()
        return CoreCandidate("candidate", "a" * 64, source, "vector_search", None, 0, "1.000000000000", 0)

    def feedback(self):
        return {
            "role": "QUAL",
            "overall_fitness": "1.000000000000",
            "subdimension_scores": {"QUAL-MET": "1.000000000000"},
            "failure_codes": {"MATERIAL_OMISSION": 2},
            "evaluable_paper_count": 5,
        }

    def test_feedback_is_aggregate_only_closed_schema(self):
        value = self.feedback()
        value["paper_id"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "fields mismatch"):
            validate_core_reflector_feedback(value)

    def test_reflector_mutates_v2_program_without_legacy_contract(self):
        response = """*** Commit Message
Title: Improve grounded instruction
- aggregate omission signal
- preserve retrieval plan
*** Begin Patch
*** Update File: program.py
@@
-INSTRUCTION_RESPONSE = "old"
+INSTRUCTION_RESPONSE = "improved"
*** End Patch"""
        prompts = []

        def complete(messages):
            prompts.append(messages[0]["content"])
            return response

        reflector = CoreReflector(
            complete=complete,
            validate_program=lambda program: None,
            max_mutation_attempts=1,
            max_compile_fix_attempts=0,
        )
        child = reflector.mutate(self.parent(), self.feedback(), 1)
        self.assertIn(b'INSTRUCTION_RESPONSE = "improved"', child)
        self.assertNotIn("paper_id", prompts[0])
        self.assertIn("aggregate-only", prompts[0])
        self.assertIn("POST_RETRIEVAL_LLM_SUMMARY", prompts[0])


if __name__ == "__main__":
    unittest.main()
