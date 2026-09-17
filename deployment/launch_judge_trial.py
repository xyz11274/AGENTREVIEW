"""Launch one durable diagnostic after an optional owned service transition."""
import argparse
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("--transition")
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
root = repo / "artifacts/shared-throughput-20260917"
protocol = json.loads((root / "baseline-protocol.json").read_text())
scoring = json.loads(Path(protocol["scoring_config"]["path"]).read_text())
assessment = json.loads((root / "baseline/result.json").read_text())["assessment"]["path"]
cmd = ["/data/xyj/envs/coco-original/bin/python", "-u", str(repo / "deployment/judge_throughput_replay.py"),
       "--source", str(root / "paper2-source"), "--root", str(root / args.name), "--assessment", assessment,
       "--execution-profile", str(root / "execution-paper2.json")]
env = dict(os.environ, AGENTREVIEW_QWEN27B_MODE_LEASE_PATH=scoring["lease_path"], PYTHONDONTWRITEBYTECODE="1",
           TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="",
           NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
actual = cmd
if args.transition:
    wrapper = """import json,os,sys,time
from pathlib import Path
p=Path(sys.argv[1])
deadline=time.monotonic()+1000
while not p.exists():
    if time.monotonic()>deadline: raise RuntimeError('No completed service transition')
    time.sleep(2)
v=json.loads(p.read_text())
if v['status']!='ready': raise RuntimeError('Service transition failed; no diagnostic launched')
os.execv(sys.argv[2],sys.argv[2:])
"""
    actual = [cmd[0], "-u", "-c", wrapper, str(Path(args.transition).resolve()), *cmd]
with (root / f"{args.name}.log").open("xb") as log:
    child = subprocess.Popen(actual, env=env, cwd=repo, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
(root / f"{args.name}-launch.json").write_text(json.dumps({"pid": child.pid, "command": cmd,
    "transition": args.transition}, indent=2)+"\n")
print(child.pid)
