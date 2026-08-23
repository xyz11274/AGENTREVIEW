import json
import math
import random
import shutil
import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .evaluator import candidate_safe_case, candidate_safe_trace, evaluate_program
from .models import Episode, EvalItem
from .quality import QualityResult, check_program_path
from .sandbox import SandboxSession
from .reflector import (
    ChangeLog,
    EvalFeedback,
    EvolutionConstraints,
    compile_fix_loop,
    mutate_program,
    validate_mutation_scope,
)
from .subset_selection import (
    build_static_validation_set,
    sample_rotating_validation_set,
    select_episode_subset,
)


@dataclass
class EvolutionConfig:
    output_dir: str = "runs/mstar"
    task_description: str = ""
    iterations: int = 21
    static_size: int = 25
    rotating_size: int = 5
    temperature: float = 0.15
    episode_budget: Optional[int] = None
    seed: int = 0
    dry_run: bool = False
    max_fix_attempts: int = 3
    max_attempts_per_lineage: Optional[int] = None
    write_timeout: float = 60.0
    read_timeout: float = 60.0
    max_llm_calls_per_operation: int = 1
    task_id: str = ""
    selection_repeats: int = 3
    selection_top_k: int = 3
    parent_policy: str = "softmax"
    mutation_scope: str = "full"
    task_model_name: str = ""
    reflector_model_name: str = ""
    judge_model_name: str = ""
    llm_provider: str = "local_openai_compatible"
    generation_model_source: str = ""
    generation_model_revision: str = ""
    vllm_version: str = ""
    llm_sampling_temperature: float = 0.0
    max_tokens: int = 8192
    retry_max_tokens: int = 16384
    reasoning_effort: str = "none"
    reasoning_effort_supported: bool = False
    classifier_adapter: str = ""
    classifier_adapter_sha256: str = ""
    review_split_revision: str = ""
    review_classify_revision: str = ""
    embedding_adapter: str = ""
    embedding_adapter_sha256: str = ""
    embedding_model_name: str = ""
    embedding_revision: str = ""
    embedding_spec: Dict[str, Any] = field(default_factory=dict)
    embedding_fingerprint: str = ""
    runtime_embedding_namespace: str = "candidate_runtime_v1"
    runtime_embedding_local_files_only: bool = True
    embedding_cache_audit_sha256: str = ""
    formal_experiment_identity: str = ""
    formal_experiment_manifest_sha256: str = ""
    formal: bool = True
    review_request_sha256: str = ""
    data_manifest_sha256: str = ""
    semantic_scholar_cache_sha256: str = ""
    historical_manifest_sha256: str = ""
    experiment_config_sha256: str = ""
    s2_asset_version: str = ""
    run_seed: int = 0
    seed_control_available: bool = False
    seed_program_sha256: Dict[str, str] = field(default_factory=dict)
    experiment_kind: str = "formal"

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            known = {field.name for field in cls.__dataclass_fields__.values()}
            return cls(**{key: item for key, item in value.items() if key in known})
        raise TypeError("config must be EvolutionConfig or dict")


@dataclass
class ProgramCandidate:
    program_id: str
    path: str
    parent_id: Optional[str]
    iteration: int
    static_score: float
    quality: QualityResult
    change_log: ChangeLog
    lineage: List[ChangeLog] = field(default_factory=list)
    parent_seed: str = ""
    lineage_name: str = ""
    program_sha256: str = ""
    completed_evaluation: bool = False


@dataclass
class EvolutionResult:
    best_program_path: str
    best_program_id: str
    best_score: float
    output_dir: str
    pool_size: int
    static_validation_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SEARCH_STATE_VERSION = "formal-search-controller-checkpoint-v2"


def resolve_formal_attempt_budget(
    iterations: int, configured_max_attempts_per_lineage: Optional[int]
) -> int:
    target_successes_per_lineage = int(iterations) // 3
    resolved = (
        3 * target_successes_per_lineage
        if configured_max_attempts_per_lineage is None
        else int(configured_max_attempts_per_lineage)
    )
    if resolved < target_successes_per_lineage:
        raise ValueError(
            "Formal max_attempts_per_lineage must be at least the successful "
            f"target per lineage: max={resolved}, target={target_successes_per_lineage}"
        )
    return resolved


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _change_log_from_value(value: Dict[str, Any]) -> ChangeLog:
    return ChangeLog(
        summary=str(value.get("summary") or ""),
        details=list(value.get("details") or []),
        score=value.get("score"),
    )


def _candidate_to_state(candidate: ProgramCandidate) -> Dict[str, Any]:
    return {
        **_candidate_record(candidate),
        "path": candidate.path,
        "quality": candidate.quality.to_dict(),
        "change_log": asdict(candidate.change_log),
        "lineage_log": [asdict(value) for value in candidate.lineage],
    }


def _candidate_from_state(value: Dict[str, Any]) -> ProgramCandidate:
    quality_value = dict(value.get("quality") or {})
    candidate = ProgramCandidate(
        program_id=str(value["program_id"]),
        path=str(value["path"]),
        parent_id=value.get("parent_id"),
        iteration=int(value.get("iteration", 0)),
        static_score=float(value.get("static_score", 0.0)),
        quality=QualityResult(
            passed=bool(quality_value.get("passed")),
            static_errors=list(quality_value.get("static_errors") or []),
            compile_errors=list(quality_value.get("compile_errors") or []),
            smoke_errors=list(quality_value.get("smoke_errors") or []),
        ),
        change_log=_change_log_from_value(dict(value.get("change_log") or {})),
        lineage=[
            _change_log_from_value(dict(item))
            for item in value.get("lineage_log") or []
        ],
        parent_seed=str(value.get("parent_seed") or ""),
        lineage_name=str(value.get("lineage") or ""),
        program_sha256=str(value.get("program_sha256") or ""),
        completed_evaluation=bool(value.get("completed_evaluation")),
    )
    path = Path(candidate.path).expanduser().resolve()
    if not path.is_file() or _sha256_file(path) != candidate.program_sha256:
        raise RuntimeError(
            f"Formal search Candidate asset changed or is missing: {candidate.program_id}"
        )
    candidate.path = str(path)
    return candidate


def _jsonl_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def _truncate_jsonl(path: Path, committed_lines: int) -> None:
    values = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if len(values) < committed_lines:
        raise RuntimeError(f"Formal search log lost committed records: {path}")
    retained = values[:committed_lines]
    path.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")


def _json_state(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_state(item) for item in value]
    if isinstance(value, list):
        return [_json_state(item) for item in value]
    return value


def _tuple_state(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuple_state(item) for item in value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _write_traces(path: Path, program_id: str, traces: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(
                json.dumps(
                    {"program_id": program_id, **trace}, ensure_ascii=False
                )
                + "\n"
            )


def run_embedding_closure_smoke(
    program_path: str,
    episode: Episode,
    validation_item: EvalItem,
    task_agent,
    toolkit,
) -> Dict[str, Any]:
    """Exercise one real vector write/read through the runtime embedder."""
    embedding = getattr(toolkit, "embedding_client", None)
    if embedding is None:
        raise RuntimeError("Embedding closure smoke requires a runtime embedder")
    if getattr(embedding, "fail_on_cache_miss", False):
        raise RuntimeError("Candidate runtime embedding must allow local cache misses")
    if getattr(embedding, "cache_namespace", "") != "candidate_runtime_v1":
        raise RuntimeError("Candidate runtime embedding namespace is invalid")

    toolkit.reset_vector_collections()
    session = SandboxSession(program_path, toolkit)
    try:
        program = session.program_interface()
        item = task_agent.extract_knowledge_item(program, episode.text)
        with toolkit.operation("embedding_closure_write"):
            session.write(item, episode.text)
        query = task_agent.generate_query(program, validation_item.question)
        with toolkit.operation("embedding_closure_read"):
            context = session.read(query)
        if not isinstance(context, str):
            raise TypeError("Embedding closure read must return str")
        query_text = str(getattr(query, "query_text", ""))
        return {
            "passed": True,
            "program": str(Path(program_path).name),
            "episode_id": episode.id,
            "validation_id": validation_item.id,
            "episode_text_sha256": hashlib.sha256(
                episode.text.encode("utf-8")
            ).hexdigest(),
            "query_text_sha256": hashlib.sha256(
                query_text.encode("utf-8")
            ).hexdigest(),
            "context_chars": len(context),
            "embedding_fingerprint": str(
                getattr(embedding, "embedding_fingerprint", "")
            ),
            "model_revision": str(getattr(embedding, "revision", "")),
            "runtime_cache": str(getattr(embedding, "cache_path", "")),
            "runtime_namespace": str(
                getattr(embedding, "cache_namespace", "")
            ),
        }
    finally:
        session.close(force=True)


def _candidate_record(
    candidate: ProgramCandidate,
    rotating_score: float = 0.0,
    compile_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "program_id": candidate.program_id,
        "parent_id": candidate.parent_id,
        "iteration": candidate.iteration,
        "static_score": candidate.static_score,
        "rotating_score": rotating_score,
        "quality_passed": candidate.quality.passed,
        "compile_errors": compile_errors or candidate.quality.errors,
        "change_summary": candidate.change_log.summary,
        "parent_seed": candidate.parent_seed,
        "lineage": candidate.lineage_name,
        "program_sha256": candidate.program_sha256,
        "completed_evaluation": candidate.completed_evaluation,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _softmax_parent(candidates, temperature: float, rng: random.Random):
    eligible = [
        candidate
        for candidate in candidates
        if candidate.quality.passed and candidate.completed_evaluation
    ]
    if not eligible:
        raise RuntimeError("No quality-passing program is available for mutation")
    temperature = max(float(temperature), 1e-6)
    logits = [candidate.static_score / temperature for candidate in eligible]
    maximum = max(logits)
    weights = [math.exp(logit - maximum) for logit in logits]
    return rng.choices(eligible, weights=weights, k=1)[0]


def _select_parent(
    candidates,
    config: EvolutionConfig,
    rng: random.Random,
    lineage_name: str = "",
):
    if lineage_name:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.lineage_name == lineage_name
        ]
    if config.parent_policy == "best":
        eligible = [
            candidate
            for candidate in candidates
            if candidate.quality.passed and candidate.completed_evaluation
        ]
        return max(eligible, key=lambda candidate: candidate.static_score)
    if config.parent_policy != "softmax":
        raise ValueError(f"Unknown parent policy: {config.parent_policy}")
    return _softmax_parent(candidates, config.temperature, rng)


def _item_id(item) -> str:
    if isinstance(item, dict):
        return str(item.get("id", ""))
    return str(getattr(item, "id", ""))


def _reference_programs(
    candidates: List[ProgramCandidate],
    parent_id: str,
    lineage_name: str = "",
) -> List[str]:
    """Expose scored high/low population designs without validation case content."""
    eligible = [
        candidate
        for candidate in candidates
        if candidate.quality.passed
        and candidate.completed_evaluation
        and candidate.program_id != parent_id
        and (not lineage_name or candidate.lineage_name == lineage_name)
    ]
    ordered = sorted(eligible, key=lambda candidate: candidate.static_score)
    selected = []
    for candidate in (
        ordered[-1:] + ordered[:1] + ordered[len(ordered) // 2 : len(ordered) // 2 + 1]
    ):
        if candidate not in selected:
            selected.append(candidate)
    return [
        (
            f"# population_program_id={candidate.program_id} "
            f"static_score={candidate.static_score:.6f}\n"
            + Path(candidate.path).read_text(encoding="utf-8")
        )
        for candidate in selected[:3]
    ]


def run_evolution(
    seed_program_paths: List[str],
    episodes,
    validation_items,
    task_agent,
    toolkit,
    metric,
    config,
    reflector_client=None,
    selection_embedding_client=None,
) -> EvolutionResult:
    cfg = EvolutionConfig.from_value(config)
    selection_embedding_client = selection_embedding_client or toolkit
    if cfg.formal and cfg.iterations % 3 != 0:
        raise ValueError(
            "Formal lineage comparison requires iterations divisible by three"
        )
    if cfg.formal:
        cfg.max_attempts_per_lineage = resolve_formal_attempt_budget(
            cfg.iterations, cfg.max_attempts_per_lineage
        )
    reflector = reflector_client or toolkit
    print(
        "[MSTAR] Starting evolution: "
        f"iterations={cfg.iterations}, static_size={cfg.static_size}, "
        f"rotating_size={cfg.rotating_size}, episode_budget={cfg.episode_budget}",
        flush=True,
    )
    episode_values = [Episode.from_value(item) for item in episodes]
    validation_values = [EvalItem.from_value(item) for item in validation_items]
    if not seed_program_paths:
        raise ValueError("At least one seed program path is required")
    if not validation_values:
        raise ValueError("Validation data must contain at least one item")
    if cfg.static_size <= 0:
        raise ValueError("static_size must be greater than zero")
    rng = random.Random(cfg.seed)
    output_dir = Path(cfg.output_dir).resolve()
    pool_dir = output_dir / "pool"
    traces_dir = output_dir / "traces"
    scores_path = output_dir / "scores.jsonl"
    lineage_path = output_dir / "lineage.jsonl"
    controller_path = output_dir / "search_controller_checkpoint.json"
    controller_identity = _canonical_sha256(
        {
            "config": asdict(cfg),
            "seed_program_sha256": {
                Path(path).name: _sha256_file(Path(path))
                for path in seed_program_paths
            },
            "episode_ids": [value.id for value in episode_values],
            "validation_ids": [_item_id(value) for value in validation_values],
        }
    )
    controller = None
    if cfg.formal and controller_path.is_file():
        controller = json.loads(controller_path.read_text(encoding="utf-8"))
        if controller.get("version") != SEARCH_STATE_VERSION:
            raise RuntimeError("Legacy or unsupported formal search checkpoint")
        if controller.get("run_identity") != controller_identity:
            raise RuntimeError("Formal search checkpoint identity mismatch")
        _truncate_jsonl(scores_path, int(controller.get("scores_lines", 0)))
        _truncate_jsonl(lineage_path, int(controller.get("lineage_lines", 0)))
        rng.setstate(_tuple_state(controller["rng_state"]))
    elif cfg.formal:
        residue = []
        for path in (scores_path, lineage_path, output_dir / "selection.json"):
            if path.is_file() and path.stat().st_size:
                residue.append(str(path))
        if pool_dir.is_dir() and any(pool_dir.iterdir()):
            residue.append(str(pool_dir))
        checkpoints = output_dir / "checkpoints"
        if checkpoints.is_dir() and any(checkpoints.iterdir()):
            residue.append(str(checkpoints))
        if residue:
            raise RuntimeError(
                "Formal fresh search has residual state but no controller checkpoint: "
                + ", ".join(residue)
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    pool_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    if controller is None:
        _write_json(
            traces_dir / "manifest.json",
            {
                "artifact_class": "evaluator_full_trace",
                "evaluator_only": True,
                "candidate_forbidden": True,
                "included_in_promotion_bundle": False,
                "candidate_mutation_may_reference": False,
            },
        )
        scores_path.write_text("", encoding="utf-8")
        lineage_path.write_text("", encoding="utf-8")
        (output_dir / "config.json").write_text(
            json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if cfg.formal:
            controller = {
                "version": SEARCH_STATE_VERSION,
                "run_identity": controller_identity,
                "phase": "subset_selection",
                "rng_state": _json_state(rng.getstate()),
                "scores_lines": 0,
                "lineage_lines": 0,
                "candidates": [],
                "seed_index": 0,
                "attempt": 0,
                "lineage_cursor": 0,
                "next_index": 0,
                "successful_by_lineage": {},
                "attempts_by_lineage": {},
                "selection_scores": {},
            }
            _atomic_write_json(controller_path, controller)
    print(
        f"[MSTAR] Loaded data: episodes={len(episode_values)}, "
        f"validation={len(validation_values)}",
        flush=True,
    )

    def evaluate_candidate(
        program_path: str,
        program_id: str,
        lineage_name: str,
        items,
        *,
        replicate_id: str,
        collect_traces: bool = True,
    ):
        program_sha = _sha256_file(Path(program_path))
        checkpoint = (
            output_dir
            / "checkpoints"
            / program_id
            / f"{replicate_id}.json"
        )
        evaluation = evaluate_program(
            program_path,
            selected_episodes,
            items,
            task_agent,
            toolkit,
            metric,
            collect_traces=collect_traces,
            write_timeout=cfg.write_timeout,
            read_timeout=cfg.read_timeout,
            formal=cfg.formal,
            checkpoint_path=str(checkpoint) if cfg.formal else None,
            run_identity=(
                {
                    "candidate_id": program_id,
                    "lineage": lineage_name,
                    "replicate_id": replicate_id,
                    "program_sha256": program_sha,
                    "historical_manifest_sha256": cfg.historical_manifest_sha256,
                    "experiment_config_sha256": cfg.experiment_config_sha256,
                    "s2_asset_sha256": cfg.semantic_scholar_cache_sha256,
                    "s2_asset_version": cfg.s2_asset_version,
                    "run_seed": cfg.run_seed,
                    "seed_control_available": cfg.seed_control_available,
                    "generation_config": {
                        "task_model_name": cfg.task_model_name,
                        "model_source": cfg.generation_model_source,
                        "model_revision": cfg.generation_model_revision,
                        "vllm_version": cfg.vllm_version,
                        "temperature": cfg.llm_sampling_temperature,
                        "max_tokens": cfg.max_tokens,
                        "retry_max_tokens": cfg.retry_max_tokens,
                        "reasoning_effort": cfg.reasoning_effort,
                        "reasoning_effort_supported": (
                            cfg.reasoning_effort_supported
                        ),
                    },
                }
                if cfg.formal
                else None
            ),
        )
        if cfg.formal and evaluation.error:
            raise RuntimeError(evaluation.error)
        return evaluation
    if cfg.formal and controller.get("phase") != "subset_selection":
        static_id_values = set(controller.get("static_validation_ids") or [])
        episode_id_values = set(controller.get("selected_episode_ids") or [])
        static_items = [
            value for value in validation_values if _item_id(value) in static_id_values
        ]
        selected_episodes = [
            value for value in episode_values if value.id in episode_id_values
        ]
        if len(static_items) != len(static_id_values) or len(selected_episodes) != len(
            episode_id_values
        ):
            raise RuntimeError("Formal search checkpoint subset identities are missing")
    else:
        static_items = build_static_validation_set(
            validation_values,
            selection_embedding_client,
            n_static=cfg.static_size,
            seed=cfg.seed,
            allow_random_fallback=not cfg.formal,
        )
        budget = cfg.episode_budget
        if budget is None:
            budget = 5 * len(static_items)
        selected_episodes = select_episode_subset(
            episode_values,
            static_items,
            selection_embedding_client,
            budget=budget,
            allow_random_fallback=not cfg.formal,
        )
        if cfg.formal:
            selected_ids = {episode.id for episode in selected_episodes}
            selected_episodes = [
                episode for episode in episode_values if episode.id in selected_ids
            ]
    static_ids = {_item_id(item) for item in static_items}
    remaining_items = [
        item for item in validation_values if _item_id(item) not in static_ids
    ]
    selection_payload = {
        "static_validation_ids": sorted(static_ids),
        "episode_ids": [episode.id for episode in selected_episodes],
        "embedding": {
            "specification": dict(cfg.embedding_spec),
            "fingerprint": cfg.embedding_fingerprint,
        },
    }
    selection_payload["selection_fingerprint"] = _canonical_sha256(
        selection_payload
    )
    if (
        cfg.formal
        and cfg.embedding_spec.get("provider") == "qwen3_embedding"
        and controller.get("phase") == "subset_selection"
    ):
        if not selected_episodes or not static_items:
            raise RuntimeError("Embedding closure smoke requires selected A/B items")
        closure = run_embedding_closure_smoke(
            str(seed_program_paths[0]),
            Episode.from_value(selected_episodes[0]),
            EvalItem.from_value(static_items[0]),
            task_agent,
            toolkit,
        )
        _write_json(output_dir / "embedding_closure_smoke.json", closure)
        print(
            "[MSTAR] Embedding closure smoke passed: "
            f"episode={closure['episode_id']}, "
            f"validation={closure['validation_id']}",
            flush=True,
        )
    if not cfg.formal or controller.get("phase") == "subset_selection":
        _write_json(output_dir / "selection.json", selection_payload)
        if cfg.formal:
            controller["static_validation_ids"] = selection_payload[
                "static_validation_ids"
            ]
            controller["selected_episode_ids"] = selection_payload["episode_ids"]
            controller["phase"] = "seeds"
            _atomic_write_json(controller_path, controller)
    print(
        f"[MSTAR] Selected static_validation={len(static_items)}, "
        f"selected_episodes={len(selected_episodes)}",
        flush=True,
    )

    constraints = EvolutionConstraints(
        task_description=cfg.task_description,
        max_llm_calls_per_operation=cfg.max_llm_calls_per_operation,
        write_timeout=cfg.write_timeout,
        read_timeout=cfg.read_timeout,
        mutation_scope=cfg.mutation_scope,
    )
    candidates: List[ProgramCandidate] = (
        [
            _candidate_from_state(dict(value))
            for value in controller.get("candidates") or []
        ]
        if cfg.formal
        else []
    )

    def commit_controller(**updates) -> None:
        if not cfg.formal:
            return
        controller.update(updates)
        controller["rng_state"] = _json_state(rng.getstate())
        controller["scores_lines"] = _jsonl_line_count(scores_path)
        controller["lineage_lines"] = _jsonl_line_count(lineage_path)
        controller["candidates"] = [
            _candidate_to_state(candidate) for candidate in candidates
        ]
        _atomic_write_json(controller_path, controller)

    if cfg.formal and controller.get("phase") == "complete":
        return EvolutionResult(**dict(controller["result"]))

    seed_names = [
        "vector_search",
        "llm_summarizer",
        "experience_learner",
    ]
    seed_start = int(controller.get("seed_index", 0)) if cfg.formal else 0
    for index, source in enumerate(seed_program_paths):
        if index < seed_start:
            continue
        suffix = seed_names[index] if index < len(seed_names) else f"seed_{index}"
        program_id = f"program_{index:03d}_seed_{suffix}"
        target = pool_dir / f"{program_id}.py"
        shutil.copyfile(source, target)
        print(f"[MSTAR] Seed {program_id}: quality check started", flush=True)
        quality = check_program_path(
            str(target),
            toolkit=toolkit,
            write_timeout=cfg.write_timeout,
            read_timeout=cfg.read_timeout,
        )
        if quality.passed:
            print(f"[MSTAR] Seed {program_id}: static evaluation started", flush=True)
            evaluation = evaluate_candidate(
                str(target),
                program_id,
                f"evolved_from_{suffix}",
                static_items,
                replicate_id=f"search-{cfg.seed}",
                collect_traces=True,
            )
            score = evaluation.mean_score
            _write_traces(
                traces_dir / f"seed_{index:03d}_static.jsonl",
                program_id,
                evaluation.traces,
            )
            failed_count = sum(
                not bool(case.get("success")) for case in evaluation.failed_cases
            )
            print(
                f"[MSTAR] Seed {program_id}: score={score:.4f}, "
                f"traces={len(evaluation.traces)}, failed_cases={failed_count}, "
                f"error={evaluation.error or 'none'}",
                flush=True,
            )
        else:
            evaluation = None
            score = 0.0
            print(
                f"[MSTAR] Seed {program_id}: quality failed: {quality.errors}",
                flush=True,
            )
        change_log = ChangeLog(summary=f"Seed program: {suffix}", score=score)
        candidate = ProgramCandidate(
            program_id=program_id,
            path=str(target),
            parent_id=None,
            iteration=0,
            static_score=score,
            quality=quality,
            change_log=change_log,
            lineage=[change_log],
            parent_seed=f"seed_{suffix}",
            lineage_name=f"evolved_from_{suffix}",
            program_sha256=_sha256_file(target),
            completed_evaluation=bool(quality.passed and evaluation is not None),
        )
        candidates.append(candidate)
        _append_jsonl(scores_path, _candidate_record(candidate))
        _append_jsonl(
            lineage_path,
            {
                "program_id": program_id,
                "parent_id": None,
                "iteration": 0,
                "change_log": asdict(change_log),
                "quality": quality.to_dict(),
                "parent_seed": candidate.parent_seed,
                "lineage": candidate.lineage_name,
                "program_sha256": candidate.program_sha256,
            },
        )
        commit_controller(seed_index=index + 1, next_index=index + 1, phase="seeds")

    if cfg.formal and controller.get("phase") == "seeds":
        commit_controller(phase="search")

    next_index = int(controller.get("next_index", len(candidates))) if cfg.formal else len(candidates)
    successful_by_lineage = {
        f"evolved_from_{name}": 0 for name in seed_names
    }
    attempts_by_lineage = {
        f"evolved_from_{name}": 0 for name in seed_names
    }
    if cfg.formal:
        successful_by_lineage.update(
            {
                str(key): int(value)
                for key, value in dict(
                    controller.get("successful_by_lineage") or {}
                ).items()
            }
        )
        stored_attempts = dict(controller.get("attempts_by_lineage") or {})
        if set(stored_attempts) - set(attempts_by_lineage):
            raise RuntimeError("Formal checkpoint has unknown lineage attempt counters")
        attempts_by_lineage.update(
            {str(key): int(value) for key, value in stored_attempts.items()}
        )
        if any(value < 0 for value in attempts_by_lineage.values()):
            raise RuntimeError("Formal checkpoint has negative lineage attempt counters")
    target_successes_per_lineage = (
        cfg.iterations // len(seed_names) if cfg.formal else None
    )

    def admit_child(
        *,
        program_id: str,
        target: Path,
        parent: ProgramCandidate,
        iteration: int,
        rotating_score: float,
        quality: QualityResult,
        change_log: ChangeLog,
        fix_attempts: int,
        static_eval,
        attempt_value: int,
        cursor_value: int,
        next_index_value: int,
    ) -> ProgramCandidate:
        static_score = static_eval.mean_score if static_eval is not None else 0.0
        change_log.score = static_score
        candidate = ProgramCandidate(
            program_id=program_id,
            path=str(target),
            parent_id=parent.program_id,
            iteration=iteration,
            static_score=static_score,
            quality=quality,
            change_log=change_log,
            lineage=parent.lineage + [change_log],
            parent_seed=parent.parent_seed,
            lineage_name=parent.lineage_name,
            program_sha256=_sha256_file(target),
            completed_evaluation=bool(quality.passed and static_eval is not None),
        )
        candidates.append(candidate)
        _append_jsonl(
            scores_path,
            _candidate_record(
                candidate,
                rotating_score=rotating_score,
                compile_errors=list(quality.errors),
            ),
        )
        _append_jsonl(
            lineage_path,
            {
                "program_id": program_id,
                "parent_id": parent.program_id,
                "iteration": iteration,
                "parent_static_score": parent.static_score,
                "static_score": static_score,
                "rotating_score": rotating_score,
                "change_log": asdict(change_log),
                "quality": quality.to_dict(),
                "compile_fix_attempts": fix_attempts,
                "parent_seed": candidate.parent_seed,
                "lineage": candidate.lineage_name,
                "program_sha256": candidate.program_sha256,
                "completed_evaluation": candidate.completed_evaluation,
            },
        )
        if candidate.completed_evaluation:
            successful_by_lineage[candidate.lineage_name] += 1
        commit_controller(
            attempt=attempt_value,
            lineage_cursor=cursor_value,
            next_index=next_index_value,
            successful_by_lineage=successful_by_lineage,
            attempts_by_lineage=attempts_by_lineage,
            inflight_candidate=None,
            phase="search",
        )
        return candidate

    if cfg.formal and controller.get("phase") == "search_inflight":
        inflight = dict(controller.get("inflight_candidate") or {})
        parent = next(
            (
                value
                for value in candidates
                if value.program_id == inflight.get("parent_id")
            ),
            None,
        )
        if parent is None:
            raise RuntimeError("Formal inflight Candidate parent is missing")
        target = Path(str(inflight.get("path") or "")).expanduser().resolve()
        if (
            not target.is_file()
            or _sha256_file(target) != inflight.get("program_sha256")
        ):
            raise RuntimeError("Formal inflight Candidate Program identity mismatch")
        quality_value = dict(inflight.get("quality") or {})
        quality = QualityResult(
            passed=bool(quality_value.get("passed")),
            static_errors=list(quality_value.get("static_errors") or []),
            compile_errors=list(quality_value.get("compile_errors") or []),
            smoke_errors=list(quality_value.get("smoke_errors") or []),
        )
        change_log = _change_log_from_value(dict(inflight.get("change_log") or {}))
        print(
            f"[MSTAR] Resuming inflight Candidate {inflight['program_id']} static evaluation",
            flush=True,
        )
        static_eval = evaluate_candidate(
            str(target),
            str(inflight["program_id"]),
            parent.lineage_name,
            static_items,
            replicate_id=f"search-{cfg.seed}",
            collect_traces=False,
        )
        admit_child(
            program_id=str(inflight["program_id"]),
            target=target,
            parent=parent,
            iteration=int(inflight["iteration"]),
            rotating_score=float(inflight["rotating_score"]),
            quality=quality,
            change_log=change_log,
            fix_attempts=int(inflight.get("fix_attempts", 0)),
            static_eval=static_eval,
            attempt_value=int(controller["attempt"]),
            cursor_value=int(controller["lineage_cursor"]),
            next_index_value=int(controller["next_index"]),
        )

    if not cfg.dry_run and (not cfg.formal or controller.get("phase") == "search"):
        attempt = int(controller.get("attempt", 0)) if cfg.formal else 0
        lineage_cursor = int(controller.get("lineage_cursor", 0)) if cfg.formal else 0
        while True:
            if cfg.formal:
                if all(
                    count == target_successes_per_lineage
                    for count in successful_by_lineage.values()
                ):
                    break
                while (
                    successful_by_lineage[
                        f"evolved_from_{seed_names[lineage_cursor % len(seed_names)]}"
                    ]
                    >= target_successes_per_lineage
                ):
                    lineage_cursor += 1
                active_lineage = (
                    f"evolved_from_{seed_names[lineage_cursor % len(seed_names)]}"
                )
                lineage_cursor += 1
                lineage_attempts = attempts_by_lineage[active_lineage]
                if lineage_attempts >= cfg.max_attempts_per_lineage:
                    raise RuntimeError(
                        "Formal lineage attempt budget exhausted: "
                        f"lineage={active_lineage}, attempts={lineage_attempts}, "
                        f"successful={successful_by_lineage[active_lineage]}, "
                        f"target={target_successes_per_lineage}, "
                        f"max_attempts_per_lineage={cfg.max_attempts_per_lineage}"
                    )
            else:
                if attempt >= max(0, cfg.iterations):
                    break
                active_lineage = (
                    f"evolved_from_{seed_names[attempt % len(seed_names)]}"
                )
            attempt += 1
            if cfg.formal:
                attempts_by_lineage[active_lineage] += 1
                commit_controller(
                    attempt=attempt,
                    lineage_cursor=lineage_cursor,
                    next_index=next_index,
                    successful_by_lineage=successful_by_lineage,
                    attempts_by_lineage=attempts_by_lineage,
                    phase="search",
                )
            iteration = attempt
            program_id = f"program_{next_index:03d}_child"
            target = pool_dir / f"{program_id}.py"
            if cfg.formal:
                # The controller checkpoint, not filesystem presence, decides
                # admission. Remove only this exact uncommitted Candidate's
                # artifacts before rerunning its whole attempt.
                target.unlink(missing_ok=True)
                uncommitted_checkpoints = output_dir / "checkpoints" / program_id
                if uncommitted_checkpoints.is_dir():
                    shutil.rmtree(uncommitted_checkpoints)
            parent = _select_parent(candidates, cfg, rng, active_lineage)
            print(
                f"[MSTAR] Iteration {iteration}: parent={parent.program_id}, "
                f"parent_static_score={parent.static_score:.4f}",
                flush=True,
            )
            rotating_items = sample_rotating_validation_set(
                remaining_items,
                n_rotating=cfg.rotating_size,
                seed=cfg.seed + iteration,
            )
            rotating_eval = evaluate_candidate(
                parent.path,
                parent.program_id,
                parent.lineage_name,
                rotating_items,
                replicate_id=f"search-{cfg.seed}",
            )
            print(
                f"[MSTAR] Iteration {iteration}: rotating_score="
                f"{rotating_eval.mean_score:.4f}",
                flush=True,
            )
            _write_traces(
                traces_dir / f"iter_{iteration:03d}_rotating.jsonl",
                parent.program_id,
                rotating_eval.traces,
            )
            feedback = EvalFeedback(
                static_score=parent.static_score,
                rotating_score=rotating_eval.mean_score,
                failed_cases=[
                    candidate_safe_case(case)
                    for case in rotating_eval.failed_cases
                ],
                success_cases=[
                    candidate_safe_case(case)
                    for case in rotating_eval.success_cases
                ],
                traces=[
                    candidate_safe_trace(trace)
                    for trace in rotating_eval.traces
                ],
                task_description=cfg.task_description,
                write_examples=[],
                debug_logs=[
                    candidate_safe_trace(trace)
                    for trace in rotating_eval.traces
                    if not trace.get("success", True)
                ][:10],
                feedback_seed=cfg.seed + iteration,
            )

            next_index += 1
            try:
                parent_code = Path(parent.path).read_text(encoding="utf-8")
                print(f"[MSTAR] Iteration {iteration}: reflector mutation started", flush=True)
                mutation = mutate_program(
                    parent_code,
                    feedback,
                    parent.lineage,
                    reflector,
                    constraints,
                    reference_programs=_reference_programs(
                        candidates,
                        parent.program_id,
                        parent.lineage_name,
                    ),
                )
                code = mutation.program_code
                change_log = mutation.change_log
                mutation_error = None
            except Exception as exc:
                mutation_error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[MSTAR] Iteration {iteration}: reflector failed: {mutation_error}",
                    flush=True,
                )
                _append_jsonl(
                    scores_path,
                    {
                        "program_id": None,
                        "parent_id": parent.program_id,
                        "iteration": iteration,
                        "static_score": None,
                        "rotating_score": rotating_eval.mean_score,
                        "quality_passed": False,
                        "compile_errors": [mutation_error],
                        "change_summary": "Reflector failed; no child admitted",
                    },
                )
                _append_jsonl(
                    lineage_path,
                    {
                        "program_id": None,
                        "parent_id": parent.program_id,
                        "iteration": iteration,
                        "rotating_score": rotating_eval.mean_score,
                        "admitted": False,
                        "error": mutation_error,
                        "lineage": parent.lineage_name,
                        "completed_budget_consumed": False,
                    },
                )
                commit_controller(
                    attempt=attempt,
                    lineage_cursor=lineage_cursor,
                    next_index=next_index,
                    successful_by_lineage=successful_by_lineage,
                    attempts_by_lineage=attempts_by_lineage,
                    phase="search",
                )
                continue
            target.write_text(code, encoding="utf-8")
            quality = check_program_path(
                str(target),
                toolkit=toolkit,
                write_timeout=cfg.write_timeout,
                read_timeout=cfg.read_timeout,
            )
            fix_attempts = 0
            if not quality.passed and mutation_error is None:
                print(
                    f"[MSTAR] Iteration {iteration}: compile-fix started: "
                    f"{quality.errors}",
                    flush=True,
                )
                fixed = compile_fix_loop(
                    code,
                    quality.errors,
                    max_attempts=cfg.max_fix_attempts,
                    llm_client=reflector,
                    constraints=constraints,
                    toolkit=toolkit,
                )
                fix_attempts = fixed.attempts
                code = fixed.program_code
                scope_valid = True
                try:
                    validate_mutation_scope(
                        parent_code,
                        code,
                        cfg.mutation_scope,
                    )
                except ValueError as exc:
                    scope_valid = False
                    quality = QualityResult(
                        passed=False,
                        static_errors=[str(exc)],
                    )
                target.write_text(code, encoding="utf-8")
                if scope_valid:
                    quality = check_program_path(
                        str(target),
                        toolkit=toolkit,
                        write_timeout=cfg.write_timeout,
                        read_timeout=cfg.read_timeout,
                    )

            static_eval = None
            if quality.passed:
                if cfg.formal:
                    commit_controller(
                        attempt=attempt,
                        lineage_cursor=lineage_cursor,
                        next_index=next_index,
                        successful_by_lineage=successful_by_lineage,
                        attempts_by_lineage=attempts_by_lineage,
                        inflight_candidate={
                            "program_id": program_id,
                            "path": str(target.resolve()),
                            "program_sha256": _sha256_file(target),
                            "parent_id": parent.program_id,
                            "iteration": iteration,
                            "rotating_score": rotating_eval.mean_score,
                            "quality": quality.to_dict(),
                            "change_log": asdict(change_log),
                            "fix_attempts": fix_attempts,
                        },
                        phase="search_inflight",
                    )
                print(f"[MSTAR] Iteration {iteration}: static evaluation started", flush=True)
                static_eval = evaluate_candidate(
                    str(target),
                    program_id,
                    parent.lineage_name,
                    static_items,
                    replicate_id=f"search-{cfg.seed}",
                    collect_traces=False,
                )
                static_score = static_eval.mean_score
                print(
                    f"[MSTAR] Iteration {iteration}: child_score={static_score:.4f}",
                    flush=True,
                )
            else:
                static_score = 0.0
                print(
                    f"[MSTAR] Iteration {iteration}: child quality failed: "
                    f"{quality.errors}",
                    flush=True,
                )
            admit_child(
                program_id=program_id,
                target=target,
                parent=parent,
                iteration=iteration,
                rotating_score=rotating_eval.mean_score,
                quality=quality,
                change_log=change_log,
                fix_attempts=fix_attempts,
                static_eval=static_eval,
                attempt_value=attempt,
                cursor_value=lineage_cursor,
                next_index_value=next_index,
            )

    if cfg.formal and controller.get("phase") == "search":
        commit_controller(
            attempt=attempt,
            lineage_cursor=lineage_cursor,
            next_index=next_index,
            successful_by_lineage=successful_by_lineage,
            attempts_by_lineage=attempts_by_lineage,
            phase="selection",
        )

    if cfg.formal:
        counts = list(successful_by_lineage.values())
        if len(set(counts)) != 1 or any(
            count != target_successes_per_lineage for count in counts
        ):
            raise RuntimeError(
                "Formal lineage comparison requires equal successful candidate "
                f"evaluations; observed {successful_by_lineage}"
            )
    _write_json(
        output_dir / "lineage_budget.json",
        {
            "budget_unit": "successful_child_candidate_evaluation",
            "requested_total": cfg.iterations,
            "target_per_lineage": target_successes_per_lineage,
            "max_attempts_per_lineage": (
                cfg.max_attempts_per_lineage if cfg.formal else None
            ),
            "attempts_by_lineage": attempts_by_lineage if cfg.formal else None,
            "successful_completed_by_lineage": successful_by_lineage,
            "equal_completed_budget_required": bool(cfg.formal),
            "equal_completed_budget_passed": (
                len(set(successful_by_lineage.values())) == 1 if cfg.formal else None
            ),
        },
    )

    eligible = [
        candidate
        for candidate in candidates
        if candidate.quality.passed and candidate.completed_evaluation
    ]
    if not eligible:
        raise RuntimeError("All seed and child programs failed quality gates")
    ranked = sorted(
        eligible,
        key=lambda candidate: candidate.static_score,
        reverse=True,
    )[: max(1, int(cfg.selection_top_k))]
    repeat_count = max(1, int(cfg.selection_repeats))
    repeated_selection = []
    selection_scores = (
        {
            str(key): [float(score) for score in value]
            for key, value in dict(controller.get("selection_scores") or {}).items()
        }
        if cfg.formal
        else {}
    )
    for candidate in ranked:
        repeated_scores = list(selection_scores.get(candidate.program_id) or [])
        if len(repeated_scores) > repeat_count:
            raise RuntimeError("Formal selection checkpoint has excess repeats")
        for repeat_index in range(len(repeated_scores), repeat_count):
            repeated = evaluate_candidate(
                candidate.path,
                candidate.program_id,
                candidate.lineage_name,
                static_items,
                replicate_id=f"selection-{cfg.seed}-{repeat_index}",
                collect_traces=False,
            )
            repeated_scores.append(repeated.mean_score)
            if cfg.formal:
                selection_scores[candidate.program_id] = list(repeated_scores)
                commit_controller(
                    phase="selection", selection_scores=selection_scores
                )
        mean = sum(repeated_scores) / len(repeated_scores)
        variance = sum((score - mean) ** 2 for score in repeated_scores) / len(
            repeated_scores
        )
        repeated_selection.append(
            {
                "candidate": candidate,
                "scores": repeated_scores,
                "replicate_ids": [
                    f"selection-{cfg.seed}-{index}" for index in range(repeat_count)
                ],
                "mean": mean,
                "variance": variance,
            }
        )
    selected = max(
        repeated_selection,
        key=lambda value: (value["mean"], -value["variance"]),
    )
    best = selected["candidate"]
    if cfg.formal:
        committed_best = str(controller.get("selected_program_id") or "")
        if committed_best and committed_best != best.program_id:
            raise RuntimeError("Formal selected Candidate identity changed on resume")
        commit_controller(
            phase="final",
            selection_scores=selection_scores,
            selected_program_id=best.program_id,
        )
    lineage_champions = {}
    for lineage_name in sorted({candidate.lineage_name for candidate in eligible}):
        lineage_candidates = [
            candidate
            for candidate in eligible
            if candidate.lineage_name == lineage_name
        ]
        champion = max(lineage_candidates, key=lambda candidate: candidate.static_score)
        lineage_dir = output_dir / "lineage_champions" / lineage_name
        lineage_dir.mkdir(parents=True, exist_ok=True)
        lineage_path = lineage_dir / "best_program.py"
        shutil.copyfile(champion.path, lineage_path)
        lineage_champions[lineage_name] = {
            "parent_seed": champion.parent_seed,
            "program_id": champion.program_id,
            "fitness": champion.static_score,
            "program": str(lineage_path),
            "program_sha256": _sha256_file(lineage_path),
            "promotion_provenance": "best static fitness within independent lineage",
        }
    _write_json(output_dir / "lineage_champions.json", lineage_champions)
    best_path = output_dir / "best_program.py"
    shutil.copyfile(best.path, best_path)
    print(
        f"[MSTAR] Best program={best.program_id}, score={selected['mean']:.4f}",
        flush=True,
    )
    final_evaluation = evaluate_candidate(
        str(best_path),
        best.program_id,
        best.lineage_name,
        static_items,
        replicate_id=f"final-{cfg.seed}",
        collect_traces=False,
    )
    best_report = {
        "program_id": best.program_id,
        "static_score": best.static_score,
        "selection_mean": selected["mean"],
        "selection_variance": selected["variance"],
        "selection_repeats": repeated_selection and [
            {
                "program_id": value["candidate"].program_id,
                "scores": value["scores"],
                "replicate_ids": value["replicate_ids"],
                "mean": value["mean"],
                "variance": value["variance"],
            }
            for value in repeated_selection
        ],
        "source_path": best.path,
        "parent_seed": best.parent_seed,
        "lineage": best.lineage_name,
        "candidate_program_sha256": best.program_sha256,
        "promoted_program_sha256": _sha256_file(best_path),
        "promotion_provenance": {
            "selection_rule": "highest repeated-selection mean; variance tie-break",
            "source_program_id": best.program_id,
            "source_lineage": best.lineage_name,
        },
        "lineage_champions": lineage_champions,
        "lineage_successful_completed_counts": successful_by_lineage,
        "lineage_attempt_counts": attempts_by_lineage if cfg.formal else None,
        "max_attempts_per_lineage": (
            cfg.max_attempts_per_lineage if cfg.formal else None
        ),
        "lineage_completed_budget_equal": (
            len(set(successful_by_lineage.values())) == 1 if cfg.formal else None
        ),
        "quality": best.quality.to_dict(),
        "change_log": asdict(best.change_log),
        "static_validation_ids": sorted(static_ids),
        "final_static_evaluation": {
            "mean_score": final_evaluation.mean_score,
            "runtime_stats": final_evaluation.runtime_stats,
            "error": final_evaluation.error,
            "case_content_hidden": True,
        },
        "test_set_used": False,
        "run_seed": cfg.run_seed,
        "seed_control_available": cfg.seed_control_available,
        "generation_config": {
            "task_model_name": cfg.task_model_name,
            "reflector_model_name": cfg.reflector_model_name,
            "judge_model_name": cfg.judge_model_name,
        },
    }
    _write_json(output_dir / "best_eval_report.json", best_report)
    result = EvolutionResult(
        best_program_path=str(best_path),
        best_program_id=best.program_id,
        best_score=selected["mean"],
        output_dir=str(output_dir),
        pool_size=len(candidates),
        static_validation_ids=sorted(static_ids),
    )
    if cfg.formal:
        commit_controller(phase="complete", result=result.to_dict())
    return result
