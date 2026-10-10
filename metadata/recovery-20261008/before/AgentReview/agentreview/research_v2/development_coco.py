"""Partition-bound standard CoCo scoring of saved, complete review manifests.

The worker executes the benchmark's own split, classification and Table-1
functions. This module owns artifact identity, finite stage attempts and
complete-cohort admission. Benchmark scoring code stays in CoCoReviewBench.
"""

from __future__ import annotations

import fcntl
import math
import os
import subprocess
import threading
import copy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .io import atomic_write_json, file_sha256, read_json, read_jsonl, value_sha256, write_immutable_jsonl
from .config import ModelConfig
from .execution_telemetry import timed

ORIGINAL_REQUEST_SETTINGS = {
    "split": {"max_completion_tokens": 32768, "effort": "medium", "max_workers": 64},
    "classify": {"max_completion_tokens": 12288, "effort": "medium", "max_workers": 128},
    "judge": {"max_completion_tokens": 32768, "effort": "medium", "temperature": 0, "max_workers": 128},
}


def _python_runtime(executable: str | Path) -> dict[str, Any]:
    """Bind the scoring interpreter and the actual packages used by CoCo."""
    script = (
        "import importlib.metadata as m,json,sys; "
        "names=['openai','torch','transformers','numpy','requests','nltk','rouge-score','bert-score']; "
        "print(json.dumps({'python':sys.version,'packages':{n:m.version(n) for n in names}}))"
    )
    import json
    observed = json.loads(subprocess.check_output([str(executable), "-s", "-B", "-c", script], text=True))
    return {"executable_sha256": file_sha256(executable), **observed}


def _validate_bert_cache(value: dict[str, Any]) -> None:
    """Original BERTScore loads roberta-large by name; bind its offline resolution."""
    cache = Path(value["hf_home"]) / "hub/models--roberta-large"
    revision = (cache / "refs/main").read_text().strip()
    snapshot = (cache / "snapshots" / revision).resolve(strict=True)
    if snapshot != Path(value["bert_score_model"]["root"]):
        raise ValueError("Original BERTScore roberta-large cache resolves to a different frozen model")

SCORING_PROTOCOL = "research-v2-coco-deepseek-judge-v2"
RECEIPT_PROTOCOL = "research-v2-development-coco-receipt-v1"
PAPER_PROTOCOL = "research-v2-development-coco-paper-v1"
REVIEW_MANIFEST_PROTOCOL = "research-v2-coco-review-manifest-v1"
REFERENCE_MANIFEST_PROTOCOL = "research-v2-coco-reference-manifest-v1"
CONFIG_PROTOCOL = "research-v2-coco-api-judge-config-v2"
COLUMNS = ("BLEU", "ROUGE-L", "BERT.", "Correct.", "Thoro.", "Ground.", "Verify.", "Clarity", "Average", "Paper.", "Complete.")


class DevelopmentCoCoFailure(RuntimeError):
    """A shared data, standard-scoring or runtime failure closes admission."""

    def __init__(self, record: dict[str, Any]):
        self.failure_record = record
        self.failure_category = record.get("category", "shared_evaluation")
        super().__init__(record["message"])


def _binding(path: str | Path) -> dict[str, str]:
    source = Path(path).expanduser().resolve(strict=True)
    return {"path": str(source), "sha256": file_sha256(source)}


def _bound_file(value: Any) -> Path:
    if isinstance(value, dict) and set(value) >= {"path", "sha256"}:
        source = Path(value["path"])
        if source.is_absolute() and source.is_file() and file_sha256(source) == value["sha256"]:
            return source.resolve()
    raise ValueError("Artifact binding requires an existing file with its recorded SHA-256")


def _ids(value: Any) -> list[str]:
    if isinstance(value, list) and value and all(isinstance(x, str) and x.strip() == x and x for x in value):
        if len(value) == len(set(value)):
            return value
    raise ValueError("Paper manifest requires a nonempty list of distinct paper IDs")


def _source_identity(root: Path) -> list[dict[str, str]]:
    paths = sorted(set((root / "src").rglob("*.py")) | set((root / "src").rglob("*.txt")))
    if all((root / "src/evaluation" / name).is_file() for name in ("_2_split.py", "_3_classify.py", "_4_evaluation.py")):
        return [_binding(path) for path in paths]
    raise ValueError("CoCo source requires the upstream split, classify and evaluation modules")


_MODEL_IDENTITY_LOCK = threading.RLock()
_MODEL_IDENTITY_CACHE = {}


def _model_tree_state(directory):
    state = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            st = path.stat()
            state.append((str(path), str(path.resolve(strict=True)), st.st_dev, st.st_ino,
                          st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    return tuple(state)


def _model_identity(root: str | Path, *, force_rehash=False) -> dict[str, Any]:
    directory = Path(root).expanduser().resolve(strict=True)
    # Local Linux notifications detect even same-tick equal-size writes with
    # restored mtime; metadata additionally binds namespace and symlink targets.
    # The cache never survives process restart. Freeze rehashes every byte.
    from concurrent.futures import ThreadPoolExecutor
    from .model_watch import ModelWatch
    with _MODEL_IDENTITY_LOCK:
        before = _model_tree_state(directory)
        cached = _MODEL_IDENTITY_CACHE.get(directory)
        changed = cached[2].changed() if cached is not None else True
        if not force_rehash and not changed and cached is not None and cached[0] == before:
            return copy.deepcopy(cached[1])
        if cached is not None:
            cached[2].close()
            del _MODEL_IDENTITY_CACHE[directory]
        watch = ModelWatch(directory)
        with ThreadPoolExecutor(max_workers=4) as executor:
            files = list(executor.map(_binding, [item[0] for item in before]))
        if watch.changed() or _model_tree_state(directory) != before:
            watch.close()
            raise ValueError("Scoring model changed during byte verification")
        if not directory.is_dir() or not files:
            raise ValueError("Scoring model requires a populated local directory")
        result = {"root": str(directory), "files": files, "tree_sha256": value_sha256(files)}
        _MODEL_IDENTITY_CACHE[directory] = (before, result, watch)
        return copy.deepcopy(result)


def materialize_scoring_config(*, coco_root: str | Path, python_executable: str | Path,
                               output_path: str | Path, services: dict[str, Any],
                               bert_score_model_root: str | Path, timeout_seconds: int = 3600,
                               max_stage_attempts: int = 3, hf_home: str | Path | None = None) -> dict[str, Any]:
    """Freeze explicitly supplied services and all standard source/model files."""
    root = Path(coco_root).expanduser().resolve(strict=True)
    configured = {}
    for stage in ("split", "classify"):
        service = services[stage]
        configured[stage] = {"base_url": service["base_url"], "model": service["model"],
                             "model_identity": _model_identity(service["model_root"])}
    configured["judge"] = ModelConfig(**services["judge"]).protocol_config()
    body = {"protocol": CONFIG_PROTOCOL, "coco_root": str(root),
            "python_executable": str(Path(python_executable).expanduser().absolute()),
            "services": configured, "source_files": _source_identity(root),
            "bert_score_model": _model_identity(bert_score_model_root),
            "timeout_seconds": timeout_seconds, "max_stage_attempts": max_stage_attempts,
            "hf_home": str(Path(hf_home or Path.home() / ".cache/huggingface").expanduser().resolve()),
            "request_settings": ORIGINAL_REQUEST_SETTINGS,
            "python_runtime": _python_runtime(python_executable)}
    value = {**body, "config_id": value_sha256(body)}
    _validate_config(value)
    atomic_write_json(output_path, value, reject_symlink=True)
    return value


def _validate_config(value: dict[str, Any]) -> None:
    body = {key: item for key, item in value.items() if key != "config_id"}
    if value.get("protocol") != CONFIG_PROTOCOL or value.get("config_id") != value_sha256(body):
        raise ValueError("Scoring configuration requires its canonical protocol and self hash")
    if value.get("request_settings") != ORIGINAL_REQUEST_SETTINGS:
        raise ValueError("Scoring configuration must retain upstream request settings")
    hf_home = Path(value.get("hf_home", ""))
    if not hf_home.is_absolute() or not hf_home.is_dir():
        raise ValueError("Scoring requires an existing absolute HF_HOME")
    _validate_bert_cache(value)
    for key in ("max_stage_attempts", "timeout_seconds"):
        if type(value.get(key)) is int and value[key] >= 1:
            continue
        raise ValueError(f"Scoring {key} requires a positive integer")
    root = Path(value["coco_root"]).resolve()
    if value["source_files"] != _source_identity(root):
        raise ValueError("Standard CoCo source identity changed")
    executable = Path(value["python_executable"])
    if executable.is_absolute() and executable.is_file():
        pass
    else:
        raise ValueError("Scoring Python requires an existing absolute executable")
    if value.get("python_runtime") != _python_runtime(executable):
        raise ValueError("Frozen scoring Python environment changed")
    for stage in ("split", "classify"):
        service = value["services"][stage]
        if "ssh" in service:
            from .ssh_service import validate_remote_identity
            validate_remote_identity(service)
        if urlparse(service["base_url"]).hostname in {"127.0.0.1", "localhost", "::1"} and service["model"]:
            continue
        raise ValueError("CoCo services require explicit local endpoints and model names")
    judge = ModelConfig(**value["services"]["judge"])
    if judge.temperature != ORIGINAL_REQUEST_SETTINGS["judge"]["temperature"]:
        raise ValueError("CoCo Judge must retain the paper's temperature=0")
    for model in [value["bert_score_model"], *(value["services"][stage]["model_identity"] for stage in ("split", "classify"))]:
        if model != _model_identity(model["root"]):
            raise ValueError("Scoring model identity changed")


def _validate_inputs(review_path: Path, reference_path: Path, partition: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    manifest, references = read_json(review_path), read_json(reference_path)
    if manifest.get("protocol") == REVIEW_MANIFEST_PROTOCOL and manifest.get("partition") == partition:
        paper_ids = _ids(manifest.get("paper_ids"))
    else:
        raise ValueError("Review manifest requires the requested partition and protocol")
    if references.get("protocol") == REFERENCE_MANIFEST_PROTOCOL and references.get("partition") == partition:
        reference_ids = _ids(references.get("paper_ids"))
    else:
        raise ValueError("Reference manifest requires the requested partition and protocol")
    if set(paper_ids) <= set(reference_ids):
        pass
    else:
        raise ValueError("Every requested paper requires a same-partition CoCo reference")
    source_ids = []
    for binding in references["benchmark_files"]:
        source_ids.extend(str(row["id"]) for row in read_jsonl(_bound_file(binding)))
    if len(source_ids) == len(set(source_ids)) and set(source_ids) == set(reference_ids):
        pass
    else:
        raise ValueError("Benchmark reference closure requires exactly its partition paper IDs")
    human_rows = read_jsonl(_bound_file(references["complete_human_classify_file"]))
    human_ids = [str(row["id"]) for row in human_rows]
    selection = read_json(_bound_file(references["reviewer_selection_file"]))
    if len(human_ids) == len(set(human_ids)) and set(human_ids) == set(reference_ids) and set(selection) == set(reference_ids):
        pass
    else:
        raise ValueError("Human categories and reviewer selections require the exact partition reference IDs")
    records = manifest.get("records")
    if isinstance(records, list) and len(records) == len(paper_ids) and {row.get("paper_id") for row in records} == set(paper_ids):
        by_id = {row["paper_id"]: row for row in records}
    else:
        raise ValueError("Frozen review records require the complete unique requested paper set")
    candidate = []
    for paper_id in paper_ids:
        review = read_json(_bound_file(by_id[paper_id]))
        text = review.get("review_text")
        if review.get("paper_id") == paper_id and isinstance(text, str) and text.strip():
            candidate.append({"id": paper_id, "text": text})
        else:
            raise ValueError("Frozen review requires matching paper ID and nonempty review_text")
    return manifest, references, candidate


def aggregate_paper_scores(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the standard per-paper Table-1 outputs with their real counts."""
    scores: dict[str, float | None] = {}
    counts: dict[str, int] = {}
    for column in COLUMNS:
        values = []
        for row in records:
            if row.get("status") == "complete":
                score, count = row["metrics"][column], row["metric_counts"][column]
                if type(count) is int and count in (0, 1):
                    pass
                else:
                    raise ValueError("Per-paper metric count requires zero or one")
                if count == 1 and type(score) in (int, float) and math.isfinite(score):
                    values.append(float(score))
                elif count == 0 and score is None:
                    pass
                else:
                    raise ValueError("Metric value and applicable denominator differ")
        counts[column] = len(values)
        scores[column] = math.fsum(values) / len(values) if values else None
    return {"absolute_scores": scores, "paper_counts": counts}


def failure_kind(error_type: str, message: str) -> str:
    """Diagnostic classification; the official client's retry policy is unchanged."""
    detail = (error_type + " " + message).lower()
    if any(word in detail for word in ("importerror", "modulenotfounderror", "relative import")):
        return "local_import"
    if any(word in detail for word in ("timeout", "timed out")):
        return "timeout"
    if any(word in detail for word in ("api", "connection refused", "connection reset", "http", "rate limit")):
        return "model_api"
    if any(word in detail for word in ("cohort coverage", "incomplete judge", "missing", "fitness")):
        return "incomplete_scoring"
    return "local_runtime"


def _stage_failure(stage: str, exc: Exception) -> dict[str, Any]:
    text = str(exc)
    transient = isinstance(exc, subprocess.TimeoutExpired) or any(marker in text.lower() for marker in
        ("connection refused", "connection reset", "timed out", "timeout", "service unavailable", "rate limit"))
    deterministic = any(marker in text.lower() for marker in ("official classification failed", "official split failed", "incomplete judge", "fields differ", "prompt budget", "context window"))
    return {"stage": stage, "category": "shared_runtime" if transient else "shared_evaluation",
            "message": text, "error_type": type(exc).__name__,
            "failure_kind": failure_kind(type(exc).__name__, text), "retryable": transient and deterministic is False,
            "fingerprint": value_sha256({"stage": stage, "type": type(exc).__name__, "message": text})}


def _run_stage(*, stage: str, request_path: Path, output: Path, config: dict[str, Any]) -> Path:
    stage_root = output / "stages" / stage
    stage_root.mkdir(parents=True, exist_ok=True)
    result_path, state_path = stage_root / "result.json", stage_root / "state.json"
    identity = _binding(request_path)
    state = read_json(state_path) if state_path.is_file() else {"request": identity, "attempt_limit": config["max_stage_attempts"], "attempts": [], "status": "pending"}
    if state["request"] != identity:
        raise ValueError("Stage checkpoint requires its frozen request identity")
    if state["status"] == "complete":
        return _bound_file(state["result"])
    if state["status"] == "failed":
        raise DevelopmentCoCoFailure(state["failure"])
    while len(state["attempts"]) < state["attempt_limit"]:
        number = len(state["attempts"]) + 1
        attempt = {"number": number, "status": "reserved"}
        state["attempts"].append(attempt)
        state["status"] = "running"
        atomic_write_json(state_path, state, immutable=False)
        log_path = stage_root / f"attempt-{number:03d}.log"
        result_path = stage_root / f"result-{number:03d}.json"
        command = [config["python_executable"], "-s", "-B", "-m", "agentreview.research_v2.development_coco_worker", "--request", str(request_path), "--stage", stage, "--output", str(result_path)]
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "CUDA_VISIBLE_DEVICES": "", "TOKENIZERS_PARALLELISM": "false",
                       "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        environment.update(HF_HOME=config.get("hf_home", str(Path.home() / ".cache/huggingface")),
                           HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
        # Only the Judge worker receives the cloud credential. Candidate sandboxes
        # never inherit it; Split/Classify need no cloud key.
        if stage == "evaluation":
            from .llm import require_api_key
            judge = ModelConfig(**config["services"]["judge"])
            environment[judge.api_key_env] = require_api_key(judge)
            for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"):
                if name in os.environ:
                    environment[name] = os.environ[name]
        for name in ("AGENTREVIEW_EXECUTION_PROFILE", "AGENTREVIEW_EXECUTION_PROFILE_SHA256", "AGENTREVIEW_TELEMETRY_ROOT"):
            if name in os.environ:
                environment[name] = os.environ[name]
        try:
            with log_path.open("wb") as log:
                result = subprocess.run(command, cwd=stage_root, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=config["timeout_seconds"], check=False)
            if result.returncode == 0 and result_path.is_file():
                attempt["status"] = "complete"
                state.update(status="complete", result=_binding(result_path))
                atomic_write_json(state_path, state, immutable=False)
                return result_path
            error_path = result_path.with_suffix(".failure.json")
            failure = read_json(error_path) if error_path.is_file() else _stage_failure(stage, RuntimeError(log_path.read_text(errors="replace")[-8000:]))
        except (subprocess.TimeoutExpired, OSError) as exc:
            failure = _stage_failure(stage, exc)
        attempt.update(status="failed", failure=failure)
        if log_path.is_file():
            attempt["log"] = _binding(log_path)
        prior = state["attempts"][-2] if len(state["attempts"]) > 1 else {}
        repeated = prior.get("failure", {}).get("fingerprint") == failure["fingerprint"]
        if failure.get("retryable") is True and repeated is False and len(state["attempts"]) < state["attempt_limit"]:
            atomic_write_json(state_path, state, immutable=False)
            continue
        state.update(status="failed", failure=failure)
        atomic_write_json(state_path, state, immutable=False)
        raise DevelopmentCoCoFailure(failure)
    failure = {"stage": stage, "category": "shared_runtime", "message": "Stage attempt budget consumed, including interrupted reservations"}
    state.update(status="failed", failure=failure)
    atomic_write_json(state_path, state, immutable=False)
    raise DevelopmentCoCoFailure(failure)


def validate_evaluation_receipt(receipt: dict[str, Any], *, expected_paper_ids: list[str] | None = None,
                                expected_epoch: int | None = None) -> dict[str, Any]:
    from .cohort_receipt import PROTOCOL as COMPOSED_PROTOCOL, validate_composed
    if receipt.get("protocol") == COMPOSED_PROTOCOL:
        return validate_composed(receipt, expected_paper_ids=expected_paper_ids, expected_epoch=expected_epoch)
    body = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if receipt.get("protocol") == RECEIPT_PROTOCOL and receipt.get("status") == "complete" and receipt.get("receipt_id") == value_sha256(body):
        pass
    else:
        raise ValueError("Candidate admission requires a complete self-hashed standard CoCo receipt")
    paper_ids = _ids(receipt["paper_ids"])
    if receipt.get("review_target") != "final":
        raise ValueError("Only final complete-review assessments can be admitted")
    if expected_paper_ids is not None and paper_ids != expected_paper_ids:
        raise ValueError("Evaluation receipt paper order differs from the frozen cohort")
    if expected_epoch is not None and receipt["evaluation_epoch"] != expected_epoch:
        raise ValueError("Evaluation receipt epoch differs from the comparison epoch")
    validate_stage_integrity(receipt.get("stage_integrity", {}), paper_ids)
    rows = [read_json(_bound_file(binding)) for binding in receipt["papers"]]
    if [row["paper_id"] for row in rows] != paper_ids or rows != receipt["rows"]:
        raise ValueError("Evaluation receipt requires exact persisted rows for its cohort")
    for row in rows:
        if row["status"] != "complete":
            raise ValueError("Evaluation receipt requires a persisted upstream result for every paper")
        for key in ("partition", "run_id", "candidate_id", "evaluation_epoch", "scoring_protocol"):
            if row[key] != receipt[key]:
                raise ValueError("Paper evaluation identity differs from its receipt")
        _bound_file(row["review"])
        raw = read_json(_bound_file(row["raw"]))
        request = read_json(_bound_file(raw["request"]))
        for key in ("partition", "run_id", "candidate_id", "evaluation_epoch", "scoring_protocol", "inputs", "paper_ids", "candidate_identity", "purpose", "review_target"):
            if request[key] != receipt[key]:
                raise ValueError("Standard scoring request differs from its receipt identity")
        detail = raw["papers"][row["paper_id"]]
        table = detail["table_1_summary"]
        if row["metrics"] == table["absolute_scores"] and row["metric_counts"] == table["paper_counts"] and row["paper_judge_scores"] == detail["paper_judge_scores"] and row["category_judge_scores"] == detail["category_judge_scores"]:
            pass
        else:
            raise ValueError("Persisted paper scores differ from their bound standard CoCo output")
        expected_applicability = {name: "applicable" if row["metric_counts"][name] else "not_applicable" for name in COLUMNS}
        if row["applicability"] != expected_applicability:
            raise ValueError("Score applicability differs from the standard effective denominators")
    for binding in receipt["inputs"].values():
        _bound_file(binding)
    manifest, _references, _candidate = _validate_inputs(_bound_file(receipt["inputs"]["reviews"]),
        _bound_file(receipt["inputs"]["references"]), receipt["partition"])
    if manifest["paper_ids"] != paper_ids:
        raise ValueError("Receipt cohort differs from its frozen review manifest")
    config = read_json(_bound_file(receipt["inputs"]["scoring_config"]))
    expected_protocol = {"protocol": SCORING_PROTOCOL, "config_sha256": receipt["inputs"]["scoring_config"]["sha256"],
                         "standard_source_sha256": value_sha256(config["source_files"])}
    if receipt["scoring_protocol"] != expected_protocol:
        raise ValueError("Receipt scoring protocol differs from its frozen configuration")
    aggregate = aggregate_paper_scores(rows)
    if aggregate == receipt["aggregate"] and receipt["mean_score"] == aggregate["absolute_scores"]["Paper."]:
        return receipt
    raise ValueError("Evaluation receipt aggregate differs from complete per-paper scoring")


def validate_assessment_receipt(receipt: dict[str, Any] | str | Path, *, expected_paper_ids: list[str] | None = None,
                                expected_epoch: int | None = None, expected_partition: str | None = None,
                                candidate_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Admission interface used by the shared candidate pool and report readers."""
    value = read_json(receipt) if isinstance(receipt, (str, Path)) else receipt
    validate_evaluation_receipt(value, expected_paper_ids=expected_paper_ids, expected_epoch=expected_epoch)
    # Selection needs one actual fitness observation per compared paper. This
    # does not rewrite or reject the original CoCo evaluation artifact, including
    # its missing metrics, applicability counts and partial judging results.
    for row in value["rows"]:
        score = row["metrics"]["Paper."]
        if (row["metric_counts"]["Paper."] != 1 or type(score) not in (int, float)
                or not math.isfinite(score)):
            raise ValueError("Evolution fitness is unavailable: original CoCo has no Paper. score for every paper")
    if expected_partition is not None and value["partition"] != expected_partition:
        raise ValueError("Receipt partition differs from its comparison partition")
    if candidate_identity is not None and value["candidate_identity"] != candidate_identity:
        raise ValueError("Receipt candidate identity differs from the admitted program and memory")
    return value


def validate_stage_integrity(stages, paper_ids):
    if set(stages) != {"split", "classify", "evaluation"}:
        raise ValueError("Assessment requires complete Split, Classify and Judge integrity receipts")
    for stage, ref in stages.items():
        value = read_json(_bound_file(ref))
        if (value.get("status") != "complete" or value.get("stage") != stage
                or value.get("unhandled_failures") != 0
                or [row["paper_id"] for row in value["checks"]] != paper_ids):
            raise ValueError("CoCo stage integrity requires complete cohort coverage without unresolved failures")
        if not value.get("tasks"):
            raise ValueError("CoCo stage integrity requires raw model completion evidence")
        for task_ref in value["tasks"]:
            task = read_json(_bound_file(task_ref))
            if task.get("status") != "complete":
                raise ValueError("CoCo stage is missing its recorded upstream result")
        # Stage diagnostics describe the author's recovery/applicability rules;
        # they are not additional acceptance thresholds on original CoCo output.


@timed('coco_evaluation', 'candidate_id', 'partition')
def evaluate_final_reviews(*, review_manifest_path: str | Path, reference_manifest_path: str | Path,
                           scoring_config_path: str | Path, output_root: str | Path,
                           partition: str, run_id: str, candidate_id: str, evaluation_epoch: int,
                           candidate_identity: dict[str, Any], purpose: str = "development",
                           review_target: str = "final") -> dict[str, Any]:
    """Score an immutable cohort; failures persist null scores and raise to pause."""
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".evaluation.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _evaluate_locked(review_manifest_path=Path(review_manifest_path).resolve(),
            reference_manifest_path=Path(reference_manifest_path).resolve(), scoring_config_path=Path(scoring_config_path).resolve(),
            output=output, partition=partition, run_id=run_id, candidate_id=candidate_id,
            evaluation_epoch=evaluation_epoch, candidate_identity=candidate_identity, purpose=purpose, review_target=review_target)


def _evaluate_locked(*, review_manifest_path: Path, reference_manifest_path: Path, scoring_config_path: Path,
                     output: Path, partition: str, run_id: str, candidate_id: str, evaluation_epoch: int,
                     candidate_identity: dict[str, Any], purpose: str, review_target: str) -> dict[str, Any]:
    try:
        if review_target != "final":
            raise ValueError("CoCo accepts only the final complete review; role scoring is disabled")
        if (partition == "sealed_test" and purpose == "final_test") or (partition != "sealed_test" and purpose == "development"):
            pass
        else:
            raise ValueError("Sealed test scoring requires the final_test purpose")
        if type(evaluation_epoch) is int and evaluation_epoch >= 0 and run_id and candidate_id and candidate_identity:
            pass
        else:
            raise ValueError("Scoring requires a run, candidate identity and nonnegative evaluation epoch")
        manifest, references, candidate = _validate_inputs(review_manifest_path, reference_manifest_path, partition)
        config = read_json(scoring_config_path)
        _validate_config(config)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        record = {"stage": "preflight", "category": "shared_data", "message": str(exc), "error_type": type(exc).__name__}
        atomic_write_json(output / "preflight.failure.json", record, immutable=False)
        raise DevelopmentCoCoFailure(record) from exc
    identity = {"partition": partition, "run_id": run_id, "candidate_id": candidate_id, "evaluation_epoch": evaluation_epoch,
        "scoring_protocol": {"protocol": SCORING_PROTOCOL, "config_sha256": file_sha256(scoring_config_path),
                             "standard_source_sha256": value_sha256(config["source_files"])}}
    inputs = {"reviews": _binding(review_manifest_path), "references": _binding(reference_manifest_path), "scoring_config": _binding(scoring_config_path)}
    request = {**identity, "inputs": inputs, "paper_ids": manifest["paper_ids"], "candidate_identity": candidate_identity,
               "purpose": purpose, "review_target": review_target, "output_root": str(output)}
    request_path = atomic_write_json(output / "request.json", request)
    receipt_path = output / "assessment.json"
    if receipt_path.is_file():
        existing = read_json(receipt_path)
        if existing["status"] == "complete":
            return validate_evaluation_receipt(existing, expected_paper_ids=manifest["paper_ids"], expected_epoch=evaluation_epoch)
        raise DevelopmentCoCoFailure(existing["failure"])
    write_immutable_jsonl(output / "candidate.jsonl", candidate)
    by_id = {row["paper_id"]: row for row in manifest["records"]}
    rows = [{"protocol": PAPER_PROTOCOL, **identity, "paper_id": paper_id, "status": "pending",
             "metrics": dict.fromkeys(COLUMNS), "metric_counts": dict.fromkeys(COLUMNS, 0),
             "applicability": dict.fromkeys(COLUMNS, "unknown"), "review": {key: by_id[paper_id][key] for key in ("path", "sha256")}}
            for paper_id in manifest["paper_ids"]]
    failure = None
    stage_integrity = {}
    try:
        for stage in ("split", "classify", "evaluation"):
            result_path = _run_stage(stage=stage, request_path=request_path, output=output, config=config)
            stage_integrity[stage] = read_json(result_path)["integrity"]
        validate_stage_integrity(stage_integrity, manifest["paper_ids"])
        raw = read_json(result_path)
        if raw["request"] != _binding(request_path):
            raise ValueError("Standard CoCo worker result requires its original frozen scoring request")
        if raw["paper_ids"] != manifest["paper_ids"] or set(raw["papers"]) != set(manifest["paper_ids"]):
            raise ValueError("Standard CoCo worker requires the exact frozen paper cohort")
        for row in rows:
            detail = raw["papers"][row["paper_id"]]
            table = detail["table_1_summary"]
            row.update(status="complete", metrics=table["absolute_scores"], metric_counts=table["paper_counts"],
                       applicability={name: "applicable" if table["paper_counts"][name] else "not_applicable" for name in COLUMNS},
                       paper_judge_scores=detail["paper_judge_scores"], category_judge_scores=detail["category_judge_scores"], raw=_binding(result_path))
        aggregate = aggregate_paper_scores(rows)
        for name in COLUMNS:
            declared = raw["table_1_summary"]["absolute_scores"][name]
            derived = aggregate["absolute_scores"][name]
            matches = declared is None and derived is None or type(declared) in (int, float) and type(derived) in (int, float) and math.isclose(declared, derived, rel_tol=1e-12, abs_tol=1e-12)
            if matches and raw["table_1_summary"]["paper_counts"][name] == aggregate["paper_counts"][name]:
                continue
            raise ValueError(f"Standard aggregate and per-paper {name} values differ")
        _validate_inputs(review_manifest_path, reference_manifest_path, partition)
        if inputs != {"reviews": _binding(review_manifest_path), "references": _binding(reference_manifest_path), "scoring_config": _binding(scoring_config_path)}:
            raise ValueError("Scoring input manifests changed during evaluation")
    except (DevelopmentCoCoFailure, ValueError, KeyError, TypeError, OSError) as exc:
        failure = exc.failure_record if isinstance(exc, DevelopmentCoCoFailure) else _stage_failure("validation", exc)
        for row in rows:
            row.update(status="failed", metrics=dict.fromkeys(COLUMNS), metric_counts=dict.fromkeys(COLUMNS, 0),
                       applicability=dict.fromkeys(COLUMNS, "unknown"), failure=failure)
    papers = []
    for index, row in enumerate(rows):
        path = atomic_write_json(output / "papers" / f"{index:06d}.json", row)
        papers.append({"paper_id": row["paper_id"], **_binding(path)})
    aggregate = aggregate_paper_scores(rows)
    receipt = {"protocol": RECEIPT_PROTOCOL, **identity, "purpose": purpose, "review_target": review_target,
        "stage_integrity": stage_integrity,
        "candidate_identity": candidate_identity, "inputs": inputs, "paper_ids": manifest["paper_ids"], "papers": papers, "rows": rows,
        "status": "paused" if failure else "complete", "expected_count": len(rows), "complete_count": sum(row["status"] == "complete" for row in rows),
        "aggregate": aggregate, "mean_score": aggregate["absolute_scores"]["Paper."] if failure is None else None}
    if failure:
        receipt["failure"] = failure
    receipt["receipt_id"] = value_sha256(receipt)
    if failure is None:
        validate_evaluation_receipt(receipt)
    atomic_write_json(receipt_path, receipt)
    if failure:
        raise DevelopmentCoCoFailure(failure)
    return receipt
