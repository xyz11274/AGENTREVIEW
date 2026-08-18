"""Command line entrypoint for the single M★ evolution architecture."""

import argparse
import importlib
import hashlib
import json
import shutil
from pathlib import Path

from .agentreview_data import (
    prepare_held_out_test_data,
    prepare_meta_data,
    prepare_openreview_data,
)
from .evaluator import evaluate_program
from .metrics import get_metric
from .meta_cache import build_meta_input_cache
from .models import Episode, EvalItem
from .quality import check_program_path
from .formal_protocol import run_formal_protocol_gate, run_formal_test_gate
from .search import EvolutionConfig, run_evolution
from .task_agent import build_task_agent
from .tasks import (
    ALL_TASKS,
    META_TASK,
    SPECIALIST_TASKS,
    experiment_provenance,
    resolve_program_path,
    verify_promoted_program,
)
from agentreview.single_paper_review_roles import EXPECTED_REVIEW_REQUEST_SHA256
from .toolkit import MemoryToolkit, load_frozen_s2_pool
from .benchmark_adapter import CoCoMetricBenchmarkAdapter


TASK_MODEL_DEFAULT = "kimi-k3"
REFLECTOR_MODEL_DEFAULT = "kimi-k3"
JUDGE_MODEL_DEFAULT = "kimi-k3"


def _load_jsonl(path: str):
    values = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backend(args, model_name: str):
    from agentreview.backends.openai import OpenAIChat

    return OpenAIChat(
        model=model_name,
        max_tokens=args.max_tokens,
        temperature=0.0,
        openai_client_type=args.openai_client_type,
        request_timeout_seconds=args.api_request_timeout_seconds,
        connect_timeout_seconds=args.api_connect_timeout_seconds,
        reasoning_effort=(
            None if args.reasoning_effort == "none" else args.reasoning_effort
        ),
        retry_max_tokens=args.retry_max_tokens,
    )


def _classifier_adapter(
    spec: str,
    split_revision: str = "",
    classify_revision: str = "",
    coco_root: str = "",
):
    """Load a frozen `(splitter, classifier)` factory without coupling repos."""
    if not spec:
        return None, None
    module_name, separator, factory_name = spec.partition(":")
    if not separator:
        raise ValueError("--classifier-adapter must use module:factory syntax")
    factory = getattr(importlib.import_module(module_name), factory_name)
    try:
        splitter, classifier = factory(
            split_revision=split_revision,
            classify_revision=classify_revision,
            coco_root=coco_root,
        )
    except TypeError:
        splitter, classifier = factory()
    if not callable(splitter) or not callable(classifier):
        raise TypeError("classifier adapter factory must return two callables")
    return splitter, classifier


def _embedding_adapter(spec: str, model_name: str, revision: str):
    if not spec:
        return None
    module_name, separator, factory_name = spec.partition(":")
    if not separator:
        raise ValueError("--embedding-adapter must use module:factory syntax")
    factory = getattr(importlib.import_module(module_name), factory_name)
    try:
        client = factory(model_name=model_name, revision=revision)
    except TypeError:
        client = factory()
    if not (
        callable(client)
        or hasattr(client, "embed")
        or hasattr(client, "embed_documents")
    ):
        raise TypeError("embedding adapter factory returned an unsupported client")
    return client


def _adapter_sha256(spec: str) -> str:
    if not spec:
        return ""
    module_name = spec.partition(":")[0]
    module = importlib.import_module(module_name)
    source = getattr(module, "__file__", None)
    if not source:
        raise ValueError(f"Cannot fingerprint adapter module: {module_name}")
    return _sha256(Path(source).resolve())


def _task_toolkit(args):
    splitter, classifier = _classifier_adapter(
        args.classifier_adapter,
        args.review_split_revision,
        args.review_classify_revision,
        getattr(args, "coco_root", ""),
    )
    s2_pool, s2_pool_sha256 = load_frozen_s2_pool(
        getattr(args, "s2_retrieval_pool", "")
    )
    return MemoryToolkit(
        llm_client=_backend(args, args.task_model_name),
        llm_model=args.task_model_name,
        embedding_client=_embedding_adapter(
            args.embedding_adapter,
            args.embedding_model_name,
            args.embedding_revision,
        ),
        embedding_model=args.embedding_model_name,
        review_splitter=splitter,
        review_classifier=classifier,
        max_llm_calls_per_operation=1,
        s2_retrieval_pool=s2_pool,
        s2_retrieval_pool_sha256=s2_pool_sha256,
    )


def _toolkits(args):
    task = _task_toolkit(args)
    reflector = MemoryToolkit(
        llm_client=_backend(args, args.reflector_model_name),
        llm_model=args.reflector_model_name,
        max_llm_calls_per_operation=1,
    )
    judge = MemoryToolkit(
        llm_client=_backend(args, args.judge_model_name),
        llm_model=args.judge_model_name,
        max_llm_calls_per_operation=1,
    )
    return task, reflector, judge


def _seed_paths():
    root = Path(__file__).resolve().parent / "seeds"
    return [
        str(root / "vector_search.py"),
        str(root / "llm_summarizer.py"),
        str(root / "experience_learner.py"),
    ]


def _experiment_kind(args) -> str:
    if args.smoke:
        return "smoke_non_reportable"
    if getattr(args, "pilot", False):
        return "pilot_non_reportable"
    deviations = []
    canonical = {
        "iterations": 21,
        "static_size": 25,
        "rotating_size": 5,
        "temperature": 0.15,
        "max_fix_attempts": 3,
        "parent_policy": "softmax",
        "mutation_scope": "full",
    }
    for name, expected in canonical.items():
        if getattr(args, name) != expected:
            deviations.append(f"{name}={getattr(args, name)}")
    return "formal" if not deviations else "ablation:" + ",".join(deviations)


def _is_nonformal(args) -> bool:
    return bool(getattr(args, "smoke", False) or getattr(args, "pilot", False))


def _add_nonformal_mode_args(parser, *, include_smoke: bool = True):
    group = parser.add_mutually_exclusive_group()
    if include_smoke:
        group.add_argument(
            "--smoke",
            action="store_true",
            help=(
                "Run a non-reportable structural smoke search. This permits the "
                "explicit-label classifier fallback and reduces the search to "
                "one mutation."
            ),
        )
    group.add_argument(
        "--pilot",
        action="store_true",
        help=(
            "Run a non-reportable small-data search with the requested search "
            "sizes and iterations. This permits the same classifier and "
            "embedding fallbacks as smoke mode without reducing the search."
        ),
    )


def _add_runtime_args(parser):
    parser.add_argument("--task-model-name", default=TASK_MODEL_DEFAULT)
    parser.add_argument("--reflector-model-name", default=REFLECTOR_MODEL_DEFAULT)
    parser.add_argument("--judge-model-name", default=JUDGE_MODEL_DEFAULT)
    parser.add_argument(
        "--classifier-adapter",
        default="",
        help=(
            "Frozen ReviewSplit/ReviewClassify adapter as module:factory. "
            "Required for formal specialist evolution."
        ),
    )
    parser.add_argument(
        "--review-split-revision",
        default="",
        help="Immutable ReviewSplit checkpoint revision for provenance.",
    )
    parser.add_argument(
        "--review-classify-revision",
        default="",
        help="Immutable ReviewClassify checkpoint revision for provenance.",
    )
    parser.add_argument(
        "--embedding-adapter",
        default="",
        help=(
            "Frozen BGE-M3 embedding client as module:factory. Required for "
            "formal M★ subset selection."
        ),
    )
    parser.add_argument("--embedding-model-name", default="BAAI/bge-m3")
    parser.add_argument(
        "--s2-retrieval-pool",
        default="",
        help="Frozen paper-specific S2 JSON/JSONL pool; never queried live or injected by default.",
    )
    parser.add_argument(
        "--asset-manifest",
        default=str(
            Path(__file__).resolve().parent.parent
            / "configs"
            / "asset_manifest_v2.json"
        ),
        help="Versioned formal Asset Manifest; fail-closed in formal mode.",
    )
    parser.add_argument(
        "--temporal-split-manifest",
        default=str(
            Path(__file__).resolve().parent.parent
            / "configs"
            / "coco_temporal_split_v2"
            / "manifest.json"
        ),
    )
    parser.add_argument(
        "--coco-root",
        default="",
        help="Configured external CoCoReviewBench checkout.",
    )
    parser.add_argument(
        "--embedding-revision",
        default="",
        help="Immutable model revision/commit recorded for formal BGE-M3 runs.",
    )
    parser.add_argument(
        "--openai-client-type",
        choices=["openai", "azure_openai"],
        default="openai",
    )
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--retry-max-tokens", type=int, default=16384)
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high"],
        default="low",
    )
    parser.add_argument("--api-request-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--api-connect-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--write-timeout", type=float, default=60.0)
    parser.add_argument("--read-timeout", type=float, default=60.0)


def _add_search_args(parser):
    parser.add_argument("--iterations", type=int, default=21)
    parser.add_argument("--static-size", type=int, default=25)
    parser.add_argument("--rotating-size", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.15)
    parser.add_argument("--episode-budget", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--selection-repeats", type=int, default=3)
    parser.add_argument("--selection-top-k", type=int, default=3)
    parser.add_argument("--max-fix-attempts", type=int, default=3)
    parser.add_argument(
        "--parent-policy",
        choices=["softmax", "best"],
        default="softmax",
        help="'best' is the no-population/linear-best ablation.",
    )
    parser.add_argument(
        "--mutation-scope",
        choices=["full", "schema_logic", "instructions_only"],
        default="full",
        help="M★ full search or a memory-only/prompt-only ablation.",
    )
    _add_nonformal_mode_args(parser)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Strict M★ MemoryProgram evolution for AgentReview."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-specialists")
    prepare.add_argument("--openreview-root", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--temporal-split-manifest", required=True)
    prepare.add_argument("--coco-root", required=True)
    prepare.add_argument("--validation-families", type=int, default=150)
    prepare.add_argument("--meta-validation-families", type=int, default=150)
    prepare.add_argument("--max-train-families", type=int, default=2000)
    prepare.add_argument("--max-paper-chars", type=int, default=60000)
    prepare.add_argument("--seed", type=int, default=0)
    prepare.add_argument(
        "--s2-cache",
        default="",
        help=(
            "Frozen Semantic Scholar JSON/JSONL cache. Data prepared without it "
            "is smoke-only and rejected by formal specialist evolution."
        ),
    )

    prepare_test = subparsers.add_parser("prepare-test")
    prepare_test.add_argument("--coco-root", required=True)
    prepare_test.add_argument("--output-dir", required=True)
    prepare_test.add_argument("--s2-cache", required=True)
    prepare_test.add_argument("--temporal-split-manifest", required=True)
    prepare_test.add_argument("--max-paper-chars", type=int, default=60000)

    meta = subparsers.add_parser("prepare-meta")
    meta.add_argument("--openreview-root", required=True)
    meta.add_argument("--specialist-outputs", required=True)
    meta.add_argument("--output-dir", required=True)

    cache = subparsers.add_parser("cache-meta-inputs")
    cache.add_argument("--data-root", required=True)
    cache.add_argument("--program-root", required=True)
    cache.add_argument("--output", required=True)
    cache.add_argument("--folds", type=int, default=5)
    _add_nonformal_mode_args(cache)
    _add_runtime_args(cache)

    evolve = subparsers.add_parser("evolve-task")
    evolve.add_argument("--task-id", choices=ALL_TASKS, required=True)
    evolve.add_argument("--data-root", required=True)
    evolve.add_argument("--output", required=True)
    _add_search_args(evolve)
    _add_runtime_args(evolve)

    suite = subparsers.add_parser("evolve-specialists")
    suite.add_argument("--data-root", required=True)
    suite.add_argument("--output", required=True)
    suite.add_argument(
        "--experiment-seeds",
        default="0",
        help=(
            "One or more comma-separated independent outer Evolution seeds. "
            "The minimal formal feasibility run uses seed 0; additional seeds "
            "are optional stability/robustness repeats."
        ),
    )
    suite.add_argument("--primary-seed", type=int, default=0)
    _add_search_args(suite)
    _add_runtime_args(suite)

    meta_suite = subparsers.add_parser("evolve-meta")
    meta_suite.add_argument("--data-root", required=True)
    meta_suite.add_argument("--output", required=True)
    meta_suite.add_argument(
        "--experiment-seeds",
        default="0",
        help=(
            "One or more comma-separated independent outer Evolution seeds. "
            "The minimal formal feasibility run uses seed 0."
        ),
    )
    meta_suite.add_argument("--primary-seed", type=int, default=0)
    _add_search_args(meta_suite)
    _add_runtime_args(meta_suite)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--task-id", choices=ALL_TASKS, required=True)
    evaluate.add_argument("--program", required=True)
    evaluate.add_argument("--episodes", required=True)
    evaluate.add_argument("--eval", required=True)
    evaluate.add_argument(
        "--selection",
        default="",
        help="selection.json from the promoted source run; required formally.",
    )
    evaluate.add_argument("--output", required=True)
    _add_nonformal_mode_args(evaluate)
    evaluate.add_argument(
        "--final-test",
        action="store_true",
        help=(
            "Forbidden legacy flag. Formal D/Test is available only through "
            "final-evaluate-suite."
        ),
    )
    _add_runtime_args(evaluate)

    final_suite = subparsers.add_parser("final-evaluate-suite")
    final_suite.add_argument("--data-root", required=True)
    final_suite.add_argument("--test-data-root", required=True)
    final_suite.add_argument("--program-root", required=True)
    final_suite.add_argument("--output", required=True)
    _add_runtime_args(final_suite)
    return parser


def _paths(data_root: str, task_id: str):
    root = Path(data_root).expanduser().resolve() / task_id
    return root / "episodes.jsonl", root / "validation.jsonl"


def _parse_experiment_seeds(value: str):
    try:
        seeds = [int(item) for item in str(value).split(",") if item.strip()]
    except ValueError as exc:
        raise SystemExit(
            "--experiment-seeds must be a comma-separated list of integers"
        ) from exc
    if not seeds:
        raise SystemExit("Formal suite evolution requires at least one experiment seed")
    if len(set(seeds)) != len(seeds):
        raise SystemExit("--experiment-seeds must not contain duplicates")
    return seeds


def _run_task(args, task_id: str, output: Path, seed: int):
    episodes_path, validation_path = _paths(args.data_root, task_id)
    episodes = [Episode.from_value(item) for item in _load_jsonl(str(episodes_path))]
    validation = [
        EvalItem.from_value(item) for item in _load_jsonl(str(validation_path))
    ]
    data_root = Path(args.data_root).expanduser().resolve()
    manifest_path = (
        data_root / META_TASK / "manifest.json"
        if task_id == META_TASK
        else data_root / "manifest.json"
    )
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    if not getattr(args, "s2_retrieval_pool", ""):
        args.s2_retrieval_pool = str(
            manifest.get("semantic_scholar_cache") or ""
        )
    formal = not _is_nonformal(args)
    if formal and args.iterations % len(_seed_paths()) != 0:
        raise SystemExit(
            "Formal evolution requires equal lineage budgets: --iterations "
            f"must be divisible by {len(_seed_paths())}."
        )
    if formal and not getattr(args, "coco_root", ""):
        raise SystemExit("Formal execution requires configured --coco-root")
    formal_gate = None
    if formal:
        formal_gate = run_formal_protocol_gate(
            asset_manifest_path=args.asset_manifest,
            temporal_split_manifest_path=args.temporal_split_manifest,
            data_root=args.data_root,
            task_id=task_id,
            s2_pool_path=args.s2_retrieval_pool,
            program_paths=_seed_paths(),
            config_identity={
                "task_id": task_id,
                "experiment_seed": seed,
                "iterations": args.iterations,
                "static_size": args.static_size,
                "rotating_size": args.rotating_size,
                "temperature": args.temperature,
                "episode_budget": args.episode_budget,
                "task_model_name": args.task_model_name,
                "reflector_model_name": args.reflector_model_name,
                "judge_model_name": args.judge_model_name,
                "generation": {
                    "max_tokens": args.max_tokens,
                    "retry_max_tokens": args.retry_max_tokens,
                    "reasoning_effort": args.reasoning_effort,
                },
            },
        )
    task_toolkit, reflector_toolkit, judge_toolkit = _toolkits(args)
    if formal and task_id != META_TASK and not args.classifier_adapter:
        raise SystemExit(
            "Formal specialist evolution requires --classifier-adapter. "
            "Use --pilot for a non-reportable small-data search."
        )
    if (
        formal
        and task_id != META_TASK
        and (
            not args.review_split_revision
            or not args.review_classify_revision
        )
    ):
        raise SystemExit(
            "Formal specialist evolution requires immutable "
            "--review-split-revision and --review-classify-revision."
        )
    if (
        formal
        and task_id != META_TASK
        and not manifest.get("formal_ready")
    ):
        raise SystemExit(
            "Formal specialist evolution requires a complete, checksummed S2 "
            "cache covering every selected paper."
        )
    if formal and task_id != META_TASK:
        actual_s2_sha = task_toolkit.s2_retrieval_pool_sha256
        expected_s2_sha = str(manifest.get("semantic_scholar_cache_sha256") or "")
        if not actual_s2_sha or actual_s2_sha != expected_s2_sha:
            raise SystemExit(
                "Formal evolution requires the manifest-pinned frozen S2 pool."
            )
    if formal and (
        not args.embedding_adapter or not args.embedding_revision
    ):
        raise SystemExit(
            "Formal evolution requires --embedding-adapter and an immutable "
            "--embedding-revision for BGE-M3."
        )
    metric = get_metric(
        "coco_review",
        task_toolkit,
        task_id=task_id,
        judge_toolkit=judge_toolkit,
        formal=formal,
    )
    config = EvolutionConfig(
        output_dir=str(output),
        task_description=(
            f"Generate a complete {task_id} review through a fixed Task Agent; "
            "evolve only the executable MemoryProgram."
        ),
        task_id=task_id,
        iterations=1 if args.smoke else args.iterations,
        static_size=min(args.static_size, 2) if args.smoke else args.static_size,
        rotating_size=min(args.rotating_size, 1) if args.smoke else args.rotating_size,
        temperature=args.temperature,
        episode_budget=min(args.episode_budget or 10, 10) if args.smoke else args.episode_budget,
        seed=seed,
        max_fix_attempts=args.max_fix_attempts,
        selection_repeats=1 if args.smoke else args.selection_repeats,
        selection_top_k=1 if args.smoke else args.selection_top_k,
        parent_policy=args.parent_policy,
        mutation_scope=args.mutation_scope,
        write_timeout=args.write_timeout,
        read_timeout=args.read_timeout,
        task_model_name=args.task_model_name,
        reflector_model_name=args.reflector_model_name,
        judge_model_name=args.judge_model_name,
        classifier_adapter=args.classifier_adapter,
        classifier_adapter_sha256=_adapter_sha256(args.classifier_adapter),
        review_split_revision=args.review_split_revision,
        review_classify_revision=args.review_classify_revision,
        embedding_adapter=args.embedding_adapter,
        embedding_adapter_sha256=_adapter_sha256(args.embedding_adapter),
        embedding_model_name=args.embedding_model_name,
        embedding_revision=args.embedding_revision,
        formal=formal,
        review_request_sha256=EXPECTED_REVIEW_REQUEST_SHA256,
        data_manifest_sha256=_sha256(manifest_path) if manifest_path.is_file() else "",
        semantic_scholar_cache_sha256=str(
            manifest.get("semantic_scholar_cache_sha256") or ""
        ),
        seed_program_sha256={
            Path(path).name: _sha256(Path(path)) for path in _seed_paths()
        },
        experiment_kind=_experiment_kind(args),
        historical_manifest_sha256=(
            formal_gate["historical_manifest_sha256"] if formal_gate else ""
        ),
        experiment_config_sha256=(
            formal_gate["experiment_config_sha256"] if formal_gate else ""
        ),
        s2_asset_version="frozen-s2-temporal-v2" if formal_gate else "",
        run_seed=seed,
        seed_control_available=False,
    )
    if formal_gate:
        output.mkdir(parents=True, exist_ok=True)
        (output / "formal_gate.json").write_text(
            json.dumps(formal_gate, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return run_evolution(
        _seed_paths(),
        episodes,
        validation,
        build_task_agent(task_toolkit, task_id),
        task_toolkit,
        metric,
        config,
        reflector_client=reflector_toolkit,
    )


def _selected_episodes(data_root: Path, source_run: str, task_id: str):
    selection_path = Path(source_run) / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected_ids = set(selection.get("episode_ids") or [])
    episodes = [
        Episode.from_value(item)
        for item in _load_jsonl(str(data_root / task_id / "episodes.jsonl"))
    ]
    selected = [episode for episode in episodes if episode.id in selected_ids]
    if not selected:
        raise RuntimeError(f"No selected episodes resolved for {task_id}")
    return selected, selection_path


def _validate_formal_runtime(args, specialists: bool = True):
    if not args.embedding_adapter or not args.embedding_revision:
        raise SystemExit(
            "Formal execution requires the frozen BGE-M3 adapter and revision."
        )
    if specialists and (
        not args.classifier_adapter
        or not args.review_split_revision
        or not args.review_classify_revision
    ):
        raise SystemExit(
            "Formal specialist execution requires the frozen CoCo adapter and "
            "immutable ReviewSplit/ReviewClassify revisions."
        )


def _final_evaluate_suite(args):
    """Execute the sealed test once: five specialists, then the sixth META task."""
    _validate_formal_runtime(args)
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite formal suite test report: {output}")
    data_root = Path(args.data_root).expanduser().resolve()
    test_root = Path(args.test_data_root).expanduser().resolve()
    program_root = Path(args.program_root).expanduser().resolve()
    suite_manifest_path = program_root / "suite_manifest.json"
    meta_manifest_path = program_root / "meta_manifest.json"
    test_manifest_path = test_root / "manifest.json"
    for required in (
        suite_manifest_path,
        meta_manifest_path,
        test_manifest_path,
    ):
        if not required.is_file():
            raise FileNotFoundError(f"Required formal manifest missing: {required}")
    suite_manifest = json.loads(
        suite_manifest_path.read_text(encoding="utf-8")
    )
    meta_manifest = json.loads(meta_manifest_path.read_text(encoding="utf-8"))
    test_manifest = json.loads(test_manifest_path.read_text(encoding="utf-8"))
    args.s2_retrieval_pool = str(test_manifest.get("semantic_scholar_cache") or "")
    formal_test_gate = run_formal_test_gate(
        asset_manifest_path=args.asset_manifest,
        temporal_split_manifest_path=args.temporal_split_manifest,
        test_manifest_path=str(test_manifest_path),
    )
    asset_gate = formal_test_gate["asset_manifest"]
    temporal_audit = formal_test_gate["four_zone_temporal_audit"]
    task_toolkit, _, judge_toolkit = _toolkits(args)
    specialist_results = {}
    specialist_answers = {}

    def final_identity(task_id, program, selection_path, lineage):
        config_identity = {
            "suite_manifest_sha256": _sha256(suite_manifest_path),
            "meta_manifest_sha256": _sha256(meta_manifest_path),
            "test_manifest_sha256": _sha256(test_manifest_path),
            "task_model_name": args.task_model_name,
            "judge_model_name": args.judge_model_name,
            "generation": {
                "max_tokens": args.max_tokens,
                "retry_max_tokens": args.retry_max_tokens,
                "reasoning_effort": args.reasoning_effort,
            },
        }
        return {
            "candidate_id": f"frozen-{task_id}",
            "lineage": str(lineage or "frozen-promoted"),
            "replicate_id": "held-out-D-once",
            "program_sha256": _sha256(program),
            "historical_manifest_sha256": _sha256(selection_path),
            "experiment_config_sha256": hashlib.sha256(
                json.dumps(
                    config_identity,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "s2_asset_sha256": task_toolkit.s2_retrieval_pool_sha256,
            "s2_asset_version": "frozen-s2-temporal-v2",
            "run_seed": None,
            "seed_control_available": False,
            "generation_config": config_identity["generation"],
        }

    def compact(evaluation):
        return {
            "mean_score": evaluation.mean_score,
            "runtime_stats": evaluation.runtime_stats,
            "error": evaluation.error,
            "cases": [
                {
                    "id": trace.get("id"),
                    "score": trace.get("score"),
                    "success": trace.get("success"),
                    "error": trace.get("error"),
                }
                for trace in evaluation.traces
                if trace.get("phase") == "read"
            ],
            "case_content_hidden": True,
        }

    for task_id in SPECIALIST_TASKS:
        promoted = (suite_manifest.get("promoted") or {}).get(task_id) or {}
        program = resolve_program_path(str(program_root), task_id)
        verify_promoted_program(
            str(program_root),
            task_id,
            program,
            args.embedding_model_name,
            args.embedding_revision,
        )
        quality = check_program_path(
            str(program),
            toolkit=task_toolkit,
            write_timeout=args.write_timeout,
            read_timeout=args.read_timeout,
        )
        if not quality.passed:
            raise RuntimeError(
                f"{task_id} failed quality gates: {quality.errors}"
            )
        episodes, selection_path = _selected_episodes(
            data_root, promoted["source_run"], task_id
        )
        eval_path = test_root / task_id / "test.jsonl"
        eval_items = [
            EvalItem.from_value(item) for item in _load_jsonl(str(eval_path))
        ]
        evaluation = evaluate_program(
            str(program),
            episodes,
            eval_items,
            build_task_agent(task_toolkit, task_id),
            task_toolkit,
            get_metric(
                "coco_review",
                task_toolkit,
                task_id=task_id,
                judge_toolkit=judge_toolkit,
                formal=True,
            ),
            benchmark_adapter_factory=CoCoMetricBenchmarkAdapter,
            formal=True,
            checkpoint_path=str(
                output.with_suffix(output.suffix + f".{task_id}.checkpoint.json")
            ),
            run_identity=final_identity(
                task_id,
                program,
                selection_path,
                promoted.get("lineage"),
            ),
            write_timeout=args.write_timeout,
            read_timeout=args.read_timeout,
        )
        if evaluation.error:
            raise RuntimeError(f"{task_id} test evaluation failed: {evaluation.error}")
        answers = {
            str(trace["id"]).rsplit("::", 1)[0]: trace.get("answer", "")
            for trace in evaluation.traces
            if trace.get("phase") == "read" and trace.get("success")
        }
        specialist_answers[task_id] = answers
        specialist_results[task_id] = {
            "program_sha256": _sha256(program),
            "test_file_sha256": _sha256(eval_path),
            "selection_sha256": _sha256(selection_path),
            "evaluation": compact(evaluation),
        }

    meta_references = _load_jsonl(
        str(test_root / META_TASK / "references.jsonl")
    )
    meta_items = []
    for reference in meta_references:
        paper_id = str((reference.get("metadata") or {}).get("paper_id") or "")
        if not all(paper_id in specialist_answers[task] for task in SPECIALIST_TASKS):
            continue
        question = (
            f"Review task: META\nPaper id: {paper_id}\n\n"
            + "\n\n".join(
                f"=== {task} ===\n{specialist_answers[task][paper_id]}"
                for task in SPECIALIST_TASKS
            )
            + "\n\nReturn the complete final meta-review."
        )
        meta_items.append(
            EvalItem(
                id=str(reference["id"]),
                question=question,
                answer=str(reference["answer"]),
                metadata=dict(reference.get("metadata") or {}),
            )
        )
    meta_promoted = meta_manifest.get("promoted") or {}
    meta_program = resolve_program_path(str(program_root), META_TASK)
    verify_promoted_program(
        str(program_root),
        META_TASK,
        meta_program,
        args.embedding_model_name,
        args.embedding_revision,
    )
    meta_episodes, meta_selection_path = _selected_episodes(
        data_root, meta_promoted["source_run"], META_TASK
    )
    meta_evaluation = evaluate_program(
        str(meta_program),
        meta_episodes,
        meta_items,
        build_task_agent(task_toolkit, META_TASK),
        task_toolkit,
        get_metric(
            "coco_review",
            task_toolkit,
            task_id=META_TASK,
            judge_toolkit=judge_toolkit,
            formal=True,
        ),
        benchmark_adapter_factory=CoCoMetricBenchmarkAdapter,
        formal=True,
        checkpoint_path=str(
            output.with_suffix(output.suffix + ".META.checkpoint.json")
        ),
        run_identity=final_identity(
            META_TASK,
            meta_program,
            meta_selection_path,
            meta_promoted.get("lineage"),
        ),
        write_timeout=args.write_timeout,
        read_timeout=args.read_timeout,
    )
    if meta_evaluation.error:
        raise RuntimeError(f"META test evaluation failed: {meta_evaluation.error}")
    report = {
        "provenance": experiment_provenance(),
        "final_test": True,
        "benchmark_adapter": "CoCoMetricBenchmarkAdapter",
        "benchmark_adapter_bypassed": False,
        "asset_gate": asset_gate,
        "formal_test_gate": formal_test_gate,
        "four_zone_temporal_audit": temporal_audit,
        "test_feedback_used_for_evolution": False,
        "test_manifest": str(test_manifest_path),
        "test_manifest_sha256": _sha256(test_manifest_path),
        "suite_manifest_sha256": _sha256(suite_manifest_path),
        "meta_manifest_sha256": _sha256(meta_manifest_path),
        "task_model_name": args.task_model_name,
        "judge_model_name": args.judge_model_name,
        "classifier_adapter": args.classifier_adapter,
        "classifier_adapter_sha256": _adapter_sha256(args.classifier_adapter),
        "review_split_revision": args.review_split_revision,
        "review_classify_revision": args.review_classify_revision,
        "embedding_adapter": args.embedding_adapter,
        "embedding_adapter_sha256": _adapter_sha256(args.embedding_adapter),
        "embedding_model_name": args.embedding_model_name,
        "embedding_revision": args.embedding_revision,
        "specialists": specialist_results,
        "meta": {
            "program_sha256": _sha256(meta_program),
            "selection_sha256": _sha256(meta_selection_path),
            "evaluated_papers": len(meta_items),
            "evaluation": compact(meta_evaluation),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "output": str(output),
        "specialist_mean_scores": {
            task: value["evaluation"]["mean_score"]
            for task, value in specialist_results.items()
        },
        "meta_mean_score": meta_evaluation.mean_score,
        "final_test": True,
    }


def main():
    args = build_parser().parse_args()
    if args.command == "prepare-specialists":
        result = prepare_openreview_data(
            args.openreview_root,
            args.output_dir,
            args.temporal_split_manifest,
            args.coco_root,
            validation_families=args.validation_families,
            meta_validation_families=args.meta_validation_families,
            max_train_families=args.max_train_families,
            max_paper_chars=args.max_paper_chars,
            seed=args.seed,
            s2_cache_path=args.s2_cache or None,
        )
    elif args.command == "prepare-test":
        result = prepare_held_out_test_data(
            args.coco_root,
            args.output_dir,
            args.s2_cache,
            args.temporal_split_manifest,
            max_paper_chars=args.max_paper_chars,
        )
    elif args.command == "prepare-meta":
        result = prepare_meta_data(
            args.openreview_root,
            args.specialist_outputs,
            args.output_dir,
        )
    elif args.command == "cache-meta-inputs":
        if (
            not _is_nonformal(args)
            and (not args.embedding_adapter or not args.embedding_revision)
        ):
            raise SystemExit(
                "Meta input caching requires the same frozen BGE-M3 "
                "--embedding-adapter and --embedding-revision as evolution."
            )
        result = build_meta_input_cache(
            args.data_root,
            args.program_root,
            args.output,
            toolkit_factory=lambda: _task_toolkit(args),
            folds=args.folds,
        )
    elif args.command == "evolve-task":
        result = _run_task(
            args,
            args.task_id,
            Path(args.output).expanduser().resolve(),
            args.seed,
        ).to_dict()
    elif args.command == "evolve-specialists":
        seeds = _parse_experiment_seeds(args.experiment_seeds)
        if args.primary_seed not in seeds:
            raise SystemExit("--primary-seed must be included in --experiment-seeds")
        results = {}
        promoted = {}
        promoted_by_lineage = {}
        for task_id in SPECIALIST_TASKS:
            results[task_id] = []
            for seed in seeds:
                output = Path(args.output).expanduser().resolve() / task_id / f"seed_{seed}"
                results[task_id].append(
                    _run_task(args, task_id, output, seed).to_dict()
                )
            winner = results[task_id][seeds.index(args.primary_seed)]
            promoted_path = (
                Path(args.output).expanduser().resolve()
                / task_id
                / "best_program.py"
            )
            promoted_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(winner["best_program_path"], promoted_path)
            promoted[task_id] = {
                "program": str(promoted_path),
                "program_sha256": _sha256(promoted_path),
                "source_run": winner["output_dir"],
                "selection_mean": winner["best_score"],
                "primary_seed": args.primary_seed,
            }
            source_champions_path = Path(winner["output_dir"]) / "lineage_champions.json"
            source_champions = json.loads(
                source_champions_path.read_text(encoding="utf-8")
            )
            promoted_by_lineage[task_id] = {}
            for lineage, entry in sorted(source_champions.items()):
                lineage_program = (
                    Path(args.output).expanduser().resolve()
                    / "lineages"
                    / lineage
                    / task_id
                    / "best_program.py"
                )
                lineage_program.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry["program"], lineage_program)
                promoted_by_lineage[task_id][lineage] = {
                    **entry,
                    "program": str(lineage_program),
                    "program_sha256": _sha256(lineage_program),
                    "source_run": winner["output_dir"],
                    "experiment_seed": args.primary_seed,
                }
        result = {
            "provenance": experiment_provenance(),
            "experiment_seeds": seeds,
            "primary_seed": args.primary_seed,
            "results": results,
            "promoted": promoted,
            "promoted_by_lineage": promoted_by_lineage,
            "meta_status": "run prepare-meta after freezing specialist winners",
        }
        manifest_path = Path(args.output).expanduser().resolve() / "suite_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif args.command == "evolve-meta":
        seeds = _parse_experiment_seeds(args.experiment_seeds)
        if args.primary_seed not in seeds:
            raise SystemExit("--primary-seed must be included in --experiment-seeds")
        runs = []
        for seed in seeds:
            output = Path(args.output).expanduser().resolve() / META_TASK / f"seed_{seed}"
            runs.append(_run_task(args, META_TASK, output, seed).to_dict())
        winner = runs[seeds.index(args.primary_seed)]
        promoted_path = (
            Path(args.output).expanduser().resolve()
            / META_TASK
            / "best_program.py"
        )
        promoted_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(winner["best_program_path"], promoted_path)
        result = {
            "provenance": experiment_provenance(),
            "experiment_seeds": seeds,
            "primary_seed": args.primary_seed,
            "results": runs,
            "promoted": {
                "program": str(promoted_path),
                "program_sha256": _sha256(promoted_path),
                "source_run": winner["output_dir"],
                "selection_mean": winner["best_score"],
                "primary_seed": args.primary_seed,
            },
        }
        manifest_path = Path(args.output).expanduser().resolve() / "meta_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif args.command == "final-evaluate-suite":
        result = _final_evaluate_suite(args)
    else:
        formal = not _is_nonformal(args)
        if args.final_test:
            raise SystemExit(
                "evaluate --final-test is forbidden. The only formal D/Test "
                "entry point is final-evaluate-suite, which enforces the formal "
                "gate and CoCo Benchmark Adapter."
            )
        if formal and (
            not args.embedding_adapter or not args.embedding_revision
        ):
            raise SystemExit(
                "Formal evaluation requires the frozen BGE-M3 "
                "--embedding-adapter and --embedding-revision."
            )
        if (
            formal
            and args.task_id != META_TASK
            and not args.classifier_adapter
        ):
            raise SystemExit(
                "Formal specialist evaluation requires --classifier-adapter."
            )
        if (
            formal
            and args.task_id != META_TASK
            and (
                not args.review_split_revision
                or not args.review_classify_revision
            )
        ):
            raise SystemExit(
                "Formal specialist evaluation requires immutable ReviewSplit "
                "and ReviewClassify revisions."
            )
        if formal and not args.selection:
            raise SystemExit(
                "Formal evaluation requires --selection from the promoted source run."
            )
        episodes = [Episode.from_value(item) for item in _load_jsonl(args.episodes)]
        selection_ids = None
        if args.selection:
            selection_path = Path(args.selection).expanduser().resolve()
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            selection_ids = set(selection.get("episode_ids") or [])
            episodes = [episode for episode in episodes if episode.id in selection_ids]
            if not episodes:
                raise SystemExit("Selection matched no training episodes")
        eval_items = [EvalItem.from_value(item) for item in _load_jsonl(args.eval)]
        task_toolkit, _, judge_toolkit = _toolkits(args)
        quality = check_program_path(
            args.program,
            toolkit=task_toolkit,
            write_timeout=args.write_timeout,
            read_timeout=args.read_timeout,
        )
        if not quality.passed:
            raise SystemExit("Program failed quality gates:\n" + "\n".join(quality.errors))
        metric = get_metric(
            "coco_review",
            task_toolkit,
            task_id=args.task_id,
            judge_toolkit=judge_toolkit,
            formal=formal,
        )
        evaluation = evaluate_program(
            args.program,
            episodes,
            eval_items,
            build_task_agent(task_toolkit, args.task_id),
            task_toolkit,
            metric,
            write_timeout=args.write_timeout,
            read_timeout=args.read_timeout,
        )
        output = Path(args.output).expanduser().resolve()
        if output.exists() and formal:
            raise SystemExit(
                f"Refusing to overwrite formal test report: {output}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "provenance": experiment_provenance(),
            "task_id": args.task_id,
            "final_test": False,
            "report_class": "development_evaluation",
            "test_feedback_used_for_evolution": False,
            "program": str(Path(args.program).expanduser().resolve()),
            "program_sha256": _sha256(Path(args.program).expanduser().resolve()),
            "episodes": str(Path(args.episodes).expanduser().resolve()),
            "selection": (
                str(Path(args.selection).expanduser().resolve())
                if args.selection
                else None
            ),
            "selected_episode_count": len(episodes),
            "evaluation_file": str(Path(args.eval).expanduser().resolve()),
            "evaluation_file_sha256": _sha256(
                Path(args.eval).expanduser().resolve()
            ),
            "task_model_name": args.task_model_name,
            "judge_model_name": args.judge_model_name,
            "classifier_adapter": args.classifier_adapter,
            "classifier_adapter_sha256": _adapter_sha256(
                args.classifier_adapter
            ),
            "review_split_revision": args.review_split_revision,
            "review_classify_revision": args.review_classify_revision,
            "embedding_adapter": args.embedding_adapter,
            "embedding_adapter_sha256": _adapter_sha256(
                args.embedding_adapter
            ),
            "embedding_model_name": args.embedding_model_name,
            "embedding_revision": args.embedding_revision,
            "evaluation": evaluation.to_dict(),
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = {
            "output": str(output),
            "mean_score": evaluation.mean_score,
            "final_test": False,
            "test_feedback_used_for_evolution": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
