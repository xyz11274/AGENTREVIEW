"""Report actual program diffs and validated, paired scores for every attempt."""
from __future__ import annotations

import csv
import difflib
import json
import math
from pathlib import Path

from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound


def scores_for(state, key):
    result = state["results"].get(key, {})
    if result.get("status") != "complete":
        return None
    recipe = state["recipes"][key]
    receipt = validate_assessment_receipt(
        bound(result["assessment"]), expected_paper_ids=recipe["paper_ids"],
        candidate_identity=result["identity"], expected_partition=recipe["split"],
        expected_epoch=result["evaluation_epoch"],
    )
    if (receipt["candidate_id"] != recipe["key"]
            or result["identity"]["candidate"] != recipe["candidate"]):
        raise ValueError("Round score belongs to a different candidate program")
    metrics = {item["paper_id"]: read_json(bound(item))["metrics"] for item in receipt["papers"]}
    scores = {pid: values["Paper."] for pid, values in metrics.items()}
    mean = sum(scores.values()) / len(scores)
    if not math.isclose(mean, result["static_score"], abs_tol=1e-10):
        raise ValueError(f"{key}: checkpoint score differs from complete CoCo receipts")
    return {"mean": mean, "per_paper": scores, "assessment": result["assessment"],
            "evaluation_epoch": result["evaluation_epoch"], "all_metrics_per_paper": metrics}


def paired_delta(candidate, parent):
    if candidate is None or parent is None:
        return None
    if (set(candidate["per_paper"]) != set(parent["per_paper"])
            or candidate["evaluation_epoch"] != parent["evaluation_epoch"]):
        raise ValueError("Round comparison requires the same cohort and scoring epoch")
    values = {pid: value - parent["per_paper"][pid]
              for pid, value in candidate["per_paper"].items()}
    return {"mean": sum(values.values()) / len(values), "per_paper": values,
            "wins": sum(v > 1e-12 for v in values.values()),
            "ties": sum(abs(v) <= 1e-12 for v in values.values()),
            "losses": sum(v < -1e-12 for v in values.values())}


def mutation_artifacts(candidate):
    """Resolve the proposed source from the reflection that produced this candidate."""
    failure = candidate.get("proposal_failure")
    reflection = failure.get("reflection") if failure else candidate.get("reflection")
    value = {"candidate_program": candidate["program"],
             "candidate_program_status": "parent_retained" if failure else "validated",
             "reflection": reflection, "reflection_responses": [], "validation_attempts": []}
    if reflection:
        trace = read_json(bound(reflection))
        value["reflection_responses"] = [binding(bound(call)) for call in trace["calls"]]
        value["validation_attempts"] = [event for event in trace["events"] if "program" in event]
        for event in value["validation_attempts"]:
            bound(event["program"])
        if failure and value["validation_attempts"]:
            value["candidate_program"] = value["validation_attempts"][-1]["program"]
            value["candidate_program_status"] = "validation_failed"
    return value


def report(root):
    root = Path(root)
    output = root / "report"
    output.mkdir(parents=True, exist_ok=True)
    protocol = read_json(root / "protocol.frozen.json")
    state_path = root / "search/search.json"
    state = read_json(state_path) if state_path.exists() else {
        "status": "not_started", "recipes": {}, "results": {}, "attempts": {}, "pool": []}
    score_cache = {}

    def score(key):
        if key not in score_cache:
            score_cache[key] = scores_for(state, key)
        return score_cache[key]

    baselines = {key: score(key) for key, recipe in state["recipes"].items()
                 if recipe["kind"] in {"seed", "no_memory", "coco_native"}}
    initial = [s["mean"] for key, s in baselines.items() if key.startswith("seed-") and s]
    initial_best = max(initial) if initial else None
    best = initial_best
    final_path = root / "final/final-test.json"
    test_state = read_json(final_path) if final_path.exists() else {"recipes": {}, "results": {}}
    trajectory_path = root / "round-test/final-rounds.json"
    if trajectory_path.exists():
        trajectory = read_json(trajectory_path)
        test_state["recipes"].update(trajectory["recipes"])
        test_state["results"].update(trajectory["results"])
    test_scores = {key: scores_for(test_state, key) for key in test_state["results"]}
    rounds = []
    for ordinal in range(1, protocol["evolution"]["attempts"] + 1):
        attempt = state["attempts"].get(str(ordinal), {})
        parent_key, candidate_key = attempt.get("parent_key"), attempt.get("candidate_key")
        parent = score(parent_key) if parent_key else None
        candidate = score(candidate_key) if candidate_key else None
        row = {"round": ordinal, "status": attempt.get("status", "not_started"),
               "parent": parent_key, "candidate": candidate_key,
               "parent_static": parent, "candidate_static": candidate,
               "delta_vs_parent": paired_delta(candidate, parent),
               "previous_best": best, "became_champion": False,
               "change_claim": attempt.get("change"),
               "rotating_paper_ids": attempt.get("rotating_paper_ids", []),
               "reflection_case_ids": attempt.get("reflection_case_ids", []),
               "rotating_parent_feedback": score(attempt["feedback_key"]) if attempt.get("feedback_key") else None}
        row.update(parent_test=test_scores.get(parent_key), candidate_test=test_scores.get(candidate_key),
                   test_delta_vs_parent=paired_delta(test_scores.get(candidate_key), test_scores.get(parent_key)))
        if candidate:
            row["became_champion"] = best is None or candidate["mean"] > best
            row["delta_vs_initial_best"] = candidate["mean"] - initial_best if initial_best is not None else None
            best = max(best, candidate["mean"]) if best is not None else candidate["mean"]
        row["best_after_round"] = best
        row["admitted_to_pool"] = candidate_key in state["pool"] if candidate_key else False
        if candidate_key:
            candidate_recipe = state["recipes"][candidate_key]
            row["failure"] = state["results"].get(candidate_key, {}).get("failure")
            row["revision"] = candidate_recipe["candidate"].get("revision", 0)
            row["repair_artifacts"] = [binding(path) for path in sorted(
                (root / "search/repairs").glob(f"e*/{candidate_key}/r*/program.py"))]
            old = bound(state["recipes"][parent_key]["candidate"]["program"])
            row.update(mutation_artifacts(candidate_recipe["candidate"]))
            new = bound(row["candidate_program"])
            old_lines, new_lines = old.read_text().splitlines(True), new.read_text().splitlines(True)
            diff = "".join(difflib.unified_diff(old_lines, new_lines,
                         fromfile=str(old), tofile=str(new)))
            path = output / "round-diffs" / f"round-{ordinal:02d}.diff"
            path.parent.mkdir(exist_ok=True)
            path.write_text(diff, encoding="utf-8")
            row.update(program_diff=binding(path), parent_program=binding(old),
                       candidate_program=binding(new), identical_program=old_lines == new_lines,
                       added_lines=sum(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines()),
                       removed_lines=sum(line.startswith("-") and not line.startswith("---") for line in diff.splitlines()))
        rounds.append(row)
    final_path = output / "paired-summary.json"
    final = read_json(final_path) if final_path.exists() else None
    value = {"status": state["status"], "planned_rounds": len(rounds),
             "completed_rounds": sum(row["status"] in {"complete", "candidate_failed"} for row in rounds),
             "protocol": binding(root / "protocol.frozen.json"), "baselines": baselines,
             "initial_best_static": initial_best, "rounds": rounds,
             "final_comparisons": final["comparisons"] if final else None,
             "test_feedback_to_search": False,
             "interpretation": "One search, three static and three held-out papers: exploratory only; no guarantee of improvement."}
    atomic_write_json(output / "evolution-rounds.json", value, immutable=False)
    columns = ["round", "status", "parent", "candidate", "parent_score", "candidate_score",
               "delta_vs_parent", "best_after_round", "became_champion", "admitted_to_pool",
               "added_lines", "removed_lines", "change_claim", "parent_test", "candidate_test", "test_delta_vs_parent"]
    with (output / "evolution-rounds.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rounds:
            writer.writerow({**{key: row.get(key) for key in columns},
                "parent_score": row["parent_static"]["mean"] if row["parent_static"] else None,
                "candidate_score": row["candidate_static"]["mean"] if row["candidate_static"] else None,
                "delta_vs_parent": row["delta_vs_parent"]["mean"] if row["delta_vs_parent"] else None,
                "parent_test": row["parent_test"]["mean"] if row["parent_test"] else None,
                "candidate_test": row["candidate_test"]["mean"] if row["candidate_test"] else None,
                "test_delta_vs_parent": row["test_delta_vs_parent"]["mean"] if row["test_delta_vs_parent"] else None})
    def number(value):
        return "待完成" if value is None else f"{value:.4f}"
    lines = ["# 0930 小规模自进化逐轮报告", "", f"状态：{state['status']}；计划 {len(rounds)} 轮。",
        "", "固定评分集用于选择，轮换集只提供反思反馈；独立测试集在 Champion 冻结后打开。",
        "分数来自完整 CoCo 回执；失败和待执行项以状态展示。一次搜索、3 篇固定评分和3 篇测试支持探索性比较。",
        "", "## 初始对照", "", "| 实验组 | 固定评分 Paper. |", "|---|---:|"]
    lines += [f"| {key} | {number(s['mean'] if s else None)} |" for key, s in baselines.items()]
    lines += ["", "## 每轮变化和效果", "", "| 轮次 | 状态 | 父候选 | 父分数 | 新分数 | 相对父候选 | 本轮刷新最优 |", "|---:|---|---|---:|---:|---:|---|"]
    for row in rounds:
        parent_mean = row["parent_static"]["mean"] if row["parent_static"] else None
        candidate_mean = row["candidate_static"]["mean"] if row["candidate_static"] else None
        delta = row["delta_vs_parent"]["mean"] if row["delta_vs_parent"] else None
        lines.append(f"| {row['round']} | {row['status']} | {row['parent'] or '—'} | {number(parent_mean)} | {number(candidate_mean)} | {number(delta)} | {'是' if row['became_champion'] else '否' if candidate_mean is not None else '待完成'} |")
    for row in rounds:
        lines += ["", f"### 第 {row['round']} 轮", "", f"模型声明的改动：{row['change_claim'] or '尚未生成'}"]
        if row.get("program_diff"):
            lines += ["", f"实际改动：新增 {row['added_lines']} 行、删除 {row['removed_lines']} 行；[完整 diff](round-diffs/round-{row['round']:02d}.diff)。",
                      f"父程序：`{row['parent_program']['path']}`；本轮程序：`{row['candidate_program']['path']}`；程序状态：`{row['candidate_program_status']}`。"]
        if row.get("reflection"):
            lines += [f"反思与验证轨迹：[mstar-trace.json]({row['reflection']['path']})。"]
            lines += [f"模型输出 {i + 1}：[原始文本]({ref['path']})。"
                      for i, ref in enumerate(row["reflection_responses"])]
            lines += [f"程序验证 {i + 1}：[源码]({event['program']['path']})；结果：{event['error'] or '通过'}。"
                      for i, event in enumerate(row["validation_attempts"])]
        if row["delta_vs_parent"]:
            delta = row["delta_vs_parent"]
            lines += [f"固定集逐篇比较：{delta['wins']} 胜 / {delta['ties']} 平 / {delta['losses']} 负；差值：`{json.dumps(delta['per_paper'], ensure_ascii=False)}`。"]
        if row["test_delta_vs_parent"]:
            delta = row["test_delta_vs_parent"]
            lines += [f"搜索结束后独立测试：父候选 {row['parent_test']['mean']:.4f} → 新候选 {row['candidate_test']['mean']:.4f}；差值 {delta['mean']:+.4f}，{delta['wins']} 胜 / {delta['ties']} 平 / {delta['losses']} 负。"]
        if row.get("failure"):
            lines += [f"失败记录：`{json.dumps(row['failure'], ensure_ascii=False)}`"]
    lines += ["", "## 最终独立测试", ""]
    if final:
        lines += ["| Champion 对照 | 平均差值 | 胜/平/负 |", "|---|---:|---|"]
        lines += [f"| {key} | {row['mean_gain']:.4f} | {row['wins']}/{row['ties']}/{row['losses']} |" for key, row in final["comparisons"].items()]
    else:
        lines += ["独立测试待执行，完成后展示各系统的配对成绩。"]
    (output / "自进化逐轮报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return value
