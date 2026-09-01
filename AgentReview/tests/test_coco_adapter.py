import tempfile
import threading
import time
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

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.completions = RecordingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)
        self.__class__.instances.append(self)


class ConcurrentEchoCompletions:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = []

    def create(self, **kwargs):
        opinion = kwargs["messages"][1]["content"].rsplit("\n", 1)[-1]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(kwargs)
        time.sleep(0.01 if opinion == "first" else 0.02)
        with self.lock:
            self.active -= 1
        return _response(content=f"{opinion}</think>")


class CoCoAdapterTest(unittest.TestCase):
    def setUp(self):
        RecordingOpenAI.instances = []

    @staticmethod
    def _official_modules():
        return (
            SimpleNamespace(
                build_prompt=lambda review: ([review], "[1] review", None),
                parse_llm_output=lambda content: {1: [1]},
                compute_coverage=lambda parsed, count: (1.0, []),
            ),
            SimpleNamespace(
                parse_labels_from_llm=lambda content, categories: (
                    ["QUAL-MET"],
                    True,
                ),
                _is_na_labelset=lambda labels: False,
            ),
            SimpleNamespace(classify_categories=lambda: ({"QUAL-MET"}, None)),
            SimpleNamespace(
                split_ai_reviewer_prompt=lambda count: "split system",
                classify_reviews_prompt=lambda: (
                    "classify system",
                    "taxonomy",
                ),
            ),
        )

    def test_clients_disable_sdk_retry_storm_and_run_generation_preflight(self):
        with patch(
            "memory_evolution.coco_adapter._load_official_modules",
            return_value=self._official_modules(),
        ), patch(
            "memory_evolution.coco_adapter.get_openai_client", RecordingOpenAI
        ), patch.object(
            FrozenCoCoAdapter, "_verify_served_revision"
        ):
            FrozenCoCoAdapter(Path("/frozen/coco"), "split-rev", "classify-rev")

        self.assertEqual(len(RecordingOpenAI.instances), 2)
        for client in RecordingOpenAI.instances:
            self.assertEqual(client.init_args, ("openai",))
            self.assertEqual(client.init_kwargs["timeout"], 600.0)
            self.assertEqual(len(client.completions.calls), 1)
            request = client.completions.calls[0]
            self.assertEqual(request["max_tokens"], 512)
            self.assertEqual(request["timeout"], 30.0)
            self.assertEqual(
                request["extra_body"],
                {"chat_template_kwargs": {"enable_thinking": False}},
            )

    def test_served_model_revision_is_read_from_huggingface_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".cache" / "huggingface" / "download"
            metadata.mkdir(parents=True)
            (metadata / "config.json.metadata").write_text(
                "expected-revision\netag\ntimestamp\n",
                encoding="utf-8",
            )
            client = SimpleNamespace(
                models=SimpleNamespace(
                    list=lambda **kwargs: SimpleNamespace(
                        data=[SimpleNamespace(id="model-a", root=str(root))]
                    )
                )
            )

            observed = FrozenCoCoAdapter._verify_served_revision(
                client,
                "model-a",
                "expected-revision",
                "http://localhost:1/v1",
            )

            self.assertEqual(observed, root.resolve())
            with self.assertRaisesRegex(RuntimeError, "revision mismatch"):
                FrozenCoCoAdapter._verify_served_revision(
                    client,
                    "model-a",
                    "wrong-revision",
                    "http://localhost:1/v1",
                )

    def test_preflight_error_identifies_exact_coco_phase(self):
        adapter = object.__new__(FrozenCoCoAdapter)
        (
            adapter.split_code,
            adapter.classify_code,
            adapter.utils,
            adapter.prompts,
        ) = self._official_modules()
        adapter.allowed_categories = {"QUAL-MET"}
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

    def test_preflight_rejects_truncated_semantic_output(self):
        adapter = object.__new__(FrozenCoCoAdapter)
        (
            adapter.split_code,
            adapter.classify_code,
            adapter.utils,
            adapter.prompts,
        ) = self._official_modules()
        adapter.allowed_categories = {"QUAL-MET"}
        adapter.split_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=RecordingCompletions(
                    response=_response(
                        content="##1: [1]",
                        finish_reason="length",
                        completion_tokens=512,
                    )
                )
            )
        )
        adapter.classify_client = SimpleNamespace(
            chat=SimpleNamespace(completions=RecordingCompletions())
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "split_preflight returned invalid semantic output.*finish_reason=length",
        ):
            adapter._preflight()

    def test_frozen_split_uses_direct_output_with_official_sampling(self):
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
                {"chat_template_kwargs": {"enable_thinking": False}},
            )

    def test_classify_opinions_uses_bounded_concurrency_and_preserves_order(self):
        calls = ConcurrentEchoCompletions()
        adapter = object.__new__(FrozenCoCoAdapter)
        adapter.classify_client = SimpleNamespace(
            chat=SimpleNamespace(completions=calls)
        )
        adapter.classify_code = SimpleNamespace(
            parse_labels_from_llm=lambda content, categories: (
                [content.removesuffix("</think>")],
                True,
            ),
            _is_na_labelset=lambda labels: False,
        )
        adapter.prompts = SimpleNamespace(
            classify_reviews_prompt=lambda: ("classify system", "taxonomy")
        )
        adapter.allowed_categories = {"first", "second", "third"}
        adapter.classify_workers = 2

        labels = adapter.classify_opinions(["first", "second", "third"])

        self.assertEqual(labels, [["first"], ["second"], ["third"]])
        self.assertEqual(len(calls.calls), 9)
        self.assertEqual(calls.max_active, 2)


if __name__ == "__main__":
    unittest.main()
