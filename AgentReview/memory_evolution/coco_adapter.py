"""Pinned CoCoReviewBench ReviewSplit/ReviewClassify adapter.

This is evaluator infrastructure only. It is never visible to, or mutable by,
an evolved MemoryProgram.
"""

import importlib
import os
import sys
import time
from pathlib import Path
from typing import List

from agentreview.utility.authentication_utils import get_openai_client
from .tasks import COCOREVIEWBENCH_COMMIT


SPLIT_BASE_URL = "http://localhost:13099/v1"
CLASSIFY_BASE_URL = "http://localhost:13098/v1"
SPLIT_MODEL = "Qwen3-8B-Split"
CLASSIFY_MODEL = "Qwen3-8B-Classify"
COCO_REQUEST_TIMEOUT_SECONDS = 600.0
COCO_PREFLIGHT_TIMEOUT_SECONDS = 30.0
SPLIT_ENABLE_THINKING = False
CLASSIFY_ENABLE_THINKING = True


def _head_commit(root: Path) -> str:
    head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    reference = head[5:].strip()
    loose = root / ".git" / reference
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    packed = root / ".git" / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "^")):
                commit, name = line.split(" ", 1)
                if name == reference:
                    return commit
    raise RuntimeError(f"Cannot resolve CoCoReviewBench git reference: {reference}")


def _load_official_modules(root: Path):
    if not root.is_dir() or _head_commit(root) != COCOREVIEWBENCH_COMMIT:
        raise RuntimeError(
            "CoCoReviewBench checkout must be pinned to "
            f"{COCOREVIEWBENCH_COMMIT}"
        )
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    split_module = importlib.import_module("src.evaluation._2_split")
    classify_module = importlib.import_module("src.evaluation._3_classify")
    utils_module = importlib.import_module("src.utils")
    prompt_module = importlib.import_module("src.benchmark.prompt_registry")
    return split_module, classify_module, utils_module, prompt_module


class FrozenCoCoAdapter:
    def __init__(
        self, root: Path, split_revision: str, classify_revision: str
    ):
        if not split_revision or not classify_revision:
            raise ValueError(
                "Immutable ReviewSplit and ReviewClassify revisions are required"
            )
        self.split_revision = split_revision
        self.classify_revision = classify_revision
        (
            self.split_code,
            self.classify_code,
            self.utils,
            self.prompts,
        ) = _load_official_modules(root)
        self.split_client = get_openai_client(
            "openai",
            base_url=SPLIT_BASE_URL,
            api_key=os.environ.get("COCO_SPLIT_API_KEY", "dummy"),
            timeout=COCO_REQUEST_TIMEOUT_SECONDS,
        )
        self.classify_client = get_openai_client(
            "openai",
            base_url=CLASSIFY_BASE_URL,
            api_key=os.environ.get("COCO_CLASSIFY_API_KEY", "dummy"),
            timeout=COCO_REQUEST_TIMEOUT_SECONDS,
        )
        self.allowed_categories, _ = self.utils.classify_categories()
        self._verify_served_revision(
            self.split_client,
            SPLIT_MODEL,
            self.split_revision,
            SPLIT_BASE_URL,
        )
        self._verify_served_revision(
            self.classify_client,
            CLASSIFY_MODEL,
            self.classify_revision,
            CLASSIFY_BASE_URL,
        )
        self._preflight()

    @staticmethod
    def _content(response) -> str:
        return str(response.choices[0].message.content or "")

    @staticmethod
    def _verify_served_revision(client, model: str, expected: str, endpoint: str):
        response = client.models.list(timeout=COCO_PREFLIGHT_TIMEOUT_SECONDS)
        served = next(
            (value for value in response.data if str(value.id) == model),
            None,
        )
        if served is None:
            raise RuntimeError(
                f"Frozen CoCo model {model!r} is absent from {endpoint}"
            )
        model_root_value = str(getattr(served, "root", "") or "").strip()
        if not model_root_value:
            raise RuntimeError(
                f"Frozen CoCo model {model!r} at {endpoint} did not expose its root"
            )
        model_root = Path(model_root_value).expanduser().resolve()
        metadata_dir = model_root / ".cache" / "huggingface" / "download"
        revisions = set()
        for metadata in metadata_dir.glob("*.metadata"):
            lines = metadata.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].strip():
                revisions.add(lines[0].strip())
        if revisions != {expected}:
            raise RuntimeError(
                f"Frozen CoCo model revision mismatch for {model}: "
                f"expected={expected}, observed={sorted(revisions)}, "
                f"root={model_root}"
            )
        return model_root

    @staticmethod
    def _request(client, phase: str, endpoint: str, **kwargs):
        started = time.monotonic()
        template_kwargs = (
            kwargs.get("extra_body", {}).get("chat_template_kwargs", {})
        )
        print(
            f"[COCO START] phase={phase}, endpoint={endpoint}, "
            f"model={kwargs.get('model')}, max_tokens={kwargs.get('max_tokens')}, "
            f"enable_thinking={template_kwargs.get('enable_thinking')}",
            flush=True,
        )
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Frozen CoCo {phase} request failed at {endpoint} after "
                f"{time.monotonic() - started:.1f}s: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        print(
            f"[COCO DONE] phase={phase}, elapsed={time.monotonic() - started:.1f}s, "
            f"finish_reason={getattr(choice, 'finish_reason', None)}, "
            f"completion_tokens={getattr(usage, 'completion_tokens', None)}",
            flush=True,
        )
        return response

    def _preflight(self) -> None:
        review = "The evaluation lacks ablations and random-seed variance."
        to_text, prompt_text, _ = self.split_code.build_prompt(review)
        split_response = self._request(
            self.split_client,
            "split_preflight",
            SPLIT_BASE_URL,
            model=SPLIT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": self.prompts.split_ai_reviewer_prompt(
                        len(to_text)
                    ).strip(),
                },
                {"role": "user", "content": prompt_text},
            ],
            temperature=0.0,
            max_tokens=512,
            timeout=COCO_PREFLIGHT_TIMEOUT_SECONDS,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": SPLIT_ENABLE_THINKING
                }
            },
        )
        split_content = self._content(split_response)
        parsed = self.split_code.parse_llm_output(split_content)
        coverage, _ = self.split_code.compute_coverage(parsed, len(to_text))
        split_finish_reason = split_response.choices[0].finish_reason
        if (
            not split_content
            or coverage < 1.0
            or split_finish_reason != "stop"
        ):
            raise RuntimeError(
                "Frozen CoCo split_preflight returned invalid semantic output "
                f"(coverage={coverage:.3f}, finish_reason="
                f"{split_finish_reason}, "
                f"content_prefix={split_content[:200]!r})"
            )

        system_prompt, prompt_body = self.prompts.classify_reviews_prompt()
        classify_response = self._request(
            self.classify_client,
            "classify_preflight",
            CLASSIFY_BASE_URL,
            model=CLASSIFY_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        prompt_body.strip()
                        + "\n\n\nReviewer Comment:\n"
                        + review
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=512,
            timeout=COCO_PREFLIGHT_TIMEOUT_SECONDS,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        classify_content = self._content(classify_response)
        labels, valid = self.classify_code.parse_labels_from_llm(
            classify_content, self.allowed_categories
        )
        classify_finish_reason = classify_response.choices[0].finish_reason
        if (
            not classify_content
            or not valid
            or not labels
            or self.classify_code._is_na_labelset(set(labels))
            or classify_finish_reason != "stop"
        ):
            raise RuntimeError(
                "Frozen CoCo classify_preflight returned invalid semantic output "
                f"(valid={valid}, labels={labels}, finish_reason="
                f"{classify_finish_reason}, "
                f"content_prefix={classify_content[:200]!r})"
            )

    def split_review(self, review_text: str) -> List[str]:
        to_text, prompt_text, _ = self.split_code.build_prompt(str(review_text))
        if not to_text:
            return []
        complete = []
        incomplete = []
        for penalty in (0.0, 0.5, 1.0):
            response = self._request(
                self.split_client,
                f"split_penalty_{penalty:g}",
                SPLIT_BASE_URL,
                model=SPLIT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": self.prompts.split_ai_reviewer_prompt(
                            len(to_text)
                        ).strip(),
                    },
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.6,
                presence_penalty=penalty,
                max_tokens=32768,
                extra_body={
                    "chat_template_kwargs": {
                        "enable_thinking": SPLIT_ENABLE_THINKING
                    }
                },
            )
            parsed = self.split_code.parse_llm_output(self._content(response))
            coverage, missing = self.split_code.compute_coverage(
                parsed, len(to_text)
            )
            parsed, _ = self.split_code.sanitize_parsed_ids(
                parsed, len(to_text)
            )
            if coverage < 1.0:
                parsed["##N/A"] = missing
            parsed = self.split_code.split_groups_on_markdown_items(
                parsed, to_text
            )
            parsed = self.split_code.merge_similar_parsed(
                parsed, jaccard_threshold=0.8
            )
            (complete if coverage == 1.0 else incomplete).append(
                (coverage, parsed)
            )
        candidates = [value for _, value in complete]
        if not candidates:
            candidates = [
                max(incomplete, key=lambda value: value[0])[1]
            ]
        candidates.sort(key=len)
        groups = self.split_code.normalize_groups(
            candidates[-1].values(), offset=-1
        )
        opinions = []
        for group in sorted([sorted(value) for value in groups]):
            text = "\n".join(
                to_text[index] for index in group if 0 <= index < len(to_text)
            ).strip()
            if text:
                opinions.append(text)
        return opinions

    def classify_opinions(self, opinions: List[str]):
        system_prompt, prompt_body = self.prompts.classify_reviews_prompt()
        output = []
        for opinion in opinions:
            candidates = []
            for penalty in (0.0, 0.5, 1.0):
                response = self._request(
                    self.classify_client,
                    f"classify_penalty_{penalty:g}",
                    CLASSIFY_BASE_URL,
                    model=CLASSIFY_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": (
                                prompt_body.strip()
                                + "\n\n\nReviewer Comment:\n"
                                + str(opinion)
                            ),
                        },
                    ],
                    temperature=0.6,
                    presence_penalty=penalty,
                    max_tokens=12288,
                    extra_body={
                        "chat_template_kwargs": {
                            "enable_thinking": CLASSIFY_ENABLE_THINKING
                        }
                    },
                )
                content = self._content(response)
                labels, valid = self.classify_code.parse_labels_from_llm(
                    content, self.allowed_categories
                )
                if content and valid and "</think>" in content:
                    candidates.append(labels or ["N/A"])
            if not candidates or any(
                self.classify_code._is_na_labelset(set(value))
                for value in candidates
            ):
                output.append(["N/A"])
            else:
                output.append(min(candidates, key=len))
        return output


_INSTANCES = {}


def create_frozen_coco_adapter(
    split_revision: str = "", classify_revision: str = "", coco_root: str = ""
):
    """Factory consumed by `--classifier-adapter module:factory`."""
    configured_root = str(coco_root or os.environ.get("COCOREVIEWBENCH_ROOT") or "")
    if not configured_root:
        raise ValueError(
            "CoCoReviewBench root must be provided by config/CLI or "
            "COCOREVIEWBENCH_ROOT"
        )
    root = Path(configured_root).expanduser().resolve()
    key = (split_revision, classify_revision, str(root))
    if key not in _INSTANCES:
        _INSTANCES[key] = FrozenCoCoAdapter(
            root, split_revision, classify_revision
        )
    instance = _INSTANCES[key]
    return instance.split_review, instance.classify_opinions
