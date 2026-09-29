"""Prepare and execute the original-scale paired benchmark in a fresh run.

Preparation is metadata/asset work. It cannot turn missing references or missing
numeric decisions into runnable defaults. Search and final evaluation are
separate persisted phases; only frozen systems can enter final evaluation.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/paired-benchmark-20260917"
DATA = REPO / "data/original"
COCO = REPO.parent / "CoCoReviewBench"
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json, read_jsonl, write_immutable_jsonl, value_sha256
from agentreview.research_v2.shared_experiment import (
    LocalSharedBackend, binding, bound, run_shared_final_test, runtime_identity,
)
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.shared_evolution import SharedEvolution
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.development_coco import validate_assessment_receipt

ARM_KEYS = [*[f"seed-{s}" for s in SEED_NAMES], *[f"no-memory-{s}" for s in SEED_NAMES], "coco-native", "champion"]


def service_capacity():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:18001/v1/models',timeout=10) as response:
        values = json.loads(response.read())['data']
    return next(row.get('max_model_len') for row in values if row['id']=='qwen3.8-27b')


def prepare(decisions_path=None, development_references_path=None):
    audit = read_json(ROOT / "audit/summary.json")
    identities = {row["paper_id"]: row for row in read_json(ROOT / "audit/identities.json")["records"]}
    cohort = read_json(ROOT / "final-cohort.selection.json")
    usage = {row["paper_id"]: row["evidence"] for row in read_json(ROOT / "audit/use-inventory.json")["records"]}
    excluded = {row["paper_id"] for row in read_json(ROOT / "audit/exclusions.json")["records"]}
    if audit["errors"]:
        raise ValueError("Use-inventory errors require resolution before protocol preparation")
    for ref in audit["original"].values():
        bound(ref)
    base = REPO / "artifacts/shared-throughput-20260917/expanded/protocol.frozen.json"
    config = copy.deepcopy(read_json(base))
    for key in ("data_isolation_gate", "throughput_gate"):
        config.pop(key, None)
    config.update(status="pending", experiment_id="shared-memory-paired-benchmark-20260917",
        claim_scope="Paired self-evolution comparison on an existing benchmark; not a strictly unseen blind test")
    # Required by the full cohort, not a throughput tuning sweep: one retained
    # paper alone has 53,986 tokens, before five reviews and Meta's 16K output.
    # Preparation never restarts a model service.
    config["model"]["max_model_len"] = 131072
    config["generation"]["input_max_tokens"] = 114688
    # Reuse the verified generation/service conditions, never the small search's
    # number of attempts, RNG seed, rotating sample or candidate failure limits.
    config["evolution"].update(attempts=None, rotating_sample_size=None, random_seed=None, parent_temperature=None)
    config["failure_policy"] = {key: None for key in config["failure_policy"]}
    config["embedding"]["cache_path"] = str(ROOT / "embedding.sqlite3")
    policy = ROOT / "inputs/original-review-policy.py"
    policy.parent.mkdir(parents=True, exist_ok=True)
    code = seed_program_path("vector_search").read_text()
    if policy.exists() and policy.read_text() != code:
        raise ValueError("The original seed review policy changed")
    if not policy.exists():
        policy.write_text(code)
    config["non_memory_conditions"]["review_policy_program"] = binding(policy)
    seed_bindings = {name: binding(seed_program_path(name)) for name in SEED_NAMES}
    atomic_write_json(ROOT / "inputs/original-seeds.json", seed_bindings)
    provided = read_json(development_references_path) if development_references_path else {}
    blockers = []
    capacity = service_capacity()
    if capacity != config["model"]["max_model_len"]:
        blockers.append({"kind":"full_paper_context_capacity_not_activated", "current_context":capacity,
        "required_configured_context":131072,"input_max_tokens":114688,
        "reason":"Retained paper 8hKCNVqrlf requires 53986 body tokens plus complete role reviews and unchanged 16384 Meta output; do not trim or exclude"})
    services = read_json(REPO / 'artifacts/deployment/original-coco/services.bounded-20260916.json')
    services['services']['judge']['max_model_len'] = config['model']['max_model_len']
    service_path = atomic_write_json(ROOT/'services.proposed.json',services)
    config['service_configuration'] = binding(service_path)
    splits = {}
    names = {"build":"canonical_review_corpus", "static":"evolution_static", "rotating":"evolution_rotating_pool"}
    for split in (*names, "sealed_test"):
        if split == "sealed_test":
            ids = cohort["paper_ids"]
            old_dir = DATA / "coco_1300/papers"
        else:
            ids = [r["paper_id"] for r in read_json(DATA / "splits" / f"{names[split]}.json")["records"]]
            old_dir = DATA / "papers"
        ref = None
        if split in {"static", "rotating"}:
            if split in provided:
                ref = provided[split]
                references = read_json(bound(ref))
                if references.get("partition") != split or references.get("paper_ids") != ids:
                    raise ValueError(f"{split} references require the original ordered IDs and partition")
                for r in [*references["benchmark_files"], references["complete_human_classify_file"], references["reviewer_selection_file"]]:
                    bound(r)
            else:
                blockers.append({"kind":"missing_development_reference", "split":split, "count":len(ids), "paper_ids":ids})
        elif split == "sealed_test":
            reference_root = ROOT / "inputs/sealed_test/scoring-only"
            benchmarks = {}
            for path in sorted((COCO / "benchmark").glob("benchmark_*.jsonl")):
                if path.name not in {"benchmark_all_human_classify.jsonl", "benchmark_left_out_human.jsonl"}:
                    benchmarks.update({row["id"]:row for row in read_jsonl(path) if row["id"] in set(ids)})
            humans = {row["id"]:row for row in read_jsonl(COCO / "benchmark/benchmark_all_human_classify.jsonl") if row["id"] in set(ids)}
            selections = read_json(REPO / "artifacts/deployment/original-coco/references/benchmark_reviewer_selection.json")
            missing = [pid for pid in ids if pid not in benchmarks or pid not in humans or pid not in selections]
            if missing:
                raise ValueError(f"Final reference assets missing: {missing}")
            benchmark = write_immutable_jsonl(reference_root / "benchmark.jsonl", [benchmarks[pid] for pid in ids])
            human = write_immutable_jsonl(reference_root / "human.jsonl", [humans[pid] for pid in ids])
            selection = atomic_write_json(reference_root / "selection.json", {pid:selections[pid] for pid in ids})
            ref = binding(atomic_write_json(reference_root / "references.json", {
                "protocol":"research-v2-coco-reference-manifest-v1", "partition":split, "paper_ids":ids,
                "benchmark_files":[binding(benchmark)], "complete_human_classify_file":binding(human),
                "reviewer_selection_file":binding(selection)}))
        rows = []
        for pid in ids:
            old_path = old_dir / f"{pid}.json"
            paper = copy.deepcopy(read_json(old_path))
            paper.setdefault("source", {}).update(source_partition=split,
                original_frozen_asset=binding(old_path), purpose="paired benchmark; scorer-only human references")
            paper_path = atomic_write_json(ROOT / "inputs" / split / "papers" / f"{pid}.json", paper)
            assets = {"paper":binding(paper_path)}
            if ref:
                assets["coco_reference"] = ref
            rows.append({**identities[pid], "source_partition":split, "assets":assets})
        manifest = atomic_write_json(ROOT / "inputs" / split / "manifest.json", {
            "protocol":"shared-memory-split-v1", "split":split, "records":rows}, immutable=False)
        config["splits"][split] = binding(manifest)
        splits[split] = ids
    exclusion = atomic_write_json(ROOT / "inputs/development-exclusions.json", {
        "scope":"Evidence-backed development use and reserved development identities; previous evaluation alone is not exclusion",
        "records":[{**identities[pid], "use_evidence":usage.get(pid, [])} for pid in sorted(identities) if pid in usage or pid in excluded],
        "audit":binding(ROOT / "audit/summary.json")}, immutable=False)
    # Existing loader's historical inventory check is applied to documented
    # development uses, not to the old blanket 'all1300 exposed' inventory.
    config["historical_exposure_manifests"] = [binding(exclusion)]
    family_audit = atomic_write_json(ROOT / "inputs/family-audit.json", {
        "status":"complete", "method":read_json(ROOT / "audit/families.json")["method"],
        "limitations":read_json(ROOT / "audit/families.json")["limitations"],
        "evidence":binding(ROOT / "audit/families.json"),
        "split_manifest_sha256":{k:v["sha256"] for k,v in config["splits"].items()}}, immutable=False)
    config["deduplication"]["family_audit"] = binding(family_audit)
    config["evaluation_design"] = {
        "protocol":"paired-existing-benchmark-v1", "original_split_bindings":audit["original"],
        "cohort_selection":binding(ROOT / "final-cohort.selection.json"), "actual_use_inventory":binding(ROOT / "audit/use-inventory.json"),
        "expected_counts":{k:len(v) for k,v in splits.items()}, "arms":ARM_KEYS,
        "fresh_build":True, "inherit_small_run_state":False, "strictly_unseen_claim":False,
        "all_arms_frozen_before_final":True, "test_feedback_to_search":False,
        "test_references":"CoCo scorer only; not retrieval, generation, reflection or candidate selection",
        "primary_comparisons":["champion versus best fixed seed chosen on static development only", "champion versus matched no-memory arm"],
        "all_fixed_seed_and_control_deltas":True, "metric":"original CoCo Paper.",
        "uncertainty":{"method":"paired family-cluster bootstrap", "confidence":0.95,"resamples":10000,"seed":20260917,
            "scope":"Paper-sampling uncertainty conditional on frozen systems; not independent-search uncertainty"},
        "failed_assessments":{"zero_imputation":False,"selection_eligible":False,"primary_complete_cohort_required":True},
        "duplicate_proposal_rule":"Existing candidate-ID admission retained; identical program proposals consume attempts and remain explicit in lineage",
        "configuration_provenance":binding(REPO / "artifacts/shared-throughput-20260917/selection-user-stop-tuning.json"),
    }
    if decisions_path:
        decisions = read_json(decisions_path)
        if decisions.get("confirmed_by_user") is not True:
            raise ValueError("Numeric study decisions require an explicit user-confirmed record")
        seeds = decisions["search_random_seeds"]
        # This launcher owns one dependent search sequence. Repeats, if requested,
        # require separate roots and freeze-all scheduling, never a hidden loop.
        if len(seeds) != 1:
            blockers.append({"kind":"multiple_search_schedule_required", "seeds":seeds})
        config["evolution"].update({key:decisions[key] for key in ("attempts","rotating_sample_size","parent_temperature")})
        config["evolution"]["random_seed"] = seeds[0]
        config["failure_policy"] = decisions["failure_policy"]
        config["study_decisions"] = binding(decisions_path)
    else:
        blockers.append({"kind":"unconfirmed_full_scale_numeric_budget", "fields":["attempts","rotating_sample_size","search_random_seeds","parent_temperature","failure_policy"]})
    config["launch_blockers"] = blockers
    pending = atomic_write_json(ROOT / "protocol.pending.json", config, immutable=False)
    atomic_write_json(ROOT / "readiness.json", {"status":"blocked" if blockers else "ready_to_freeze",
        "blockers":blockers, "protocol":binding(pending), "final_evaluation_started":False,
        "search_started":False,"new_pilot_required":False}, immutable=False)
    print(json.dumps({"protocol":str(pending),"counts":{k:len(v) for k,v in splits.items()},
        "blockers":[{k:v for k,v in b.items() if k!='paper_ids'} for b in blockers]},ensure_ascii=False,indent=2))
    return pending


def run(protocol_path):
    protocol = load_experiment_protocol(protocol_path)
    config = protocol.document
    design = config.get("evaluation_design", {})
    if design.get("protocol") != "paired-existing-benchmark-v1" or config.get("launch_blockers"):
        raise ValueError("Paired run requires a resolved paired protocol")
    if design.get("arms") != ARM_KEYS or design.get("inherit_small_run_state") is not False:
        raise ValueError("Paired run requires all eight fresh, prespecified arms")
    implementation = config['implementation']
    if implementation['runtime'] != runtime_identity() or implementation['launcher'] != binding(__file__):
        raise ValueError('Paired execution requires the frozen implementation')
    bound(implementation['reporter'])
    for ref in read_json(bound(implementation['original_seeds'])).values():
        bound(ref)
    if service_capacity() != config['model']['max_model_len']:
        raise ValueError('Active model service capacity differs from the frozen complete-paper configuration')
    if {k:len(protocol.records(k)) for k in ("build","static","rotating")} != {"build":125,"static":25,"rotating":125}:
        raise ValueError("Do not replace original-scale development with a small-run budget")
    paused = read_json(REPO / "artifacts/shared-throughput-20260917/expanded/run/search.json")
    if paused.get("status") != "paused":
        raise ValueError("Current small run must have reached its durable paused checkpoint")
    # Fresh paths prevent inherited candidate pools, writable role state or
    # reflection history. Existing same-run checkpoints remain resumable.
    run_root = ROOT / "search"
    os.environ["AGENTREVIEW_QWEN27B_MODE_LEASE_PATH"] = read_json(bound(config["scoring_config"]))["lease_path"]
    for key in ("http_proxy","https_proxy","all_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(ROOT / "telemetry")
    from throughput_profile import install
    event, stop, thread = install(ROOT / "telemetry")
    started = time.time()
    try:
        host = LocalSharedBackend(protocol=protocol, output_root=run_root)
        search = SharedEvolution(protocol=protocol, output_root=run_root, backend=host)
        launch = ROOT / "launched.json"
        if launch.exists():
            previous = read_json(launch)
            if previous["protocol"] != binding(protocol_path):
                raise ValueError("Resume must preserve the original launch protocol")
            atomic_write_json(ROOT / "resumes" / f"{time.time_ns()}.json", {"pid":os.getpid(),"resumed":started})
        else:
            atomic_write_json(launch, {"pid":os.getpid(),"started":started,
                "protocol":binding(protocol_path),"runtime":runtime_identity(),"new_build":True,
                "historical_or_small_state_inherited":False})
        state = search.run()
        if state["status"] != "complete":
            atomic_write_json(ROOT / "execution-status.json", {"phase":"search","status":state["status"]}, immutable=False)
            return state
        frozen = state["winner"]
        expected = set(ARM_KEYS) - {"champion"}
        if not expected <= set(frozen["recipes"]):
            raise ValueError("All seeds and matched controls must be frozen before test")
        for key, result in frozen["assessments"].items():
            host.validate(result, recipe=frozen["recipes"][key], epoch=result["evaluation_epoch"])
            validate_assessment_receipt(bound(result["assessment"]))
        winner = frozen["winner"]
        host.frozen_snapshots[winner["candidate_id"]] = winner["identity"]["snapshots"]
        recipe = {**state["recipes"][winner["key"]], "key":"champion-development-final"}
        final_dev = host.assess(recipe, epoch=search.epoch)
        host.validate(final_dev, recipe=recipe, epoch=search.epoch)
        atomic_write_json(ROOT / "development-final.json", {"recipe":recipe,"result":final_dev,
            "selection_changed":False,"frozen_system":binding(run_root / "winner.json")})
        atomic_write_json(ROOT / "execution-status.json", {"phase":"final_evaluation","status":"running",
            "frozen_system":binding(run_root / "winner.json")}, immutable=False)
        final = run_shared_final_test(protocol_path=protocol_path, output_root=ROOT / "final",
                                      frozen_system_path=run_root / "winner.json")
        atomic_write_json(ROOT / "execution-status.json", {"phase":"final_evaluation","status":final["status"]}, immutable=False)
        if final["status"] == "complete":
            from paired_benchmark_report import report
            report(ROOT)
        return final
    except Exception as exc:
        atomic_write_json(ROOT / "execution-failure.json", {"status":"paused","time":time.time(),
            "type":type(exc).__name__,"error":str(exc),"zero_imputation":False}, immutable=False)
        raise
    finally:
        stop.set()
        thread.join(timeout=12)


def freeze(config_path=None):
    config = read_json(config_path or ROOT / "protocol.pending.json")
    if config.get("launch_blockers"):
        raise ValueError("Unresolved full-scale numeric decisions or development references; no default substitution")
    token_path = ROOT / "audit/proposed-capacity-budget.json"
    budget = read_json(token_path)
    if budget.get("violations") or budget["tokenizer_sha256"] != config["model"]["tokenizer_sha256"]:
        raise ValueError("Complete paper budget violations must be resolved without trimming or dropping papers")
    expected = {(split,row["paper_id"]) for split,ref in config["splits"].items() for row in read_json(bound(ref))["records"]}
    if expected != {(row["split"],row["paper_id"]) for row in budget["records"]}:
        raise ValueError("Budget audit must cover every unchanged cohort paper")
    if budget['split_bindings'] != config['splits'] or any(
            row['input_limit'] != config['generation']['input_max_tokens'] or row['model_context'] != config['model']['max_model_len']
            for row in budget['records']):
        raise ValueError('Capacity audit requires these exact inputs and budgets')
    if service_capacity() != config['model']['max_model_len']:
        raise ValueError('The proposed full-paper service capacity has not been activated')
    config["input_budget_audit"] = binding(token_path)
    config["implementation"] = {"runtime":runtime_identity(), "launcher":binding(__file__),
        "reporter":binding(Path(__file__).with_name("paired_benchmark_report.py")),
        "original_seeds":binding(ROOT / "inputs/original-seeds.json")}
    config["status"] = "frozen"
    # The loader must check complete partition-local references, actual-use
    # exclusions and family overlap before publishing an execution-ready file.
    staged = atomic_write_json(ROOT / "protocol.validation.json", config, immutable=False)
    protocol = load_experiment_protocol(staged)
    for split in ("static","rotating"):
        ref = protocol.records(split)[0]["assets"]["coco_reference"]
        references = read_json(bound(ref))
        expected_ids = {r["paper_id"] for r in protocol.records(split)}
        groups = [[r["id"] for b in references["benchmark_files"] for r in read_jsonl(bound(b))],
            [r["id"] for r in read_jsonl(bound(references["complete_human_classify_file"]))],
            list(read_json(bound(references["reviewer_selection_file"]))) ]
        if any(len(ids)!=len(set(ids)) or set(ids)!=expected_ids for ids in groups):
            raise ValueError(f"Incomplete partition-local original CoCo references: {split}")
    result = atomic_write_json(ROOT / "protocol.frozen.json", config)
    load_experiment_protocol(result)
    atomic_write_json(ROOT / "readiness.json", {"status":"ready","protocol":binding(result),
        "search_started":False,"final_evaluation_started":False,"new_pilot_required":False}, immutable=False)
    print(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare","freeze","run"))
    parser.add_argument("--decisions")
    parser.add_argument("--development-references")
    parser.add_argument("--protocol")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.decisions, args.development_references)
    elif args.action == "freeze":
        freeze()
    else:
        run(args.protocol)
