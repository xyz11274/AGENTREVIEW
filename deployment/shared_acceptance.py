"""Gated real shared-memory development acceptance, followed by a small pilot.

All scores are development results. Preparation binds reserved sealed assets,
but no sealed paper enters generation, retrieval or scoring in this runner.
This run makes no held-out generalization claim.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import random
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.experiment_protocol import load_experiment_protocol, normalized_content_sha256
from agentreview.research_v2.io import atomic_write_json, read_json, read_jsonl, value_sha256, write_immutable_jsonl
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding, bound, runtime_identity, generation_identity
from agentreview.research_v2.shared_evolution import SharedEvolution, select_parent
from agentreview.research_v2.failure_policy import validate_promotion_policy

ASSETS = REPO / "artifacts/shared-acceptance-20260916"
COCO = Path("/data/xyj/shengao/CoCoReviewBench")
HISTORY = Path("/data/xyj/shengao/AgentReview_0831/artifacts/research_v2/data/coco_1300/papers")


def identity(paper):
    return {"paper_id": paper["paper_id"], "title": paper["title"],
            "content_sha256": hashlib.sha256(paper["text"].encode()).hexdigest(),
            "normalized_content_sha256": normalized_content_sha256(paper["text"]),
            "work_family": "coco-title-" + value_sha256(" ".join(paper["title"].casefold().split())),
            "version_ids": [paper["paper_id"]]}


def prepare():
    old = {p.stem: read_json(p) for p in sorted(HISTORY.glob("*.json"))}
    benchmark = {}
    for path in sorted((COCO / "benchmark").glob("benchmark_*.jsonl")):
        if path.stem in {"benchmark_all_human_classify", "benchmark_left_out_human"}:
            continue
        for row in read_jsonl(path):
            benchmark[row["id"]] = row
    human = {row["id"]: row for row in read_jsonl(COCO / "benchmark/benchmark_all_human_classify.jsonl")}
    selection = read_json(REPO / "artifacts/deployment/original-coco/references/benchmark_reviewer_selection.json")
    candidates = sorted((len(p["text"]), pid) for pid, p in old.items()
                        if 12000 < len(p["text"]) < 35000 and pid in human and pid in selection)
    exposed = "-EHqoysUYLx"
    short = [pid for _, pid in candidates if pid != exposed]
    reserved = next(pid for pid in sorted(benchmark) if pid not in old and pid in human and pid in selection
                    and (COCO / benchmark[pid]["MD_path"]).is_file())
    row = benchmark[reserved]
    old[reserved] = {"paper_id": reserved, "title": row["content"]["title"],
                     "abstract": row["content"].get("abstract", ""),
                     "text": (COCO / row["MD_path"]).read_text(), "source": {}}
    old[exposed] = read_json(REPO / "artifacts/deployment/original-coco/paper.json")
    history = atomic_write_json(ASSETS / "historical-exposure.json", {
        "scope": "Conservative exclusion: all 1300 papers in prior development inventory plus native acceptance paper",
        "formal_exposure_inventory_complete": False,
        "records": [identity(p) for pid, p in old.items() if pid != reserved]})
    for name, ids, attempts in (
        ("acceptance", {"build": short[:1], "static": [exposed], "rotating": short[1:2], "sealed_test": [reserved]}, 1),
        ("pilot", {"build": short[:4], "static": [exposed, *short[4:6]], "rotating": short[6:10], "sealed_test": [reserved]}, 3),
    ):
        root = ASSETS / name
        config = read_json(REPO / "AgentReview/configs/shared_memory_experiment.pilot-20260916.pending.json")
        config.update(status="frozen", experiment_id=f"shared-memory-{name}-20260916",
                      claim_scope="development-only; no sealed test execution or generalization claim")
        config["generation"].update(review_max_tokens=8192, meta_max_tokens=16384,
            memory_max_tokens=4096, native_max_tokens=8192, input_max_tokens=49152)
        config["evolution"].update(attempts=attempts, rotating_sample_size=1 if name == "acceptance" else 2,
                                   max_reflection_tokens=16384)
        config["embedding"]["cache_path"] = str(ASSETS / "embedding.sqlite3")
        config["historical_exposure_manifests"] = [binding(history)]
        for split, paper_ids in ids.items():
            refs = None
            if split != "build":
                benchmark_path = write_immutable_jsonl(root / split / "benchmark.jsonl", [benchmark[pid] for pid in paper_ids])
                human_path = write_immutable_jsonl(root / split / "human.jsonl", [human[pid] for pid in paper_ids])
                selected = atomic_write_json(root / split / "selection.json", {pid: selection[pid] for pid in paper_ids})
                refs = atomic_write_json(root / split / "references.json", {
                    "protocol": "research-v2-coco-reference-manifest-v1", "partition": split, "paper_ids": paper_ids,
                    "benchmark_files": [binding(benchmark_path)], "complete_human_classify_file": binding(human_path),
                    "reviewer_selection_file": binding(selected)})
            records = []
            for pid in paper_ids:
                paper = copy.deepcopy(old[pid])
                paper["source"].update(source_partition=split, purpose="development acceptance/pilot only")
                paper_path = atomic_write_json(root / split / "papers" / f"{pid}.json", paper)
                assets = {"paper": binding(paper_path)}
                if refs:
                    assets["coco_reference"] = binding(refs)
                records.append({**identity(paper), "source_partition": split, "assets": assets})
            manifest = atomic_write_json(root / split / "manifest.json", {
                "protocol": "shared-memory-split-v1", "split": split, "records": records})
            config["splits"][split] = binding(manifest)
        audit = atomic_write_json(root / "family-audit.json", {
            "status": "complete", "scope": "development partition integrity only",
            "method": "Exact IDs, normalized titles, content and normalized-content hashes; title-derived family IDs. No claim of exhaustive cross-publication family linkage or complete historical exposure audit.",
            "split_manifest_sha256": {split: ref["sha256"] for split, ref in config["splits"].items()}})
        config["deduplication"]["family_audit"] = binding(audit)
        protocol = atomic_write_json(root / "protocol.json", config)
        load_experiment_protocol(protocol)
        print(name, {split: len(pids) for split, pids in ids.items()}, protocol, flush=True)


def backend(name="acceptance", protocol_path=None):
    protocol = load_experiment_protocol(protocol_path or ASSETS / name / "protocol.json")
    result = LocalSharedBackend(protocol=protocol, output_root=ASSETS / name / "run")
    return result, SharedEvolution(protocol=protocol, output_root=result.root, backend=result)


def check_result(host, result, recipe, epoch):
    host.validate(result, recipe=recipe, epoch=epoch)
    receipt = validate_assessment_receipt(bound(result["assessment"]),
        expected_paper_ids=recipe["paper_ids"], expected_epoch=epoch, expected_partition=recipe["split"],
        candidate_identity=result["identity"])
    if receipt["review_target"] != "final" or result.get("role_diagnostics"):
        raise ValueError("Acceptance requires final-review-only scoring")
    return receipt


def gate(repair_evidence=None):
    host, search = backend()
    host.preflight()
    if repair_evidence:
        try:
            search._recover(repair_evidence)
        except Exception as exc:
            search._pause(exc, "acceptance_recovery")
            raise
    candidate = host.seed("vector_search")
    recipe = search._register("seed-vector_search", candidate, kind="seed")
    try:
        result = search._evaluate(recipe)
    except Exception as exc:
        search._pause(exc, "acceptance_gate")
        raise
    receipt = check_result(host, result, recipe, search.epoch)
    search._admit(recipe["key"])
    gate_receipt = {"status": "complete", "formal_experiment": False, "evaluation_epoch": search.epoch,
        "runtime": runtime_identity(), "assessment": result["assessment"], "score": receipt["mean_score"],
        "chain": ["shared_memory", "five_roles", "meta", "split", "classify", "judge", "Paper."]}
    atomic_write_json(ASSETS / f"gate-one-paper.e{search.epoch}.json", gate_receipt)
    atomic_write_json(ASSETS / "gate-one-paper.json", gate_receipt, immutable=False)
    print("ONE PAPER GATE COMPLETE", receipt["mean_score"], flush=True)


def acceptance_iteration(host, search):
    """One real MSTAR transition; the full three-seed search belongs to pilot.

Use the same selection, proposal, repair, assessment, admission and freeze
implementations as SharedEvolution.run, starting from the admitted gate seed.
This is an execution acceptance slice, not a completed three-seed experiment.
"""
    with (host.root / "search.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        search.controller.require_running()
        options = host.config["evolution"]
        attempt = search.state["attempts"].get("1")
        if attempt is None:
            entries = [search._entry(key) for key in search.state["pool"]]
            parent = select_parent(entries, temperature=options["parent_temperature"],
                                   random_seed=options["random_seed"] + 1)
            attempt = {"ordinal": 1, "parent_key": parent["key"], "status": "pending"}
            search.state["attempts"]["1"] = attempt
            search.save()
        key = "candidate-0001"
        recipe = search.state["recipes"].get(key)
        if recipe is None:
            parent = search._entry(attempt["parent_key"])
            sample = random.Random(options["random_seed"] + 1).sample(
                [row["paper_id"] for row in host.protocol.records("rotating")], options["rotating_sample_size"])
            feedback_key = f"feedback-0001-e{search.epoch}"
            feedback_recipe = search._register(feedback_key, search.state["recipes"][parent["key"]]["candidate"],
                kind="parent_feedback", split="rotating", paper_ids=sample)
            feedback = search._evaluate(feedback_recipe)
            attempt["feedback_key"] = feedback_key
            search.save()
            candidate = host.propose(parent=parent, feedback=feedback, history=search.state["attempts"],
                                     directory=host.root / "proposals" / f"a1-e{search.epoch}")
            candidate.update(candidate_id=key, parent_id=parent["candidate_id"], seed=parent["seed"], revision=0,
                             round=1, lineage=[*parent.get("lineage", []), parent["candidate_id"]])
            recipe = search._register(key, candidate, kind="candidate")
        result = search._evaluate(recipe)
        if result["status"] != "complete":
            raise ValueError("Real child acceptance failed; do not skip the failed child")
        search._admit(key)
        attempt.update(status="complete", candidate_key=key, static_score=result["static_score"],
                       change=recipe["candidate"].get("change"), seed=recipe["candidate"]["seed"],
                       program_sha256=recipe["candidate"]["program_sha256"])
        for item, value in search.state["results"].items():
            if search.state["recipes"][item]["split"] == "static" or value["evaluation_epoch"] == search.epoch:
                check_result(host, value, search.state["recipes"][item], search.epoch)
        winner = max((search._entry(item) for item in search.state["pool"]), key=lambda item: item["static_score"])
        frozen = host.freeze(winner, search.state)
        with search.controller.path.with_suffix(".lock").open("a+b") as policy_lock:
            fcntl.flock(policy_lock.fileno(), fcntl.LOCK_EX)
            validate_promotion_policy(read_json(search.controller.path))
            atomic_write_json(host.root / f"winner.e{search.epoch}.json", frozen)
            atomic_write_json(host.root / "winner.json", frozen, immutable=False)
            search.state.update(status="acceptance_complete", winner=frozen,
                                execution_scope="single-lineage real MSTAR acceptance; full three-seed comparison deferred to pilot")
            search.save()
        host.write_report_input(search.state)
        return search.state


def evolution():
    first = read_json(ASSETS / "gate-one-paper.json")
    if first["status"] != "complete" or first["runtime"] != runtime_identity():
        raise ValueError("Evolution requires current one-paper acceptance")
    validate_assessment_receipt(bound(first["assessment"]))
    host, search = backend()
    # Check the distinct native serialization boundary before spending on the
    # remaining seed comparisons. It is still the same required control recipe.
    native = search._register("coco-native", host.native_identity(), kind="coco_native", memory_enabled=False)
    try:
        check_result(host, search._evaluate(native), native, search.epoch)
    except Exception as exc:
        search._pause(exc, "acceptance_native_control")
        raise
    try:
        state = acceptance_iteration(host, search)
    except Exception as exc:
        search._pause(exc, "acceptance_mstar_transition")
        raise
    unresolved = [key for key, result in state["results"].items() if result["status"] != "complete"]
    if unresolved:
        raise ValueError(f"Acceptance has unresolved failed evaluations: {unresolved}")
    children = [key for key in state["pool"] if state["recipes"][key]["kind"] == "candidate"]
    if state["status"] != "acceptance_complete" or not children:
        raise ValueError("Acceptance requires a real evaluated child admitted to the MSTAR pool")
    if not any(state["recipes"][key]["candidate"]["program_sha256"] !=
               state["recipes"][state["attempts"][str(state["recipes"][key]["candidate"]["round"])]["parent_key"]]["candidate"]["program_sha256"]
               for key in children):
        raise ValueError("Acceptance requires at least one child with a changed memory program")
    for key in children:
        check_result(host, state["results"][key], state["recipes"][key], search.epoch)
    frozen = read_json(host.root / "winner.json")
    if frozen != state["winner"] or frozen["freeze_id"] != value_sha256({k:v for k,v in frozen.items() if k != "freeze_id"}):
        raise ValueError("Champion freeze failed integrity validation")
    champion = frozen["winner"]
    original = state["recipes"][champion["key"]]
    host.frozen_system = frozen
    host.frozen_snapshots = {champion["candidate_id"]: champion["identity"]["snapshots"]}
    final_recipe = {**original, "key": "development-final-champion"}
    try:
        result = host.assess(final_recipe, epoch=search.epoch)
        receipt = check_result(host, result, final_recipe, search.epoch)
        if result["identity"]["snapshots"] != champion["identity"]["snapshots"]:
            raise ValueError("Development final must use frozen Champion build memories")
    except Exception as exc:
        search._pause(exc, "acceptance_development_final")
        raise
    atomic_write_json(ASSETS / "gate-child-freeze-development-final.json", {
        "status": "complete", "formal_experiment": False, "runtime": runtime_identity(),
        "generation_identity": generation_identity(), "children_in_pool": children,
        "frozen_system": binding(host.root / "winner.json"), "assessment": result["assessment"],
        "development_final_score": receipt["mean_score"],
        "limitation": "Final replay uses exposed static development paper; not unseen-test evidence."})
    print("CHILD / FREEZE / DEVELOPMENT FINAL COMPLETE", flush=True)


def pilot():
    acceptance = read_json(ASSETS / "acceptance/run/search.json")
    policy = read_json(ASSETS / "acceptance/run/failure-policy.json")
    validate_promotion_policy(policy)
    if acceptance["status"] != "acceptance_complete" or acceptance.get("winner") is None:
        raise ValueError("Pilot requires a live completed acceptance and frozen Champion")
    for name in ("gate-one-paper.json", "gate-child-freeze-development-final.json"):
        receipt = read_json(ASSETS / name)
        if receipt["status"] != "complete" or receipt["runtime"] != runtime_identity():
            raise ValueError("Pilot requires both acceptance gates under current runtime")
        validate_assessment_receipt(bound(receipt["assessment"]), expected_epoch=policy["evaluation_epoch"])
        if "frozen_system" in receipt and read_json(bound(receipt["frozen_system"])) != acceptance["winner"]:
            raise ValueError("Pilot requires the current frozen Champion, not an archived acceptance")
    base = ASSETS / "pilot/protocol.json"
    document = read_json(base)
    document["failure_policy"]["candidate_failures"] = 1
    document["launch_policy"] = {"derived_from": binding(base),
        "reason": "Stop the pilot at the first candidate still failing after bounded repairs; do not skip it."}
    launch_protocol = atomic_write_json(ASSETS / "pilot/protocol.fail-fast.json", document)
    host, search = backend("pilot", protocol_path=launch_protocol)
    atomic_write_json(ASSETS / "pilot-started.json", {"status": "started", "protocol": binding(host.protocol.path),
        "prerequisites": [binding(ASSETS / name) for name in ("gate-one-paper.json", "gate-child-freeze-development-final.json")]})
    state = search.run()
    print("PILOT", state["status"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "gate", "evolution", "pilot"])
    parser.add_argument("--repair-evidence", type=Path)
    args = parser.parse_args()
    if args.action == "gate":
        gate(args.repair_evidence)
    else:
        {"prepare": prepare, "evolution": evolution, "pilot": pilot}[args.action]()
