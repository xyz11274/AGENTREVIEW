import json
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.io import atomic_write_json, read_json, write_derived_jsonl, value_sha256
from agentreview.research_v2.memory import MemoryProgramContractError, build_memory_snapshot, retrieve_memory, seed_program_path
from agentreview.research_v2.failure_policy import failure_detail
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review import run_specialist_review
from agentreview.research_v2.review_preparation import prepare_review_invocation


class MemoryCompletion:
    def __init__(self, *, generated_role="QUAL"):
        self.calls = []
        self.generated_role = generated_role

    def complete(self, messages, *, max_tokens, component, response_format=None):
        text = "\n".join(message["content"] for message in messages)
        self.calls.append({"component": component, "messages": messages, "max_tokens": max_tokens})
        if "The KnowledgeItem must conform" in text:
            return json.dumps({
                "trigger": "Research evaluation", "review_heuristic": "Check ablation evidence",
                "evidence_to_check": "Evaluate fixed baselines", "failure_mode": "Confounded comparison",
                "why_it_matters": "Results require comparable conditions", "reviewer_dimension": self.generated_role,
            })
        if "The Query must conform" in text:
            return json.dumps({"query_text": "QUERY_FROM_MEMORY_INSTRUCTION", "reviewer_dimension": self.generated_role})
        if "Frozen hit set:" in text:
            return "SUMMARY_OF_ROLE_LOCAL_EXPERIENCE"
        if component.startswith("specialist_review:"):
            return "Review generated with its frozen task policy."
        raise AssertionError(f"Unexpected completion {component}")


class SharedMemoryContractTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paper = Paper("current", "Current Study", "Current abstract", "Complete current paper.")
        self.paper_path = self.root / "current.json"
        atomic_write_json(self.paper_path, self.paper.to_dict())
        self.s2 = self.root / "s2.jsonl"
        write_derived_jsonl(self.s2, [{"paper_id": "current", "pool_version": "fixture", "retrieved_papers": [{"paperId": "related", "title": "Related Study", "abstract": "Frozen related evidence"}]}])
        self.embedding = {"provider": "deterministic-fallback-v1"}
        self.runtime = {"query_mode": "instruction", "read_llm_calls": 1, "build_llm_calls": 1, "max_tokens": 900}

    def tearDown(self):
        self.temporary.cleanup()

    def review_source(self, role):
        path = self.root / "sources" / f"{role}.json"
        atomic_write_json(path, {
            "review_id": f"source-{role}", "role": role, "paper_id": "build-paper",
            "review_text": f"TRACE_UNIQUE_{role}: examine appropriate evidence.",
        })
        return path

    def snapshot(self, seed, role, *, client=None, program=None, review_paths=None, name=None):
        output = self.root / "snapshots" / f"{name or seed}-{role}.json"
        build_memory_snapshot(
            role=role, program_path=program or seed_program_path(seed), program_version="shared-fixture",
            review_paths=[self.review_source(role)] if review_paths is None else review_paths,
            evaluation_paths=[], output_path=output, seed_kind=seed,
            embedding_identity=self.embedding, completion_client=client,
            completion_identity={"model": "fixture"} if client else None,
            require_bubblewrap=False, memory_runtime=self.runtime,
        )
        return output

    def prepare(self, seed, snapshot, client, *, enabled=True, role="QUAL", program=None):
        return prepare_review_invocation(
            paper=self.paper, role=role, program_path=program or seed_program_path(seed),
            snapshot_path=snapshot, completion_client=client, frozen_s2_path=self.s2,
            embedding_identity=self.embedding, require_bubblewrap=False,
            memory_enabled=enabled, memory_runtime=self.runtime,
        )

    def test_three_seeds_keep_task_policy_and_s2_when_memory_is_disabled(self):
        for seed in ("vector_search", "llm_summarizer", "experience_learner"):
            with self.subTest(seed=seed):
                client = MemoryCompletion()
                snapshot = self.snapshot(seed, "QUAL", client=client)
                client.calls.clear()
                enabled = self.prepare(seed, snapshot, client)
                enabled_calls = list(client.calls)
                client.calls.clear()
                disabled = self.prepare(seed, snapshot, client, enabled=False)
                self.assertEqual(enabled.review_plan, disabled.review_plan)
                self.assertEqual(enabled.retrieval_provenance["s2"], disabled.retrieval_provenance["s2"])
                self.assertEqual(client.calls, [])
                self.assertEqual(disabled.retrieval_provenance["memory"]["context"], "")
                self.assertEqual(disabled.retrieval_provenance["memory"]["resources"]["llm_call_count"], 0)
                prompt = disabled.messages[-1]["content"]
                self.assertNotIn("[MEMORY RESPONSE INSTRUCTION]", prompt)
                self.assertNotIn("[MEMORY ALWAYS-ON KNOWLEDGE]", prompt)
                self.assertNotIn("TRACE_UNIQUE_QUAL", prompt)
                self.assertEqual(sum("The Query must conform" in call["messages"][-1]["content"] for call in enabled_calls), 1)
                self.assertEqual(len(enabled_calls), 2 if seed == "llm_summarizer" else 1)

    def test_no_memory_specialist_performs_only_its_review_completion(self):
        client = MemoryCompletion()
        snapshot = self.snapshot("experience_learner", "QUAL", client=client, review_paths=[])
        self.assertEqual(client.calls, [])
        result = run_specialist_review(
            paper_path=self.paper_path, role="QUAL", program_path=seed_program_path("experience_learner"),
            snapshot_path=snapshot, run_id="no-memory", output_root=self.root, completion_client=client,
            model_identity={"model": "fixture"}, program_version="shared-fixture", frozen_s2_path=self.s2,
            embedding_identity=self.embedding, require_bubblewrap=False, memory_enabled=False, memory_runtime=self.runtime,
        )
        self.assertEqual([call["component"] for call in client.calls], ["specialist_review:QUAL"])
        self.assertFalse(result["retrieval_provenance"]["memory"]["memory_enabled"])

    def test_instruction_query_changes_retrieval_and_canonical_planning_stays_fixed(self):
        source = seed_program_path("vector_search").read_text(encoding="utf-8")
        source = source.replace('"decision_label": "seed_always_consult_top5",', '"decision_label": query.query_text + ":" + query.reviewer_dimension,')
        source = source.replace('"Use the deterministic title and abstract query supplied by the host."', '"Construct a task-aware memory retrieval query."')
        program = self.root / "planning-fixture.py"
        program.write_text(source, encoding="utf-8")
        client = MemoryCompletion(generated_role="CLAR")
        snapshot = self.snapshot("vector_search", "QUAL", client=client, program=program)
        prepared = self.prepare("vector_search", snapshot, client, program=program)
        memory = prepared.retrieval_provenance["memory"]
        self.assertEqual(memory["query"], {"query_text": "QUERY_FROM_MEMORY_INSTRUCTION", "reviewer_dimension": "QUAL"})
        self.assertEqual(prepared.review_plan["decision_label"], "Current Study\nCurrent abstract:QUAL")
        self.assertIn("Construct a task-aware memory retrieval query.", client.calls[0]["messages"][-1]["content"])
        self.assertIn("TRACE_UNIQUE_QUAL", memory["context"])

    def test_response_instruction_and_always_on_knowledge_are_memory_context(self):
        client = MemoryCompletion()
        snapshot = self.snapshot("llm_summarizer", "QUAL", client=client)
        prepared = self.prepare("llm_summarizer", snapshot, client)
        memory = prepared.retrieval_provenance["memory"]
        prompt = prepared.messages[-1]["content"]
        self.assertIn("[MEMORY RESPONSE INSTRUCTION]\n" + memory["instruction_response"], prompt)
        self.assertIn("[MEMORY ALWAYS-ON KNOWLEDGE]\n" + memory["always_on_knowledge"], prompt)
        self.assertIn("SUMMARY_OF_ROLE_LOCAL_EXPERIENCE", prompt)

    def test_five_snapshots_read_only_their_own_source_reviews(self):
        runtime = {**self.runtime, "query_mode": "deterministic"}
        for role in ROLES:
            with self.subTest(role=role):
                snapshot = self.snapshot("vector_search", role)
                client = MemoryCompletion()
                memory = retrieve_memory(paper=self.paper, role=role, program_path=seed_program_path("vector_search"), snapshot_path=snapshot,
                    embedding_identity=self.embedding, completion_client=client, require_bubblewrap=False, memory_runtime=runtime)
                self.assertIn(f"TRACE_UNIQUE_{role}", memory["context"])
                for other in set(ROLES) - {role}:
                    self.assertNotIn(f"TRACE_UNIQUE_{other}", memory["context"])
                self.assertEqual(read_json(snapshot)["source_review_ids"], [f"source-{role}"])
                self.assertEqual(client.calls, [])

    def test_foreign_source_review_or_snapshot_is_rejected_before_model_calls(self):
        client = MemoryCompletion()
        with self.assertRaisesRegex(ValueError, "cross-role Review"):
            self.snapshot("experience_learner", "QUAL", client=client, review_paths=[self.review_source("CLAR")])
        snapshot = self.snapshot("vector_search", "CLAR")
        with self.assertRaisesRegex(ValueError, "snapshot role mismatch"):
            self.prepare("vector_search", snapshot, client, role="QUAL")
        self.assertEqual(client.calls, [])

    def test_each_paper_reads_a_fresh_copy_even_when_read_mutates_memory(self):
        code = seed_program_path("vector_search").read_text()
        code = code.replace('    def read(self, query: Query) -> str:\n',
            '    def read(self, query: Query) -> str:\n'
            '        previous = "READ_MUTATION" in self.seen\n'
            '        self.seen.add("READ_MUTATION")\n'
            '        return "dirty" if previous else "fresh"\n')
        program = self.root / "mutating_read.py"
        program.write_text(code)
        snapshot = self.snapshot("vector_search", "QUAL", program=program)
        before = snapshot.read_bytes()
        for _ in range(2):
            result = retrieve_memory(paper=self.paper, role="QUAL", program_path=program, snapshot_path=snapshot,
                embedding_identity=self.embedding, completion_client=MemoryCompletion(), require_bubblewrap=False,
                memory_runtime={**self.runtime, "query_mode": "deterministic"})
            self.assertEqual(result["context"], "fresh")
            self.assertEqual(snapshot.read_bytes(), before)

    def test_generated_knowledge_item_uses_the_host_role_identity(self):
        client = MemoryCompletion(generated_role="CLAR")
        snapshot = self.snapshot("experience_learner", "QUAL", client=client)
        self.assertEqual(read_json(snapshot)["items"][0]["payload"]["reviewer_dimension"], "QUAL")

    def test_checkpoint_replay_validates_item_role_before_reusing_it(self):
        client = MemoryCompletion()
        snapshot = self.snapshot("experience_learner", "QUAL", client=client)
        checkpoint = next(snapshot.parent.glob(f"{snapshot.name}.items/*.json"))
        value = read_json(checkpoint)
        value["item"]["payload"]["reviewer_dimension"] = "CLAR"
        value["checkpoint_id"] = value_sha256({key: item for key, item in value.items() if key != "checkpoint_id"})
        atomic_write_json(checkpoint, value, immutable=False)
        snapshot.unlink()
        client.calls.clear()
        with self.assertRaisesRegex(ValueError, "owning host role QUAL"):
            self.snapshot("experience_learner", "QUAL", client=client)
        self.assertEqual(client.calls, [])

    def test_existing_snapshot_replay_validates_item_role(self):
        client = MemoryCompletion()
        snapshot = self.snapshot("experience_learner", "QUAL", client=client)
        value = read_json(snapshot)
        value["items"][0]["payload"]["reviewer_dimension"] = "CLAR"
        value["snapshot_id"] = value_sha256({key: item for key, item in value.items() if key != "snapshot_id"})
        atomic_write_json(snapshot, value, immutable=False)
        client.calls.clear()
        with self.assertRaisesRegex(ValueError, "owning host role QUAL"):
            self.snapshot("experience_learner", "QUAL", client=client)
        self.assertEqual(client.calls, [])

    def test_returned_query_json_schema_errors_consume_candidate_failure_scope(self):
        snapshot = self.snapshot("vector_search", "QUAL")
        for response in ("unstructured response", json.dumps({"query_text": 3, "reviewer_dimension": "QUAL"}), json.dumps({"unexpected": "field"})):
            with self.subTest(response=response):
                class Client:
                    def complete(self, *_args, **_kwargs):
                        return response
                with self.assertRaises(MemoryProgramContractError) as caught:
                    self.prepare("vector_search", snapshot, Client())
                error = caught.exception
                self.assertEqual(error.stage, "memory_query")
                self.assertEqual(error.failure_record["schema"], "Query")
                self.assertIn("raw_response_prefix", error.failure_record["parser"])
                self.assertEqual(failure_detail(error, phase="assess")["category"], "candidate")

    def test_returned_knowledge_item_schema_error_is_candidate_local(self):
        class Client:
            def complete(self, *_args, **_kwargs):
                return json.dumps({"unrecognized_item_field": "value"})
        with self.assertRaises(MemoryProgramContractError) as caught:
            self.snapshot("experience_learner", "QUAL", client=Client())
        self.assertEqual(caught.exception.stage, "knowledge_item_generation")
        self.assertEqual(caught.exception.failure_record["schema"], "KnowledgeItem")
        self.assertEqual(failure_detail(caught.exception, phase="assess")["category"], "candidate")

    def test_transport_and_filesystem_errors_keep_shared_failure_scope(self):
        snapshot = self.snapshot("vector_search", "QUAL")
        for stage in ("knowledge_item_generation", "memory_query"):
            for original in (ConnectionError("service unavailable"), PermissionError("storage unavailable"), ValueError("invalid endpoint configuration")):
                with self.subTest(stage=stage, error=type(original).__name__):
                    class Client:
                        def complete(self, *_args, **_kwargs):
                            raise original
                    try:
                        if stage == "memory_query":
                            self.prepare("vector_search", snapshot, Client())
                        else:
                            self.snapshot("experience_learner", "QUAL", client=Client(), name=f"failure-{type(original).__name__}")
                    except Exception as error:
                        self.assertIs(error, original)
                        self.assertIn(failure_detail(error, phase="assess")["category"], {"shared_data", "shared_runtime"})
                    else:
                        self.fail("Shared request failure must propagate")


if __name__ == "__main__":
    unittest.main()
