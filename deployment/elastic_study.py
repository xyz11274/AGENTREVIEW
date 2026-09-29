"""Frozen execution appendix: four paper workers, stage batches, demand-only GPUs 4-6."""
from pathlib import Path
import argparse
import fcntl
import sys

from four_gpu_study import ROOT, DEPLOY as ORIGINAL_DEPLOY, CONFIG as ORIGINAL_CONFIG
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound
from elastic_resources import BatchResources
import batch_execution

DEPLOY = ROOT / "deployment-elastic"
CONFIG = DEPLOY / "configuration.json"
MANIFEST = DEPLOY / "deployment.frozen.json"


def prepare():
    config = {"protocol":"elastic-stage-batches-v1", "base_configuration":binding(ORIGINAL_CONFIG),
        "base_gpus":[0,1,2,3], "borrowable_gpus":[4,5,6], "excluded_gpus":[7],
        "paper_workers":4,"batch_size":25,"stage_workers":2,"aggregate_token_capacity":262144,
        "idle_seconds":30,"event_root":str(DEPLOY),
        "gpu_lock_root":"/data/xyj/.cache/agentreview/gpu-leases",
        "authorization":"User approved four paper workers and on-demand borrowing of GPUs 4-6; GPU 7 permanently excluded",
        "release":"after each completed stage batch or exception; independent guardian releases on controller exit",
        "fallback":"unchanged four-GPU mode rotation if no idle pair is available",
        "model_policy":"original BF16 TP2 Split/Classify only on temporary GPUs; no precision, context, output, temperature or thinking changes"}
    atomic_write_json(CONFIG, config)
    print(CONFIG)


def resources():
    config = read_json(CONFIG)
    base = read_json(bound(config["base_configuration"]))
    base["event_root"] = str(DEPLOY / "rotation")
    return config, BatchResources(config, base)


def run():
    manifest = read_json(MANIFEST)
    for reference in manifest["bindings"].values():
        bound(reference)
    if manifest["bindings"]["entrypoint"] != binding(__file__) or manifest["bindings"]["configuration"] != binding(CONFIG):
        raise ValueError("Elastic execution requires its exact frozen deployment appendix")
    with (ORIGINAL_DEPLOY / "rotation.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config, owner = resources()
        try:
            batch_execution.install(owner, paper_workers=config["paper_workers"], batch_size=config["batch_size"],
                token_capacity=config["aggregate_token_capacity"], deployment_identity=binding(MANIFEST))
            import three_search_study
            three_search_study.run()
        finally:
            owner.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    args = parser.parse_args()
    prepare() if args.action == "prepare" else run()
