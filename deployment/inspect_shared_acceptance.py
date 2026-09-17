"""Revalidate the real acceptance gates and emit a compact evidence inventory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AgentReview"))
from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.program_checks import program_capability_manifest
from agentreview.research_v2.shared_experiment import binding, bound, runtime_identity

ROOT = Path(__file__).resolve().parents[1] / "artifacts/shared-acceptance-20260916"
ROLES = {"QUAL", "CLAR", "SIGN", "ORIG", "POL"}


def inspect_completion(response):
    value = read_json(response)
    request = read_json(response.parent / "request.json")
    assert len(value["completion"]["choices"]) == 1
    assert value["completion"]["choices"][0]["finish_reason"] == "stop"
    tokens, usage = value["tokens"], value["completion"]["usage"]
    assert tokens["input_tokens"] <= request["input_max_tokens"]
    assert tokens["input_tokens"] + request["max_tokens"] <= tokens["max_model_len"]
    assert usage["prompt_tokens"] + usage["completion_tokens"] <= tokens["max_model_len"]
    assert usage["completion_tokens"] <= request["max_tokens"]
    return {"response": binding(response), "component": request["component"],
            "tokens": tokens, "output_budget": request["max_tokens"], "usage": usage}


def inspect_assessment(reference):
    path = bound(reference)
    receipt = validate_assessment_receipt(path)
    assert receipt["status"] == "complete" and receipt["review_target"] == "final"
    stages = {}
    for name, ref in receipt["stage_integrity"].items():
        stage = read_json(bound(ref))
        assert stage["status"] == "complete" and stage["unhandled_failures"] == 0
        stages[name] = {"checks": stage["checks"], "complete_tasks": len(stage["tasks"])}
    identity = receipt["candidate_identity"]
    roles = {}
    memory_request_roots = set()
    for role, ref in identity.get("snapshots", {}).items():
        memory = read_json(bound(ref))
        memory_request_roots.add(bound(ref).parent / "requests")
        assert memory["role"] == role
        assert memory["embedding_identity"]["model"] == "Qwen/Qwen3-Embedding-8B"
        assert memory["embedding_identity"]["provider"] == "qwen3_embedding"
        records = [record for collection in memory["snapshot"]["collections"].values()
                   for record in collection["records"]]
        assert records and all(item["metadata"]["role"] == role for item in records)
        assert all(item["embedding_dimension"] == 4096 for item in records)
        reviews = [read_json(bound(ref)) for ref in identity["role_reviews"][role]]
        assert all(item["memory_snapshot_id"] == memory["snapshot_id"] for item in reviews)
        assert all(item["retrieval_provenance"]["memory"]["context"].strip() for item in reviews)
        roles[role] = {"snapshot": binding(bound(ref)), "records": len(records),
            "embedding_identity": memory["embedding_identity"], "embedding_dimension": 4096,
            "retrieved_context_chars": [len(item["retrieval_provenance"]["memory"]["context"])
                                        for item in reviews]}
    if roles:
        assert set(roles) == ROLES
        assert len({item["snapshot"]["path"] for item in roles.values()}) == 5
    requests = [inspect_completion(response) for response in sorted(
        path.parent.parent.glob("review-requests/*/response.json"))]
    memory_requests = [inspect_completion(response) for root in sorted(memory_request_roots)
                       for response in sorted(root.glob("*/response.json"))]
    return {"assessment": reference, "candidate_id": receipt["candidate_id"],
            "epoch": receipt["evaluation_epoch"], "Paper.": receipt["mean_score"],
            "stages": stages, "roles": roles, "generation_requests": requests,
            "memory_build_requests": memory_requests}


def main():
    gates = {name: read_json(ROOT / name) for name in
             ("gate-one-paper.json", "gate-child-freeze-development-final.json")}
    for gate in gates.values():
        assert gate["status"] == "complete" and gate["runtime"] == runtime_identity()
    state = read_json(ROOT / "acceptance/run/search.json")
    policy = read_json(ROOT / "acceptance/run/failure-policy.json")
    assert policy["status"] == "running" and not policy["pending_candidates"]
    epoch = policy["evaluation_epoch"]
    assessments = {}
    for key, recipe in state["recipes"].items():
        result = state["results"][key]
        if recipe["split"] == "static":
            assert result["evaluation_epoch"] == epoch and result["status"] == "complete"
            assert not result.get("role_diagnostics")
            assessments[key] = inspect_assessment(result["assessment"])
    final_gate = gates["gate-child-freeze-development-final.json"]
    frozen = read_json(bound(final_gate["frozen_system"]))
    assert frozen == state["winner"]
    assert frozen["freeze_id"] == value_sha256({k: v for k, v in frozen.items() if k != "freeze_id"})
    assert frozen["winner"]["static_score"] == max(state["results"][k]["static_score"] for k in state["pool"])
    children = {}
    for key in final_gate["children_in_pool"]:
        assert key in state["pool"]
        candidate = state["recipes"][key]["candidate"]
        parent = state["recipes"][state["attempts"][str(candidate["round"])]["parent_key"]]["candidate"]
        child_manifest = program_capability_manifest(path=bound(candidate["program"]))
        parent_manifest = program_capability_manifest(path=bound(parent["program"]))
        assert candidate["program_sha256"] != parent["program_sha256"]
        assert child_manifest["policy_ast_sha256"] == parent_manifest["policy_ast_sha256"]
        children[key] = {"revision": candidate["revision"], "program": candidate["program"],
                         "parent_program": parent["program"], "fixed_policy_preserved": True}
    assessments["development-final-champion"] = inspect_assessment(final_gate["assessment"])
    output = {"status": "complete", "evaluation_epoch": epoch, "assessments": assessments,
              "children": children, "champion": frozen["winner"]["key"],
              "repair_budget": policy["repair_budget"], "runtime": runtime_identity(),
              "claim_scope": "Real execution acceptance on exposed development data; not generalization evidence"}
    atomic_write_json(ROOT / "acceptance-inspection.json", output)
    print({"status": "complete", "epoch": epoch, "champion": output["champion"],
           "scores": {key: item["Paper."] for key, item in assessments.items()}})


if __name__ == "__main__":
    main()
