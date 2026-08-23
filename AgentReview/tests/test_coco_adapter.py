import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_evolution.coco_adapter import FrozenCoCoAdapter


def _response(content="OK", finish_reason="stop", completion_tokens=1):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(completion_tokens=completion_tokens),
    )


class RecordingCompletions:
    def __init__(self, response=None, error=None):
        self.response = response or _response()
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class RecordingOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.completions = RecordingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)
        self.__class__.instances.append(self)


class CoCoAdapterTest(unittest.TestCase):
    def setUp(self):
        RecordingOpenAI.instances = []

    @staticmethod
    def _official_modules():
        return (
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(classify_categories=lambda: ({"QUAL-MET"}, None)),
            SimpleNamespace(),
        )

    def test_clients_disable_sdk_retry_storm_and_run_generation_preflight(self):
        with patch(
            "memory_evolution.coco_adapter._load_official_modules",
            return_value=self._official_modules(),
        ), patch("memory_evolution.coco_adapter.OpenAI", RecordingOpenAI):
            FrozenCoCoAdapter(Path("/frozen/coco"), "split-rev", "classify-rev")

        self.assertEqual(len(RecordingOpenAI.instances), 2)
        for client in RecordingOpenAI.instances:
            self.assertEqual(client.init_kwargs["timeout"], 600.0)
            self.assertEqual(client.init_kwargs["max_retries"], 0)
            self.assertEqual(len(client.completions.calls), 1)
            request = client.completions.calls[0]
            self.assertEqual(request["max_tokens"], 1)
            self.assertEqual(request["timeout"], 30.0)
            self.assertEqual(
                request["extra_body"],
                {"chat_template_kwargs": {"enable_thinking": False}},
            )

    def test_preflight_error_identifies_exact_coco_phase(self):
        adapter = object.__new__(FrozenCoCoAdapter)
        failing = RecordingCompletions(error=TimeoutError("engine stalled"))
        adapter.split_client = SimpleNamespace(
            chat=SimpleNamespace(completions=failing)
        )
        adapter.classify_client = SimpleNamespace(
            chat=SimpleNamespace(completions=RecordingCompletions())
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Frozen CoCo split_preflight request failed.*engine stalled",
        ):
            adapter._preflight()

    def test_frozen_split_sampling_parameters_remain_unchanged(self):
        calls = RecordingCompletions(
            response=_response(content="##1: [1]", completion_tokens=4)
        )
        adapter = object.__new__(FrozenCoCoAdapter)
        adapter.split_client = SimpleNamespace(
            chat=SimpleNamespace(completions=calls)
        )
        adapter.split_code = SimpleNamespace(
            build_prompt=lambda review: ([review], "[1] review", None),
            parse_llm_output=lambda content: {1: [1]},
            compute_coverage=lambda parsed, count: (1.0, []),
            sanitize_parsed_ids=lambda parsed, count: (parsed, []),
            split_groups_on_markdown_items=lambda parsed, text: parsed,
            merge_similar_parsed=lambda parsed, jaccard_threshold: parsed,
            normalize_groups=lambda groups, offset: [
                [index + offset for index in group] for group in groups
            ],
        )
        adapter.prompts = SimpleNamespace(
            split_ai_reviewer_prompt=lambda count: "split system"
        )

        opinions = adapter.split_review("review opinion")

        self.assertEqual(opinions, ["review opinion"])
        self.assertEqual(len(calls.calls), 3)
        self.assertEqual(
            [call["presence_penalty"] for call in calls.calls],
            [0.0, 0.5, 1.0],
        )
        for call in calls.calls:
            self.assertEqual(call["max_tokens"], 32768)
            self.assertEqual(call["temperature"], 0.6)
            self.assertEqual(
                call["extra_body"],
                {"chat_template_kwargs": {"enable_thinking": True}},
            )


if __name__ == "__main__":
    unittest.main()
