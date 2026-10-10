"""Read runtime acceptance/study progress without importing model packages."""
import argparse
import json
from pathlib import Path


def process_start_ticks(pid):
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, ValueError, TypeError):
        return None


def effective_status(value):
    result = dict(value)
    if result.get('status') == 'running':
        result['recorded_status'] = 'running'
        ticks = process_start_ticks(result.get('pid'))
        expected = result.get('process_start_ticks')
        if ticks is None or (expected is not None and ticks != expected):
            result.update(status='interrupted', reason='Recorded worker has exited; durable history retained')
    return result


def progress(root):
    result = {"root": str(root.resolve())}
    for name in ("acceptance.json", "study-state.json", "pilot-state.json", "supervisor-state.json", "call-budget.json"):
        path = root / name
        if path.exists():
            value = effective_status(json.loads(path.read_text()))
            result[name] = {key: item for key, item in value.items()
                            if key not in {"runtime", "evidence"}}
    searches = [root / "search/search.json", *sorted(root.glob("runs/*/search/search.json"))]
    result["searches"] = []
    for path in searches:
        if not path.exists():
            continue
        value = json.loads(path.read_text())
        policy_path = path.with_name("failure-policy.json")
        policy = json.loads(policy_path.read_text()) if policy_path.exists() else {}
        result["searches"].append({"path": str(path), "status": value["status"],
            "controller_status": policy.get("status"),
            "evaluation_epoch": policy.get("evaluation_epoch"),
            "pending_candidates": policy.get("pending_candidates", []),
            "repair_budget": policy.get("repair_budget"),
            "candidate_failure_budget": policy.get("candidate_failure_budget"),
            "complete_groups": [key for key, item in value["results"].items() if item.get("status") == "complete"],
            "attempts": {key: {k: v for k, v in item.items() if k in {"status", "static_score", "candidate_key"}}
                         for key, item in value["attempts"].items()},
            "champion": {k: v for k, v in (value.get("champion") or {}).items() if k in {"key", "static_score"}}})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(progress(args.root), ensure_ascii=False, indent=2))
