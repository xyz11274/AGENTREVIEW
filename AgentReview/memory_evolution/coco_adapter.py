"""Pinned CoCoReviewBench ReviewSplit/ReviewClassify adapter.

This is evaluator infrastructure only. It is never visible to, or mutable by,
an evolved MemoryProgram.
"""

import importlib
import os
import sys
from pathlib import Path
from typing import List

from openai import OpenAI

from .tasks import COCOREVIEWBENCH_COMMIT


DEFAULT_COCO_ROOT = Path("/data0/xyj/shengao/CoCoReviewBench")
SPLIT_BASE_URL = "http://localhost:13099/v1"
CLASSIFY_BASE_URL = "http://localhost:13098/v1"
SPLIT_MODEL = "Qwen3-8B-Split"
CLASSIFY_MODEL = "Qwen3-8B-Classify"


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
        self.split_client = OpenAI(
            base_url=SPLIT_BASE_URL,
            api_key=os.environ.get("COCO_SPLIT_API_KEY", "dummy"),
        )
        self.classify_client = OpenAI(
            base_url=CLASSIFY_BASE_URL,
            api_key=os.environ.get("COCO_CLASSIFY_API_KEY", "dummy"),
        )
        self.allowed_categories, _ = self.utils.classify_categories()

    @staticmethod
    def _content(response) -> str:
        return str(response.choices[0].message.content or "")

    def split_review(self, review_text: str) -> List[str]:
        to_text, prompt_text, _ = self.split_code.build_prompt(str(review_text))
        if not to_text:
            return []
        complete = []
        incomplete = []
        for penalty in (0.0, 0.5, 1.0):
            response = self.split_client.chat.completions.create(
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
                extra_body={"chat_template_kwargs": {"enable_thinking": True}},
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
                response = self.classify_client.chat.completions.create(
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
    split_revision: str = "", classify_revision: str = ""
):
    """Factory consumed by `--classifier-adapter module:factory`."""
    key = (split_revision, classify_revision)
    if key not in _INSTANCES:
        root = Path(
            os.environ.get("COCOREVIEWBENCH_ROOT", str(DEFAULT_COCO_ROOT))
        ).expanduser().resolve()
        _INSTANCES[key] = FrozenCoCoAdapter(
            root, split_revision, classify_revision
        )
    instance = _INSTANCES[key]
    return instance.split_review, instance.classify_opinions
