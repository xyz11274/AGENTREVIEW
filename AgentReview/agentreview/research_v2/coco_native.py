"""Generate the CoCo AI-Scientist baseline with its original prompt helpers."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import CompletionClient
from .models import Paper


PROTOCOL = "research-v2-coco-native-review-v1"
REQUEST_PROTOCOL = "research-v2-coco-native-request-v1"
NATIVE_MODE = "qwen3.8-27b"


class NativeReviewFailure(RuntimeError):
    """A shared generation or source failure requiring coordinator recovery."""

    failure_category = "shared_runtime"

    def __init__(self, message: str, *, failure_record: dict[str, Any] | None = None):
        super().__init__(message)
        self.failure_record = failure_record or {"stage": "coco_native", "category": self.failure_category, "message": message}


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def _load_source_nodes(source: Path, names: set[str], namespace: dict[str, Any]) -> dict[str, str]:
    """Compile the original selected definitions, excluding CLI/module startup.

    AST nodes retain the source function bodies and constant expressions. This
    loads the same helpers without executing unrelated server and dataset imports.
    """
    data = source.read_bytes()
    tree = ast.parse(data, filename=str(source))
    selected = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node_names = {node.name}
        elif isinstance(node, ast.Assign):
            node_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        else:
            node_names = set()
        if node_names.intersection(names):
            selected.append(node)
            found.update(node_names.intersection(names))
    if found != names:
        raise ValueError(f"CoCo native helper source requires definitions: {sorted(names - found)}")
    namespace["__file__"] = str(source)
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(source), "exec"), namespace)
    return {"path": str(source), "sha256": hashlib.sha256(data).hexdigest()}


def load_native_helpers(coco_root: str | Path, *, num_fewshot: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load CoCo's original AI-Scientist prompt and extraction functions."""
    if type(num_fewshot) is int and 0 <= num_fewshot <= 3:
        root = Path(coco_root).expanduser().resolve()
    else:
        raise ValueError("CoCo native num_fewshot requires an explicit integer from zero through three")
    evaluation = root / "src" / "evaluation"
    namespace: dict[str, Any] = {"Any": Any, "Dict": Dict, "json": json, "os": os, "re": re, "unicodedata": unicodedata}
    sources = []
    sources.append(_load_source_nodes(
        evaluation / "ai_scientist" / "perform_llm_review.py",
        {"reviewer_system_prompt_base", "template_instructions", "neurips_form", "dir_path", "fewshot_papers", "fewshot_reviews", "load_review", "get_review_fewshot_examples_wo_paper"},
        namespace,
    ))
    sources.append(_load_source_nodes(
        evaluation / "ai_scientist" / "llm.py", {"extract_json_between_markers"}, namespace,
    ))
    namespace["get_review_fewshot_examples"] = namespace["get_review_fewshot_examples_wo_paper"]
    sources.append(_load_source_nodes(
        evaluation / "_1_query_ai_reviewer.py",
        {"ensure_ai_scientist_imports", "_build_prompts", "build_ai_scientist_review", "extract_review_text"},
        namespace,
    ))
    examples = []
    for paper_path, review_path in zip(namespace["fewshot_papers"][:num_fewshot], namespace["fewshot_reviews"][:num_fewshot]):
        text_path = Path(paper_path).with_suffix(".txt")
        for path in (text_path, Path(review_path)):
            if path.is_file() and path.resolve().is_relative_to(root):
                examples.append(_binding(path))
            else:
                raise ValueError("CoCo native few-shot source requires its saved text and review assets")
    return namespace, {"mode": NATIVE_MODE, "source_files": sources, "num_fewshot": num_fewshot, "fewshot_assets": examples}


def _request(*, paper_path: Path, coco_root: str | Path, run_id: str, model_identity: dict[str, Any], max_tokens: int, num_fewshot: int) -> tuple[dict[str, Any], dict[str, Any], Paper]:
    if isinstance(run_id, str) and run_id.strip() and type(max_tokens) is int and max_tokens > 0:
        pass
    else:
        raise ValueError("CoCo native generation requires a run ID and positive completion budget")
    model = model_identity.get("model")
    thinking = model_identity.get("enable_thinking")
    if isinstance(model, str) and model.lower() == NATIVE_MODE and type(thinking) is bool:
        pass
    else:
        raise ValueError("CoCo native generation requires local qwen3.8-27b and explicit enable_thinking")
    paper_bytes = paper_path.read_bytes()
    paper = Paper.from_dict(json.loads(paper_bytes))
    paper_binding = {"path": str(paper_path), "sha256": hashlib.sha256(paper_bytes).hexdigest()}
    helpers, source_identity = load_native_helpers(coco_root, num_fewshot=num_fewshot)
    prefix, suffix = helpers["_build_prompts"](
        ai_scientist_mode=True,
        args=SimpleNamespace(num_fs_examples=num_fewshot, no_think=thinking is False),
    )
    # The original helper reads saved example text and reviews. Bind that exact
    # material after prompt construction before persisting the request.
    for binding in source_identity["fewshot_assets"]:
        if file_sha256(binding["path"]) != binding["sha256"]:
            raise ValueError("CoCo native few-shot assets changed during prompt construction")
    if file_sha256(paper_path) != paper_binding["sha256"]:
        raise ValueError("CoCo native paper changed during prompt construction")
    messages = [
        {"role": "system", "content": helpers["reviewer_system_prompt_base"]},
        {"role": "user", "content": prefix + paper.text + suffix},
    ]
    request = {
        "protocol": REQUEST_PROTOCOL, "run_id": run_id, "paper_id": paper.paper_id,
        "completion_contract": "complete-stop-v1",
        "paper": paper_binding, "paper_text_sha256": hashlib.sha256(paper.text.encode("utf-8")).hexdigest(),
        "paper_character_count": len(paper.text), "input_policy": "complete_paper_text",
        "model_identity": dict(model_identity), "max_tokens": max_tokens,
        "native_identity": source_identity, "messages": messages,
    }
    request["request_id"] = value_sha256(request)
    return request, helpers, paper


def generate_native_review(
    *,
    paper_path: str | Path,
    coco_root: str | Path,
    run_id: str,
    output_root: str | Path,
    completion_client: CompletionClient,
    model_identity: dict[str, Any],
    max_tokens: int,
    num_fewshot: int,
) -> dict[str, Any]:
    """Save one baseline request, raw response and original extracted review.

    The caller supplies a completion client enforcing its frozen token budget.
    A terminal failure is replayed on resume; coordinator recovery creates a new
    evaluation namespace. Successful raw output resumes extraction without a call.
    """
    root = Path(os.path.abspath(Path(output_root).expanduser()))
    if root.resolve() != root:
        raise NativeReviewFailure("CoCo native output requires a canonical directory")
    try:
        request, helpers, paper = _request(
            paper_path=Path(paper_path).expanduser().resolve(), coco_root=coco_root,
            run_id=run_id, model_identity=model_identity, max_tokens=max_tokens,
            num_fewshot=num_fewshot,
        )
        source_root = Path(coco_root).expanduser().resolve()
        if root.is_relative_to(source_root):
            raise ValueError("CoCo native outputs require a directory outside the benchmark repository")
        request_path = atomic_write_json(root / "request.json", request, reject_symlink=True)
        raw_path, attempt_path, review_path = root / "raw.json", root / "attempt.json", root / "review.json"
        if any(path.is_symlink() for path in (raw_path, attempt_path, review_path)):
            raise ValueError("CoCo native saved artifacts require ordinary files")
        if raw_path.exists():
            raw = read_json(raw_path)
            if raw.get("request_id") != request["request_id"]:
                raise ValueError("CoCo native raw output requires the saved request identity")
        elif attempt_path.exists():
            raise NativeReviewFailure("CoCo native interrupted request requires coordinator recovery")
        else:
            atomic_write_json(attempt_path, {"request_id": request["request_id"], "status": "started"}, reject_symlink=True)
            try:
                text = completion_client.complete(request["messages"], max_tokens=max_tokens, component=f"coco-native:{run_id}:{paper.paper_id}")
                if isinstance(text, str) and text.strip():
                    raw = {"request_id": request["request_id"], "status": "complete", "raw_model_output": text}
                else:
                    raise ValueError("CoCo native completion requires nonempty assistant text")
            except Exception as exc:
                raw = {"request_id": request["request_id"], "status": "failed", "failure": {"stage": "coco_native_generation", "category": "shared_runtime", "error_type": type(exc).__name__, "message": str(exc)}}
                atomic_write_json(raw_path, raw, reject_symlink=True)
                raise NativeReviewFailure(str(exc), failure_record=raw["failure"]) from exc
            atomic_write_json(raw_path, raw, reject_symlink=True)
        if raw.get("status") == "failed":
            raise NativeReviewFailure(raw["failure"]["message"], failure_record=raw["failure"])
        raw_text = raw.get("raw_model_output")
        if isinstance(raw_text, str) and raw_text.strip():
            built_review = helpers["build_ai_scientist_review"](raw_text, paper.paper_id)
            structured = helpers["extract_json_between_markers"](raw_text)
            review_text = helpers["extract_review_text"](built_review, NATIVE_MODE)
        else:
            raise ValueError("CoCo native saved response requires nonempty assistant text")
        if isinstance(review_text, str) and review_text.strip():
            review = {
                "protocol": PROTOCOL, "paper_id": paper.paper_id, "run_id": run_id,
                "variant_kind": "coco_native", "request_id": request["request_id"],
                "review_text": review_text, "raw_model_output": raw_text,
                "structured_review": structured, "native_identity": request["native_identity"],
                "identity": {"request_id": request["request_id"], "native": request["native_identity"]},
                "model_identity": dict(model_identity), "input_policy": "complete_paper_text",
                "request": _binding(request_path), "raw": _binding(raw_path),
            }
            atomic_write_json(review_path, review, reject_symlink=True)
            return {**review, "artifact_path": str(review_path)}
        raise ValueError("CoCo native extraction requires nonempty review text")
    except NativeReviewFailure:
        raise
    except Exception as exc:
        raise NativeReviewFailure(f"CoCo native source or extraction failed: {exc}") from exc


__all__ = ["NativeReviewFailure", "generate_native_review", "load_native_helpers"]
