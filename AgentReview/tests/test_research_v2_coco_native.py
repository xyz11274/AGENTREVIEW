import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentreview.research_v2.coco_native import (
    NativeReviewFailure,
    generate_native_review,
    load_native_helpers,
)
from agentreview.research_v2.io import atomic_write_json, file_sha256


COCO_ROOT = Path(__file__).resolve().parents[3] / "CoCoReviewBench"


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class CoCoNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paper_path = self.root / "paper.json"
        self.body = "COMPLETE-PAPER-START\n" + "An experiment sentence.\n" * 20000 + "COMPLETE-PAPER-END"
        atomic_write_json(self.paper_path, {"paper_id": "p1", "title": "Example paper", "text": self.body})
        self.response = '```json\n{"Summary":"summary", "Strengths":["strength1","strength2"], "Weaknesses":["weakness1"], "Questions":["question1"], "Limitations":["limitation1"], "Decision":"Reject"}\n```'
        self.client = _Client(self.response)
        self.kwargs = {"paper_path": self.paper_path, "coco_root": COCO_ROOT, "run_id": "native-run", "output_root": self.root / "native", "completion_client": self.client, "model_identity": {"model": "qwen3.8-27b", "temperature": 0.7, "enable_thinking": False}, "max_tokens": 4096, "num_fewshot": 0}

    def test_original_helpers_generate_complete_paper_prompt_and_extract_saved_review(self):
        helpers, identity = load_native_helpers(COCO_ROOT, num_fewshot=0)
        prefix, suffix = helpers["_build_prompts"](True, SimpleNamespace(num_fs_examples=0, no_think=True))
        paper_hash = file_sha256(self.paper_path)
        result = generate_native_review(**self.kwargs)
        self.assertEqual(self.client.calls[0]["messages"], [
            {"role": "system", "content": helpers["reviewer_system_prompt_base"]},
            {"role": "user", "content": prefix + self.body + suffix},
        ])
        self.assertIn("COMPLETE-PAPER-END", self.client.calls[0]["messages"][1]["content"])
        original_review = helpers["extract_review_text"](helpers["build_ai_scientist_review"](self.response, "p1"), "qwen3.8-27b")
        self.assertEqual(result["review_text"], original_review)
        self.assertEqual(result["raw_model_output"], self.response)
        self.assertEqual(result["native_identity"], identity)
        self.assertEqual(file_sha256(self.paper_path), paper_hash)
        saved = json.loads(Path(result["artifact_path"]).read_text())
        self.assertEqual(saved["structured_review"]["Strengths"], ["strength1", "strength2"])
        self.assertEqual(saved["input_policy"], "complete_paper_text")
        self.assertEqual(len(saved["native_identity"]["source_files"]), 3)

    def test_replay_uses_saved_raw_without_model_call_and_parameter_drift_requires_new_run(self):
        result = generate_native_review(**self.kwargs)
        Path(result["artifact_path"]).unlink()
        replay = generate_native_review(**self.kwargs)
        self.assertEqual(replay, result)
        self.assertEqual(len(self.client.calls), 1)
        with self.assertRaisesRegex(NativeReviewFailure, "Immutable artifact"):
            generate_native_review(**{**self.kwargs, "max_tokens": 2048})
        self.assertEqual(len(self.client.calls), 1)

    def test_saved_terminal_failure_and_interrupted_request_consume_one_call(self):
        client = _Client(TimeoutError("service timeout"))
        for attempt in range(2):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(NativeReviewFailure, "service timeout"):
                generate_native_review(**{**self.kwargs, "completion_client": client})
        self.assertEqual(len(client.calls), 1)
        raw_path = self.root / "native" / "raw.json"
        self.assertEqual(json.loads(raw_path.read_text())["status"], "failed")
        raw_path.unlink()
        with self.assertRaisesRegex(NativeReviewFailure, "interrupted request"):
            generate_native_review(**{**self.kwargs, "completion_client": client})
        self.assertEqual(len(client.calls), 1)

    def test_actual_fewshot_assets_and_thinking_suffix_are_bound(self):
        kwargs = {**self.kwargs, "num_fewshot": 1, "model_identity": {**self.kwargs["model_identity"], "enable_thinking": True}}
        result = generate_native_review(**kwargs)
        assets = result["native_identity"]["fewshot_assets"]
        self.assertEqual(len(assets), 2)
        for binding in assets:
            self.assertEqual(file_sha256(binding["path"]), binding["sha256"])
        self.assertTrue(self.client.calls[0]["messages"][1]["content"].endswith("Please reason step by step, and put your final peer review of the above paper in json format."))
        sample = json.loads((COCO_ROOT / "src/evaluation/ai_scientist/fewshot_examples/132_automated_relational.json").read_text())["review"]
        self.assertIn(sample, self.client.calls[0]["messages"][1]["content"])

    def test_original_raw_text_fallback_is_preserved(self):
        client = _Client("The experiment lacks a reproducible validation protocol.")
        result = generate_native_review(**{**self.kwargs, "completion_client": client})
        self.assertEqual(result["review_text"], client.response)
        self.assertIsNone(result["structured_review"])

    def test_empty_completion_and_unsupported_configuration_pause_before_scoring(self):
        with self.assertRaisesRegex(NativeReviewFailure, "nonempty assistant"):
            generate_native_review(**{**self.kwargs, "completion_client": _Client("")})
        with self.assertRaisesRegex(NativeReviewFailure, "explicit enable_thinking"):
            generate_native_review(**{**self.kwargs, "model_identity": {"model": "another-model"}})
        with self.assertRaisesRegex(NativeReviewFailure, "zero through three"):
            generate_native_review(**{**self.kwargs, "num_fewshot": 4})
        self.assertEqual(len(self.client.calls), 0)

    def test_output_inside_benchmark_repository_is_rejected_before_writing(self):
        protected_output = COCO_ROOT / "native-test-output-forbidden"
        with self.assertRaisesRegex(NativeReviewFailure, "outside the benchmark repository"):
            generate_native_review(**{**self.kwargs, "output_root": protected_output})
        self.assertFalse(protected_output.exists())
        self.assertEqual(len(self.client.calls), 0)


if __name__ == "__main__":
    unittest.main()
