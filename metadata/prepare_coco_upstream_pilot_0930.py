"""Freeze a source-preserving CoCo repair revision of the authorized small pilot."""
import copy
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AgentReview"))
from agentreview.research_v2.io import read_json, atomic_write_json, file_sha256, value_sha256
from agentreview.research_v2.shared_experiment import binding, runtime_identity
from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.experiment_protocol import load_experiment_protocol

old_root = ROOT / "artifacts/pilot-0930-model-capacity"
new_root = ROOT / "artifacts/pilot-0930-coco-upstream"
config_dir = ROOT / "config/0930-pilot-coco-upstream"
old_protocol = ROOT / "config/0930-pilot-model-capacity/protocol.frozen.json"
before = read_json(old_root / "repairs/coco-upstream/before-manifest.json")
search = read_json(old_root / "search/search.json")
policy = read_json(old_root / "search/failure-policy.json")
assert len(search["attempts"]) == 0
assert all(value["draws"] == 0 for value in search["rng_streams"].values())
assert policy["repair_budget"]["used"] == policy["candidate_failure_budget"]["used"] == 0
preserved = [path for path in before["files"] if "/config/" in path or "/artifacts/" in path]
assert all(file_sha256(path) == before["files"][path] for path in preserved)
changed_runtime = [row["path"] for row in runtime_identity()
                   if before["files"].get(row["path"]) != row["sha256"]]
assert {Path(p).name for p in changed_runtime} == {
    "coco_integrity.py", "development_coco.py", "development_coco_worker.py"}

document = copy.deepcopy(read_json(old_protocol))
expected_model = ModelConfig(**document["model"]).identity()
reused = []
for response_path in sorted((old_root / "search").rglob("response.json")):
    response = read_json(response_path)
    if "raw_model_output" not in response:
        continue
    directory = response_path.parent
    request = read_json(directory / "request.json")
    assert value_sha256(request) == directory.name == response["request_id"]
    assert request["model"] == expected_model
    assert response["response_sha256"] == value_sha256({k: v for k, v in response.items() if k != "response_sha256"})
    assert response["completion"]["choices"][0]["finish_reason"] == "stop"
    target = new_root / directory.relative_to(old_root)
    shutil.copytree(directory, target, dirs_exist_ok=True)
    reused.append({"source": binding(response_path), "target": binding(target / "response.json"),
                   "request": binding(target / "request.json")})
revision = {
    "created_at": time.time(), "reason": "Remove host semantic overrides and bind pristine latest official CoCo",
    "official_commit": "2580687ee5d4ad2ac4b9730afb8f1dfda22e40db",
    "official_source": binding(ROOT / "metadata/coco-official-source-verification.json"),
    "predecessor_protocol": binding(old_protocol), "predecessor_search": binding(old_root / "search/search.json"),
    "preserved_manifest": binding(old_root / "repairs/coco-upstream/before-manifest.json"),
    "changed_runtime": changed_runtime, "old_attempts": 0,
    "old_rng_draws": {k: v["draws"] for k, v in search["rng_streams"].items()},
    "old_failure_budgets_used": {k: policy[k]["used"] for k in ("repair_budget", "candidate_failure_budget")},
    "generation_reuse": "Only complete content-addressed API responses with identical requests and model identities; all manifests and memories rebuilt; scores recomputed",
    "embedding_reuse": "Same Qwen model, revision, full-text input and existing keyed cache; no model substitution",
    "reused_responses": reused, "imported_scores": 0, "imported_candidates": 0,
    "full_experiment_authorized": False,
}
revision_path = new_root / "revision.json"
atomic_write_json(revision_path, revision)
document.update(experiment_id="0930-pilot-coco-upstream-five-rounds-seed-0",
                scoring_config=binding(config_dir / "scoring.frozen.json"),
                coco_upstream_revision=binding(revision_path))
document["implementation"]["runtime"] = runtime_identity()
protocol_path = config_dir / "protocol.frozen.json"
atomic_write_json(protocol_path, document)
protocol = load_experiment_protocol(protocol_path)
atomic_write_json(new_root / "protocol.frozen.json", document)
print({"protocol": str(protocol_path), "protocol_sha256": protocol.protocol_sha256,
       "reused_complete_generation_responses": len(reused), "preserved_old_artifacts": len(preserved),
       "counts": document["evaluation_design"]["expected_counts"], "attempts": document["evolution"]["attempts"]})
