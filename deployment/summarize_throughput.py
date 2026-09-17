"""Summarize observed work and queue telemetry, with explicit measurement scope."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def summarize(root):
    root = Path(root)
    status = json.loads((root / "status.json").read_text())
    samples = lines(root / "metrics.jsonl")
    events = lines(root / "events.jsonl") + lines(root / "worker-events.jsonl")
    stages = defaultdict(list)
    for event in events:
        if event.get("event") == "end":
            stages[event["stage"]].append(event)
    stage_summary = {name: {"completed": sum(e.get("status")=="complete" for e in values),
        "failed": sum(e.get("status")=="failed" for e in values),
        "sum_seconds": sum(e["seconds"] for e in values), "max_seconds": max(e["seconds"] for e in values),
        "durations": [e["seconds"] for e in values]} for name, values in stages.items()}
    services = {}
    for port in ("18001", "13099", "13098"):
        parsed = []
        for sample in samples:
            raw = sample["services"].get(port)
            if not isinstance(raw, list):
                continue
            row = {}
            for line in raw:
                metric, value = line.rsplit(" ", 1)
                name = metric.split("{", 1)[0].removeprefix("vllm:")
                if "request_success" in name:
                    name += ":" + metric.split('finished_reason="')[1].split('"')[0]
                row[name] = float(value)
            parsed.append((sample["time"], row))
        if not parsed:
            services[port] = {"observed": False}
            continue
        duration = parsed[-1][0]-parsed[0][0]
        weights = [(min(5, b[0]-a[0]), a[1]) for a,b in zip(parsed, parsed[1:])]
        deltas = {name: parsed[-1][1].get(name,0)-parsed[0][1].get(name,0) for name in parsed[0][1] if "total" in name}
        query, hits = deltas.get("prefix_cache_queries_total",0), deltas.get("prefix_cache_hits_total",0)
        services[port] = {"observed": True, "sample_count": len(parsed), "seconds": duration,
            "running_max": max(row.get("num_requests_running",0) for _,row in parsed),
            "waiting_max": max(row.get("num_requests_waiting",0) for _,row in parsed),
            "kv_cache_peak_fraction": max(row.get("kv_cache_usage_perc",0) for _,row in parsed),
            "running_seconds": sum(dt for dt,row in weights if row.get("num_requests_running",0)>0),
            "waiting_seconds": sum(dt for dt,row in weights if row.get("num_requests_waiting",0)>0),
            "prefix_cache_token_hit_fraction": hits/query if query else None, "counter_deltas": deltas}
    admission = [event for event in events if event.get("event")=="admitted"]
    summary = {"status": status, "stages": stage_summary, "services": services,
        "admission_wait_seconds_sum": sum(e["wait_seconds"] for e in admission),
        "admission_wait_seconds_max": max((e["wait_seconds"] for e in admission), default=0),
        "embedding_actual_loads": sum(e.get("actual_load",False) for e in events if e.get("event")=="start" and e.get("stage")=="embedding_load_if_needed"),
        "embedding_uncached_texts": sum(e.get("texts",0) for e in events if e.get("event")=="start" and e.get("stage")=="embedding_uncached_encode"),
        "notes": ["Stage sums include concurrent/nested work and must not be added to infer wall time.",
                  "Gauge occupancy measures service activity; completed full-flow reviews require complete validated assessment receipts.",
                  "Diagnostic Judge replays are excluded from effective full-flow review throughput."]}
    (root / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    args = parser.parse_args()
    print(json.dumps(summarize(args.root), indent=2))
