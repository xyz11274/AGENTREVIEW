#!/usr/bin/env python3
"""Select max-num-seqs from nine measured Core v1 capacity samples."""

import argparse
import json
from pathlib import Path

from memory_evolution.capacity_gate import CapacitySample, select_qwen_capacity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    samples = []
    for path in args.samples:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        fields = CapacitySample.__dataclass_fields__
        samples.append(CapacitySample(**{key: value[key] for key in fields}))
    decision = select_qwen_capacity(samples)
    output = {
        "selected_max_num_seqs": decision.selected_max_num_seqs,
        "median_throughput": decision.median_throughput,
        "median_p95_latency_seconds": decision.median_p95_latency_seconds,
        "min_observed_free_mib": decision.min_observed_free_mib,
        "rejected": {str(key): list(value) for key, value in decision.rejected.items()},
    }
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
