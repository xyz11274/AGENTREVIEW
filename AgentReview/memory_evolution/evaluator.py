import time
from typing import Callable, List

from .models import Episode, EvalItem, EvalResult, to_serializable
from .sandbox import SandboxSession


def _score_metric(metric, prediction: str, gold: str, item: EvalItem) -> float:
    item_dict = to_serializable(item)
    try:
        score = metric(prediction, gold, item_dict)
    except TypeError:
        score = metric(prediction, gold)
    return max(0.0, min(1.0, float(score)))


def evaluate_program(
    program_path: str,
    episodes: List[Episode],
    eval_items: List[EvalItem],
    task_agent,
    toolkit,
    metric: Callable,
    collect_traces: bool = True,
    write_timeout: float = 60.0,
    read_timeout: float = 60.0,
    require_bubblewrap: bool = True,
) -> EvalResult:
    started = time.monotonic()
    traces = []
    write_failures = 0
    session = None
    try:
        toolkit.reset_vector_collections()
        session = SandboxSession(
            program_path,
            toolkit,
            timeout_seconds=max(write_timeout, read_timeout),
            cpu_seconds=max(1, int(max(write_timeout, read_timeout))),
            require_bubblewrap=require_bubblewrap,
        )
        program = session.program_interface()
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

    for episode in episode_values:
        write_started = time.monotonic()
        trace = {
            "phase": "write",
            "episode_id": episode.id,
            "raw_text": episode.text,
        }
        try:
            item = task_agent.extract_knowledge_item(program, episode.text)
            with toolkit.operation("memory_write"):
                session.write(item, episode.text)
            trace.update(
                {
                    "knowledge_item": to_serializable(item),
                    "duration_seconds": time.monotonic() - write_started,
                    "success": True,
                }
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
        if collect_traces:
            traces.append(trace)

    evaluated_cases = []
    scores = []
    read_durations = []
    for eval_item in item_values:
        read_started = time.monotonic()
        case = {
            "id": eval_item.id,
            "question": eval_item.question,
            "gold": eval_item.answer,
        }
        try:
            query = task_agent.generate_query(program, eval_item.question)
            with toolkit.operation("memory_read"):
                context = session.read(query)
            if not isinstance(context, str):
                raise TypeError("KnowledgeBase.read() must return str")
            if len(context) > 3000:
                raise ValueError("KnowledgeBase.read() exceeded 3000 characters")
            answer = task_agent.generate_response(
                program, eval_item.question, context
            )
            score = _score_metric(metric, answer, eval_item.answer, eval_item)
            case.update(
                {
                    "query": to_serializable(query),
                    "context": context,
                    "answer": answer,
                    "score": score,
                    "success": True,
                }
            )
        except Exception as exc:
            score = 0.0
            case.update(
                {
                    "context": case.get("context", ""),
                    "answer": case.get("answer", ""),
                    "score": 0.0,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        duration = time.monotonic() - read_started
        read_durations.append(duration)
        case["duration_seconds"] = duration
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
            "mean_read_seconds": (
                sum(read_durations) / len(read_durations) if read_durations else 0.0
            ),
        },
    )
    if session is not None:
        session.close()
    return result
