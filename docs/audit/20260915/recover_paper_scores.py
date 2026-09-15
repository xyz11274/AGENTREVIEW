#!/usr/bin/env python3
"""Recover historical Paper. scores from existing CoCo caches without model calls.

Run with the historical evaluation environment:
  PYTHONDONTWRITEBYTECODE=1 \
    /data0/xyj/shengao/AgentReview_0831/artifacts/research_v2/runtime/coco_eval_env_v1/bin/python3.10 \
    recover_paper_scores.py

This is a retrospective diagnostic. It reconstructs exact historical requests,
reads the corresponding cache files, verifies their messages, and checks the
recovered aggregate against the saved evaluation. It does not run an experiment
or make the generation settings comparable. No model client is instantiated.
The sole output is paired_paper_scores.json beside this script by default.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/data0/xyj/shengao"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("paired_paper_scores.json"))
    args = parser.parse_args()
    root = args.workspace.resolve()
    base = root / "AgentReview_0831/artifacts/research_v2"
    coco = root / "CoCoReviewBench"
    native = base / "qwen38_27b_native_coco_common902_v1"
    sys.path[:0] = [str(coco / "src/evaluation"), str(coco)]
    from src.evaluation import _4_evaluation as evaluation

    manifest_path = base / "preliminary_common902/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    native_manifest_path = native / "manifest.json"
    native_raw = native_manifest_path.read_text()
    # The historical manifest has trailing characters. Record the defect while
    # recovering its original first JSON object; never modify the source file.
    native_manifest, end = json.JSONDecoder().raw_decode(native_raw)
    native_trailing = native_raw[end:].strip()
    benchmark_files = evaluation.resolve_benchmark_files([str(coco / "benchmark/benchmark_*_20*.jsonl")])
    benchmark = evaluation.load_benchmark_records(None, benchmark_files)
    human = evaluation.load_human_review_data(benchmark, benchmark_files)
    judge_prompt = evaluation.get_judge_system_prompt()
    arms = {}
    per_paper = {}
    bindings = {}
    ports = {"memory_meta": 18101, "memory_concat": 18102,
             "no_memory_meta": 18103, "no_memory_concat": 18104, "native": 18201}

    for arm, port in ports.items():
        is_native = arm == "native"
        directory = native if is_native else base / "preliminary_common902" / arm
        input_path = directory / ("standard/classify_successful.jsonl" if is_native else "candidate_classified_common902.jsonl")
        summary_path = directory / ("formal/evaluation_primary_native_successful.json" if is_native else "evaluation_common902.json")
        input_sha = file_hash(input_path)
        if is_native:
            spec = {"protocol": native_manifest["protocol"], "input_sha256": input_sha,
                    "cohort_sha256": native_manifest["comparison_cohort"]["common_ids_sha256"]}
        else:
            expected_sha = manifest["outputs"][arm]["sha256"]
            if input_sha != expected_sha:
                raise RuntimeError(f"Historical input hash differs for {arm}")
            spec = {"protocol": manifest["protocol"], "arm": arm, "input_sha256": expected_sha}
        spec.update({"judge_model": evaluation.JUDGE_MODEL,
                     "judge_temperature": evaluation.JUDGE_TEMPERATURE,
                     "judge_max_completion_tokens": evaluation.JUDGE_MAX_COMPLETION_TOKENS,
                     "judge_effort": evaluation.JUDGE_EFFORT,
                     "category_level_eval": not is_native, "calc_old_metrics": True,
                     "paper_level_eval": True, "require_complete": False})
        base_url = f"http://127.0.0.1:{port}/v1"
        identity = {
            "scoring_spec_id": value_hash(spec), "sealed_judge_runtime": None,
            "judge": {
                "base_url": base_url, "model": evaluation.JUDGE_MODEL,
                "max_in_flight_client_requests": evaluation.JUDGE_MAX_WORKERS,
                "effort": evaluation.JUDGE_EFFORT,
                "temperature": evaluation.JUDGE_TEMPERATURE,
                "max_completion_tokens": evaluation.JUDGE_MAX_COMPLETION_TOKENS,
                "context_window_tokens": evaluation.JUDGE_CONTEXT_WINDOW_TOKENS,
                "prompt_preflight": {
                    "algorithm": evaluation.JUDGE_PROMPT_PREFLIGHT_ALGORITHM,
                    "max_model_len": evaluation.JUDGE_CONTEXT_WINDOW_TOKENS,
                    "max_completion_tokens": evaluation.JUDGE_MAX_COMPLETION_TOKENS,
                    "enable_thinking": evaluation.JUDGE_ENABLE_THINKING,
                },
            },
        }
        version = "qwen38-native-coco-common902-primary-judge-v1" if is_native else "preliminary-common902-judge-v1"
        scores = {}
        counters = Counter()
        missing_ids = []
        invalid_ids = []
        cache_directories = [directory / ".llm_cache", root / ".llm_cache", coco / ".llm_cache"]
        for paper_id, test_text in evaluation.load_ai_paper_level_data(str(input_path)).items():
            messages = [
                {"role": "system", "content": judge_prompt},
                {"role": "user", "content": evaluation.build_judge_user_prompt(human[paper_id]["paper_correct"], test_text)},
            ]
            request = {"cache_version": version, "cache_identity": identity,
                       "base_url": base_url, "model": evaluation.JUDGE_MODEL,
                       "messages": messages, "temperature": evaluation.JUDGE_TEMPERATURE,
                       "max_completion_tokens": evaluation.JUDGE_MAX_COMPLETION_TOKENS,
                       "enable_thinking": evaluation.JUDGE_ENABLE_THINKING}
            key = value_hash(request)
            paths = [d / f"{key}.json" for d in cache_directories if (d / f"{key}.json").is_file()]
            if len(paths) == 0:
                missing_ids.append(paper_id)
                continue
            cache_path = paths[0]
            cached = json.loads(cache_path.read_text())
            if cached["cache_version"] != version or cached["new_msg_history"][:2] != messages:
                raise RuntimeError(f"Cache request identity differs for {arm}:{paper_id}")
            try:
                parsed = evaluation.parse_llm_json_response(cached["content"])
                judge = evaluation.calculate_scores_from_parsed(parsed)
                score = evaluation.compute_table_average_from_scores(judge)
                if score is None:
                    raise ValueError("Paper. score is absent")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                invalid_ids.append({"paper_id": paper_id, "error": type(error).__name__})
                continue
            scores[paper_id] = score
            counters[str(cache_path.parent)] += 1
            per_paper.setdefault(paper_id, {})[arm] = {
                "Paper.": score,
                "metrics": {name: judge[field] for name, field in evaluation.table_judge_metric_map.items()},
                "cache_path": str(cache_path), "cache_sha256": file_hash(cache_path),
                "request_sha256": key, "request_messages_match": True,
            }
        saved = json.loads(summary_path.read_text())["table_1_summary"]
        recovered_mean = statistics.mean(scores.values())
        expected_mean = saved["absolute_scores"]["Paper."]
        expected_count = saved["paper_counts"]["Paper."]
        matches = len(scores) == expected_count and abs(recovered_mean - expected_mean) <= 1e-10
        if matches is False:
            raise RuntimeError(f"Recovered aggregate differs for {arm}: {len(scores)}, {recovered_mean}")
        arms[arm] = {"scores": scores, "count": len(scores), "mean": recovered_mean,
                     "saved_count": expected_count, "saved_mean": expected_mean,
                     "saved_aggregate_matches": matches, "missing_cache_ids": sorted(missing_ids),
                     "invalid_cache_ids": invalid_ids, "cache_directories": dict(counters)}
        bindings[arm] = {"input": str(input_path), "input_sha256": input_sha,
                         "saved_summary": str(summary_path), "saved_summary_sha256": file_hash(summary_path),
                         "scoring_spec_id": identity["scoring_spec_id"], "cache_version": version}

    native_scores = arms["native"]["scores"]
    pairs = {}
    for arm, data in arms.items():
        if arm == "native":
            continue
        ids = sorted(set(data["scores"]) & set(native_scores))
        deltas = [native_scores[i] - data["scores"][i] for i in ids]
        pairs[arm] = {"count": len(ids), "paper_ids": ids,
                      "native_mean": statistics.mean(native_scores[i] for i in ids),
                      "arm_mean": statistics.mean(data["scores"][i] for i in ids),
                      "native_minus_arm_mean": statistics.mean(deltas),
                      "paired_delta_sample_sd": statistics.stdev(deltas),
                      "native_higher": sum(d > 0 for d in deltas),
                      "equal": sum(d == 0 for d in deltas),
                      "native_lower": sum(d < 0 for d in deltas)}
    common_ids = sorted(set.intersection(*(set(data["scores"]) for data in arms.values())))
    source_paths = [Path(__file__).resolve(), Path(evaluation.__file__), coco / "src/utils.py",
                    coco / "src/evaluation/prompt/judge.txt", coco / "benchmark/benchmark_reviewer_selection.json",
                    manifest_path, native_manifest_path, *benchmark_files]
    report = {
        "protocol": "historical-coco-paper-cache-paired-diagnostic-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0, "historical_artifacts_modified": False,
        "limitations": [
            "Retrospective common-success diagnostic; sample attrition remains.",
            "Generation paper context, prompts, thinking, temperature, and token budgets differ across systems.",
            "Native category-level CoCo judge evaluation was disabled in its historical run.",
            "Paper. uses classified non-N/A opinions and the existing same-family judge, not a blind human assessment.",
        ],
        "native_manifest_trailing_characters": native_trailing,
        "source_bindings": {str(path): file_hash(path) for path in source_paths},
        "arm_bindings": bindings,
        "arm_recovery": {arm: {k: v for k, v in data.items() if k != "scores"} for arm, data in arms.items()},
        "native_pairwise": pairs,
        "all_five_common": {"count": len(common_ids), "paper_ids": common_ids,
                            "means": {arm: statistics.mean(data["scores"][i] for i in common_ids) for arm, data in arms.items()}},
        "per_paper": {paper_id: per_paper[paper_id] for paper_id in sorted(per_paper)},
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "all_five_common": report["all_five_common"]["means"],
                      "count": len(common_ids), "all_saved_aggregates_match": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
