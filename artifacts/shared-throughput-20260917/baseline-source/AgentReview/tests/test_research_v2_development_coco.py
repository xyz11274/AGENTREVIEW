"""Standard CoCo boundary: complete cohorts, real formulas and finite attempts."""

from __future__ import annotations

import ast
import copy
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json, value_sha256, write_immutable_jsonl


def binding(path):
    return {"path": str(path), "sha256": file_sha256(path)}


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    ids = ["paper-a", "paper-b"]
    records = []
    for paper_id in ids:
        path = atomic_write_json(tmp_path / f"{paper_id}.json", {"paper_id": paper_id, "review_text": f"An anchored review for {paper_id}."})
        records.append({"paper_id": paper_id, **binding(path)})
    review = atomic_write_json(tmp_path / "reviews.json", {"protocol": coco.REVIEW_MANIFEST_PROTOCOL, "partition": "development", "paper_ids": ids, "records": records})
    benchmark = write_immutable_jsonl(tmp_path / "benchmark.jsonl", [{"id": p} for p in ids])
    human = write_immutable_jsonl(tmp_path / "human.jsonl", [{"id": p} for p in ids])
    selection = atomic_write_json(tmp_path / "selection.json", {p: "Reviewer 1" for p in ids})
    references = atomic_write_json(tmp_path / "references.json", {"protocol": coco.REFERENCE_MANIFEST_PROTOCOL, "partition": "development", "paper_ids": ids,
        "benchmark_files": [binding(benchmark)], "complete_human_classify_file": binding(human), "reviewer_selection_file": binding(selection)})
    config = atomic_write_json(tmp_path / "config.json", {"source_files": [], "max_stage_attempts": 3, "timeout_seconds": 1, "python_executable": sys.executable})
    monkeypatch.setattr(coco, "_validate_config", lambda config: None)
    return dict(review_manifest_path=review, reference_manifest_path=references, scoring_config_path=config,
                output_root=tmp_path / "scores", partition="development", run_id="run1", candidate_id="vector", evaluation_epoch=0,
                candidate_identity={"program_sha256": "a" * 64, "memory": {"QUAL": "b" * 64}})


def raw_results(ids, values=(2.0, 4.0)):
    papers = {}
    for paper_id, score in zip(ids, values, strict=True):
        metrics = {name: score for name in coco.COLUMNS}
        counts = dict.fromkeys(coco.COLUMNS, 1)
        metrics["Verify."], counts["Verify."] = None, 0
        papers[paper_id] = {"table_1_summary": {"absolute_scores": metrics, "paper_counts": counts},
            "paper_judge_scores": {"success": True, "alignment": score, "verifiability": "X", "average": score + 1}, "category_judge_scores": {}}
    rows = [{"status": "complete", "metrics": value["table_1_summary"]["absolute_scores"], "metric_counts": value["table_1_summary"]["paper_counts"]} for value in papers.values()]
    return {"paper_ids": ids, "papers": papers, "table_1_summary": coco.aggregate_paper_scores(rows)}


def fake_standard(monkeypatch, experiment, *, mutate=None):
    calls = []
    def run_stage(*, stage, request_path, output, config):
        calls.append(stage)
        raw = raw_results(["paper-a", "paper-b"])
        raw["request"] = binding(request_path)
        raw["integrity"] = fixture_integrity(output, stage, ["paper-a", "paper-b"])
        if mutate:
            mutate(raw)
        return atomic_write_json(output / f"{stage}.json", raw)
    monkeypatch.setattr(coco, "_run_stage", run_stage)
    return calls


def fixture_integrity(output, stage, ids):
    task = atomic_write_json(output / f"{stage}-task.json", {"status": "complete",
        "completion": {"choices": [{"finish_reason": "stop", "message": {"content": "fixture response"}}]},
        "tokens": {"input_tokens": 10, "output_budget": 10, "max_model_len": 100}})
    fields = {"split": {"opinion_count": 1, "raw_coverage": [1.0]},
              "classify": {"opinion_count": 1, "classified_count": 1},
              "evaluation": {"required_categories": [], "completed_categories": [], "paper_judge_complete": True}}[stage]
    return binding(atomic_write_json(output / f"{stage}-integrity.json", {
        "stage": stage, "status": "complete", "unhandled_failures": 0,
        "checks": [{"paper_id": pid, **fields} for pid in ids], "tasks": [binding(task)]}))


def test_complete_receipt_uses_paper_table_and_preserves_x(experiment, monkeypatch):
    calls = fake_standard(monkeypatch, experiment)
    receipt = coco.evaluate_final_reviews(**experiment)
    assert receipt["mean_score"] == 3.0
    assert receipt["aggregate"]["paper_counts"]["Paper."] == 2
    assert receipt["aggregate"]["paper_counts"]["Verify."] == 0
    assert receipt["rows"][0]["paper_judge_scores"]["verifiability"] == "X"
    assert receipt["rows"][0]["applicability"]["Verify."] == "not_applicable"
    coco.validate_assessment_receipt(Path(experiment["output_root"]) / "assessment.json", expected_epoch=0, expected_partition="development", candidate_identity=experiment["candidate_identity"])
    assert coco.evaluate_final_reviews(**experiment) == receipt
    assert calls == ["split", "classify", "evaluation"]


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["papers"].pop("paper-b"),
    lambda raw: raw["papers"]["paper-a"]["table_1_summary"]["absolute_scores"].update({"Paper.": float("nan")}),
    lambda raw: raw["papers"]["paper-a"]["table_1_summary"]["paper_counts"].update({"Paper.": 0}),
    lambda raw: raw["table_1_summary"]["absolute_scores"].update({"Paper.": 5.0}),
])
def test_incomplete_or_invalid_scores_pause_with_null_values(experiment, monkeypatch, mutate):
    fake_standard(monkeypatch, experiment, mutate=mutate)
    with pytest.raises(coco.DevelopmentCoCoFailure):
        coco.evaluate_final_reviews(**experiment)
    receipt = read_json(Path(experiment["output_root"]) / "assessment.json")
    assert receipt["status"] == "paused"
    assert receipt["mean_score"] is None
    assert all(row["metrics"]["Paper."] is None and row["applicability"]["Paper."] == "unknown" for row in receipt["rows"])
    with pytest.raises(ValueError):
        coco.validate_assessment_receipt(receipt)


def test_modified_source_review_invalidates_complete_receipt(experiment, monkeypatch):
    fake_standard(monkeypatch, experiment)
    receipt = coco.evaluate_final_reviews(**experiment)
    Path(receipt["rows"][0]["review"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        coco.validate_assessment_receipt(receipt)


def test_rehashed_fabricated_score_is_rejected_by_raw_binding(experiment, monkeypatch):
    fake_standard(monkeypatch, experiment)
    receipt = coco.evaluate_final_reviews(**experiment)
    row = receipt["rows"][0]
    row["metrics"]["Paper."] = 5.0
    row_path = Path(receipt["papers"][0]["path"])
    atomic_write_json(row_path, row, immutable=False)
    receipt["papers"][0].update(binding(row_path))
    receipt["aggregate"] = coco.aggregate_paper_scores(receipt["rows"])
    receipt["mean_score"] = receipt["aggregate"]["absolute_scores"]["Paper."]
    receipt["receipt_id"] = value_sha256({key: value for key, value in receipt.items() if key != "receipt_id"})
    with pytest.raises(ValueError, match="bound standard CoCo output"):
        coco.validate_assessment_receipt(receipt)


@pytest.mark.parametrize("change", ["missing_review", "duplicate", "reference_partition", "test_purpose"])
def test_bad_partition_or_manifest_stops_before_any_scoring(experiment, monkeypatch, change):
    calls = fake_standard(monkeypatch, experiment)
    if change == "test_purpose":
        experiment["partition"] = "sealed_test"
    elif change == "reference_partition":
        value = read_json(experiment["reference_manifest_path"])
        value["partition"] = "sealed_test"
        atomic_write_json(experiment["reference_manifest_path"], value, immutable=False)
    else:
        value = read_json(experiment["review_manifest_path"])
        if change == "missing_review":
            value["records"].pop()
        else:
            value["records"][1] = value["records"][0]
        atomic_write_json(experiment["review_manifest_path"], value, immutable=False)
    with pytest.raises(coco.DevelopmentCoCoFailure):
        coco.evaluate_final_reviews(**experiment)
    assert calls == []


def test_failure_resume_spends_zero_new_stage_calls(experiment, monkeypatch):
    calls = []
    def failed(**kwargs):
        calls.append(kwargs["stage"])
        raise coco.DevelopmentCoCoFailure({"stage": "split", "category": "shared_evaluation", "message": "Official split failed"})
    monkeypatch.setattr(coco, "_run_stage", failed)
    for _ in range(2):
        with pytest.raises(coco.DevelopmentCoCoFailure):
            coco.evaluate_final_reviews(**experiment)
    assert calls == ["split"]


@pytest.mark.parametrize("failure_text,expected_calls", [("Official classification failed after three rounds", 1), ("Connection refused", 2)])
def test_persisted_stage_attempt_budget_stops_deterministic_retries(tmp_path, monkeypatch, failure_text, expected_calls):
    request = atomic_write_json(tmp_path / "request.json", {"request": "one"})
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(failure_text.encode())
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(coco.subprocess, "run", process)
    kwargs = dict(stage="classify", request_path=request, output=tmp_path,
                  config={"max_stage_attempts": 3, "timeout_seconds": 1, "python_executable": sys.executable})
    for _ in range(2):
        with pytest.raises(coco.DevelopmentCoCoFailure):
            coco._run_stage(**kwargs)
    assert len(calls) == expected_calls
    assert len(read_json(tmp_path / "stages/classify/state.json")["attempts"]) == expected_calls


def standard_pure_functions():
    """Compile the exact benchmark pure functions without importing model stacks."""
    root = Path(__file__).resolve().parents[3] / "CoCoReviewBench"
    if root.is_dir() is False:
        root = Path(__file__).resolve().parents[3] / "CoCoReviewBench"
    source = ast.parse((root / "src/evaluation/_4_evaluation.py").read_text())
    names = {"calculate_scores_from_parsed", "compute_table_average_from_scores", "is_numeric_score", "summarize_full_judge_results"}
    namespace = {"Any": Any, "Dict": Dict, "List": List, "Tuple": Tuple, "Optional": Optional,
        "np": SimpleNamespace(integer=int, floating=float),
        "table_judge_metric_map": dict(zip(("Correct.", "Thoro.", "Ground.", "Verify.", "Clarity"),
                                         ("alignment", "completeness", "grounding_specificity", "verifiability", "clarity")))}
    tree = ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
    exec(compile(tree, str(root / "src/evaluation/_4_evaluation.py"), "exec"), namespace)
    return namespace


def test_actual_standard_paper_formula_excludes_actionability_and_keeps_x():
    standard = standard_pure_functions()
    raw = {"claim_extraction": "claim", "actionability": 1, "grounding_specificity": 5, "verifiability": 5,
           "alignment": 5, "completeness": 5, "clarity": 5}
    scored = standard["calculate_scores_from_parsed"](raw)
    assert scored["average"] == 26 / 6
    assert standard["compute_table_average_from_scores"](scored) == 5.0
    raw.update(claim_extraction="normal", actionability="X", verifiability="X")
    scored = standard["calculate_scores_from_parsed"](raw)
    aggregate, counts = standard["summarize_full_judge_results"]({"paper": {"judge_scores": {**scored, "success": True}}})
    assert aggregate["Paper."] == 5.0 and counts["Paper."] == 1
    assert aggregate["Verify."] is None and counts["Verify."] == 0


def test_standard_formula_and_adapter_aggregation_match_with_mixed_applicability():
    standard = standard_pure_functions()
    first = {"claim_extraction": "claim", "actionability": 1, "grounding_specificity": 2, "verifiability": 4,
             "alignment": 2, "completeness": 2, "clarity": 2}
    second = {**first, "claim_extraction": "normal", "actionability": "X", "verifiability": "X", "alignment": 4}
    scores = [standard["calculate_scores_from_parsed"](item) for item in (first, second)]
    full, counts = standard["summarize_full_judge_results"]({str(i): {"judge_scores": {**item, "success": True}} for i, item in enumerate(scores)})
    rows = []
    for item in scores:
        metrics, effective = dict.fromkeys(coco.COLUMNS), dict.fromkeys(coco.COLUMNS, 0)
        metrics["Paper."], effective["Paper."] = standard["compute_table_average_from_scores"](item), 1
        rows.append({"status": "complete", "metrics": metrics, "metric_counts": effective})
    assert math.isclose(coco.aggregate_paper_scores(rows)["absolute_scores"]["Paper."], full["Paper."])
    assert counts["Paper."] == 2


def test_worker_calls_actual_standard_reference_filter_judge_and_table(tmp_path, monkeypatch):
    import json
    from agentreview.research_v2.development_coco_worker import _load_standard, evaluate_loaded_standard
    root = Path(__file__).resolve().parents[3] / "CoCoReviewBench"
    evaluation = _load_standard(root, "_4_evaluation")
    original_selection = evaluation.benchmark_reviewer_selection_file
    benchmark_row = {"id": "paper", "sentence_texts": ["Held out reviewer opinion.", "The reference accurately explains the mathematical claim."],
        "reviews": [[{"role": "Reviewer 1", "sentence_ids": {"value": {"review": [0]}}}],
                    [{"role": "Reviewer 2", "sentence_ids": {"value": {"review": [1]}}}]],
        "opinions": [{"category": ["QUAL-MTH"], "data": [{"role": "Reviewer 2", "sentence_ids": [1]}]}],
        "opinion_groups": [[0]], "conflicts_validation": ["correct"], "rebuttal_validation": ["correct"]}
    benchmark = write_immutable_jsonl(tmp_path / "benchmark.jsonl", [benchmark_row])
    human = write_immutable_jsonl(tmp_path / "human.jsonl", [{"id": "paper", "category": [["QUAL-MTH"]]}])
    selection = atomic_write_json(tmp_path / "selection.json", {"paper": "Reviewer 1"})
    text = "The generated review explains a mathematical property accurately."
    classified = write_immutable_jsonl(tmp_path / "classified.jsonl", [{"id": "paper", "text": text, "sentence_texts": [text], "opinions": [[0]], "category": [["QUAL-MTH"]]}])
    refs = {"benchmark_files": [binding(benchmark)], "complete_human_classify_file": binding(human), "reviewer_selection_file": binding(selection)}
    raw_judge = {"claim_extraction": "claim", "actionability": 1, "grounding_specificity": 5,
                 "alignment": 5, "completeness": 5, "verifiability": 5, "clarity": 5}
    class Judge:
        base_url = "unit-test"
        def count_chat_tokens(self, messages, **kwargs):
            assert messages[0]["content"] == evaluation.get_judge_system_prompt()
            return 100, 65536
        def submit_task(self, prompt, **kwargs):
            assert kwargs["temperature"] == 0.0
            assert kwargs["reasoning_effort"] == "medium"
            assert "max_completion_tokens" not in kwargs
            assert "The reference accurately explains" in prompt
            return prompt
        def get_result(self, key):
            return {"status": "done", "content": json.dumps(raw_judge)}
    def old_metrics(hyps, refs):
        assert hyps == [text]
        assert refs == [["## Review\nThe reference accurately explains the mathematical claim."]]
        values = {"bleu": 0.1, "rougeL": 0.2, "bertscore_f1": 0.3}
        return values, {key: [value] for key, value in values.items()}
    monkeypatch.setattr(evaluation, "compute_full_text_metrics", old_metrics)
    try:
        result = evaluate_loaded_standard(evaluation=evaluation, request={"paper_ids": ["paper"]},
            config={}, references=refs, input_file=classified, judge_client=Judge())
    finally:
        evaluation.benchmark_reviewer_selection_file = original_selection
    assert result["table_1_summary"]["absolute_scores"]["Paper."] == 5.0
    assert result["papers"]["paper"]["paper_judge_scores"]["average"] == 26 / 6
    assert result["table_1_summary"]["absolute_scores"]["Complete."] == 100.0
    assert result["table_1_summary"]["absolute_scores"]["BLEU"] == 10.0
    assert result["table_1_summary"]["absolute_scores"]["BERT."] == 30.0
    assert result["table_1_summary"]["paper_counts"]["BERT."] == 1
    assert result["raw_table_1_summary"]["absolute_scores"]["BERT-F1"] == 30.0
    assert result["raw_table_1_summary"]["absolute_scores"]["BERT."] is None
    assert set(result["table_1_summary"]["absolute_scores"]) == set(coco.COLUMNS)


def test_model_digest_detects_equal_length_weight_changes(tmp_path):
    from agentreview.research_v2 import development_coco as module
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"original")
    first = module._model_identity(tmp_path)
    weights.write_bytes(b"modified")
    assert module._model_identity(tmp_path) != first
