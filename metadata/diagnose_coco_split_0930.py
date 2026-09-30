"""Offline replay of the paused CoCo Split; never sends a model request."""
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout, redirect_stderr
import argparse
import io
import json
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.development_coco_worker import _load_standard
from agentreview.research_v2.coco_integrity import audit_split
from agentreview.research_v2.io import read_json, read_jsonl, file_sha256


def diagnose(coco_root):
    root = REPO / ("artifacts/pilot-0930-model-capacity/search/assessments/e0/"
        "seed-llm_summarizer/r0/coco/parts/"
        "26f83a96501af9239d32dce1cee1dde4cde63558ef4ce1e5ecf769ac72ee90bd")
    request = read_json(root / "request.json")
    module = _load_standard(coco_root, "_2_split")
    tasks = {p.stem: read_json(p) for p in sorted((root / "cache/split").glob("*.audit.json"))}
    inputs = read_jsonl(root / "candidate.jsonl")
    recorded_output = read_jsonl(root / "stages/split/result-001.jsonl")

    class RecordedClient:
        def submit_task(self, text, *, system_prompt, presence_penalty, **kwargs):
            matches = [key for key, receipt in tasks.items()
                if receipt["payload"]["presence_penalty"] == presence_penalty
                and receipt["messages"][-1]["content"] == text
                and receipt["messages"][0]["content"] == system_prompt]
            assert len(matches) == 1, "Replay must resolve exactly one original request"
            return matches[0]

        def get_result(self, key):
            return {"content": tasks[key]["completion"]["choices"][0]["message"]["content"]}

    module.client = RecordedClient()
    with tempfile.TemporaryDirectory(prefix="coco-split-diagnosis-") as tmp:
        path = Path(tmp) / "upstream-output.jsonl"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            module.split_all(SimpleNamespace(input_jsonl=str(root / "candidate.jsonl"),
                output_jsonl=str(path), effort="medium"))
        replayed = read_jsonl(path)
    assert replayed == recorded_output, "Unchanged upstream replay must reproduce the saved final groups"
    texts, _, _ = module.build_prompt(inputs[0]["text"])
    evidence = []
    for key, task in tasks.items():
        choice = task["completion"]["choices"][0]
        parsed = module.parse_llm_output(choice["message"]["content"])
        coverage, missing = module.compute_coverage(parsed, len(texts))
        evidence.append({"audit_key": key, "raw_coverage": coverage, "missing_part_ids": missing,
            "finish_reason": choice["finish_reason"], "usage": task["completion"]["usage"],
            "presence_penalty": task["payload"]["presence_penalty"]})
    try:
        audit_split(module, inputs, replayed, tasks)
        rejection = None
    except ValueError as exc:
        rejection = str(exc)
    covered = {i for group in replayed[0]["opinions"] for i in group}
    result = {"status": "diagnosis_complete", "model_calls": 0,
        "paper_id": inputs[0]["id"], "input_parts": len(texts), "raw_responses": evidence,
        "upstream_replay_matches_saved_output": replayed == recorded_output,
        "upstream_final_covered_parts": len(covered),
        "upstream_final_missing_indices": sorted(set(range(len(texts))) - covered),
        "upstream_final_groups_containing_last_part": [g for g in replayed[0]["opinions"] if len(texts)-1 in g],
        "last_part_text": texts[-1], "host_rejection": rejection,
        "root_cause": "Host audit rejects the author's normal missing-part recovery even though the unchanged upstream result covers every input part.",
        "upstream_source": {"path": module.__file__, "sha256": file_sha256(module.__file__)},
        "host_audit_source": {"path": str(REPO / "AgentReview/agentreview/research_v2/coco_integrity.py"),
            "sha256": file_sha256(REPO / "AgentReview/agentreview/research_v2/coco_integrity.py")},
        "experiment_mutated": False, "fixed": rejection is None}
    assert len(texts) == 95 and all(row["missing_part_ids"] == [95] for row in evidence)
    assert covered == set(range(len(texts)))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-host-admission", action="store_true")
    parser.add_argument("--coco-root", type=Path,
                        default=REPO.parent / "CoCoReviewBench_official_2580687")
    args = parser.parse_args()
    result = diagnose(args.coco_root)
    target = REPO / "metadata/coco-split-replay-current.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "input_parts",
        "upstream_final_covered_parts", "upstream_replay_matches_saved_output", "host_rejection",
        "model_calls", "fixed")}, ensure_ascii=False))
    if args.require_host_admission and result["host_rejection"]:
        raise SystemExit(1)
