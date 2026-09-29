"""Freeze repaired seed assets, then launch the existing full-scale paired study.

Never invent search budgets, inherit pilot state, or overwrite old protocols.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse
import ast
import json
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/coco-paired-resplit-20260917"
SERVICE_CONFIG = REPO / "artifacts/deployment/original-coco/services.fullscale-frozen-20260917.json"
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.evolution import validate_program
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding, bound, runtime_identity
from agentreview.reviewer_runtime.memory_budget import MEMORY_CONTRACT, budget_options


def prepare(decisions_path=None):
    config = read_json(ROOT / "protocol.pending.json")
    validation = ROOT / "audit/memory-repair-validation.json"
    if read_json(validation).get("status") != "passed":
        raise ValueError("Memory repair regression receipt is required")
    if read_json(validation)["runtime"] != runtime_identity():
        raise ValueError("The regression receipt must cover the exact code being frozen")
    for item in read_json(validation)["logs"]:
        bound(item)
    seed_refs = {}
    old_policy = bound(config["non_memory_conditions"]["review_policy_program"])
    def policy(code):
        return ast.dump(next(n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name == "plan_review"), include_attributes=False)
    expected_policy = policy(old_policy.read_text())
    host = LocalSharedBackend(protocol=SimpleNamespace(document=config), output_root=ROOT / "search")
    runtime = host.memory_runtime()
    budget_options(runtime)  # Verify tokenizer bytes before publishing any seed.
    for name in SEED_NAMES:
        source = seed_program_path(name)
        code = source.read_text()
        if policy(code) != expected_policy:
            raise ValueError("Memory repair changed the fixed review policy")
        errors = validate_program(source, require_bubblewrap=True, memory_runtime=runtime, seed_kind=name)
        if errors:
            raise ValueError(f"Invalid repaired seed {name}: {errors}")
        target = ROOT / "inputs/memory-repair-v2" / f"{name}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_text() != code:
                raise ValueError("Repaired frozen seed changed; use a new version directory")
        else:
            with target.open("x") as handle:
                handle.write(code)
        seed_refs[name] = binding(target)
    seed_manifest = atomic_write_json(ROOT / "inputs/original-seeds.json", seed_refs)
    config["initial_memory_programs"] = seed_refs
    config["non_memory_conditions"]["review_policy_program"] = seed_refs["vector_search"]
    services = read_json(SERVICE_CONFIG)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    observations = {}
    for role, service in services["services"].items():
        with opener.open(service["base_url"] + "/models", timeout=10) as response:
            models = json.load(response)["data"]
        observations[role] = next(m for m in models if m["id"] == service["model"])
    if observations["judge"]["max_model_len"] != config["model"]["max_model_len"]:
        raise ValueError("Live generation context differs from the full-paper protocol")
    service_receipt = atomic_write_json(ROOT / "audit/service-readiness" / f"{time.time_ns()}.json", {
        "checked_at": time.time(), "models": observations, "status": "ready", "model_calls": 0,
        "service_configuration": binding(SERVICE_CONFIG)})
    resolved = {"required_model_services_unavailable", "unresolved_memory_feedback_and_summary_truncation",
                "two_original_coco_differential_tests_incomplete", "unconfirmed_full_scale_numeric_budget"}
    blockers = [b for b in config.get("launch_blockers", []) if b["kind"] not in resolved]
    if decisions_path:
        decisions = read_json(decisions_path)
        if decisions.get("confirmed_by_user") is not True:
            raise ValueError("Use explicit study decisions, never pilot defaults")
        if len(decisions["search_random_seeds"]) != 1:
            raise ValueError("Multiple search seeds require separate run roots and freeze-all scheduling")
        for key in ("attempts", "rotating_sample_size", "parent_temperature"):
            config["evolution"][key] = decisions[key]
        config["evolution"]["random_seed"] = decisions["search_random_seeds"][0]
        config["failure_policy"] = decisions["failure_policy"]
        config["study_decisions"] = binding(decisions_path)
    else:
        blockers.append({"kind": "unconfirmed_full_scale_numeric_budget", "fields": [
            "attempts", "rotating_sample_size", "search_random_seeds", "parent_temperature", "failure_policy"]})
    freeze = atomic_write_json(ROOT / "audit/memory-implementation.frozen.json", {
        "contract": MEMORY_CONTRACT, "runtime": runtime_identity(), "initial_programs": binding(seed_manifest),
        "upstream_mstar_commit": "09a8d34f69588a85fde78b20702d14f056267956", "validation": binding(validation)})
    config["memory_repair"] = {"implementation": binding(freeze), "services": binding(service_receipt),
                               "adapter": binding(__file__), "memory_runtime": runtime}
    config["service_configuration"] = binding(SERVICE_CONFIG)
    config["launch_blockers"] = blockers
    config["launch_policy"] = {"reason": "Full-size existing-benchmark study with repaired original seed families; no pilot or tuning."}
    pending = atomic_write_json(ROOT / "protocol.memory-fixed.pending.json", config, immutable=False)
    atomic_write_json(ROOT / "readiness.memory-fixed.json", {"status": "blocked" if blockers else "ready_to_freeze",
        "protocol": binding(pending), "implementation": binding(freeze), "blockers": blockers,
        "counts": {split: len(read_json(bound(ref))["records"]) for split, ref in config["splits"].items()},
        "search_started": False, "test_started": False, "new_pilot_required": False}, immutable=False)
    print(json.dumps({"protocol": str(pending), "blockers": blockers}, ensure_ascii=False, indent=2))
    return pending


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--decisions")
    args = parser.parse_args()
    if args.action == "run" and (ROOT / "protocol.frozen.json").is_file():
        if args.decisions:
            raise ValueError("Resume uses the existing frozen protocol; do not replace study decisions")
        import paired_benchmark
        paired_benchmark.ROOT = ROOT
        paired_benchmark.run(ROOT / "protocol.frozen.json")
    else:
        pending = prepare(args.decisions)
        if args.action == "run":
            import paired_benchmark
            paired_benchmark.ROOT = ROOT
            frozen = paired_benchmark.freeze(pending)
            paired_benchmark.run(frozen)
