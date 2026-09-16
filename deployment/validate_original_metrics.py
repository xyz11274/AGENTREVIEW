"""Run the original BLEU/ROUGE/BERTScore implementation offline on a fixed probe."""
import argparse
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=REPO / "artifacts/deployment/original-coco")
    parser.add_argument("--coco-root", type=Path, default=REPO.parent / "CoCoReviewBench")
    args = parser.parse_args()
    assets = args.assets.resolve()
    model = json.loads((assets / "bert-model.json").read_text())
    os.environ.update(HF_HOME=model["hf_home"], HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM="false")
    sys.path[:0] = [str(REPO / "AgentReview"), str(args.coco_root), str(args.coco_root / "src/evaluation")]
    from agentreview.research_v2.io import atomic_write_json, file_sha256
    from agentreview.research_v2.development_coco import _validate_bert_cache
    _validate_bert_cache({"hf_home": model["hf_home"], "bert_score_model": {"root": model["snapshot"]}})
    from _4_evaluation import compute_full_text_metrics
    probe = "The paper presents a clear mathematical analysis with supporting experiments."
    metrics, details = compute_full_text_metrics([probe], [[probe]])
    if not all(abs(metrics[name] - 1.0) < 1e-5 for name in ("bleu", "rougeL", "bertscore_f1")):
        raise RuntimeError(f"Identical text probe did not produce expected similarity scores: {metrics}")
    report = {"status": "complete", "type": "original-metrics-offline-probe", "metrics": metrics,
              "details": details, "device": "cpu", "bert_model": model,
              "coco_evaluation_sha256": file_sha256(args.coco_root / "src/evaluation/_4_evaluation.py"),
              "real_generative_model_acceptance": False}
    atomic_write_json(assets / "metrics-validation.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
