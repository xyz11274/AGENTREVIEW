"""Isolated execution host for the unchanged standard CoCo implementation."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agentreview.research_v2.development_coco import (  # noqa: E402
    _binding, _bound_file, _source_identity, _stage_failure, _validate_inputs,
)
from agentreview.research_v2.io import atomic_write_json, read_json, read_jsonl, value_sha256  # noqa: E402
from agentreview.research_v2.judge_lease import Qwen27BModeLease  # noqa: E402
from agentreview.research_v2.coco_service_runtime import (  # noqa: E402
    _listening_sockets, _socket_owner_pids, _read_null_separated, _process_start_identity,
)


def _load_standard(root: Path, name: str) -> Any:
    for directory in (root, root / "src/evaluation"):
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


def _runner(root: Path) -> Any:
    spec = importlib.util.spec_from_file_location("standard_candidate_contract", root / "scripts/run_official_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service_ready(service: dict[str, Any]) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(service["base_url"].rstrip("/") + "/models", timeout=15) as response:
        models = json.load(response)
    if service["model"] in {item.get("id") for item in models.get("data", [])}:
        endpoint = urlparse(service["base_url"])
        sockets = [item for item in _listening_sockets() if item[1] == endpoint.port]
        owners = _socket_owner_pids({item[2] for item in sockets})
        pids = {pid for values in owners.values() for pid in values}
        if len(sockets) == 1 and len(pids) == 1:
            pid = next(iter(pids))
            args = _read_null_separated(Path(f"/proc/{pid}/cmdline"), label="standard CoCo service")
            def option(name):
                values = [args[index + 1] for index, value in enumerate(args[:-1]) if value == name]
                values += [value.split("=", 1)[1] for value in args if value.startswith(name + "=")]
                if len(values) == 1:
                    return values[0]
                raise ValueError(f"Scoring service requires one explicit {name} argument")
            model_root = Path(option("--model")).expanduser().resolve()
            if model_root == Path(service["model_identity"]["root"]) and option("--served-model-name") == service["model"]:
                return {"pid": pid, "start_identity": _process_start_identity(pid, proc_root=Path("/proc")),
                        "argv_sha256": value_sha256(args), "model_root": str(model_root),
                        "model": service["model"], "base_url": service["base_url"],
                        "max_model_len": int(option("--max-model-len"))}
            raise ValueError("Live scoring process model path differs from the frozen model identity")
        raise ValueError("Local scoring endpoint requires one identifiable service process")
    raise ValueError("Local scoring service model name differs from its frozen configuration")


def _client(config: dict[str, Any], stage: str, request: dict[str, Any]) -> Any:
    utils = importlib.import_module("src.utils")
    service = config["services"][stage]
    return utils.LLMClient(api_key="local-vllm", base_url=service["base_url"], model_name=service["model"],
        max_workers={"split": 64, "classify": 128, "judge": 4}[stage],
        cache=True, allow_local_cache=True, cache_dir=str(Path(request["output_root"]) / "cache" / stage),
        cache_version=value_sha256({"request": request, "stage": stage}), cache_identity=request)


def _previous_stage(request: dict[str, Any], stage: str) -> Path:
    state = read_json(Path(request["output_root"]) / "stages" / stage / "state.json")
    if state["status"] == "complete":
        result = read_json(_bound_file(state["result"]))
        return _bound_file(result["artifact"])
    raise ValueError("Scoring stage requires a completed predecessor checkpoint")


def _split_or_classify(request: dict[str, Any], config: dict[str, Any], stage: str, output: Path) -> dict[str, Any]:
    root = Path(config["coco_root"])
    module = _load_standard(root, "_2_split" if stage == "split" else "_3_classify")
    contract = _runner(root)
    source = Path(request["output_root"]) / "candidate.jsonl" if stage == "split" else _previous_stage(request, "split")
    target = output.with_suffix(".jsonl")
    records = read_jsonl(source)
    service = config["services"][stage]
    model_root = Path(service["model_identity"]["root"])
    if stage == "split":
        preflight = contract._preflight_split_prompts(candidate_records=records, model_root=model_root,
            python_executable=Path(config["python_executable"]), expected_transformers_version=None,
            max_model_len=40960, max_completion_tokens=8192)
    else:
        preflight = contract._preflight_classify_prompts(split_records=records, model_root=model_root,
            python_executable=Path(config["python_executable"]), expected_transformers_version=None,
            max_model_len=32768, max_completion_tokens=4096)
    args = SimpleNamespace(input_jsonl=str(source), output_jsonl=str(target), model=service["model"],
        base_url=service["base_url"], max_workers=64 if stage == "split" else 128,
        max_completion_tokens=8192 if stage == "split" else 4096, effort="medium")
    client = _client(config, stage, request)
    module.client = client
    try:
        if stage == "split":
            module.split_all(args)
        else:
            module.classify_opinions(args)
    finally:
        client.shutdown()
    contract._validate_processed_output(target, stage=stage, expected_records=records)
    return {"stage": stage, "input": _binding(source), "artifact": _binding(target), "prompt_preflight": preflight}


def evaluate_loaded_standard(*, evaluation: Any, request: dict[str, Any], config: dict[str, Any],
                             references: dict[str, Any], input_file: Path, judge_client: Any) -> dict[str, Any]:
    """Call standard loading, filtering, judging, coverage and aggregation functions."""
    paper_ids = request["paper_ids"]
    benchmark_files = [_bound_file(binding) for binding in references["benchmark_files"]]
    # This is an input-path binding. The held-out-reviewer rule is the original
    # CoCo left_out_one function and consumes the frozen development assignment.
    evaluation.benchmark_reviewer_selection_file = _bound_file(references["reviewer_selection_file"])
    benchmark_records = evaluation.load_benchmark_records(paper_ids, benchmark_files)
    human_categories = evaluation.load_human_complete_category_sets(_bound_file(references["complete_human_classify_file"]), paper_ids)
    _, full_refs = evaluation.load_human_full_text_data(benchmark_records, benchmark_files)
    human_refs = evaluation.load_human_review_data(benchmark_records, benchmark_files)
    ai_full = evaluation.load_ai_full_text_data(str(input_file))
    ai_categories = evaluation.load_ai_review_data(str(input_file))
    ai_paper = evaluation.load_ai_paper_level_data(str(input_file))
    for values in (benchmark_records, human_categories, full_refs, human_refs, ai_full, ai_categories, ai_paper):
        if set(values) == set(paper_ids):
            continue
        raise ValueError("Standard CoCo input loading requires complete cohort coverage")
    evaluation.preflight_all_judge_prompts(judge_client=judge_client, human_ref_data=human_refs,
        ai_category_data=ai_categories, ai_paper_level_data=ai_paper, category_level_eval=True,
        category_incorrect_eval=False, paper_level_eval=True)
    average, metric_details = evaluation.compute_full_text_metrics([ai_full[p] for p in paper_ids], [full_refs[p] for p in paper_ids])
    category = evaluation.evaluate_categories_batch(human_refs, ai_categories, judge_client, ref_label_for_eval="category_correct", require_complete=True)
    paper = evaluation.evaluate_full_judge_batch(human_refs, ai_paper, judge_client, ref_label_for_eval="paper_correct", require_complete=True)
    if set(category) == set(paper_ids) and set(paper) == set(paper_ids):
        pass
    else:
        raise ValueError("Standard CoCo judging requires complete cohort coverage")
    full = {"average": average, "count": len(paper_ids)}
    table = evaluation.build_table_1_summary("candidate", full, category, paper, ai_categories, human_categories)
    details = {}
    for index, paper_id in enumerate(paper_ids):
        single_full = {"average": {metric: values[index] for metric, values in metric_details.items()}, "count": 1}
        single_category, single_paper = {paper_id: category[paper_id]}, {paper_id: paper[paper_id]}
        single_ai, single_human = {paper_id: ai_categories[paper_id]}, {paper_id: human_categories[paper_id]}
        single_table = evaluation.build_table_1_summary(paper_id, single_full, single_category, single_paper, single_ai, single_human)
        details[paper_id] = {"table_1_summary": single_table, "paper_judge_scores": paper[paper_id]["judge_scores"],
                             "category_judge_scores": category[paper_id], "full_text_metrics": single_full["average"]}
    return {"paper_ids": paper_ids, "papers": details, "table_1_summary": table,
            "judge_prompt_preflight": evaluation.build_judge_prompt_preflight_summary(candidate_count=len(paper_ids),
                prompt_token_counts=list(getattr(judge_client, "official_judge_prompt_token_counts", [])))}


def _evaluate(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    evaluation = _load_standard(Path(config["coco_root"]), "_4_evaluation")
    references = read_json(_bound_file(request["inputs"]["references"]))
    os.environ["COCO_BERT_SCORE_MODEL_ROOT"] = config["bert_score_model"]["root"]
    os.environ["COCO_BERT_SCORE_NUM_LAYERS"] = "17"
    with Qwen27BModeLease("judge"):
        client = _client(config, "judge", request)
        try:
            classified = _previous_stage(request, "classify")
            result = evaluate_loaded_standard(evaluation=evaluation, request=request, config=config,
                references=references, input_file=classified, judge_client=client)
            result["input"] = _binding(classified)
            return result
        finally:
            client.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--stage", choices=("split", "classify", "evaluation"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    try:
        request = read_json(args.request)
        config = read_json(_bound_file(request["inputs"]["scoring_config"]))
        root = Path(config["coco_root"])
        if _source_identity(root) != config["source_files"]:
            raise ValueError("Frozen standard CoCo source identity changed")
        _validate_inputs(_bound_file(request["inputs"]["reviews"]), _bound_file(request["inputs"]["references"]), request["partition"])
        service_role = "judge" if args.stage == "evaluation" else args.stage
        before_runtime = _service_ready(config["services"][service_role])
        required_context = {"split": 40960, "classify": 32768, "judge": 65536}[service_role]
        if before_runtime["max_model_len"] == required_context:
            pass
        else:
            raise ValueError("Scoring service context window differs from the standard prompt preflight")
        result = _evaluate(request, config) if args.stage == "evaluation" else _split_or_classify(request, config, args.stage, target)
        after_runtime = _service_ready(config["services"][service_role])
        if before_runtime != after_runtime:
            raise ValueError("Scoring service instance changed during the stage")
        result["service_runtime"] = before_runtime
        result["request"] = _binding(args.request)
        if _source_identity(root) != config["source_files"]:
            raise ValueError("Standard CoCo source changed during scoring")
        atomic_write_json(target, result)
        return 0
    except Exception as exc:
        atomic_write_json(target.with_suffix(".failure.json"), _stage_failure(args.stage, exc))
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
