import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentreview.cli import run_review
from agentreview.reviewer_runtime import ReviewerEngine, ReviewRequest
from agentreview.reviewer_runtime.artifacts import (
    CandidateReviewRecord,
    CandidateReviewWriter,
)
from agentreview.reviewer_runtime.task_agent import build_task_agent
from agentreview.reviewer_runtime.tasks import reviewer_identity
from agentreview.reviewer_runtime.toolkit import MemoryToolkit
from memory_evolution.evaluator import evaluate_program
from memory_evolution.metrics import exact_match
from memory_evolution.models import EvalItem


class DeterministicReviewerLLM:
    def llm_completion(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        if "KnowledgeItem must conform" in prompt:
            schema = json.loads(prompt.split("schema:\n", 1)[1].split("\n\n", 1)[0])
            return json.dumps(
                {
                    name: "QUAL" if "dimension" in name else "memory"
                    for name in schema["properties"]
                }
            )
        if "Query must conform" in prompt:
            schema = json.loads(prompt.split("schema:\n", 1)[1].split("\n\n", 1)[0])
            return json.dumps(
                {
                    name: "QUAL" if "dimension" in name else "paper"
                    for name in schema["properties"]
                }
            )
        if "Provide the final answer only" in prompt:
            return "deterministic candidate review"
        if "query-focused memory summary" in prompt:
            return "memory"
        return "memory"


class ReviewerRuntimeDecouplingTest(unittest.TestCase):
    @property
    def root(self):
        return Path(__file__).parents[1]

    def _request(self):
        return ReviewRequest(
            paper_id="paper-1",
            task_id="QUAL",
            question="Review task: QUAL\n\nPaper text",
            model="mock-model",
            revision="mock-revision",
            run_identity="mock-run",
        )

    def _engine(self):
        toolkit = MemoryToolkit(
            llm_client=DeterministicReviewerLLM(), llm_model="mock-model"
        )
        return ReviewerEngine(toolkit, require_bubblewrap=False, timeout_seconds=5)

    def test_seed_reviewer_only(self):
        result = self._engine().review(
            self._request(),
            self.root / "memory_evolution/seeds/vector_search.py",
        )
        self.assertEqual(result.review_text, "deterministic candidate review")
        self.assertEqual(result.memory_program_identity["source_kind"], "seed")

    def test_existing_best_program_reviewer_only(self):
        seed_program = self.root / "memory_evolution/seeds/vector_search.py"
        with tempfile.TemporaryDirectory() as directory:
            promoted_program = Path(directory) / "QUAL" / "best_program.py"
            promoted_program.parent.mkdir(parents=True)
            promoted_program.write_bytes(seed_program.read_bytes())
            result = self._engine().review(self._request(), promoted_program)
        self.assertEqual(result.review_text, "deterministic candidate review")
        self.assertEqual(
            result.memory_program_identity["source_kind"], "promoted_best_program"
        )

    def test_future_optimizer_program_runs_without_optimizer_metadata(self):
        source = """
from dataclasses import dataclass
INSTRUCTION_KNOWLEDGE_ITEM = "remember"
INSTRUCTION_QUERY = "query"
INSTRUCTION_RESPONSE = "respond"
ALWAYS_ON_KNOWLEDGE = "stable"
@dataclass
class KnowledgeItem:
    content: str = ""
@dataclass
class Query:
    query_text: str = ""
class KnowledgeBase:
    def __init__(self, toolkit): self.values = []
    def write(self, item, raw_text): self.values.append(raw_text)
    def read(self, query): return "\\n".join(self.values)[:3000]
""".lstrip()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future_optimizer_output.py"
            path.write_text(source, encoding="utf-8")
            result = self._engine().review(self._request(), path)
        self.assertEqual(result.review_text, "deterministic candidate review")
        self.assertEqual(
            result.memory_program_identity["source_kind"], "optimizer_output"
        )

    def test_core_import_does_not_load_memory_evolution(self):
        command = [
            sys.executable,
            "-c",
            (
                "import json,sys; import agentreview.reviewer_runtime; "
                "print(json.dumps(sorted(k for k in sys.modules "
                "if k == 'memory_evolution' or k.startswith('memory_evolution.'))))"
            ),
        ]
        completed = subprocess.run(
            command,
            cwd=str(self.root),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout), [])

    def test_old_evolution_generation_and_core_are_semantically_equal(self):
        program = self.root / "memory_evolution/seeds/vector_search.py"
        old_toolkit = MemoryToolkit(
            llm_client=DeterministicReviewerLLM(), llm_model="mock-model"
        )
        evaluation = evaluate_program(
            str(program),
            [],
            [
                EvalItem(
                    id="paper-1::QUAL",
                    question=self._request().question,
                    answer="deterministic candidate review",
                )
            ],
            build_task_agent(old_toolkit, "QUAL"),
            old_toolkit,
            exact_match,
            require_bubblewrap=False,
        )
        new_result = self._engine().review(self._request(), program)
        self.assertEqual(evaluation.traces[-1]["answer"], new_result.review_text)
        self.assertEqual(evaluation.mean_score, 1.0)

    def test_candidate_schema_keeps_evolution_provenance_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_reviews.jsonl"
            record = CandidateReviewRecord(
                paper_id="paper-1",
                task_id="META",
                review_text="review",
                reviewer_identity=reviewer_identity("META"),
                memory_program_identity={
                    "logical_identity": "future",
                    "source_kind": "optimizer_output",
                },
                memory_program_sha256="a" * 64,
                model="mock",
                revision="r1",
                run_identity="run-1",
            )
            CandidateReviewWriter(path).append(record)
            value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(value["provenance"], {})
        self.assertNotIn("lineage", value)
        self.assertNotIn("checkpoint", value)

    def test_temporal_batch_requires_explicit_history_unless_debug_override(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                output=str(Path(directory) / "candidate_reviews.jsonl"),
                overwrite=False,
                episodes_jsonl="",
                temporal_manifest="frozen-manifest.json",
                allow_fresh_memory=False,
            )
            with self.assertRaisesRegex(ValueError, "explicit frozen"):
                run_review(args)


if __name__ == "__main__":
    unittest.main()
