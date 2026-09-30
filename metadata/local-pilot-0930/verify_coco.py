"""Verify local Split/Classify and API Judge with a saved complete review."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AgentReview"))
from agentreview.research_v2.development_coco import evaluate_final_reviews, validate_assessment_receipt
from agentreview.research_v2.io import read_json, atomic_write_json

old_root = ROOT / "artifacts/pilot-0930-model-capacity/search/assessments/e0/seed-llm_summarizer/r0/coco/parts"
path = next(p for p in sorted(old_root.glob("*/request.json"))
            if read_json(p)["paper_ids"] == ["f-FQE1fjPK"])
request = read_json(path)
output = ROOT / "artifacts/pilot-0930-local/acceptance"
started = time.time()
summary = {"status": "running", "started_at": started, "old_request": str(path),
           "output_root": str(output), "official_commit": "2580687ee5d4ad2ac4b9730afb8f1dfda22e40db"}
summary_path = ROOT / "metadata/local-pilot-0930/coco-acceptance.json"
atomic_write_json(summary_path, summary, immutable=False)
try:
    result = evaluate_final_reviews(
        review_manifest_path=request["inputs"]["reviews"]["path"],
        reference_manifest_path=request["inputs"]["references"]["path"],
        scoring_config_path=ROOT / "config/0930-pilot-local/scoring.frozen.json",
        output_root=output, partition=request["partition"],
        run_id="0930-local-coco-deployment-acceptance", candidate_id=request["candidate_id"],
        evaluation_epoch=request["evaluation_epoch"], candidate_identity=request["candidate_identity"],
        purpose=request["purpose"], review_target=request["review_target"])
    validate_assessment_receipt(result)
    summary.update(status="complete", mean_score=result["mean_score"],
                   metrics=result["aggregate"], paper_ids=result["paper_ids"],
                   assessment=str(output / "assessment.json"))
except Exception as exc:
    summary.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    raise
finally:
    summary.update(finished_at=time.time(), elapsed_seconds=round(time.time() - started, 3))
    atomic_write_json(summary_path, summary, immutable=False)
    print({k: v for k, v in summary.items() if k != "metrics"}, flush=True)
