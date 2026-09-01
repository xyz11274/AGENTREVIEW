import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentreview.history_checkpoint import canonical_sha256
from agentreview.reviewer_runtime.artifacts import CandidateReviewRecord
from agentreview.reviewer_runtime.engine import (
    EXPERIENCE_PROTOCOL,
    EXPERIENCE_PROTOCOL_VERSION,
    ReviewerSession,
)
from agentreview.reviewer_runtime.models import ReviewRequest
from agentreview.reviewer_runtime.tasks import ALL_TASKS

from .benchmark_adapter import ReviewSubmission, evaluate_frozen_review
from .checkpoint import load_checkpoint, save_checkpoint
from .evaluation_orchestrator import run_ordered_evaluation_pipeline
from .models import Episode, EvalItem, EvalResult, to_serializable


@dataclass(frozen=True)
class _PreparedEvaluation:
    """Frozen handoff from Reviewer production to evaluator-only scoring."""

    eval_item: EvalItem
    case: dict[str, Any]
    submission: ReviewSubmission | None
    candidate_review_record: dict[str, Any] | None
    parser_debug: dict[str, Any]
    started_at: float


def candidate_safe_case(case: dict[str, Any]) -> dict[str, Any]:
    """Reduce evaluator-side state to feedback that cannot reproduce gold."""
    error = str(case.get("error") or "")
    error_type = error.split(":", 1)[0] if error else ""
    output = {
        "score": float(case.get("score") or 0.0),
        "success": bool(case.get("success")),
    }
    if error_type:
        output["error_category"] = error_type
    metrics = case.get("structured_metrics")
    if isinstance(metrics, dict):
        output["metrics"] = {
            str(key): value
            for key, value in metrics.items()
            if isinstance(value, (int, float, bool)) or value is None
        }
    return output


def candidate_safe_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Strip questions, answers, references and free-text judge rationales."""
    output = {
        "phase": str(trace.get("phase") or ""),
        "success": bool(trace.get("success")),
    }
    if "score" in trace:
        output["score"] = float(trace.get("score") or 0.0)
    if trace.get("error"):
        output["error_category"] = str(trace["error"]).split(":", 1)[0]
    return output


def _score_metric(metric, prediction: str, gold: str, item: EvalItem) -> float:
    item_dict = to_serializable(item)
    try:
        score = metric(prediction, gold, item_dict)
    except TypeError:
        score = metric(prediction, gold)
    return max(0.0, min(1.0, float(score)))


def evaluate_program(
    program_path: str,
    episodes: list[Episode],
    eval_items: list[EvalItem],
    task_agent,
    toolkit,
    metric: Callable,
    collect_traces: bool = True,
    write_timeout: float = 60.0,
    read_timeout: float = 60.0,
    sandbox_memory_mb: int = 1024,
    require_bubblewrap: bool = True,
    formal: bool = False,
    checkpoint_path: str | None = None,
    run_identity: dict[str, Any] | None = None,
    benchmark_adapter_factory: Callable | None = None,
    candidate_review_sink: Callable[[dict[str, Any]], None] | None = None,
    evaluation_pipeline_depth: int = 1,
) -> EvalResult:
    started = time.monotonic()
    resource_before = (
        toolkit.resource_snapshot() if hasattr(toolkit, "resource_snapshot") else {}
    )
    judge_toolkit = getattr(metric, "judge_toolkit", None)
    judge_resource_before = (
        judge_toolkit.resource_snapshot()
        if hasattr(judge_toolkit, "resource_snapshot")
        else {}
    )
    traces = []
    write_failures = 0
    session = None
    try:
        toolkit.reset_vector_collections()
        runtime_task_id = str(getattr(task_agent, "task_id", "") or "")
        if runtime_task_id not in ALL_TASKS:
            suffix = str(eval_items[0].id).rsplit("::", 1)[-1] if eval_items else ""
            runtime_task_id = suffix if suffix in ALL_TASKS else "QUAL"
        reviewer_runtime = ReviewerSession(
            task_id=runtime_task_id,
            memory_program=program_path,
            toolkit=toolkit,
            task_agent=task_agent,
            timeout_seconds=max(write_timeout, read_timeout),
            memory_mb=sandbox_memory_mb,
            cpu_seconds=max(1, int(max(write_timeout, read_timeout))),
            require_bubblewrap=require_bubblewrap,
        )
        session = reviewer_runtime.sandbox
    except Exception as exc:
        if session is not None:
            session.close(force=True)
        return EvalResult(
            mean_score=0.0,
            runtime_stats={"total_seconds": time.monotonic() - started},
            error=f"{type(exc).__name__}: {exc}",
        )

    episode_values = [Episode.from_value(value) for value in episodes]
    item_values = [EvalItem.from_value(value) for value in eval_items]

    frozen_episode_ids = [episode.id for episode in episode_values]
    completed_ids: list[str] = []
    if formal:
        if not checkpoint_path or not isinstance(run_identity, dict):
            session.close(force=True)
            return EvalResult(
                mean_score=0.0,
                error="FormalProtocolError: formal replay requires identity-bound checkpointing",
            )
        for episode in episode_values:
            if episode.metadata.get("experience_protocol") != EXPERIENCE_PROTOCOL:
                session.close(force=True)
                return EvalResult(
                    mean_score=0.0,
                    error=(
                        "FormalProtocolError: missing, mismatched, or legacy "
                        f"experience protocol for {episode.id}"
                    ),
                )
            if (
                episode.metadata.get("experience_protocol_version")
                != EXPERIENCE_PROTOCOL_VERSION
            ):
                session.close(force=True)
                return EvalResult(
                    mean_score=0.0,
                    error=(
                        "FormalProtocolError: experience protocol version "
                        f"mismatch for {episode.id}"
                    ),
                )
        try:
            checkpoint = load_checkpoint(
                checkpoint_path,
                expected_identity=run_identity,
                frozen_ids=frozen_episode_ids,
            )
            if checkpoint:
                completed_ids = list(checkpoint["completed_paper_ids"])
                session.restore(checkpoint["memory_snapshot"])
        except Exception as exc:
            session.close(force=True)
            return EvalResult(
                mean_score=0.0,
                error=f"FormalCheckpointError: {type(exc).__name__}: {exc}",
            )

    for episode_index, episode in enumerate(episode_values):
        if episode_index < len(completed_ids):
            continue
        write_started = time.monotonic()
        trace = {
            "phase": "write",
            "episode_id": episode.id,
            "raw_text": episode.text,
        }
        try:
            raw_text = episode.text
            paper_scope = (
                toolkit.paper_scope(episode.id)
                if hasattr(toolkit, "paper_scope")
                else nullcontext()
            )
            with paper_scope:
                if episode.metadata.get("experience_protocol") == EXPERIENCE_PROTOCOL:
                    # Historical Paper_i is reviewed against only Memory built from
                    # Paper_1..Paper_{i-1}.  Writeback happens strictly afterwards.
                    generated = reviewer_runtime.review(
                        ReviewRequest(
                            paper_id=episode.id.split("::", 1)[0],
                            task_id=runtime_task_id,
                            question=episode.text,
                            model=str(
                                getattr(toolkit, "llm_model", "") or "unspecified"
                            ),
                            revision=str(
                                (
                                    (run_identity or {}).get("generation_config") or {}
                                ).get("model_revision")
                                or "unspecified"
                            ),
                            run_identity=(
                                canonical_sha256(run_identity)
                                if isinstance(run_identity, dict)
                                else f"evaluation:{Path(program_path).stem}"
                            ),
                            read_operation="historical_memory_read",
                        )
                    )
                    raw_text = (
                        "HISTORICAL PAPER TASK\n"
                        f"{episode.text}\n\n"
                        "SELF-GENERATED REVIEW EXPERIENCE\n"
                        f"{generated.review_text}"
                    )
                    trace.update(
                        {
                            "experience_protocol": "review_then_write",
                            "prior_memory_chars": len(generated.memory_context),
                            "generated_review_chars": len(generated.review_text),
                        }
                    )
                item = reviewer_runtime.ingest_text(raw_text)
            trace.update(
                {
                    "knowledge_item": to_serializable(item),
                    "duration_seconds": time.monotonic() - write_started,
                    "success": True,
                }
            )
            if formal:
                snapshot = session.snapshot()
                completed_ids.append(episode.id)
                save_checkpoint(
                    checkpoint_path,
                    run_identity=run_identity,
                    frozen_ids=frozen_episode_ids,
                    completed_ids=completed_ids,
                    memory_snapshot=snapshot,
                    paper_record={
                        "paper_id": episode.id,
                        "paper_index": episode_index,
                        "experience_protocol": EXPERIENCE_PROTOCOL,
                        "experience_protocol_version": EXPERIENCE_PROTOCOL_VERSION,
                        "completed": True,
                    },
                )
        except Exception as exc:
            write_failures += 1
            trace.update(
                {
                    "duration_seconds": time.monotonic() - write_started,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if formal:
                if collect_traces:
                    traces.append(trace)
                session.close(force=True)
                return EvalResult(
                    mean_score=0.0,
                    traces=traces,
                    runtime_stats={
                        "total_seconds": time.monotonic() - started,
                        "completed_historical_prefix": list(completed_ids),
                        "interrupted_paper_id": episode.id,
                        "interrupted_paper_reused": False,
                    },
                    error=f"HistoricalReplayError: {trace['error']}",
                )
        if collect_traces:
            traces.append(trace)

    historical_snapshot = session.snapshot() if formal else None
    evaluated_cases = []
    scores = []
    read_durations = []

    def produce_review(eval_item: EvalItem) -> _PreparedEvaluation:
        if formal:
            # B is read-only state: every Validation paper starts from exactly
            # the same A-prefix snapshot, so B ordering cannot alter fitness.
            session.restore(historical_snapshot)
        read_started = time.monotonic()
        case = {
            "id": eval_item.id,
            "question": eval_item.question,
            "gold": eval_item.answer,
        }
        candidate_review_record = None
        try:
            paper_scope = (
                toolkit.paper_scope(eval_item.id)
                if hasattr(toolkit, "paper_scope")
                else nullcontext()
            )
            with paper_scope:
                model_revision = str(
                    ((run_identity or {}).get("generation_config") or {}).get(
                        "model_revision"
                    )
                    or getattr(getattr(toolkit, "llm_client", None), "revision", "")
                    or "unspecified"
                )
                stable_run_identity = (
                    canonical_sha256(run_identity)
                    if isinstance(run_identity, dict)
                    else f"evaluation:{Path(program_path).stem}"
                )
                review_result = reviewer_runtime.review(
                    ReviewRequest(
                        paper_id=eval_item.id.split("::", 1)[0],
                        task_id=runtime_task_id,
                        question=eval_item.question,
                        metadata=dict(eval_item.metadata),
                        model=str(getattr(toolkit, "llm_model", "") or "unspecified"),
                        revision=model_revision,
                        run_identity=stable_run_identity,
                        provenance={
                            key: (run_identity or {}).get(key)
                            for key in (
                                "candidate_id",
                                "lineage",
                                "replicate_id",
                            )
                            if (run_identity or {}).get(key) is not None
                        },
                    )
                )
                query = review_result.query
                context = review_result.memory_context
                answer = review_result.review_text
            candidate_review_record = CandidateReviewRecord.from_result(
                review_result
            ).to_dict()
            case.update(
                {
                    "query": to_serializable(query),
                    "context": context,
                    "answer": answer,
                    "candidate_review": candidate_review_record,
                }
            )
            submission = ReviewSubmission(
                paper_id=eval_item.id.split("::", 1)[0],
                review=answer,
                metadata=to_serializable(eval_item),
            )
        except Exception as exc:
            parser_debug = getattr(task_agent, "last_parser_debug", None)
            case.update(
                {
                    "context": case.get("context", ""),
                    "answer": case.get("answer", ""),
                    "score": 0.0,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "parser_debug": (
                        dict(parser_debug) if isinstance(parser_debug, dict) else {}
                    ),
                    "evaluator_debug": {},
                }
            )
            submission = None
        parser_debug = getattr(task_agent, "last_parser_debug", None)
        return _PreparedEvaluation(
            eval_item=eval_item,
            case=case,
            submission=submission,
            candidate_review_record=candidate_review_record,
            parser_debug=(
                dict(parser_debug) if isinstance(parser_debug, dict) else {}
            ),
            started_at=read_started,
        )

    def score_review(prepared: _PreparedEvaluation):
        eval_item = prepared.eval_item
        case = prepared.case
        score = 0.0
        if prepared.submission is None:
            prepared.case["duration_seconds"] = (
                time.monotonic() - prepared.started_at
            )
            return prepared, score
        try:
            if benchmark_adapter_factory is not None:
                adapter = benchmark_adapter_factory(
                    metric, eval_item.answer, to_serializable(eval_item)
                )
                benchmark_result = evaluate_frozen_review(
                    adapter,
                    prepared.submission,
                )
                adapter_result = benchmark_result["result"]
                score = float(adapter_result["score"])
                metric_details = adapter_result.get("structured_metrics")
                case["benchmark_adapter"] = benchmark_result["benchmark"]
            else:
                score = _score_metric(
                    metric,
                    prepared.submission.review,
                    eval_item.answer,
                    eval_item,
                )
                metric_details = getattr(metric, "last_details", None)
            case.update(
                {
                    "score": score,
                    "success": True,
                    "structured_metrics": (
                        dict(metric_details) if isinstance(metric_details, dict) else {}
                    ),
                }
            )
        except Exception as exc:
            score = 0.0
            evaluator_debug = getattr(metric, "last_details", None)
            case.update(
                {
                    "context": case.get("context", ""),
                    "answer": case.get("answer", ""),
                    "score": 0.0,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "parser_debug": prepared.parser_debug,
                    "evaluator_debug": (
                        dict(evaluator_debug)
                        if isinstance(evaluator_debug, dict)
                        else {}
                    ),
                }
            )
        case["duration_seconds"] = time.monotonic() - prepared.started_at
        return prepared, score

    pipeline_run = run_ordered_evaluation_pipeline(
        item_values,
        produce=produce_review,
        score=score_review,
        max_inflight=evaluation_pipeline_depth,
    )
    for prepared, score in pipeline_run.results:
        case = prepared.case
        duration = float(case["duration_seconds"])
        if (
            prepared.candidate_review_record is not None
            and candidate_review_sink is not None
        ):
            try:
                candidate_review_sink(prepared.candidate_review_record)
            except Exception as exc:
                case["candidate_artifact_error"] = f"{type(exc).__name__}: {exc}"
        read_durations.append(duration)
        scores.append(score)
        evaluated_cases.append(case)
        if collect_traces:
            traces.append({"phase": "read", **case})

    total_seconds = time.monotonic() - started
    ordered_cases = sorted(
        evaluated_cases,
        key=lambda value: float(value.get("score", 0.0)),
    )
    failed_cases = ordered_cases
    success_cases = list(reversed(ordered_cases))[: min(2, len(ordered_cases))]
    result = EvalResult(
        mean_score=sum(scores) / len(scores) if scores else 0.0,
        success_cases=success_cases,
        failed_cases=failed_cases,
        traces=traces,
        runtime_stats={
            "total_seconds": total_seconds,
            "episode_count": len(episode_values),
            "eval_item_count": len(item_values),
            "write_failures": write_failures,
            "completed_historical_prefix": list(completed_ids),
            "validation_state_reset_per_item": bool(formal),
            "evaluation_pipeline": pipeline_run.stats,
            "mean_read_seconds": (
                sum(read_durations) / len(read_durations) if read_durations else 0.0
            ),
            "actual_context_size": sum(
                len(str(case.get("question") or ""))
                + len(str(case.get("context") or ""))
                for case in evaluated_cases
            ),
            "memory_context_size": sum(
                len(str(case.get("context") or "")) for case in evaluated_cases
            ),
            "resources": (
                toolkit.resource_delta(resource_before)
                if hasattr(toolkit, "resource_delta")
                else {}
            ),
            "evaluator_resources": (
                judge_toolkit.resource_delta(judge_resource_before)
                if hasattr(judge_toolkit, "resource_delta")
                else {}
            ),
        },
    )
    if session is not None:
        session.close()
    return result
