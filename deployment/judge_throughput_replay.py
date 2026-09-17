"""Controlled original Judge replay on frozen complete Split/Classify inputs.

This diagnostic is not a new full-flow review and is never admitted to MSTAR.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "root", "assessment", "execution_profile"):
        parser.add_argument("--"+name.replace("_", "-"), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source) / "AgentReview"))
    from throughput_profile import install
    from agentreview.research_v2.io import atomic_write_json, read_json, file_sha256
    from agentreview.research_v2 import development_coco as coco
    from agentreview.research_v2 import development_coco_worker as worker
    from agentreview.research_v2.judge_lease import Qwen27BModeLease
    root = Path(args.root)
    # Match the original worker's local transport environment. This standalone
    # diagnostic otherwise inherits the IDE's proxy and receives spurious 502s.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    os.environ["AGENTREVIEW_TELEMETRY_ROOT"] = str(root)
    os.environ["AGENTREVIEW_EXECUTION_PROFILE"] = args.execution_profile
    os.environ["AGENTREVIEW_EXECUTION_PROFILE_SHA256"] = file_sha256(args.execution_profile)
    event, stop, thread = install(root)
    status = {"status": "running", "started": time.time(), "pid": os.getpid(), "valid_full_flow_reviews": 0,
              "scope": "original Judge replay; fixed complete reviews, Split and Classify; no pool admission"}
    atomic_write_json(root / "status.json", status, immutable=False)
    try:
        receipt = coco.validate_assessment_receipt(args.assessment)
        config = read_json(coco._bound_file(receipt["inputs"]["scoring_config"]))
        coco._validate_config(config)
        os.environ.update(HF_HOME=config["hf_home"], HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CUDA_VISIBLE_DEVICES="")
        source_request = read_json(coco._bound_file(read_json(coco._bound_file(receipt["rows"][0]["raw"]))["request"]))
        source_output = Path(source_request["output_root"])
        classified_result = read_json(coco._bound_file(read_json(source_output / "stages/classify/state.json")["result"]))
        classified = coco._bound_file(classified_result["artifact"])
        request = {**source_request, "output_root": str(root), "diagnostic_reuse": {
            "assessment": coco._binding(args.assessment), "classified": coco._binding(classified)}}
        atomic_write_json(root / "request.json", request)
        before = worker._service_ready(config["services"]["judge"])
        evaluation = worker._load_standard(Path(config["coco_root"]), "_4_evaluation")
        with Qwen27BModeLease("judge"):
            client = worker._client(config, "judge", request)
            try:
                result = worker.evaluate_loaded_standard(evaluation=evaluation, request=request, config=config,
                    references=read_json(coco._bound_file(receipt["inputs"]["references"])), input_file=classified, judge_client=client)
                integrity = worker._integrity(client, request, "evaluation", result["completeness_checks"])
            finally:
                client.shutdown()
        if before != worker._service_ready(config["services"]["judge"]):
            raise ValueError("Judge service changed during controlled replay")
        coco._bound_file(request["diagnostic_reuse"]["classified"])
        coco.validate_stage_integrity({**receipt["stage_integrity"], "evaluation": integrity}, receipt["paper_ids"])
        result.update(integrity=integrity, service_runtime=before, request=coco._binding(root / "request.json"))
        atomic_write_json(root / "result.json", result)
        elapsed = time.time()-status["started"]
        status.update(status="complete", ended=time.time(), seconds=elapsed,
            paper_scores={pid: row["table_1_summary"]["absolute_scores"]["Paper."] for pid, row in result["papers"].items()},
            scored_papers=len(result["papers"]), diagnostic_scored_papers_per_hour=len(result["papers"])*3600/elapsed,
            failed_scores=0, failure_rate=0, result=coco._binding(root / "result.json"))
    except BaseException as exc:
        status.update(status="failed", ended=time.time(), error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        atomic_write_json(root / "status.json", status, immutable=False)
        event({"event": "run_finished", **status})
        stop.set()
        thread.join(timeout=12)
    print(json.dumps(status), flush=True)


if __name__ == "__main__":
    main()
