"""Launch a fresh two-paper pipeline under the selected measured service config."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound
from agentreview.research_v2.experiment_protocol import load_experiment_protocol


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--services", required=True)
    parser.add_argument("--diagnostic", required=True)
    args = parser.parse_args()
    root = REPO / "artifacts/shared-throughput-20260917"
    services = read_json(args.services)
    comparison = read_json(root / "judge-comparison.json")
    trial = next(row for row in comparison["trials"] if row["name"] == args.diagnostic)
    if trial.get("primary_gate") != "passed":
        raise ValueError("Choose a Paper.-stable service configuration before full-flow confirmation")
    protocol = read_json(root / "paper2-protocol.json")
    protocol.update(experiment_id="shared-throughput-paper2-final-20260917", services_config=binding(args.services),
                    fixed_input_control=binding(root / args.diagnostic / "result.json"))
    protocol["model"]["max_num_seqs"] = services["services"]["judge"]["max_num_seqs"]
    path = atomic_write_json(root / "paper2-final-protocol.json", protocol)
    load_experiment_protocol(path)
    scoring = read_json(bound(protocol["scoring_config"]))
    env = dict(os.environ, AGENTREVIEW_QWEN27B_MODE_LEASE_PATH=scoring["lease_path"],
        PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    cmd = ["/data/xyj/envs/coco-original/bin/python", "-u", str(REPO / "deployment/throughput_profile.py"),
           "--source", str(root / "paper2-source"), "--root", str(root / "paper2-final"),
           "--protocol", str(path), "--pilot", str(REPO / "artifacts/shared-acceptance-20260916/pilot/run/search.json")]
    with (root / "paper2-final.log").open("xb") as log:
        child = subprocess.Popen(cmd, env=env, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    atomic_write_json(root / "paper2-final-launch.json", {"pid": child.pid, "command": cmd})
    print(child.pid)


if __name__ == "__main__":
    main()
