"""Check the standalone inputs or invoke the unchanged 0915 study runner."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config/0930"
RUN_ROOT = REPO / "artifacts/study-0930"
sys.path.insert(0, str(REPO / "AgentReview"))


def reject_old_checkout_reads(event, args):
    """Fail if validation accidentally opens data or code in a sibling version."""
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0])
        if path.is_absolute():
            for part in path.parts:
                if part.startswith("AgentReview_0") and part != REPO.name:
                    raise RuntimeError(f"0930 attempted to read an old checkout: {path}")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def check():
    sys.addaudithook(reject_old_checkout_reads)
    from agentreview.research_v2.experiment_protocol import load_experiment_protocol
    from agentreview.research_v2.io import read_json
    from agentreview.research_v2.shared_experiment import runtime_identity

    inventory = read_json(REPO / "metadata/inputs.sha256.json")
    for relative, expected in inventory.items():
        path = REPO / relative
        if not path.resolve().is_relative_to(REPO) or digest(path) != expected:
            raise ValueError(f"Local input missing, changed, or outside this checkout: {relative}")
    proof = read_json(REPO / "metadata/0930-migration.json")
    for relative, evidence in proof["paper_text_preserved"].items():
        paper = read_json(REPO / relative)
        if hashlib.sha256(paper["text"].encode()).hexdigest() != evidence["text_sha256"]:
            raise ValueError(f"Full paper text differs from 0915: {relative}")
    from agentreview.research_v2.io import value_sha256
    for path in (REPO / "data/original").rglob("*.json"):
        value = read_json(path)
        for field in ("manifest_sha256", "split_sha256"):
            if field in value and value[field] != value_sha256({k:v for k,v in value.items() if k != field}):
                raise ValueError(f"Original data self hash differs: {path}")
    counts = None
    for seed in (0, 1000, 2000):
        protocol = load_experiment_protocol(CONFIG / f"protocol.seed-{seed}.frozen.json")
        observed = {split: len(rows) for split, rows in protocol.splits.items()}
        if observed != {"build": 125, "static": 25, "rotating": 125, "sealed_test": 1300}:
            raise ValueError(f"Split counts changed: {observed}")
        counts = observed
        for rows in protocol.splits.values():
            for row in rows:
                for ref in row["assets"].values():
                    if not Path(ref["path"]).resolve().is_relative_to(REPO / "data"):
                        raise ValueError(f"Dataset asset is not local: {ref['path']}")
    study = read_json(CONFIG / "study.frozen.json")
    if study["implementation"]["runtime"] != runtime_identity():
        raise ValueError("Runtime changed after baseline freeze; refreeze a new experiment before running")
    return {"status": "passed", "local_input_files": len(inventory), "splits": counts,
            "protocols": 3, "full_papers_preserved": len(proof["paper_text_preserved"]),
            "old_checkout_reads": 0, "model_calls": 0,
            "runtime_services_checked": False}


def run():
    check()
    # This is an explicit run command, never invoked by check or setup.
    # Keep all three searches and the freeze-all-before-test barrier from 0915.
    from agentreview.research_v2.io import read_json
    from agentreview.research_v2.shared_experiment import bound
    import three_search_study

    study = read_json(CONFIG / "study.frozen.json")
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    source = CONFIG / "study.frozen.json"
    target = RUN_ROOT / source.name
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise ValueError("Run root belongs to a different frozen study")
    if not target.exists():
        shutil.copy2(source, target)
    for spec in study["searches"]:
        root = Path(spec["run_root"])
        root.mkdir(parents=True, exist_ok=True)
        source = bound(spec["protocol"])
        target = root / "protocol.frozen.json"
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError("Search root belongs to a different frozen protocol")
        if not target.exists():
            shutil.copy2(source, target)
    three_search_study.ROOT = RUN_ROOT
    three_search_study.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run"))
    args = parser.parse_args()
    if args.action == "check":
        print(json.dumps(check(), ensure_ascii=False, indent=2))
    else:
        run()
