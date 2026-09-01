import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentreview.reviewer_runtime.task_agent import dataclass_schema
from agentreview.research_v2.formal_evolution import BoundedCompletionClient
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.memory import (
    DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY,
    build_memory_snapshot,
    retrieve_memory,
    seed_program_path,
)
from agentreview.research_v2.models import Paper


class InterruptingCompletion:
    def __init__(self, responses, *, fail_on_call=None):
        self.responses = list(responses)
        self.fail_on_call = fail_on_call
        self.calls = []

    def complete(self, messages, *, max_tokens, component):
        self.calls.append(component)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated interruption")
        return self.responses.pop(0)


class StructuredCompletion:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(
        self,
        messages,
        *,
        max_tokens,
        component,
        response_format,
    ):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "component": component,
                "response_format": response_format,
            }
        )
        return self.responses.pop(0)


class FixedEmbedding:
    def embed(self, texts):
        return [[1.0, float(bool(str(text).strip()))] for text in texts]


@dataclass
class MixedKnowledgeItem:
    text: str
    count: int
    ratio: float
    enabled: bool
    tags: list[str]
    note: Optional[str]


def _experience_payload(label):
    return json.dumps(
        {
            "trigger": f"trigger-{label}",
            "review_heuristic": f"heuristic-{label}",
            "evidence_to_check": f"evidence-{label}",
            "failure_mode": f"failure-{label}",
            "why_it_matters": f"importance-{label}",
            "reviewer_dimension": "QUAL",
        }
    )


class ResumableMemoryBuildTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reviews = []
        self.evaluations = []
        for ordinal in range(2):
            review_id = f"review-{ordinal}"
            review_path = self.root / f"{review_id}.json"
            evaluation_path = self.root / f"evaluation-{ordinal}.json"
            atomic_write_json(
                review_path,
                {
                    "review_id": review_id,
                    "paper_id": f"paper-{ordinal}",
                    "role": "QUAL",
                    "review_text": f"Review text {ordinal}",
                    "paper": {"title": f"Paper {ordinal}"},
                },
            )
            atomic_write_json(
                evaluation_path,
                {
                    "evaluation_id": f"evaluation-{ordinal}",
                    "review_id": review_id,
                    "paper_id": f"paper-{ordinal}",
                    "role": "QUAL",
                    "review_quality": 3,
                },
            )
            self.reviews.append(review_path)
            self.evaluations.append(evaluation_path)
        self.embedding_identity = {
            "provider": "test-embedding",
            "model": "fixed",
            "revision": "one",
        }
        self.completion_identity = {
            "provider": "test-completion",
            "model": "fixed",
            "revision": "one",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _build(self, output, client, **overrides):
        arguments = {
            "role": "QUAL",
            "program_path": seed_program_path("experience_learner"),
            "program_version": "experience-v1",
            "review_paths": self.reviews,
            "evaluation_paths": self.evaluations,
            "output_path": output,
            "seed_kind": "experience_learner",
            "embedding_client": FixedEmbedding(),
            "embedding_identity": self.embedding_identity,
            "completion_client": client,
            "completion_identity": self.completion_identity,
            "require_bubblewrap": False,
        }
        arguments.update(overrides)
        return build_memory_snapshot(**arguments)

    def test_resume_replays_checkpoint_without_repeating_induction(self):
        interrupted_output = self.root / "interrupted" / "snapshot.json"
        first = InterruptingCompletion(
            [_experience_payload("zero"), _experience_payload("one")],
            fail_on_call=2,
        )
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            self._build(interrupted_output, first)
        self.assertEqual(len(first.calls), 2)
        checkpoints = sorted(
            interrupted_output.with_name("snapshot.json.items").glob("*.json")
        )
        self.assertEqual(len(checkpoints), 1)

        resumed = InterruptingCompletion([_experience_payload("one")])
        resumed_snapshot = self._build(interrupted_output, resumed)
        self.assertEqual(len(resumed.calls), 1)

        clean = InterruptingCompletion(
            [_experience_payload("zero"), _experience_payload("one")]
        )
        clean_snapshot = self._build(self.root / "clean" / "snapshot.json", clean)
        self.assertEqual(resumed_snapshot, clean_snapshot)
        self.assertEqual(
            resumed_snapshot["embedding_identity"], self.embedding_identity
        )
        self.assertEqual(
            [item["payload"] for item in resumed_snapshot["items"]],
            [
                json.loads(_experience_payload("zero")),
                json.loads(_experience_payload("one")),
            ],
        )

        no_op = InterruptingCompletion([])
        self.assertEqual(self._build(interrupted_output, no_op), resumed_snapshot)
        self.assertEqual(no_op.calls, [])

    def test_memory_induction_uses_one_strict_json_schema_call_per_item(self):
        transport = StructuredCompletion(
            [_experience_payload("zero"), _experience_payload("one")]
        )
        snapshot = self._build(
            self.root / "structured" / "snapshot.json",
            BoundedCompletionClient(transport, 1),
        )

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(snapshot["items"]), 2)
        for call in transport.calls:
            response_format = call["response_format"]
            self.assertEqual(response_format["type"], "json_schema")
            specification = response_format["json_schema"]
            self.assertEqual(specification["name"], "KnowledgeItem")
            self.assertTrue(specification["strict"])
            schema = specification["schema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["required"], list(schema["properties"]))
            self.assertEqual(
                {value["type"] for value in schema["properties"].values()},
                {"string"},
            )

    def test_dataclass_schema_maps_supported_python_annotations(self):
        schema = dataclass_schema(MixedKnowledgeItem)

        self.assertEqual(
            schema["properties"],
            {
                "text": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "note": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
            },
        )
        self.assertEqual(schema["required"], list(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])

    def test_checkpoint_rejects_changed_embedding_or_source_artifact(self):
        output = self.root / "strict" / "snapshot.json"
        interrupted = InterruptingCompletion(
            [_experience_payload("zero"), _experience_payload("one")],
            fail_on_call=2,
        )
        with self.assertRaises(RuntimeError):
            self._build(output, interrupted)

        with self.assertRaisesRegex(ValueError, "input identity differs"):
            self._build(
                output,
                InterruptingCompletion([_experience_payload("one")]),
                embedding_identity={"provider": "different"},
            )

        with self.assertRaisesRegex(ValueError, "input identity differs"):
            self._build(
                output,
                InterruptingCompletion([_experience_payload("one")]),
                completion_identity={"provider": "different"},
            )

        review = read_json(self.reviews[0])
        review["review_text"] = "changed source artifact"
        self.reviews[0].write_text(
            json.dumps(review, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "input identity differs"):
            self._build(
                output,
                InterruptingCompletion([_experience_payload("one")]),
            )

    def test_snapshot_hash_is_checked_before_reuse_and_retrieval(self):
        output = self.root / "tampered" / "snapshot.json"
        snapshot = self._build(
            output,
            InterruptingCompletion(
                [_experience_payload("zero"), _experience_payload("one")]
            ),
        )
        snapshot["snapshot"]["tampered"] = True
        output.write_text(json.dumps(snapshot), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "snapshot identity mismatch"):
            self._build(output, InterruptingCompletion([]))
        with self.assertRaisesRegex(ValueError, "snapshot identity mismatch"):
            retrieve_memory(
                paper=Paper(
                    paper_id="paper-x",
                    title="Paper",
                    abstract="Abstract",
                    text="Text",
                    source={},
                ),
                role="QUAL",
                program_path=seed_program_path("experience_learner"),
                snapshot_path=output,
                require_bubblewrap=False,
            )

    def test_embedding_client_requires_explicit_identity(self):
        with self.assertRaisesRegex(ValueError, "requires embedding_identity"):
            self._build(
                self.root / "missing-identity" / "snapshot.json",
                InterruptingCompletion(
                    [_experience_payload("zero"), _experience_payload("one")]
                ),
                embedding_client=object(),
                embedding_identity=None,
            )

    def test_completion_client_requires_explicit_identity(self):
        with self.assertRaisesRegex(ValueError, "requires completion_identity"):
            self._build(
                self.root / "missing-completion-identity" / "snapshot.json",
                InterruptingCompletion(
                    [_experience_payload("zero"), _experience_payload("one")]
                ),
                completion_identity=None,
            )

    def test_retrieval_rejects_a_different_embedding_identity(self):
        output = self.root / "embedding-runtime" / "snapshot.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            program_version="vector-v1",
            review_paths=[],
            evaluation_paths=[],
            output_path=output,
            seed_kind="vector_search",
            embedding_client=FixedEmbedding(),
            embedding_identity=self.embedding_identity,
            require_bubblewrap=False,
        )
        arguments = {
            "paper": Paper(
                paper_id="paper-x",
                title="Paper",
                abstract="Abstract",
                text="Text",
                source={},
            ),
            "role": "QUAL",
            "program_path": seed_program_path("vector_search"),
            "snapshot_path": output,
            "embedding_client": object(),
            "require_bubblewrap": False,
        }
        with self.assertRaisesRegex(ValueError, "requires embedding_identity"):
            retrieve_memory(**arguments)
        with self.assertRaisesRegex(ValueError, "identity differs"):
            retrieve_memory(
                **arguments,
                embedding_identity={"provider": "different"},
            )
        arguments.pop("embedding_client")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            retrieve_memory(**arguments)
        with self.assertRaisesRegex(ValueError, "deterministic fallback"):
            retrieve_memory(
                **arguments,
                embedding_identity=self.embedding_identity,
            )

    def test_build_rejects_external_identity_without_its_client(self):
        with self.assertRaisesRegex(ValueError, "deterministic fallback"):
            build_memory_snapshot(
                role="QUAL",
                program_path=seed_program_path("vector_search"),
                program_version="vector-invalid-runtime-v1",
                review_paths=[],
                evaluation_paths=[],
                output_path=self.root / "invalid-runtime" / "snapshot.json",
                seed_kind="vector_search",
                embedding_identity=self.embedding_identity,
                require_bubblewrap=False,
            )

    def test_fallback_built_snapshot_remains_readable_without_a_client(self):
        output = self.root / "fallback-runtime" / "snapshot.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            program_version="vector-fallback-v1",
            review_paths=[],
            evaluation_paths=[],
            output_path=output,
            seed_kind="vector_search",
            require_bubblewrap=False,
        )
        self.assertEqual(
            read_json(output)["embedding_identity"],
            DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY,
        )
        result = retrieve_memory(
            paper=Paper(
                paper_id="paper-fallback",
                title="Paper",
                abstract="Abstract",
                text="Text",
                source={},
            ),
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            snapshot_path=output,
            require_bubblewrap=False,
        )
        self.assertEqual(result["snapshot_id"], read_json(output)["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
