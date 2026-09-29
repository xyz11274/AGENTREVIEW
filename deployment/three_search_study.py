"""Three independent MSTAR searches, then all three frozen paired evaluations."""
from pathlib import Path
import argparse
import copy
import fcntl
import json
import os
import sys
import time

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/coco-paired-3search-20260917"
PREVIOUS = REPO / "artifacts/coco-paired-resplit-20260917"
SEEDS = (0, 1000, 2000)
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding, bound, runtime_identity, generation_identity, run_shared_final_test
from agentreview.research_v2.shared_evolution import SharedEvolution
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.development_coco import validate_assessment_receipt


def prepare():
    readiness = read_json(ROOT / "data-readiness.json")
    assert readiness["counts"] == {"build":125, "static":25, "rotating":125, "sealed_test":1300}
    config = read_json(PREVIOUS / "protocol.memory-fixed.pending.json")
    config["splits"] = readiness["manifests"]
    for split in ("build", "static", "rotating"):
        before = read_json(bound(read_json(PREVIOUS / "protocol.pending.json")["splits"][split]))["records"]
        after = read_json(bound(config["splits"][split]))["records"]
        assert [(r["paper_id"], r["content_sha256"]) for r in before] == [(r["paper_id"], r["content_sha256"]) for r in after]
    family = atomic_write_json(ROOT / "inputs/family-audit.json", {"status":"complete",
        "method":"Original CoCo work-family/version/text audit; zero cross-partition identity overlap",
        "split_manifest_sha256":{k:v["sha256"] for k,v in config["splits"].items()},
        "evidence":binding(ROOT / "data-readiness.json")})
    config["deduplication"]["family_audit"] = binding(family)
    original = read_json(ROOT / "original-1300-preserved.json")
    cohort = atomic_write_json(ROOT / "evaluation-cohort.json", {"paper_ids":original["paper_ids"],
        "original_basis":original["binding"], "historical_use_annotations":binding(ROOT / "audit/historical-use-annotations.json"),
        "excluded":[], "strictly_unseen_claim":False})
    config["evaluation_design"].update(protocol="paired-existing-benchmark-three-search-v1",
        historical_overlap_policy="retain-label-no-test-feedback", original_evaluation_basis=original["binding"],
        historical_use_annotations=binding(ROOT / "audit/historical-use-annotations.json"),
        cohort_selection=binding(cohort), expected_counts=readiness["counts"],
        data_audit=binding(ROOT / "data-readiness.json"),
        development_split_bindings={k:v for k,v in config["splits"].items() if k != "sealed_test"},
        comparison_units="Three independent eight-arm studies, each with fresh build experience and role memories; all Champions reported",
        test_feedback_to_search=False, all_arms_frozen_before_final=True)
    config["evaluation_design"].pop("case_exclusions", None)
    config["evolution"].update(attempts=20, rotating_sample_size=5, random_seed=0,
        parent_temperature=0.6, score_scale="raw_1_to_5", detailed_reflection_cases=2,
        detailed_case_rule="Two lowest Paper. scores, independent seeded tie-breaks; overview of all five",
        random_streams_protocol="sha256-separated-python-random-v1")
    config["failure_policy"] = {"request_attempts":3, "candidate_repairs":1,
        "round_repairs":10, "candidate_failures":5, "consecutive_candidate_failures":3}
    decisions = atomic_write_json(ROOT / "study-decisions.json", {"confirmed_by_user":True,
        "search_random_seeds":list(SEEDS), "attempts":20,"rotating_sample_size":5,"parent_temperature":0.6,
        "failure_policy":config["failure_policy"], "final_papers":1300,
        "model_generation_temperature_or_thinking_changed":False,
        "request_retry_owner":"PersistentCompletionClient; SDK and outer-wrapper extra retries disabled",
        "failure_events":"Only terminal candidate failure after its one repair; public faults do not count; diagnosis repairs share the ceiling of ten"})
    config["study_decisions"] = binding(decisions)
    config["study"] = {"protocol":"three-independent-searches-v1", "random_seeds":list(SEEDS),
        "freeze_manifest":str(ROOT / "all-systems-frozen.json"), "selection_on_test":False}
    config["embedding"]["cache_path"] = str(ROOT / "embedding.sqlite3")
    config["launch_blockers"] = []
    config["launch_policy"] = {"reason":"User-confirmed 3 x 20 independent searches and all original 1300 final papers; no pilot or tuning"}
    config.pop("memory_repair", None)  # Old implementation receipt remains preserved in its old run root.
    config.pop("implementation", None)
    atomic_write_json(ROOT / "protocol.pending.json", config)
    print(json.dumps({"data": readiness["counts"], "seeds":SEEDS, "attempts_each":20}, ensure_ascii=False))


def freeze():
    config = read_json(ROOT / "protocol.pending.json")
    validation = read_json(ROOT / "audit/validation.json")
    if validation["status"] != "passed" or validation["runtime"] != runtime_identity():
        raise ValueError("Freeze requires passing regressions of these exact runtime bytes")
    budget = read_json(ROOT / "audit/proposed-capacity-budget.json")
    if budget["violations"] or budget["split_bindings"] != config["splits"]:
        raise ValueError("Every unchanged full paper must pass the budget audit")
    from paired_benchmark import service_capacity
    if service_capacity() != config["model"]["max_model_len"]:
        raise ValueError("Live generation capacity differs from frozen paper budgets")
    config["input_budget_audit"] = binding(ROOT / "audit/proposed-capacity-budget.json")
    config["implementation"] = {"runtime":runtime_identity(), "launcher":binding(__file__),
        "reporter":binding(Path(__file__).with_name("paired_benchmark_report.py")),
        "validation":binding(ROOT / "audit/validation.json")}
    refs = []
    for seed in SEEDS:
        run_root = ROOT / "runs" / f"seed-{seed}"
        document = copy.deepcopy(config)
        document.update(status="frozen", experiment_id=f"coco-paired-1300-search-{seed}-20260917")
        document["evolution"]["random_seed"] = seed
        target = atomic_write_json(run_root / "protocol.frozen.json", document)
        protocol = load_experiment_protocol(target)
        host = LocalSharedBackend(protocol=protocol, output_root=run_root / "search")
        host.preflight()
        refs.append({"seed":seed, "protocol":binding(target), "run_root":str(run_root)})
    study = {"protocol":"three-search-study-v1", "status":"frozen", "searches":refs,
        "implementation":config["implementation"], "cohort":config["evaluation_design"]["cohort_selection"],
        "all_searches_frozen_before_any_test":True, "never_select_a_run_by_test_score":True}
    atomic_write_json(ROOT / "study.frozen.json", {**study, "study_id":value_sha256(study)})
    print(ROOT / "study.frozen.json")


def run():
    study = read_json(ROOT / "study.frozen.json")
    if study["implementation"]["runtime"] != runtime_identity() or study["implementation"]["launcher"] != binding(__file__):
        raise ValueError("Running study requires its exact frozen implementation")
    bound(study["implementation"]["reporter"])
    for name in ("http_proxy","https_proxy","all_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"):
        os.environ.pop(name, None)
    os.environ.update(NO_PROXY="127.0.0.1,localhost", CUDA_VISIBLE_DEVICES="")
    from throughput_profile import install
    _, stop, thread = install(ROOT / "telemetry")
    state_path = ROOT / "study-state.json"
    state = read_json(state_path) if state_path.exists() else {"study_id":study["study_id"],"status":"running","phase":"searches","searches":{},"final":{}}
    if state["study_id"] != study["study_id"]:
        raise ValueError("Study resume requires identical frozen configuration")
    def save():
        state["updated_at"] = time.time()
        atomic_write_json(state_path, state, immutable=False)
    def environment(protocol, run_root):
        scoring = read_json(bound(protocol.document["scoring_config"]))
        os.environ["AGENTREVIEW_QWEN27B_MODE_LEASE_PATH"] = scoring["lease_path"]
        os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(run_root / "telemetry")
    try:
        with (ROOT / "study.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            state.update(status="running", pid=os.getpid())
            save()
            for spec in study["searches"]:
                seed, run_root = spec["seed"], Path(spec["run_root"])
                protocol = load_experiment_protocol(bound(spec["protocol"]))
                environment(protocol, run_root)
                launch = run_root / "launched.json"
                if not launch.exists():
                    atomic_write_json(launch, {"started":time.time(),"protocol":spec["protocol"],"independent_search_seed":seed})
                state.update(phase="searches", active_search_seed=seed)
                save()
                host = LocalSharedBackend(protocol=protocol, output_root=run_root / "search")
                engine = SharedEvolution(protocol=protocol, output_root=run_root / "search", backend=host)
                evidence = run_root / "repair-evidence.json"
                started = time.monotonic()
                result = engine.run(repair_evidence=evidence if evidence.exists() else None)
                timing = run_root / "phase-timings.json"
                times = read_json(timing) if timing.exists() else {}
                times["search_seconds"] = times.get("search_seconds", 0) + time.monotonic() - started
                atomic_write_json(timing, times, immutable=False)
                state["searches"][str(seed)] = {"status":result["status"], "attempts_recorded":len(result["attempts"]),
                    "checkpoint":str(engine.state_path)}
                save()
                if result["status"] not in {"complete","ended"} or result.get("winner") is None:
                    state.update(status="paused", reason=f"Search {seed} requires diagnosis; no final evaluation opened")
                    save()
                    return
                frozen = result["winner"]
                for key, assessment in frozen["assessments"].items():
                    host.validate(assessment, recipe=frozen["recipes"][key], epoch=assessment["evaluation_epoch"])
                dev = run_root / "development-final.json"
                if not dev.exists():
                    started = time.monotonic()
                    winner = frozen["winner"]
                    host.frozen_snapshots[winner["candidate_id"]] = winner["identity"]["snapshots"]
                    recipe = {**result["recipes"][winner["key"]], "key":"champion-development-final"}
                    assessment = host.assess(recipe, epoch=engine.epoch)
                    host.validate(assessment, recipe=recipe, epoch=engine.epoch)
                    atomic_write_json(dev, {"recipe":recipe,"result":assessment,"selection_changed":False})
                    times["development_final_seconds"] = time.monotonic() - started
                atomic_write_json(timing, times, immutable=False)
                dev_record = read_json(dev)
                host.validate(dev_record["result"], recipe=dev_record["recipe"], epoch=engine.epoch)
                state["searches"][str(seed)]["frozen_system"] = binding(run_root / "search/winner.json")
                save()
            freeze_all = {"protocol":"three-search-freeze-v1","status":"all_frozen", "random_seeds":list(SEEDS),
                "generation_identity":generation_identity(), "systems":[{"seed":spec["seed"], "protocol":spec["protocol"],
                    "frozen_system":state["searches"][str(spec["seed"])]["frozen_system"]} for spec in study["searches"]]}
            atomic_write_json(ROOT / "all-systems-frozen.json", freeze_all)
            for spec in study["searches"]:
                seed, run_root = spec["seed"], Path(spec["run_root"])
                protocol = load_experiment_protocol(bound(spec["protocol"]))
                environment(protocol, run_root)
                state.update(phase="final_evaluation", active_search_seed=seed)
                save()
                started = time.monotonic()
                final = run_shared_final_test(protocol_path=bound(spec["protocol"]), output_root=run_root / "final",
                    frozen_system_path=run_root / "search/winner.json")
                timing = run_root / "phase-timings.json"
                times = read_json(timing)
                times["final_seconds"] = times.get("final_seconds", 0) + time.monotonic() - started
                atomic_write_json(timing, times, immutable=False)
                state["final"][str(seed)] = {"status":final["status"], "checkpoint":str(run_root / "final/final-test.json")}
                save()
                if final["status"] != "complete":
                    state.update(status="paused", reason="Final evaluation failed; no score imputation or evolutionary feedback")
                    save()
                    return
                from paired_benchmark_report import report
                report(run_root)
            aggregate_report(study)
            state.update(status="complete", phase="reported_all_three")
            save()
    except Exception as exc:
        state.update(status="paused", error={"type":type(exc).__name__,"message":str(exc)})
        save()
        raise
    finally:
        stop.set()
        thread.join(timeout=12)


def aggregate_report(study):
    import csv
    from paired_benchmark_report import paired_summary
    rows, champions = [], {}
    for spec in study["searches"]:
        root = Path(spec["run_root"])
        summary = read_json(root / "report/paired-summary.json")
        data = list(csv.DictReader((root / "report/paired-paper-deltas.csv").open()))
        champions[spec["seed"]] = {row["paper_id"]:float(row["champion"]) for row in data}
        families = {row["paper_id"]:row["work_family"] for row in data}
        rows.append({"search_seed":spec["seed"],"summary":binding(root / "report/paired-summary.json"),
            "primary_results":{key:summary["comparisons"][key] for key in summary["primary_control_keys"]},
            "throughput":summary["throughput"]})
    differences = {}
    for index, seed in enumerate(SEEDS):
        for other in SEEDS[index+1:]:
            differences[f"{seed}_minus_{other}"] = paired_summary(champions[seed], champions[other], families, seed=20260917, resamples=10000)
    atomic_write_json(ROOT / "three-search-results.json", {"status":"complete","paper_count":1300,
        "prespecified_search_seeds":list(SEEDS),"runs":rows,"between_champion_differences":differences,
        "test_based_run_selection":False,"all_results_reported":True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare","freeze","run"))
    args = parser.parse_args()
    {"prepare":prepare,"freeze":freeze,"run":run}[args.action]()
