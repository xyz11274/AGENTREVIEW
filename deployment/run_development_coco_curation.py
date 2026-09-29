"""Run original CoCo development-reference stages with complete-output receipts.

Two isolated paper processes share the local Qwen service. Upstream source,
prompts, parsing and postprocessing are imported unchanged. Outer checks reject
incomplete annotations before any dependent stage can consume them.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

from prepare_development_coco_references import (
    REPO, ROOT, OUT, COCO, binding, read, read_jsonl, immutable,
    json_bytes, jsonl_bytes, upstream,
)

RUN = OUT / "qwen-four-gpu-v1"
STAGES = ("discussion_split", "block_classify", "sentence_alignment", "sentence_refinement")
sys.path[:0] = [str(REPO / "AgentReview"), str(COCO / "src/benchmark"), str(COCO)]
from agentreview.research_v2.coco_integrity import audited_client_class
from agentreview.research_v2.io import atomic_write_json
from src.utils import LLMClient


def stamp():
    return datetime.datetime.now().astimezone().isoformat()


def stage_client(directory, validator):
    base = audited_client_class(LLMClient)

    class CheckedClient(base):
        def get_result(self, key, clear_cache=False):
            result = super().get_result(key, clear_cache=clear_cache)
            validator(result, self.audit_tasks[key])
            return result

    return CheckedClient(api_key="local-vllm", base_url="http://127.0.0.1:18001/v1",
                         model_name="qwen3.8-27b", max_workers=4, cache=True,
                         cache_dir=directory, cache_version="paired-curation-original-prompts-v1")


def reject_empty(result, task):
    if not isinstance(result.get("content"), str) or not result["content"].strip():
        raise ValueError("Empty curation response is not an annotation")


def validate_split_response(module, expected, result, task):
    reject_empty(result, task)
    parsed = module.parse_llm_output(result["content"])
    actual = {i for ids in parsed.values() for i in ids}
    if actual != expected or any(not (type(label) is int and label >= 0 or label == "N/A") for label in parsed):
        raise ValueError(f"Discussion split lacks exact part coverage: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")


def validate_blocks(record, key):
    sentences = record.get("sentence_texts", [])
    blocks = record.get(key, [])
    if not sentences or not blocks:
        raise ValueError(f"Missing {key} or canonical sentences")
    for block in blocks:
        if len(block) != 2 or not isinstance(block[1], list) or not block[1] or None in block[1]:
            raise ValueError(f"Incomplete categories in {key}")
        for role, ids in block[0]:
            if not isinstance(role, str) or any(type(i) is not int or not 0 <= i < len(sentences) for i in ids):
                raise ValueError(f"Invalid sentence identity in {key}")


def run_stage(name, paper, source, inputs, prepared):
    directory = RUN / "papers" / paper["id"] / name
    receipt_path = directory / "receipt.json"
    if receipt_path.exists():
        receipt = read(receipt_path)
        if receipt["status"] != "complete" or receipt["inputs"] != inputs:
            raise ValueError("Stage resume inputs differ from the complete receipt")
        if binding(Path(receipt["output"]["path"])) != receipt["output"]:
            raise ValueError("Stage output changed after validation")
        return read(Path(receipt["output"]["path"])), receipt
    directory.mkdir(parents=True, exist_ok=True)
    for number in (1, 2):
        attempt_dir = directory / f"attempt-{number}"
        finished = attempt_dir / "result.json"
        if finished.exists():
            # Failed attempts stay consumed across restarts. A successful
            # attempt always commits its enclosing receipt before returning.
            old = read(finished)
            if old["status"] == "failed":
                continue
            raise ValueError("Uncommitted successful attempt requires recovery inspection")
        if attempt_dir.exists():
            raise ValueError("Interrupted stage attempt requires inspection before retry")
        attempt_dir.mkdir()
        started = time.time()
        atomic_write_json(attempt_dir / "started.json", {"paper_id": paper["id"], "stage": name,
            "attempt": number, "started": started, "inputs": inputs})
        client = None
        previous = Path.cwd()
        try:
            os.chdir(attempt_dir)
            args = SimpleNamespace(org="eval", year=2026, model="qwen3.8-27b", effort="medium")
            if name == "discussion_split":
                module = upstream("curation_original_split", "src/benchmark/_1_split_reviews.py")
                expected = set(range(1, prepared["part_count"] + 1))
                client = stage_client(attempt_dir / "requests", lambda result, task:
                    validate_split_response(module, expected, result, task))
                module.client = client
                immutable(attempt_dir / "pre_eval/evaluation.jsonl", jsonl_bytes([source]))
                (attempt_dir / "pre_eval/split").mkdir(parents=True)
                module.classify_all(args)
                outputs = read_jsonl(attempt_dir / "pre_eval/split/evaluation_qwen3.8-27b_medium_split.jsonl")
                if len(outputs) != 1 or outputs[0]["id"] != paper["id"]:
                    raise ValueError("Original split lost or changed the paper")
                result = outputs[0]
                if len(result["parsed"]) != len(result["split_texts"]) or {
                        i for ids in result["parsed"] for i in ids} != expected:
                    raise ValueError("Postprocessed discussion split lost parts")
                if not client.audit_tasks or any(task["messages"] != prepared["messages"] for task in client.audit_tasks.values()):
                    raise ValueError("Submitted discussion differs from the checked original prompt")
            elif name == "block_classify":
                module = upstream("curation_original_classify", "src/benchmark/_2_classify_reviews.py")
                _, main = module.classify_reviews_prompt()
                allowed, _ = module.extract_labels_from_prompt_text(main)
                allowed.add("N/A")
                def check_classification(result, task):
                    reject_empty(result, task)
                    labels = module.parse_llm_output(result["content"], allowed)
                    if not labels or any(label not in allowed for label in labels):
                        raise ValueError("Unparsed or invalid block category")
                client = stage_client(attempt_dir / "requests", check_classification)
                module.client = client
                # The unchanged upstream CLI fixes this input basename to
                # gpt-5. This is only an input alias; receipts identify Qwen.
                immutable(attempt_dir / "pre_eval/split/evaluation_gpt-5_medium_split.jsonl", jsonl_bytes([source]))
                module.classify_all(args)
                outputs = read_jsonl(attempt_dir / "pre_eval/classify/evaluation_qwen3.8-27b_medium_classify.jsonl")
                if len(outputs) != 1 or outputs[0]["id"] != paper["id"]:
                    raise ValueError("Original classify lost or changed the paper")
                result = outputs[0]
                if len(result["classify"]) != len(source["split_texts"]) - 1:
                    raise ValueError("Incomplete classified block coverage")
                if any(not tags or any(tag not in allowed for tag in tags) for tags in result["classify"]):
                    raise ValueError("Incomplete classification")
            elif name == "sentence_alignment":
                module = upstream("curation_original_alignment", "src/benchmark/_3_preprocess_file.py")
                full = copy.deepcopy(source)
                for key in ("decision", "reviews", "metareview"):
                    full[key] = copy.deepcopy(paper[key])
                result = module.process_paper(full, min_words=0, strict_commas=True)
                validate_blocks(result, "split_texts")
            elif name == "sentence_refinement":
                module = upstream("curation_original_refine", "src/benchmark/_3_refine_split_and_classify.py")
                client = stage_client(attempt_dir / "requests", reject_empty)
                module.client = client
                result, count = module.process_single_record(args, 0, json.dumps(source, ensure_ascii=False))
                if result is None:
                    raise ValueError("Refinement skipped the paper")
                validate_blocks(result, "new_split_texts")
                expected = {(idx, role, sid) for idx, block in enumerate(source["split_texts"])
                            for role, ids in block[0] for sid in ids}
                actual = set()
                for group in result["classify_new"]:
                    for row in group:
                        if row.get("parsing_status") in {"failed", "no_response"} or not row["label"] or None in row["label"]:
                            raise ValueError("Refinement has an unhandled sentence-label failure")
                        actual.add((row["block_idx"], row["role"], row["sentence_id"]))
                if actual != expected:
                    raise ValueError("Refinement did not label every original sentence/role/block")
            else:
                raise ValueError(name)
            output = immutable(attempt_dir / "output.json", json_bytes(result))
            audits = [binding(client.cache_dir / f"{key}.audit.json") for key in client.audit_tasks] if client else []
            receipt = {"status": "complete", "paper_id": paper["id"], "stage": name, "attempt": number,
                       "inputs": inputs, "output": output, "request_receipts": audits,
                       "started": started, "ended": time.time(), "seconds": time.time()-started,
                       "model": "qwen3.8-27b" if client else None,
                       "original_module": binding(Path(module.__file__)), "scoring_ready": False}
            immutable(receipt_path, json_bytes(receipt))
            atomic_write_json(finished, receipt)
            return result, receipt
        except Exception as exc:
            atomic_write_json(finished, {"status": "failed", "paper_id": paper["id"], "stage": name,
                "attempt": number, "started": started, "ended": time.time(),
                "error_type": type(exc).__name__, "error": str(exc), "downstream_eligible": False})
            print(json.dumps({"event": "stage_failed", "paper_id": paper["id"], "stage": name,
                              "attempt": number, "error": str(exc)}, ensure_ascii=False), flush=True)
        finally:
            os.chdir(previous)
            if client:
                client.shutdown()
    raise RuntimeError(f"{paper['id']}/{name}: two stage attempts exhausted; no downstream admission")


def paper_worker(split, paper_id):
    paper = next(row for row in read_jsonl(OUT / "sources" / f"{split}.jsonl") if row["id"] == paper_id)
    prepared = next(row for row in read_jsonl(OUT / "prepared" / split / "discussion-split.requests.jsonl") if row["id"] == paper_id)
    source, inputs = paper, {"source": binding(OUT / "sources" / f"{split}.jsonl"),
                            "prepared_prompts": binding(OUT / "prepared" / split / "discussion-split.requests.jsonl")}
    receipts = []
    for stage in STAGES:
        atomic_write_json(RUN / "papers" / paper_id / "progress.json", {
            "paper_id": paper_id, "split": split, "stage": stage, "status": "running", "updated_at": stamp()}, immutable=False)
        source, receipt = run_stage(stage, paper, source, inputs, prepared)
        receipts.append(binding(RUN / "papers" / paper_id / stage / "receipt.json"))
        inputs = {"previous_stage_receipt": receipts[-1], "original_discussions": binding(OUT / "sources" / f"{split}.jsonl")}
    immutable(RUN / "papers" / paper_id / "through-refinement.json", json_bytes({
        "status": "complete", "paper_id": paper_id, "split": split, "stages": receipts,
        "scoring_ready": False, "next_stage": "discussion_clustering"}))


def sample_metrics():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:18001/metrics", timeout=3) as response:
            lines = response.read().decode().splitlines()
        selected = [line for line in lines if not line.startswith("#") and any(key in line for key in (
            "num_requests_running", "num_requests_waiting", "kv_cache_usage_perc", "num_preemptions", "generation_tokens_total"))]
        return {"checked_at": stamp(), "metrics": selected}
    except Exception as exc:
        return {"checked_at": stamp(), "error": str(exc)}


class AdoptedWorker:
    """Observe a surviving owned paper process without duplicating its requests."""
    def __init__(self, pid, identity):
        self.pid, self.identity = pid, identity

    def poll(self):
        try:
            root = Path(f"/proc/{self.pid}")
            fields = (root / "stat").read_text().rsplit(") ", 1)[1].split()
            identity = (root.stat().st_uid, fields[19], (root / "cmdline").read_bytes())
            if fields[0] != "Z" and identity == self.identity:
                return None
        except FileNotFoundError:
            pass
        # A reparented worker's exit status is unavailable. Inspect its complete
        # receipts, then resume through the same finite-attempt stage gates.
        return 75


def find_surviving_worker(paper_id, split):
    expected = [str(Path(__file__).resolve()), "--paper", paper_id, "--split", split]
    for root in Path("/proc").iterdir():
        if not root.name.isdigit():
            continue
        try:
            if root.stat().st_uid != os.getuid():
                continue
            cmdline = (root / "cmdline").read_bytes()
            args = [value.decode() for value in cmdline.split(b"\0") if value]
            if args[1:] != expected and args[1:] != ["-u", *expected]:
                continue
            fields = (root / "stat").read_text().rsplit(") ", 1)[1].split()
            if fields[0] != "Z":
                return AdoptedWorker(int(root.name), (root.stat().st_uid, fields[19], cmdline))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return None


def orchestrate():
    RUN.mkdir(parents=True, exist_ok=True)
    lock = (RUN / "run.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for key in list(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            os.environ.pop(key)
    os.environ.update(NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1", CUDA_VISIBLE_DEVICES="")
    profile = REPO / "artifacts/shared-throughput-20260917/execution-paper2.json"
    os.environ["AGENTREVIEW_EXECUTION_PROFILE"] = str(profile)
    os.environ["AGENTREVIEW_EXECUTION_PROFILE_SHA256"] = binding(profile)["sha256"]
    source = read(OUT / "preparation-receipt.json")
    service = read(OUT / "service-start-four-gpu.json")
    if service["status"] != "ready" or source["status"] != "complete":
        raise ValueError("Validated prepared sources and ready owned service required")
    from coco_services import process_identity
    expected_process = service["after"]["process"]
    if process_identity(expected_process["pid"]) != expected_process or expected_process["uid"] != os.getuid():
        raise ValueError("Service receipt is not the current owned process; copied host receipts are not readiness")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:18001/v1/models", timeout=5) as response:
        models = json.load(response)["data"]
    if not any(row["id"] == "qwen3.8-27b" and row.get("max_model_len") == 131072 for row in models):
        raise ValueError("Current curation service identity or complete-context capacity differs")
    config = {"purpose": "new development-reference curation, not evolution or test evaluation",
        "original_source": binding(OUT / "preparation-receipt.json"), "runner": binding(Path(__file__).resolve()),
        "service": binding(OUT / "services-four-gpu.json"), "service_readiness": binding(OUT / "service-start-four-gpu.json"),
        "execution_profile": binding(profile), "paper_workers": 2, "request_workers_per_paper": 4,
        "model": "qwen3.8-27b", "reasoning_effort": "medium", "upstream_temperature": 0.6,
        "stage_attempts": 2, "transport_attempts_per_payload": 3,
        "failure_policy_scope": "reference curation only; does not set MSTAR search budgets",
        "stages": list(STAGES), "original_coco_scoring_modified": False, "scoring_ready": False}
    configuration_path = RUN / "configuration.json"
    if configuration_path.exists():
        original = read(configuration_path)
        if {k: v for k, v in original.items() if k != "runner"} != {k: v for k, v in config.items() if k != "runner"}:
            raise ValueError("Curation conditions changed; use a separately versioned run")
    else:
        immutable(configuration_path, json_bytes(config))
    runner_snapshot = RUN / "implementation" / f"{config['runner']['sha256']}.py"
    immutable(runner_snapshot, Path(__file__).read_bytes())
    immutable(RUN / "implementation" / f"{config['runner']['sha256']}.json", json_bytes({
        "runner": binding(runner_snapshot), "configuration": binding(configuration_path),
        "scope": "controller implementation; upstream model requests and curation conditions unchanged"}))
    queue = [(split, row["id"]) for split in ("static", "rotating") for row in read_jsonl(OUT / "sources" / f"{split}.jsonl")]
    active, completed, failures = {}, [], []
    prior_progress = RUN / "progress.json"
    started = read(prior_progress).get("started", time.time()) if prior_progress.exists() else time.time()
    def launch(item):
        split, pid = item
        surviving = find_surviving_worker(pid, split)
        if surviving:
            active[pid] = (surviving, split)
            return
        target = RUN / "papers" / pid
        target.mkdir(parents=True, exist_ok=True)
        log = (target / "worker.log").open("ab")
        proc = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--paper", pid, "--split", split],
                                stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy())
        log.close()
        active[pid] = (proc, split)
    while queue or active:
        while queue and len(active) < 2 and not failures:
            item = queue.pop(0)
            if (RUN / "papers" / item[1] / "through-refinement.json").exists():
                completed.append(item[1])
            else:
                launch(item)
        for pid, (proc, split) in list(active.items()):
            code = proc.poll()
            if code is None:
                continue
            if code in {0, 75} and (RUN / "papers" / pid / "through-refinement.json").exists():
                completed.append(pid)
            elif code == 75:
                queue.insert(0, (split, pid))
            else:
                failures.append({"paper_id": pid, "split": split, "exit_code": code,
                                 "log": str(RUN / "papers" / pid / "worker.log")})
            del active[pid]
        receipts = {stage: len(list((RUN / "papers").glob(f"*/{stage}/receipt.json"))) for stage in STAGES}
        state = {"status": "draining_after_failure" if failures else "running", "updated_at": stamp(),
                 "started": started, "elapsed_seconds": time.time()-started,
                 "total_papers": 150, "completed_through_refinement": len(completed),
                 "completed_by_stage": receipts, "active_papers": list(active), "queued": len(queue),
                 "failures": failures, "scoring_ready": False, "valid_scored_reviews": 0,
                 "scope": "development reference annotation; these are not evaluated system reviews"}
        atomic_write_json(RUN / "progress.json", state, immutable=False)
        with (RUN / "metrics.jsonl").open("a") as stream:
            stream.write(json.dumps(sample_metrics()) + "\n")
        if failures and not active:
            state["status"] = "failed_requires_diagnosis"
            atomic_write_json(RUN / "progress.json", state, immutable=False)
            raise RuntimeError("Curation stopped after bounded failure; inspect preserved requests")
        if active:
            time.sleep(10)
    state.update(status="complete_through_refinement", next_stage="discussion_clustering", active_papers=[])
    atomic_write_json(RUN / "progress.json", state, immutable=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper")
    parser.add_argument("--split", choices=("static", "rotating"))
    args = parser.parse_args()
    if args.paper:
        paper_worker(args.split, args.paper)
    else:
        orchestrate()
