import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.memory import build_memory_snapshot, seed_program_path
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review import run_specialist_review
from agentreview.research_v2.review_preparation import ReviewPlanError


class FakeCompletion:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def complete(self, messages, *, max_tokens, component, response_format=None):
        self.calls.append(component)
        if self.error is not None:
            raise self.error
        return "prepared Specialist Review"


class ReviewPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paper_path = self.root / "paper.json"
        atomic_write_json(
            self.paper_path,
            Paper(
                paper_id="p1",
                title="Current Test Paper",
                abstract="A study of robust evaluation.",
                text="# Current Test Paper\n\nMethod and results.",
                source={"conference": "ICLR"},
            ).to_dict(),
        )
        self.s2_path = self.root / "s2.jsonl"
        self._write_s2_pool()
        self.intervention = {
            "protocol": "s2-intervention-v1",
            "mode": "program_autonomous",
            "label": "autonomous-test-v1",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _write_s2_pool(self, suffix=""):
        self.s2_path.write_text(
            json.dumps(
                {
                    "paper_id": "p1",
                    "pool_version": "test-s2-v1",
                    "retrieved_papers": [
                        {
                            "paperId": "s1",
                            "title": "Prior Evidence One",
                            "abstract": "First frozen abstract.",
                        },
                        {
                            "paperId": "s2",
                            "title": "Prior Evidence Two",
                            "abstract": "Second frozen abstract.",
                        },
                    ],
                }
            )
            + "\n"
            + suffix,
            encoding="utf-8",
        )

    def _program(self, name, transform=None):
        source = seed_program_path("vector_search").read_text(encoding="utf-8")
        if transform is not None:
            source = transform(source)
        path = self.root / name / "program.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def _snapshot(self, program, name):
        path = self.root / name / "snapshot.json"
        build_memory_snapshot(
            role="QUAL",
            program_path=program,
            program_version=name,
            review_paths=[],
            evaluation_paths=[],
            output_path=path,
            seed_kind="vector_search",
            embedding_identity={"provider": "deterministic-fallback-v1"},
            require_bubblewrap=False,
        )
        return path

    def _review(self, *, program, snapshot, run_id, client=None, **kwargs):
        embedding_identity = kwargs.pop(
            "embedding_identity", {"provider": "deterministic-fallback-v1"}
        )
        require_bubblewrap = kwargs.pop("require_bubblewrap", False)
        return run_specialist_review(
            paper_path=self.paper_path,
            role="QUAL",
            program_path=program,
            snapshot_path=snapshot,
            run_id=run_id,
            output_root=self.root,
            completion_client=client or FakeCompletion(),
            model_identity={"model": "mock-reviewer", "revision": "fixed"},
            program_version=snapshot.parent.name,
            frozen_s2_path=self.s2_path,
            s2_intervention=self.intervention,
            embedding_identity=embedding_identity,
            require_bubblewrap=require_bubblewrap,
            **kwargs,
        )

    def test_program_can_skip_or_select_an_ordered_subset(self):
        def skip_policy(source):
            return source.replace(
                "evidence = toolkit.retrieve_current_s2(limit=5)",
                "evidence = []",
                1,
            ).replace(
                '"use" if selected_ids else "consulted_no_use"',
                '"use" if selected_ids else "not_consulted"',
                1,
            )

        skip_program = self._program("skip-v1", skip_policy)
        skip_review = self._review(
            program=skip_program,
            snapshot=self._snapshot(skip_program, "skip-v1"),
            run_id="skip-run",
        )
        skip_s2 = skip_review["retrieval_provenance"]["s2"]
        self.assertEqual(skip_s2["s2_disposition"], "not_consulted")
        self.assertEqual(skip_s2["lookup_count"], 0)
        self.assertEqual(skip_s2["candidate_ids"], [])

        subset_program = self._program(
            "subset-v1",
            lambda source: source.replace(
                '"selected_s2_ids": selected_ids,',
                '"selected_s2_ids": selected_ids[-1:],',
                1,
            ),
        )
        subset_review = self._review(
            program=subset_program,
            snapshot=self._snapshot(subset_program, "subset-v1"),
            run_id="subset-run",
        )
        subset_s2 = subset_review["retrieval_provenance"]["s2"]
        self.assertEqual(subset_s2["candidate_ids"], ["s1", "s2"])
        self.assertEqual(subset_s2["selected_ids"], ["s2"])
        self.assertEqual(subset_s2["injected_ids"], ["s2"])
        request = read_json(
            self.root / "reviews" / "subset-run" / "p1" / "QUAL.request.json"
        )
        prompt = request["messages"][1]["content"]
        self.assertNotIn("Prior Evidence One", prompt)
        self.assertIn("Prior Evidence Two", prompt)

    def test_invalid_selected_id_fails_before_model_and_records_preparation_failure(
        self,
    ):
        program = self._program(
            "invalid-v1",
            lambda source: source.replace(
                '"selected_s2_ids": selected_ids,',
                '"selected_s2_ids": selected_ids + ["forged-id"],',
                1,
            ),
        )
        snapshot = self._snapshot(program, "invalid-v1")
        client = FakeCompletion()
        with self.assertRaisesRegex(ReviewPlanError, "not returned"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="invalid-run",
                client=client,
            )
        target = self.root / "reviews" / "invalid-run" / "p1"
        self.assertFalse((target / "QUAL.request.json").exists())
        self.assertFalse((target / "QUAL.raw.json").exists())
        failures = list((target / "failures" / "QUAL").glob("*.json"))
        self.assertEqual(len(failures), 1)
        self.assertEqual(read_json(failures[0])["phase"], "review_preparation")
        self.assertEqual(client.calls, [])

    def test_request_materialization_resumes_without_reexecuting_program(self):
        program = self._program("resume-v1")
        snapshot = self._snapshot(program, "resume-v1")
        first = self._review(
            program=program,
            snapshot=snapshot,
            run_id="resume-run",
        )
        request_path = self.root / "reviews" / "resume-run" / "p1" / "QUAL.request.json"
        request = read_json(request_path)
        self.assertEqual(
            first["generation_config"]["preparation_sha256"],
            request["preparation_sha256"],
        )

        failing_client = FakeCompletion(error=AssertionError("must not be called"))
        with patch(
            "agentreview.research_v2.review.prepare_review_invocation",
            side_effect=AssertionError("Program must not be re-executed"),
        ):
            resumed = self._review(
                program=program,
                snapshot=snapshot,
                run_id="resume-run",
                client=failing_client,
            )
        self.assertEqual(resumed["review_id"], first["review_id"])
        self.assertEqual(failing_client.calls, [])

        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="resume-run",
                embedding_identity={"provider": "different"},
            )
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="resume-run",
                require_bubblewrap=True,
            )

    def test_historical_v1_request_missing_true_bubblewrap_resumes_immutably(self):
        program = self._program("historical-resume-v1")
        snapshot = self._snapshot(program, "historical-resume-v1")
        run_id = "historical-resume-run"
        self._review(
            program=program,
            snapshot=snapshot,
            run_id=run_id,
            require_bubblewrap=True,
        )
        target = self.root / "reviews" / run_id / "p1"
        request_path = target / "QUAL.request.json"
        request = read_json(request_path)
        request["input_identity"].pop("require_bubblewrap")
        body = {
            name: value
            for name, value in request.items()
            if name != "preparation_sha256"
        }
        request["preparation_sha256"] = value_sha256(body)
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (target / "QUAL.raw.json").unlink()
        (target / "QUAL.json").unlink()
        historical_bytes = request_path.read_bytes()

        client = FakeCompletion()
        with patch(
            "agentreview.research_v2.review.prepare_review_invocation",
            side_effect=AssertionError("Historical Program must not be re-executed"),
        ):
            with self.assertRaisesRegex(ValueError, "input identity mismatch"):
                self._review(
                    program=program,
                    snapshot=snapshot,
                    run_id=run_id,
                    client=client,
                    require_bubblewrap=True,
                )
            resumed = self._review(
                program=program,
                snapshot=snapshot,
                run_id=run_id,
                client=client,
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        self.assertEqual(resumed["review_text"], "prepared Specialist Review")
        self.assertEqual(client.calls, ["specialist_review:QUAL"])
        self.assertEqual(request_path.read_bytes(), historical_bytes)

    def test_historical_bubblewrap_compatibility_rejects_all_other_drift(self):
        program = self._program("historical-rejection-v1")
        snapshot = self._snapshot(program, "historical-rejection-v1")

        def rewrite_request(
            run_id,
            mutate,
            *,
            initial_require_bubblewrap=True,
            refresh_hash=True,
        ):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id=run_id,
                require_bubblewrap=initial_require_bubblewrap,
            )
            request_path = (
                self.root / "reviews" / run_id / "p1" / "QUAL.request.json"
            )
            request = read_json(request_path)
            mutate(request)
            if refresh_hash:
                request["preparation_sha256"] = value_sha256(
                    {
                        name: value
                        for name, value in request.items()
                        if name != "preparation_sha256"
                    }
                )
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

        rewrite_request(
            "historical-expected-false",
            lambda value: value["input_identity"].pop("require_bubblewrap"),
            initial_require_bubblewrap=False,
        )
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-expected-false",
                require_bubblewrap=False,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        rewrite_request(
            "historical-stored-false",
            lambda value: value["input_identity"].update(
                {"require_bubblewrap": False}
            ),
        )
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-stored-false",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        rewrite_request(
            "historical-stored-null",
            lambda value: value["input_identity"].update(
                {"require_bubblewrap": None}
            ),
        )
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-stored-null",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        def remove_bubblewrap_and_drift(value):
            value["input_identity"].pop("require_bubblewrap")
            value["input_identity"]["embedding_identity"] = {
                "provider": "different"
            }

        rewrite_request("historical-other-drift", remove_bubblewrap_and_drift)
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-other-drift",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        rewrite_request(
            "historical-stale-materialization",
            lambda value: value["input_identity"].pop("require_bubblewrap"),
            refresh_hash=False,
        )
        with self.assertRaisesRegex(ValueError, "materialization hash mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-stale-materialization",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        def remove_bubblewrap_and_drift_prompt_hash(value):
            value["input_identity"].pop("require_bubblewrap")
            value["request_sha256"] = "0" * 64

        rewrite_request(
            "historical-prompt-hash",
            remove_bubblewrap_and_drift_prompt_hash,
        )
        with self.assertRaisesRegex(ValueError, "prompt hash mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-prompt-hash",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

        def remove_bubblewrap_and_drift_provenance(value):
            value["input_identity"].pop("require_bubblewrap")
            value["retrieval_provenance"]["assembly"]["prompt_sha256"] = "0" * 64

        rewrite_request(
            "historical-provenance",
            remove_bubblewrap_and_drift_provenance,
        )
        with self.assertRaisesRegex(ValueError, "provenance identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="historical-provenance",
                require_bubblewrap=True,
                _allow_legacy_missing_require_bubblewrap=True,
            )

    def test_request_materialization_rejects_pool_or_payload_change(self):
        program = self._program("identity-v1")
        snapshot = self._snapshot(program, "identity-v1")
        self._review(
            program=program,
            snapshot=snapshot,
            run_id="pool-change-run",
        )
        self._write_s2_pool(suffix="\n")
        with self.assertRaisesRegex(ValueError, "input identity mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="pool-change-run",
            )

        self._write_s2_pool()
        self._review(
            program=program,
            snapshot=snapshot,
            run_id="tamper-run",
        )
        request_path = self.root / "reviews" / "tamper-run" / "p1" / "QUAL.request.json"
        request = read_json(request_path)
        request["messages"][1]["content"] += "\ntampered"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "materialization hash mismatch"):
            self._review(
                program=program,
                snapshot=snapshot,
                run_id="tamper-run",
            )


if __name__ == "__main__":
    unittest.main()
