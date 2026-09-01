import fcntl
import json
import sys
import tempfile
import unittest
from pathlib import Path

from agentreview.research_v2.evaluation import run_internal_evaluation
from agentreview.research_v2.coco_protocol import proportional_quotas
from agentreview.research_v2.config import ResearchConfig
from agentreview.research_v2.coco import run_coco_evaluation
from agentreview.research_v2.data import prepare_data
from agentreview.research_v2.evolution import (
    EvolutionPool,
    PoolEntry,
    propose_candidate,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json
from agentreview.research_v2.memory import (
    build_memory_snapshot,
    retrieve_memory,
    seed_program_path,
)
from agentreview.research_v2.meta import generate_final_review
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review import run_specialist_review
from agentreview.research_v2.s2 import (
    FrozenS2Store,
    SemanticScholarSearchClient,
    build_frozen_s2_pool,
)


class FakeCompletion:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def complete(self, messages, *, max_tokens, component):
        self.calls.append(
            {"messages": messages, "max_tokens": max_tokens, "component": component}
        )
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        if component.startswith("specialist_review"):
            return "complete Specialist Review"
        if component == "memory_summarizer":
            return "fixed-hit summary"
        if component.startswith("internal_evaluator"):
            return json.dumps(
                {
                    "review_quality": 4,
                    "major_missing_points": ["missing ablation"],
                    "factual_evidence_issues": [],
                    "improvement_suggestions": ["request an ablation"],
                }
            )
        if component == "meta_synthesis":
            return (
                "Synthesis:\n- QUAL: sound\n- CLAR: clear\n- SIGN: useful\n"
                "- ORIG: novel\n- POL: no issue\n\nEvidence anchors used:\n"
                "- SRC-001(page=1): evidence\n\nFinal recommendation: Accept\n"
                "Actionable suggestions:\n- add ablation"
            )
        raise AssertionError(component)


class ResearchV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paper_path = self.root / "paper.json"
        atomic_write_json(
            self.paper_path,
            Paper(
                paper_id="p1",
                title="Test Paper",
                abstract="An abstract about robust evaluation.",
                text="# Test Paper\n\nMethod and experiments.",
                source={"conference": "ICLR 2020"},
            ).to_dict(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _review_artifact(self, role="QUAL", review_id="r1", text="review"):
        return {
            "review_id": review_id,
            "paper_id": "p1",
            "role": role,
            "program_version": "secret-program-version",
            "review_text": text,
            "retrieval_provenance": {
                "memory": {
                    "context": "review experience",
                    "query": {"query_text": "Test Paper"},
                    "logs": [],
                    "seed_kind": "vector_search",
                },
                "s2": {"result_ids": [], "evidence": []},
            },
        }

    def _review_path(self, role="QUAL", review_id="r1", text="review"):
        path = self.root / f"{role}-{review_id}.json"
        atomic_write_json(path, self._review_artifact(role, review_id, text))
        return path

    def _evaluation_path(self, role="QUAL", review_id="r1", score=2.0):
        path = self.root / f"eval-{role}-{review_id}.json"
        atomic_write_json(
            path,
            {
                "evaluation_id": f"e-{review_id}",
                "review_id": review_id,
                "paper_id": "p1",
                "role": role,
                "review_quality": score,
                "major_missing_points": ["missing"],
                "factual_evidence_issues": ["unsupported"],
                "improvement_suggestions": ["improve"],
            },
        )
        return path

    def _empty_s2_pool(self) -> Path:
        path = self.root / "empty-s2.jsonl"
        if not path.is_file():
            path.write_text(
                json.dumps(
                    {
                        "paper_id": "p1",
                        "pool_version": "test-empty-s2-v1",
                        "retrieved_papers": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return path

    def test_immutable_json_rejects_changed_content(self):
        path = self.root / "artifact.json"
        atomic_write_json(path, {"value": 1})
        atomic_write_json(path, {"value": 1})
        with self.assertRaises(FileExistsError):
            atomic_write_json(path, {"value": 2})

    def test_formal_model_and_embedding_identities_are_pinned(self):
        config = ResearchConfig()
        self.assertEqual(config.model.temperature, 0.0)
        self.assertFalse(config.model.enable_thinking)
        self.assertEqual(len(config.model.revision), 40)
        self.assertEqual(config.embedding_provider, "qwen3_embedding")
        self.assertEqual(len(config.embedding_revision), 40)

    def test_coco_largest_remainder_uses_actual_bucket_proportions(self):
        self.assertEqual(
            proportional_quotas({"0-3": 14, "3-5": 98, "5-7": 148, "7-10": 40}, 100),
            {"0-3": 5, "3-5": 33, "5-7": 49, "7-10": 13},
        )

    def test_coco_process_requires_exact_manifest_bound_final_review_ids(self):
        manifest_path = self.root / "coco-manifest.json"
        atomic_write_json(
            manifest_path,
            {
                "protocol": "coco_1300_protocol_compatible_resample_v1",
                "count": 1,
                "manifest_sha256": "test-manifest",
                "records": [{"paper_id": "p1"}],
            },
        )
        system_path = self.root / "system.json"
        atomic_write_json(
            system_path,
            {
                "system_id": "system-1",
                "external_evaluation": {
                    "coco_1300_manifest": {
                        "path": str(manifest_path),
                        "sha256": file_sha256(manifest_path),
                    }
                },
            },
        )
        final_path = self.root / "final.json"
        atomic_write_json(final_path, {"paper_id": "p1", "review_text": "final"})
        fake_coco = self.root / "fake-coco-success"
        fake_coco.write_text(
            f"""#!{sys.executable}
import json
import hashlib
import os
from pathlib import Path

output = Path(os.environ["COCO_OUTPUT_ROOT"])
evaluation_enabled = output.name == "missing-evaluation-output"
split_path = output / "candidate_official_split.jsonl"
classify_path = output / "candidate_official_classify.jsonl"
split_path.write_text(
    json.dumps({{"id": "p1", "sentence_texts": ["final"], "opinions": [[0]]}}) + "\\n",
    encoding="utf-8",
)
classify_path.write_text(
    json.dumps({{
        "id": "p1",
        "sentence_texts": ["final"],
        "opinions": [[0]],
        "category": [["QUAL-MET"]],
    }}) + "\\n",
    encoding="utf-8",
)

def binding(path):
    return {{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}

candidate_input = Path(os.environ["COCO_CANDIDATE_INPUT"])
(output / "candidate_official_runner.json").write_text(
    json.dumps({{
        "protocol": "coco-official-candidate-runner-v1",
        "status": "complete",
        "evaluation_enabled": evaluation_enabled,
        "candidate_input": binding(candidate_input),
        "artifacts": {{
            "split": binding(split_path),
            "classify": binding(classify_path),
        }},
    }}) + "\\n",
    encoding="utf-8",
)
""",
            encoding="utf-8",
        )
        fake_coco.chmod(0o700)
        result = run_coco_evaluation(
            final_review_paths=[final_path],
            frozen_system_path=system_path,
            coco_root=self.root,
            output_root=self.root / "coco-output",
            command=[str(fake_coco)],
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(result["artifacts"]),
            {"runner", "split", "classify"},
        )
        self.assertTrue(
            all(
                len(value["sha256"]) == 64
                for value in result["artifacts"].values()
            )
        )
        with self.assertRaisesRegex(RuntimeError, "enabled evaluation"):
            run_coco_evaluation(
                final_review_paths=[final_path],
                frozen_system_path=system_path,
                coco_root=self.root,
                output_root=self.root / "missing-evaluation-output",
                command=[str(fake_coco)],
            )
        wrong = self.root / "wrong-final.json"
        atomic_write_json(wrong, {"paper_id": "wrong", "review_text": "final"})
        with self.assertRaisesRegex(ValueError, "exact frozen paper set"):
            run_coco_evaluation(
                final_review_paths=[wrong],
                frozen_system_path=system_path,
                coco_root=self.root,
                output_root=self.root / "wrong-output",
                command=["/bin/true"],
            )

    def test_coco_process_rejects_success_without_contract_artifacts(self):
        manifest_path = self.root / "missing-artifacts-coco-manifest.json"
        atomic_write_json(
            manifest_path,
            {
                "protocol": "coco_1300_protocol_compatible_resample_v1",
                "count": 1,
                "manifest_sha256": "test-manifest",
                "records": [{"paper_id": "p1"}],
            },
        )
        system_path = self.root / "missing-artifacts-system.json"
        atomic_write_json(
            system_path,
            {
                "system_id": "system-1",
                "external_evaluation": {
                    "coco_1300_manifest": {
                        "path": str(manifest_path),
                        "sha256": file_sha256(manifest_path),
                    }
                },
            },
        )
        final_path = self.root / "missing-artifacts-final.json"
        atomic_write_json(final_path, {"paper_id": "p1", "review_text": "final"})

        with self.assertRaisesRegex(RuntimeError, "contract artifact"):
            run_coco_evaluation(
                final_review_paths=[final_path],
                frozen_system_path=system_path,
                coco_root=self.root,
                output_root=self.root / "missing-artifacts-output",
                command=["/bin/true"],
            )
        failed_report = read_json(self.root / "missing-artifacts-output" / "run.json")
        self.assertEqual(failed_report["status"], "failed")
        self.assertIn("contract artifact", failed_report["artifact_validation_error"])

    def test_vector_memory_is_role_local_deterministic_and_non_generative(self):
        review = self._review_path(text="Check ablations and statistical variance.")
        snapshot = self.root / "snapshot-vector.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            program_version="vector-v1",
            review_paths=[review],
            evaluation_paths=[],
            output_path=snapshot,
            seed_kind="vector_search",
            require_bubblewrap=False,
        )
        first = retrieve_memory(
            paper=Paper.from_dict(read_json(self.paper_path)),
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            snapshot_path=snapshot,
            require_bubblewrap=False,
        )
        second = retrieve_memory(
            paper=Paper.from_dict(read_json(self.paper_path)),
            role="QUAL",
            program_path=seed_program_path("vector_search"),
            snapshot_path=snapshot,
            require_bubblewrap=False,
        )
        self.assertEqual(first["query"], second["query"])
        self.assertEqual(first["logs"], second["logs"])
        self.assertIn("REVIEW EXPERIENCE", first["context"])
        self.assertNotIn("# Test Paper", first["context"])

    def test_summarizer_selects_hits_before_one_explicit_call_and_has_no_fallback(self):
        review = self._review_path(text="Check baselines and uncertainty.")
        snapshot = self.root / "snapshot-summary.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=seed_program_path("llm_summarizer"),
            program_version="summary-v1",
            review_paths=[review],
            evaluation_paths=[],
            output_path=snapshot,
            seed_kind="llm_summarizer",
            require_bubblewrap=False,
        )
        client = FakeCompletion(["fixed-hit summary"])
        result = retrieve_memory(
            paper=Paper.from_dict(read_json(self.paper_path)),
            role="QUAL",
            program_path=seed_program_path("llm_summarizer"),
            snapshot_path=snapshot,
            completion_client=client,
            require_bubblewrap=False,
        )
        self.assertEqual(result["context"], "fixed-hit summary")
        self.assertEqual(len(client.calls), 1)
        events = [value["event"] for value in result["logs"]]
        self.assertEqual(events[:2], ["retrieval_hits", "summarizer_output"])
        failing = FakeCompletion(error=RuntimeError("summary failed"))
        with self.assertRaisesRegex(Exception, "summary failed"):
            retrieve_memory(
                paper=Paper.from_dict(read_json(self.paper_path)),
                role="QUAL",
                program_path=seed_program_path("llm_summarizer"),
                snapshot_path=snapshot,
                completion_client=failing,
                require_bubblewrap=False,
            )

    def test_review_saves_raw_and_full_without_online_evaluation_or_writeback(self):
        snapshot = self.root / "snapshot-empty.json"
        program = seed_program_path("vector_search")
        build_memory_snapshot(
            role="QUAL",
            program_path=program,
            program_version="vector-v1",
            review_paths=[],
            evaluation_paths=[],
            output_path=snapshot,
            seed_kind="vector_search",
            require_bubblewrap=False,
        )
        before = file_sha256(snapshot)
        client = FakeCompletion()
        review = run_specialist_review(
            paper_path=self.paper_path,
            role="QUAL",
            program_path=program,
            snapshot_path=snapshot,
            run_id="review-run",
            output_root=self.root,
            completion_client=client,
            model_identity={"model": "mock"},
            program_version="vector-v1",
            frozen_s2_path=self._empty_s2_pool(),
            require_bubblewrap=False,
        )
        review_dir = self.root / "reviews" / "review-run" / "p1"
        self.assertTrue((review_dir / "QUAL.raw.json").is_file())
        self.assertTrue((review_dir / "QUAL.json").is_file())
        self.assertEqual(review["review_text"], "complete Specialist Review")
        self.assertEqual(
            review["retrieval_provenance"]["s2"]["s2_disposition"],
            "consulted_no_use",
        )
        self.assertEqual(file_sha256(snapshot), before)
        self.assertFalse((self.root / "evaluations").exists())
        self.assertEqual(len(client.calls), 1)
        with self.assertRaisesRegex(ValueError, "version differs"):
            run_specialist_review(
                paper_path=self.paper_path,
                role="QUAL",
                program_path=program,
                snapshot_path=snapshot,
                run_id="wrong-version",
                output_root=self.root,
                completion_client=client,
                model_identity={"model": "mock"},
                program_version="different-version",
                frozen_s2_path=self._empty_s2_pool(),
                require_bubblewrap=False,
            )

    def test_memory_build_rejects_cross_role_internal_evaluation(self):
        with self.assertRaisesRegex(ValueError, "cross-role Internal Evaluation"):
            build_memory_snapshot(
                role="QUAL",
                program_path=seed_program_path("experience_learner"),
                program_version="experience-v1",
                review_paths=[self._review_path(role="QUAL", review_id="r-cross")],
                evaluation_paths=[
                    self._evaluation_path(role="CLAR", review_id="r-cross")
                ],
                output_path=self.root / "cross-role-snapshot.json",
                seed_kind="experience_learner",
                completion_client=FakeCompletion(),
                require_bubblewrap=False,
            )

    def test_internal_evaluator_is_blind_to_program_and_coco(self):
        review_path = self._review_path()
        client = FakeCompletion()
        result = run_internal_evaluation(
            paper_path=self.paper_path,
            review_path=review_path,
            run_id="eval-run",
            output_root=self.root,
            completion_client=client,
            evaluator_identity={"model": "same-base"},
        )
        prompt = "\n".join(
            message["content"] for message in client.calls[0]["messages"]
        )
        self.assertNotIn("secret-program-version", prompt)
        self.assertNotIn("CoCo", prompt)
        self.assertEqual(result["review_quality"], 4.0)
        self.assertEqual(set(result), {
            "schema_version", "evaluation_id", "review_id", "paper_id", "role",
            "review_quality", "major_missing_points", "factual_evidence_issues",
            "improvement_suggestions", "raw_model_output", "evaluator_identity",
            "prompt_version", "run_id", "created_at"
        })

    def test_meta_has_no_score_and_never_falls_back_to_concat(self):
        paths = [
            self._review_path(role, f"r-{role}", f"{role} review")
            for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
        ]
        valid = generate_final_review(
            paper_path=self.paper_path,
            review_paths=paths,
            mode="meta",
            run_id="meta-run",
            output_root=self.root,
            completion_client=FakeCompletion(),
            model_identity={"model": "mock"},
        )
        self.assertNotIn("Final score", valid["review_text"])
        self.assertNotIn("Confidence:", valid["review_text"])
        invalid = FakeCompletion(
            [
                "Final score: 9\nFinal recommendation: Accept",
                "Confidence: 5\nFinal recommendation: Accept",
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "concat fallback is forbidden"):
            generate_final_review(
                paper_path=self.paper_path,
                review_paths=paths,
                mode="meta",
                run_id="meta-invalid",
                output_root=self.root,
                completion_client=invalid,
                model_identity={"model": "mock"},
            )
        self.assertFalse(
            (self.root / "final_reviews" / "meta-invalid" / "p1.json").exists()
        )

    def test_s2_self_match_is_rejected_at_runtime(self):
        pool = self.root / "s2.jsonl"
        pool.write_text(
            json.dumps(
                {
                    "paper_id": "p1",
                    "pool_version": "test",
                    "retrieved_papers": [
                        {"paperId": "s2-p1", "title": "Test Paper"}
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "self match"):
            FrozenS2Store(pool).retrieve(
                paper_id="p1", target_title="Test Paper", limit=5
            )

    def test_s2_builder_freezes_post_filter_order_without_generation(self):
        class Search:
            def search_title(self, title, *, limit):
                self.request = (title, limit)
                return [
                    {"paperId": "self", "title": "Test Paper"},
                    {"paperId": "a", "title": "Related A"},
                    {"paperId": "a", "title": "Related A duplicate id"},
                    {"paperId": "b", "title": "Related B"},
                ]

        path = self.root / "frozen-s2.jsonl"
        search = Search()
        manifest = build_frozen_s2_pool(
            targets=[{"paper_id": "p1", "canonical_query_title": "Test Paper"}],
            output_path=path,
            client=search,
            pool_version="test-pool",
            raw_limit=50,
            top_k=5,
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(search.request, ("Test Paper", 50))
        self.assertEqual(
            [value["paperId"] for value in record["retrieved_papers"]],
            ["a", "b"],
        )
        self.assertEqual(manifest["rejection_counts"]["exact_canonical_title"], 1)
        self.assertEqual(manifest["rejection_counts"]["duplicate_s2_id"], 1)
        self.assertEqual(len(record["raw_response_sha256"]), 64)

    def test_s2_builder_resumes_from_per_target_checkpoint(self):
        targets = [
            {"paper_id": "p1", "canonical_query_title": "First Paper"},
            {"paper_id": "p2", "canonical_query_title": "Second Paper"},
        ]
        path = self.root / "resumable-s2.jsonl"

        class InterruptedSearch:
            def __init__(self):
                self.calls = []

            def search_title(self, title, *, limit):
                self.calls.append((title, limit))
                if title == "Second Paper":
                    raise RuntimeError("transient interruption")
                return [{"paperId": "related-1", "title": "Related First"}]

        interrupted = InterruptedSearch()
        with self.assertRaisesRegex(RuntimeError, "transient interruption"):
            build_frozen_s2_pool(
                targets=targets,
                output_path=path,
                client=interrupted,
                pool_version="resumable-pool",
            )
        self.assertFalse(path.exists())
        self.assertEqual(
            len(list(path.with_name(f"{path.name}.parts").glob("*.json"))),
            1,
        )

        class ResumedSearch:
            def __init__(self):
                self.calls = []

            def search_title(self, title, *, limit):
                self.calls.append((title, limit))
                return [{"paperId": "related-2", "title": "Related Second"}]

        resumed = ResumedSearch()
        manifest = build_frozen_s2_pool(
            targets=targets,
            output_path=path,
            client=resumed,
            pool_version="resumable-pool",
        )
        self.assertEqual(resumed.calls, [("Second Paper", 50)])
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(
            [json.loads(line)["paper_id"] for line in path.read_text().splitlines()],
            ["p1", "p2"],
        )

        class CompletedSearch:
            def search_title(self, title, *, limit):
                raise AssertionError("completed pool must not query the network")

        replay = build_frozen_s2_pool(
            targets=targets,
            output_path=path,
            client=CompletedSearch(),
            pool_version="resumable-pool",
        )
        self.assertEqual(replay["sha256"], manifest["sha256"])

    def test_s2_builder_rejects_a_second_process_for_the_artifact_root(self):
        path = self.root / "s2" / "locked.jsonl"
        path.parent.mkdir(parents=True)
        lock_path = path.parent / ".s2-build.lock"

        class Search:
            def search_title(self, title, *, limit):
                raise AssertionError("lock rejection must happen before HTTP")

        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                build_frozen_s2_pool(
                    targets=[
                        {"paper_id": "p1", "canonical_query_title": "Test Paper"}
                    ],
                    output_path=path,
                    client=Search(),
                    pool_version="locked-pool",
                )

    def test_s2_client_retries_transient_status_only(self):
        class FakeRequestException(Exception):
            pass

        class FakeResponse:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self.ok = status_code == 200
                self.headers = {}
                self.payload = payload or {"data": []}

            def raise_for_status(self):
                raise FakeRequestException(f"HTTP {self.status_code}")

            def json(self):
                return self.payload

        class FakeRequests:
            RequestException = FakeRequestException

            def __init__(self, responses):
                self.responses = list(responses)
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return self.responses.pop(0)

        client = SemanticScholarSearchClient(
            max_attempts=3,
            retry_base_seconds=0,
            min_request_interval_seconds=0,
        )
        unauthorized = FakeRequests([FakeResponse(401)])
        client.requests = unauthorized
        with self.assertRaisesRegex(FakeRequestException, "HTTP 401"):
            client.search_title("Paper", limit=5)
        self.assertEqual(unauthorized.calls, 1)

        transient = FakeRequests(
            [
                FakeResponse(429),
                FakeResponse(200, {"data": [{"paperId": "related"}]}),
            ]
        )
        client.requests = transient
        self.assertEqual(
            client.search_title("Paper", limit=5),
            [{"paperId": "related"}],
        )
        self.assertEqual(transient.calls, 2)

        retry_until_success = FakeRequests(
            [FakeResponse(429) for _ in range(8)]
            + [FakeResponse(200, {"data": [{"paperId": "eventual"}]})]
        )
        persistent_client = SemanticScholarSearchClient(
            max_attempts=None,
            retry_base_seconds=0,
            min_request_interval_seconds=0,
        )
        persistent_client.requests = retry_until_success
        self.assertEqual(
            persistent_client.search_title("Eventually Available", limit=5),
            [{"paperId": "eventual"}],
        )
        self.assertEqual(retry_until_success.calls, 9)

        empty = FakeRequests([FakeResponse(200, {"total": 0, "offset": 0})])
        client.requests = empty
        self.assertEqual(client.search_title("No Matching Paper", limit=5), [])
        self.assertEqual(empty.calls, 1)

        malformed = FakeRequests([FakeResponse(200, {"total": 1, "offset": 0})])
        client.requests = malformed
        with self.assertRaisesRegex(ValueError, "lacks data list"):
            client.search_title("Malformed Result", limit=5)

    def test_prepare_data_makes_three_disjoint_deterministic_splits(self):
        data_root = self.root / "dataset"
        asset_root = data_root / "assets"
        coco_root = data_root / "coco"
        (coco_root / "benchmark").mkdir(parents=True)
        (coco_root / "benchmark" / "benchmark_ICLR_2017.jsonl").write_text(
            json.dumps(
                {"id": "held-out", "content": {"title": "Held Out Title"}}
            )
            + "\n",
            encoding="utf-8",
        )
        records = []
        for index in range(7):
            paper_id = f"paper-{index}"
            relative = Path("train") / "MD" / paper_id
            folder = asset_root / relative
            folder.mkdir(parents=True)
            (folder / "md_content_no_appendix.md").write_text(
                f"# Paper {index}\n\nunique method {index}", encoding="utf-8"
            )
            records.append(
                {
                    "id": paper_id,
                    "MD_path": str(relative),
                    "conference": "ICLR 2020" if index % 2 == 0 else "ICLR 2021",
                    "content": {
                        "title": f"Paper {index}",
                        "abstract": f"Abstract {index}",
                        "paperhash": f"hash-{index}",
                    },
                }
            )
        held_folder = asset_root / "train" / "MD" / "held-copy"
        held_folder.mkdir(parents=True)
        (held_folder / "md_content_no_appendix.md").write_text(
            "# held", encoding="utf-8"
        )
        records.append(
            {
                "id": "held-copy",
                "MD_path": "train/MD/held-copy",
                "conference": "ICLR 2020",
                "content": {"title": "Held Out Title", "paperhash": "held-copy"},
            }
        )
        train = data_root / "train.jsonl"
        train.write_text(
            "".join(json.dumps(value) + "\n" for value in records),
            encoding="utf-8",
        )
        output = self.root / "prepared"
        manifest = prepare_data(
            train_clean_path=train,
            paper_asset_root=asset_root,
            coco_root=coco_root,
            output_root=output,
            split_sizes={
                "canonical_review_corpus": 1,
                "evolution_static": 1,
                "evolution_rotating_pool": 1,
            },
            seed=0,
        )
        ids = [
            paper_id
            for split in manifest["splits"].values()
            for paper_id in split["paper_ids"]
        ]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(manifest["rejection_counts"]["coco_canonical_title"], 1)
        self.assertEqual(len(list((output / "data" / "papers").glob("*.json"))), 3)

    def test_role_local_pool_and_single_repair_attempt(self):
        program = seed_program_path("vector_search")
        parent = PoolEntry(
            candidate_id="seed-vector",
            role="QUAL",
            program_path=str(program),
            program_sha256=file_sha256(program),
            parent_id=None,
            static_score=3.0,
            seed_kind="vector_search",
        )
        pool = EvolutionPool("QUAL", [parent])
        with self.assertRaisesRegex(ValueError, "cross-role"):
            EvolutionPool(
                "QUAL",
                [PoolEntry(**{**parent.__dict__, "role": "CLAR"})],
            )
        patch = """*** Commit Message
Title: Clarify grounding
- experience needs stronger grounding
- update always-on guidance
*** Begin Patch
*** Update File: program.py
@@
-ALWAYS_ON_KNOWLEDGE = "Ground every judgment in the Frozen Paper."
+ALWAYS_ON_KNOWLEDGE = "Ground every major judgment in the Frozen Paper."
*** End Patch"""
        client = FakeCompletion(["invalid patch", patch])
        outcome = propose_candidate(
            role="QUAL",
            pool=pool,
            evaluation_paths=[self._evaluation_path()],
            review_paths=[self._review_path()],
            attempt=1,
            run_id="evolution-run",
            output_root=self.root,
            completion_client=client,
            require_bubblewrap=False,
        )
        self.assertEqual(outcome["status"], "proposed")
        self.assertTrue(outcome["repair_used"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(pool.entries), 1)
        replay_client = FakeCompletion(error=AssertionError("must not call Reflector"))
        replayed = propose_candidate(
            role="QUAL",
            pool=pool,
            evaluation_paths=[self._evaluation_path()],
            review_paths=[self._review_path()],
            attempt=1,
            run_id="evolution-run",
            output_root=self.root,
            completion_client=replay_client,
            require_bubblewrap=False,
        )
        self.assertEqual(replayed, outcome)
        self.assertEqual(replay_client.calls, [])
        with self.assertRaisesRegex(ValueError, "cross-role Internal Evaluation"):
            propose_candidate(
                role="QUAL",
                pool=pool,
                evaluation_paths=[
                    self._evaluation_path(role="CLAR", review_id="cross-feedback")
                ],
                review_paths=[
                    self._review_path(role="QUAL", review_id="cross-feedback")
                ],
                attempt=2,
                run_id="cross-feedback-run",
                output_root=self.root,
                completion_client=FakeCompletion(),
                require_bubblewrap=False,
            )

    def test_tiny_five_role_review_then_separate_evaluation_and_finalization(self):
        roles = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
        program = seed_program_path("vector_search")
        frozen_s2_path = self._empty_s2_pool()
        review_paths = []
        review_client = FakeCompletion()
        for role in roles:
            snapshot = self.root / "tiny" / "snapshots" / f"{role}.json"
            build_memory_snapshot(
                role=role,
                program_path=program,
                program_version=f"{role.lower()}-vector-v1",
                review_paths=[],
                evaluation_paths=[],
                output_path=snapshot,
                seed_kind="vector_search",
                require_bubblewrap=False,
            )
            run_specialist_review(
                paper_path=self.paper_path,
                role=role,
                program_path=program,
                snapshot_path=snapshot,
                run_id="tiny-review",
                output_root=self.root,
                completion_client=review_client,
                model_identity={"model": "mock"},
                program_version=f"{role.lower()}-vector-v1",
                frozen_s2_path=frozen_s2_path,
                require_bubblewrap=False,
            )
            review_paths.append(self.root / "reviews" / "tiny-review" / "p1" / f"{role}.json")
        self.assertEqual(len(review_client.calls), 5)
        self.assertFalse((self.root / "evaluations" / "tiny-eval").exists())

        evaluation_client = FakeCompletion()
        for path in review_paths:
            run_internal_evaluation(
                paper_path=self.paper_path,
                review_path=path,
                run_id="tiny-eval",
                output_root=self.root,
                completion_client=evaluation_client,
                evaluator_identity={"model": "same-mock-base"},
            )
        self.assertEqual(len(evaluation_client.calls), 5)
        evaluation_dir = self.root / "evaluations" / "tiny-eval" / "p1"
        self.assertTrue(all((evaluation_dir / f"{role}.json").is_file() for role in roles))

        meta = generate_final_review(
            paper_path=self.paper_path,
            review_paths=review_paths,
            mode="meta",
            run_id="tiny-meta",
            output_root=self.root,
            completion_client=FakeCompletion(),
            model_identity={"model": "mock"},
        )
        concat = generate_final_review(
            paper_path=self.paper_path,
            review_paths=review_paths,
            mode="concat",
            run_id="tiny-concat",
            output_root=self.root,
            completion_client=None,
            model_identity=None,
        )
        self.assertEqual(meta["mode"], "meta")
        self.assertEqual(concat["mode"], "concat")
        self.assertEqual(meta["source_review_ids"], concat["source_review_ids"])
        self.assertNotIn("review_quality", meta)


if __name__ == "__main__":
    unittest.main()
