"""Real native review -> original CoCo scoring acceptance on an exposed development paper.

This is the scoring integration gate. Full shared Memory/Evolution acceptance and
formal execution still require a separate complete experiment protocol.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.coco_native import generate_native_review
from agentreview.research_v2.development_coco import (
    _validate_config, evaluate_final_reviews, validate_assessment_receipt,
)
from agentreview.research_v2.development_coco_worker import _service_ready
from agentreview.research_v2.io import atomic_write_json, file_sha256, value_sha256
from agentreview.research_v2.llm import OpenAICompletionClient


def binding(path):
    return {"path": str(path), "sha256": file_sha256(path)}


def readiness(services):
    results = {}
    for role, service in services.items():
        try:
            results[role] = {"status": "ready", "runtime": _service_ready(service)}
        except Exception as exc:
            results[role] = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "run"])
    parser.add_argument("--assets", type=Path, default=REPO / "artifacts/deployment/original-coco")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    assets = args.assets.resolve()
    pending = json.loads((assets / "services.pending.json").read_text())
    services = {name: {**service, "model_identity": {"root": service["model_root"]}}
                for name, service in pending["services"].items()}
    state = readiness(services)
    all_ready = all(value["status"] == "ready" for value in state.values())
    report = {"protocol": "original-coco-development-readiness-v1", "services": state,
              "scoring_config_exists": (assets / "scoring.frozen.json").is_file(),
              "real_model_acceptance_complete": False}
    atomic_write_json(assets / "readiness.json", report, immutable=False)
    print(json.dumps(report, indent=2))
    if args.action == "check":
        return 0 if all_ready else 2
    if not all_ready:
        raise RuntimeError("Real model acceptance requires all three local services; no generation was submitted")
    config_path = assets / "scoring.frozen.json"
    config = json.loads(config_path.read_text())
    _validate_config(config)
    output = (args.output or assets / "native-scoring-smoke").resolve()
    # Model defaults use metadata from the actual service installation, not the old handoff versions.
    versions = json.loads(subprocess.check_output([pending["serving_python"], "-c",
        "import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ['vllm','transformers']}))"], text=True))
    model_root = Path(config["services"]["judge"]["model_identity"]["root"])
    model = replace(ModelConfig(), temperature=0.0, enable_thinking=True,
                    vllm_version=versions["vllm"], transformers_version=versions["transformers"],
                    tokenizer_sha256=file_sha256(model_root / "tokenizer.json"),
                    chat_template_sha256=file_sha256(model_root / "chat_template.jinja"))
    inputs = {"paper": binding(assets / "paper.json"), "references": binding(assets / "smoke-references/references.json"),
              "scoring_config": binding(config_path), "model": model.identity(), "native_max_tokens": 32768,
              "purpose": "development acceptance only"}
    atomic_write_json(output / "acceptance-inputs.json", inputs)
    client = OpenAICompletionClient(model)
    try:
        review = generate_native_review(paper_path=assets / "paper.json", coco_root=config["coco_root"],
            run_id="original-coco-development-smoke", output_root=output / "native", completion_client=client,
            model_identity=model.identity(), max_tokens=32768, num_fewshot=0)
    finally:
        client.close()
    manifest = atomic_write_json(output / "reviews.json", {
        "protocol": "research-v2-coco-review-manifest-v1", "partition": "development",
        "paper_ids": [review["paper_id"]], "records": [{"paper_id": review["paper_id"],
            **binding(Path(review["artifact_path"]))}],
    })
    receipt = evaluate_final_reviews(review_manifest_path=manifest,
        reference_manifest_path=assets / "smoke-references/references.json", scoring_config_path=config_path,
        output_root=output / "scoring", partition="development", run_id="original-coco-development-smoke",
        candidate_id="native", evaluation_epoch=0, candidate_identity={"acceptance_input_sha256": value_sha256(inputs)})
    validate_assessment_receipt(receipt)
    atomic_write_json(output / "acceptance.json", {"status": "complete", "kind": "native-generation-and-scoring",
        "assessment": binding(output / "scoring/assessment.json"), "formal_experiment": False,
        "shared_memory_evolution_validated": False})
    print("Real native generation and CoCo scoring passed:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
