"""One real, budgeted development search; inject one pre-checkpoint interruption."""
from pathlib import Path
import argparse
import os
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import read_json, atomic_write_json, value_sha256
from agentreview.research_v2.shared_experiment import APISharedBackend, binding, bound, runtime_identity
from agentreview.research_v2.shared_evolution import SharedEvolution
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.transport_observer import process_identity
from agentreview.research_v2.constants import ROLES
from evolution_round_report import report


def verify_chain(protocol, root, state, *, key='candidate-0001'):
    """Check the complete candidate cohort and all five prompt/snapshot bindings."""
    host = APISharedBackend(protocol=protocol, output_root=root / "search")
    recipe, result = state["recipes"][key], state["results"][key]
    host.validate(result, recipe=recipe, epoch=result["evaluation_epoch"])
    assert result["status"] == "complete" and key in state["pool"]
    assert state["pool"].count(key) == 1
    assert any(a['candidate_key'] == key and a['status'] == 'complete' for a in state['attempts'].values())
    expected = max(state["pool"], key=lambda k: state["results"][k]["static_score"])
    assert state["champion"]["static_score"] == state["results"][expected]["static_score"]
    candidate = recipe["candidate"]
    chain = []
    for role in ROLES:
        snapshot_ref = result["identity"]["snapshots"][role]
        snapshot = read_json(bound(snapshot_ref))
        assert snapshot["role"] == role and snapshot["program_sha256"] == candidate["program"]["sha256"]
        for review_ref in result["identity"]["role_reviews"][role]:
            review_path = bound(review_ref)
            review = read_json(review_path)
            request = read_json(review_path.with_suffix(".request.json"))
            inputs = request["input_identity"]
            assert inputs["program_sha256"] == candidate["program"]["sha256"]
            assert inputs["snapshot_sha256"] == snapshot_ref["sha256"]
            assert review["program_version"] == candidate["candidate_id"] and review["role"] == role
            assert review["memory_snapshot_id"] == snapshot["snapshot_id"]
            assert request["request_sha256"] == value_sha256(request["messages"])
            memory = request["retrieval_provenance"]["memory"]
            assert memory["program_sha256"] == candidate["program"]["sha256"]
            assert memory["snapshot_id"] == snapshot["snapshot_id"]
            context = memory["context"]
            assert not context or context in "\n".join(m["content"] for m in request["messages"])
            chain.append({"role": role, "review": review_ref, "request": binding(review_path.with_suffix(".request.json")),
                          "snapshot": snapshot_ref, "retrieval": request["retrieval_provenance"]})
    assert len({v["sha256"] for v in result["identity"]["snapshots"].values()}) == 5
    manifest = read_json(root / "search/assessments" / f"e{result['evaluation_epoch']}" / key / f"r{candidate.get('revision',0)}" / "reviews.json")
    finals = manifest["records"]
    for final_ref in finals:
        final = read_json(bound(final_ref))
        source_ids = {read_json(bound(ref))["review_id"] for role in ROLES
                      for ref in result["identity"]["role_reviews"][role] if ref["paper_id"] == final_ref["paper_id"]}
        assert set(final["source_review_ids"]) == source_ids and len(source_ids) == 5
    return {"candidate": candidate, "assessment": result["assessment"], "Paper.": result["static_score"],
            "final_reviews": finals, "chain": chain, "winner": binding(root / "search/winner.json")}


def run(protocol_path, root):
    root = root.resolve()
    protocol = load_experiment_protocol(protocol_path)
    assert protocol.document["implementation"]["runtime"] == runtime_identity()
    for ref in protocol.document["implementation"].values():
        if isinstance(ref, dict) and "sha256" in ref:
            bound(ref)
    budget = protocol.document["runtime_acceptance_budget"]
    budget_path = root / "call-budget.json"
    if not budget_path.exists():
        atomic_write_json(budget_path, {"limits": budget["http_attempts"], "used": {"cloud": 0, "local": 0},
                                      "deadline_at": time.time() + budget["wall_seconds"]})
    os.environ["AGENTREVIEW_CALL_BUDGET"] = str(budget_path)
    os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(root / "telemetry")
    atomic_write_json(root / "process-identity.json", process_identity(), immutable=False)
    status = {"status": "running", "protocol": binding(protocol_path), "started_at": time.time()}
    status_path = root / "acceptance.json"
    atomic_write_json(status_path, status, immutable=False)
    try:
        host = APISharedBackend(protocol=protocol, output_root=root / "search")
        experiment = SharedEvolution(protocol=protocol, output_root=root / "search", backend=host)
        original = experiment._record_result
        def interrupt_after_score(recipe, result, *, previous_recipe):
            if recipe["key"] == "candidate-0001" and result["status"] == "complete":
                atomic_write_json(root / "interruption.json", {"location": "after valid score, before checkpoint result/admission",
                    "assessment": result["assessment"], "before_budget": read_json(budget_path),
                    "checkpoint": read_json(experiment.state_path), "policy": read_json(experiment.controller.path)})
                raise KeyboardInterrupt("Acceptance checkpoint interruption")
            return original(recipe, result, previous_recipe=previous_recipe)
        if not (root / "interruption.json").exists():
            experiment._record_result = interrupt_after_score
        try:
            recovery = protocol.document.get('recovery')
            state = experiment.run(repair_evidence=bound(recovery['evidence']) if recovery else None)
        except KeyboardInterrupt:
            if not (root / "interruption.json").exists():
                raise
            host = APISharedBackend(protocol=protocol, output_root=root / "search")
            state = SharedEvolution(protocol=protocol, output_root=root / "search", backend=host).run()
        if state["status"] != "complete":
            raise RuntimeError(f"Acceptance search stopped: {state['status']}; {state.get('last_failure')}")
        evidence = verify_chain(protocol, root, state)
        interruption = read_json(root / "interruption.json")
        assert interruption["before_budget"]["used"] == read_json(budget_path)["used"]
        after_policy = read_json(root / "search/failure-policy.json")
        for name in ("repair_budget", "candidate_failure_budget", "candidate_repairs", "evaluation_epoch"):
            assert interruption["policy"][name] == after_policy[name]
        assert after_policy["champions"]["shared"] == state["champion"]
        before = read_json(budget_path)
        replay = SharedEvolution(protocol=protocol, output_root=root / "search", backend=host).run()
        assert replay == read_json(root / "search/search.json") and before == read_json(budget_path)
        report(root)
        status.update(status="complete", evidence=evidence, checkpoint=binding(root / "search/search.json"),
                      interruption_recovered=True, replay_zero_http_calls=True,
                      call_budget=read_json(budget_path), runtime=runtime_identity(), sealed_test_opened=False)
    except BaseException as exc:
        status.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        status["finished_at"] = time.time()
        atomic_write_json(status_path, status, immutable=False)
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    value = run(args.protocol.resolve(), args.output_root)
    print({"status": value["status"], "checkpoint": value["checkpoint"]}, flush=True)
