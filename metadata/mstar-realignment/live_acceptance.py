"""Real API mutation of a saved parent/feedback pair; never starts CoCo or a search."""
from pathlib import Path
import argparse
import difflib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AgentReview"))
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.shared_experiment import APISharedBackend, binding
from agentreview.research_v2.io import read_json, atomic_write_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--protocol', type=Path, default=ROOT / 'config/0930-pilot-mstar-native-v2/protocol.frozen.json')
parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/mstar-adaptation-0930-v2')
parser.add_argument('--summary', type=Path, default=ROOT / 'metadata/mstar-realignment/live-acceptance-v2.json')
args = parser.parse_args()
protocol = load_experiment_protocol(args.protocol)
output = args.output
backend = APISharedBackend(protocol=protocol, output_root=output)
state_path = ROOT / "artifacts/pilot-0930-coco-upstream/search/search.json"
state = read_json(state_path)
parent = state["recipes"]["seed-vector_search"]["candidate"]
attempt = state["attempts"]["2"]
feedback = {**state["results"]["feedback-0002-e0"],
            "reflection_case_ids": attempt["reflection_case_ids"],
            "reflection_selection": attempt["reflection_selection"]}
status = {"status": "running", "started_at": time.time(), "source_state": binding(state_path),
          "parent": parent["program"], "scoring_services_started": False, "search_started": False,
          "protocol": binding(protocol.path)}
path = args.summary
atomic_write_json(path, status, immutable=False)
try:
    result = backend.propose(parent=parent, feedback=feedback, history=state["attempts"], directory=output / "reflection")
    backend._candidate_check(result)
    before = Path(parent["program"]["path"]).read_text()
    after = Path(result["program"]["path"]).read_text()
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile="parent/program.py", tofile="child/program.py"))
    (output / "reflection/program.diff").write_text(diff)
    trace = read_json(result["reflection"]["path"])
    status.update(status="complete", proposal=result, real_api_calls=len(trace["calls"]),
                  native_patch_applied=any("*** Begin Patch" in Path(call["path"]).read_text() for call in trace["calls"]),
                  compiled_and_sandbox_validated=True, fixed_review_policy_preserved=True,
                  program_changed=before != after, diff=binding(output / "reflection/program.diff"),
                  fitness_evaluated=False)
except Exception as exc:
    status.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    raise
finally:
    status.update(finished_at=time.time(), elapsed_seconds=round(time.time()-status["started_at"],3))
    atomic_write_json(path,status,immutable=False)
    print({k:v for k,v in status.items() if k not in ('proposal','source_state','parent','protocol')},flush=True)
