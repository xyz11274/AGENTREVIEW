"""Single stage-oriented CLI for Research Core v2."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .bootstrap import (
    DEFAULT_FORMAL_RUN_ID,
    BootstrapInfrastructureFailure,
    BootstrapCorpusOptions,
    run_canonical_bootstrap,
)
from .coco import run_coco_evaluation
from .coco_final_audit import (
    audit_coco_final,
    seal_coco_pre_score,
    validate_coco_final_audit,
)
from .coco_protocol import prepare_coco_1300
from .coco_review_batch import (
    CocoReviewBatchOptions,
    ResearchV2ReviewGenerationAdapter,
    run_coco_review_batch,
)
from .coco_scoring_spec import materialize_coco_scoring_spec
from .config import ResearchConfig
from .constants import ROLES
from .data import prepare_data
from .evaluation import run_internal_evaluation
from .evaluation_batch import EvaluationBatchOptions, run_evaluation_batch
from .evolution import EvolutionPool, commit_candidate, initialize_pool, propose_candidate
from .formal_transition import (
    freeze_formal_system,
    materialize_formal_freeze_spec,
    validate_frozen_system_transition,
)
from .formal_evolution import (
    advance_role_attempt,
    materialize_role_attempt,
    preflight_formal_role_evolution,
    run_formal_evolution_roles,
    run_formal_role_evolution,
)
from .io import atomic_write_json, file_sha256, read_json, read_jsonl
from .llm import OpenAICompletionClient
from .memory import build_memory_snapshot, seed_program_path
from .meta import generate_final_review
from .review import run_specialist_review
from .s2 import SemanticScholarSearchClient, build_frozen_s2_pool

CLI_NO_S2_DIAGNOSTIC_INTERVENTION = {
    "protocol": "s2-intervention-v1",
    "mode": "forced_off",
    "label": "cli-no-s2-diagnostic-v1",
}
CLI_AUTONOMOUS_REVIEW_INTERVENTION = {
    "protocol": "s2-intervention-v1",
    "mode": "program_autonomous",
    "label": "cli-autonomous-review-v1",
}
EVOLUTION_AUTONOMOUS_REVIEW_LABEL = "evolution-development-v1"


def _base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agentreview.research_v2")
    parser.add_argument("--config", default=None)
    stages = parser.add_subparsers(dest="stage", required=True)

    prepare = stages.add_parser("prepare-data")
    prepare.add_argument("--tiny", action="store_true")
    prepare.add_argument("--build-s2", action="store_true")
    prepare.add_argument("--s2-output", default="")
    prepare.add_argument("--s2-api-key-env", default="S2_API_KEY")

    prepare_coco = stages.add_parser("prepare-coco-1300")
    prepare_coco.add_argument("--build-s2", action="store_true")
    prepare_coco.add_argument("--s2-output", default="")
    prepare_coco.add_argument("--s2-api-key-env", default="S2_API_KEY")

    build_s2 = stages.add_parser("build-s2")
    build_s2.add_argument(
        "--dataset",
        choices=("development", "coco"),
        required=True,
    )
    build_s2.add_argument("--output", default="")
    build_s2.add_argument("--s2-api-key-env", default="S2_API_KEY")
    build_s2.add_argument(
        "--min-request-interval-seconds",
        type=float,
        default=1.2,
    )
    build_s2.add_argument("--retry-until-success", action="store_true")

    build = stages.add_parser("build-memory")
    build.add_argument("--role", required=True)
    build.add_argument("--seed-kind", required=True)
    build.add_argument("--program", default="")
    build.add_argument("--program-version", required=True)
    build.add_argument("--reviews", nargs="*", default=[])
    build.add_argument("--evaluations", nargs="*", default=[])
    build.add_argument("--output", required=True)

    review = stages.add_parser("review")
    review.add_argument("--paper", required=True)
    review.add_argument("--role")
    review.add_argument("--program")
    review.add_argument("--snapshot")
    review.add_argument("--program-version")
    review.add_argument("--run-id", required=True)
    review.add_argument(
        "--no-s2",
        action="store_true",
        help="diagnostic intervention: deny Program S2 access and record it in identity",
    )
    review.add_argument("--final-mode", choices=("meta", "concat"))
    review.add_argument("--reviews", nargs="*", default=[])

    evaluate = stages.add_parser("internal-eval")
    evaluate.add_argument("--paper", required=True)
    evaluate.add_argument("--review", required=True)
    evaluate.add_argument("--run-id", required=True)

    evaluate_corpus = stages.add_parser("evaluate-corpus")
    evaluate_corpus.add_argument("--review-run-id", default=DEFAULT_FORMAL_RUN_ID)
    evaluate_corpus.add_argument("--run-id", required=True)
    evaluate_corpus.add_argument("--max-workers", type=int, default=8)
    evaluate_corpus.add_argument("--limit", type=int, default=0)

    evolve = stages.add_parser("evolve-step")
    evolve.add_argument("--action", choices=("init", "propose", "commit"), required=True)
    evolve.add_argument("--role", required=True)
    evolve.add_argument("--pool", required=True)
    evolve.add_argument("--run-id", required=True)
    evolve.add_argument("--attempt", type=int, default=0)
    evolve.add_argument("--reviews", nargs="*", default=[])
    evolve.add_argument("--evaluations", nargs="*", default=[])
    evolve.add_argument("--attempt-artifact")
    evolve.add_argument("--seed-evaluations", default="")
    evolve.add_argument("--seed-snapshots", default="")
    evolve.add_argument("--seed-reviews", default="")
    evolve.add_argument("--memory-snapshot", default="")

    run_evolution = stages.add_parser("run-evolution")
    run_evolution.add_argument("--spec", required=True)

    formal_evolution = stages.add_parser("formal-evolution")
    formal_evolution.add_argument(
        "--action",
        choices=(
            "materialize-attempt",
            "advance-attempt",
            "preflight-all",
            "preflight-role",
            "run-all-roles",
            "run-role",
        ),
        required=True,
    )
    formal_evolution.add_argument("--role", default="")
    formal_evolution.add_argument("--run-id", required=True)
    formal_evolution.add_argument("--attempt", type=int, default=0)
    formal_evolution.add_argument("--pool", default="")
    formal_evolution.add_argument("--canonical-evaluation-summary", default="")
    formal_evolution.add_argument("--canonical-split", default="")
    formal_evolution.add_argument("--static-split", default="")
    formal_evolution.add_argument("--rotating-split", default="")
    formal_evolution.add_argument("--work", default="")
    formal_evolution.add_argument("--rotating-reviews", nargs="*", default=[])
    formal_evolution.add_argument("--rotating-evaluations", nargs="*", default=[])
    formal_evolution.add_argument("--candidate-memory-snapshot", default="")
    formal_evolution.add_argument("--candidate-static-reviews", nargs="*", default=[])
    formal_evolution.add_argument(
        "--candidate-static-evaluations", nargs="*", default=[]
    )

    freeze = stages.add_parser("freeze-system")
    freeze.add_argument("--spec", required=True)
    freeze.add_argument("--output", required=True)

    materialize_freeze = stages.add_parser("materialize-freeze-spec")
    materialize_freeze.add_argument("--run-id", required=True)
    materialize_freeze.add_argument("--output", required=True)

    materialize_scoring = stages.add_parser("materialize-coco-scoring-spec")
    materialize_scoring.add_argument("--winner-run-id", required=True)
    materialize_scoring.add_argument("--evaluation-python", required=True)
    materialize_scoring.add_argument("--evaluation-proxy-url", required=True)
    materialize_scoring.add_argument("--output", required=True)

    coco_reviews = stages.add_parser("generate-coco-reviews")
    coco_reviews.add_argument("--frozen-system", required=True)
    coco_reviews.add_argument("--coco-manifest", default="")
    coco_reviews.add_argument("--run-id", required=True)
    coco_reviews.add_argument("--max-workers", type=int, default=8)

    seal_coco = stages.add_parser("seal-coco-pre-score")
    seal_coco.add_argument("--frozen-system", required=True)
    seal_coco.add_argument("--winner-run-id", required=True)
    seal_coco.add_argument("--output", required=True)

    coco = stages.add_parser("coco-eval")
    coco.add_argument("--pre-score-seal", required=True)
    coco.add_argument("--final-mode", choices=("meta", "concat"), required=True)
    coco.add_argument("command", nargs=argparse.REMAINDER)

    audit_coco = stages.add_parser("audit-coco-final")
    audit_coco.add_argument("--pre-score-seal", required=True)
    audit_coco.add_argument("--output", required=True)

    validate_coco = stages.add_parser("validate-coco-final")
    validate_coco.add_argument("--audit", required=True)
    validate_coco.add_argument("--live-service", action="store_true")

    bootstrap = stages.add_parser("bootstrap-corpus")
    bootstrap.add_argument("--run-id", default=DEFAULT_FORMAL_RUN_ID)
    bootstrap.add_argument("--limit", type=int, default=0)
    bootstrap.add_argument("--max-workers", type=int, default=8)
    bootstrap.add_argument("--no-s2", action="store_true")
    return parser


def _load_pool(path: str, role: str) -> EvolutionPool:
    source = Path(path).expanduser().resolve()
    return EvolutionPool.from_dict(read_json(source)) if source.exists() else EvolutionPool(role)


def _embedding_client(config: ResearchConfig):
    if config.embedding_provider != "qwen3_embedding":
        raise ValueError(f"Unsupported formal embedding provider: {config.embedding_provider}")
    from memory_evolution.embedding_adapter import create_qwen3_embedding

    return create_qwen3_embedding(
        model_name=config.embedding_model,
        revision=config.embedding_revision,
        cache_path=config.embedding_cache_path,
        device=config.embedding_device,
        cache_namespace="research_v2_runtime_v1",
    )


def _s2_path_for_paper(config: ResearchConfig, paper_path: str) -> str:
    paper_value = read_json(paper_path)
    dataset = str((paper_value.get("source") or {}).get("dataset") or "")
    return (
        config.frozen_coco_s2_path
        if dataset.startswith("CoCoReviewBench/")
        else config.frozen_s2_path
    )


def _diagnostic_s2_intervention(no_s2: bool) -> dict:
    return dict(
        CLI_NO_S2_DIAGNOSTIC_INTERVENTION
        if no_s2
        else CLI_AUTONOMOUS_REVIEW_INTERVENTION
    )


def _spec_s2_intervention(item: dict) -> dict | None:
    legacy = sorted({"s2_enabled", "review_strategy"}.intersection(item))
    if legacy:
        raise ValueError(
            "run-evolution Review steps use Program-autonomous S2 planning; "
            f"remove legacy host policy keys: {legacy}"
        )
    value = item.get("s2_intervention")
    if value is None:
        label = str(
            item.get("s2_autonomous_label") or EVOLUTION_AUTONOMOUS_REVIEW_LABEL
        ).strip()
        if not label:
            raise ValueError("s2_autonomous_label must be a non-empty string")
        return {
            "protocol": "s2-intervention-v1",
            "mode": "program_autonomous",
            "label": label,
        }
    if not isinstance(value, dict):
        raise ValueError("s2_intervention must be an explicit diagnostic object")
    required = {"protocol", "mode", "label"}
    missing = sorted(required - set(value))
    unexpected = sorted(set(value) - required)
    if missing or unexpected:
        raise ValueError(
            "s2_intervention identity fields differ from the diagnostic protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if value.get("protocol") != "s2-intervention-v1":
        raise ValueError("Unsupported s2_intervention protocol")
    if value.get("mode") not in {"forced_off", "forced_consult"}:
        raise ValueError(
            "run-evolution accepts only forced_off/forced_consult diagnostics; "
            "omit s2_intervention for the Program-autonomous Primary"
        )
    if not str(value.get("label") or "").strip():
        raise ValueError("s2_intervention requires a non-empty diagnostic label")
    return dict(value)


def _run_evolution_spec(config: ResearchConfig, spec_path: str) -> dict:
    """Thin coordinator over explicit saved artifacts; it does not hide stage state."""
    spec = read_json(spec_path)
    results = []
    client = OpenAICompletionClient(config.model)
    for item in spec.get("steps") or []:
        action = str(item.get("action") or "")
        role = str(item.get("role") or "")
        if action == "build-memory":
            seed_kind = str(item["seed_kind"])
            completion_client = (
                None
                if seed_kind in {"no_memory", "vector_search", "llm_summarizer"}
                else client
            )
            outcome = build_memory_snapshot(
                role=role,
                program_path=item["program"],
                program_version=str(item["program_version"]),
                review_paths=list(item.get("reviews") or []),
                evaluation_paths=list(item.get("evaluations") or []),
                output_path=item["output"],
                seed_kind=seed_kind,
                embedding_client=_embedding_client(config),
                embedding_identity=config.embedding_identity(),
                completion_client=completion_client,
                completion_identity=(
                    config.model.identity() if completion_client is not None else None
                ),
                require_bubblewrap=config.require_bubblewrap,
            )
        elif action == "review":
            s2_intervention = _spec_s2_intervention(item)
            outcome = run_specialist_review(
                paper_path=item["paper"],
                role=role,
                program_path=item["program"],
                snapshot_path=item["snapshot"],
                run_id=str(item["run_id"]),
                output_root=config.artifact_path(),
                completion_client=client,
                model_identity=config.model.identity(),
                program_version=str(item["program_version"]),
                frozen_s2_path=_s2_path_for_paper(config, item["paper"]),
                s2_intervention=s2_intervention,
                embedding_client=_embedding_client(config),
                embedding_identity=config.embedding_identity(),
                require_bubblewrap=config.require_bubblewrap,
            )
        elif action == "internal-eval":
            outcome = run_internal_evaluation(
                paper_path=item["paper"],
                review_path=item["review"],
                run_id=str(item["run_id"]),
                output_root=config.artifact_path(),
                completion_client=client,
                evaluator_identity=config.model.identity(),
            )
        elif action == "init":
            outcome = initialize_pool(
                role=role,
                seed_static_evaluations=dict(item["seed_evaluations"]),
                seed_memory_snapshots=dict(item["seed_snapshots"]),
                seed_static_reviews=dict(item["seed_reviews"]),
                pool_path=item["pool"],
            ).to_dict()
        elif action == "propose":
            pool_path = str(item["pool"])
            pool = _load_pool(pool_path, role)
            outcome = propose_candidate(
                role=role,
                pool=pool,
                evaluation_paths=list(item["rotating_evaluations"]),
                review_paths=list(item["rotating_reviews"]),
                attempt=int(item["attempt"]),
                run_id=str(spec["run_id"]),
                output_root=config.artifact_path(),
                completion_client=client,
                completion_identity=config.model.identity(),
                require_bubblewrap=config.require_bubblewrap,
            )
        elif action == "commit":
            pool_path = str(item["pool"])
            pool = _load_pool(pool_path, role)
            outcome = asdict(
                commit_candidate(
                    pool=pool,
                    attempt_path=item["attempt_artifact"],
                    static_review_paths=list(item["static_reviews"]),
                    static_evaluation_paths=list(item["static_evaluations"]),
                    pool_path=pool_path,
                    memory_snapshot_path=item["memory_snapshot"],
                )
            )
        else:
            raise ValueError(f"Unknown run-evolution action: {action}")
        results.append(outcome)
    summary = {"run_id": spec["run_id"], "outcomes": results}
    atomic_write_json(config.artifact_path() / "runs" / spec["run_id"] / "run.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _base_parser().parse_args(argv)
    config = ResearchConfig.load(args.config)
    output_root = config.artifact_path()
    if args.stage == "prepare-data":
        sizes = {
            "canonical_review_corpus": 2,
            "evolution_static": 2,
            "evolution_rotating_pool": 2,
        } if args.tiny else None
        result = prepare_data(
            train_clean_path=config.train_clean_path,
            paper_asset_root=config.paper_asset_root,
            coco_root=config.coco_root,
            output_root=output_root,
            split_sizes=sizes,
            seed=config.split_seed,
        )
        if args.build_s2:
            s2_output = args.s2_output or str(
                output_root / "s2" / "frozen_s2_external_development_v1.jsonl"
            )
            result["frozen_s2"] = build_frozen_s2_pool(
                targets=read_jsonl(output_root / "data" / "s2_title_queries.jsonl"),
                output_path=s2_output,
                client=SemanticScholarSearchClient(args.s2_api_key_env),
                pool_version="frozen_s2_external_development_v1",
            )
    elif args.stage == "prepare-coco-1300":
        result = prepare_coco_1300(
            coco_root=config.coco_root,
            output_root=output_root,
            seed=config.split_seed,
        )
        if args.build_s2:
            s2_output = args.s2_output or str(
                output_root / "s2" / "frozen_s2_coco1300_v1.jsonl"
            )
            result["frozen_s2"] = build_frozen_s2_pool(
                targets=read_jsonl(
                    output_root / "data" / "coco_1300" / "s2_title_queries.jsonl"
                ),
                output_path=s2_output,
                client=SemanticScholarSearchClient(args.s2_api_key_env),
                pool_version="frozen_s2_coco1300_v1",
            )
    elif args.stage == "build-s2":
        if args.dataset == "development":
            query_path = output_root / "data" / "s2_title_queries.jsonl"
            default_output = (
                output_root / "s2" / "frozen_s2_external_development_v1.jsonl"
            )
            pool_version = "frozen_s2_external_development_v1"
        else:
            query_path = output_root / "data" / "coco_1300" / "s2_title_queries.jsonl"
            default_output = output_root / "s2" / "frozen_s2_coco1300_v1.jsonl"
            pool_version = "frozen_s2_coco1300_v1"
        result = build_frozen_s2_pool(
            targets=read_jsonl(query_path),
            output_path=args.output or default_output,
            client=SemanticScholarSearchClient(
                args.s2_api_key_env,
                max_attempts=None if args.retry_until_success else 6,
                min_request_interval_seconds=args.min_request_interval_seconds,
            ),
            pool_version=pool_version,
        )
    elif args.stage == "build-memory":
        deterministic_builds = {"no_memory", "vector_search", "llm_summarizer"}
        client = (
            None
            if args.seed_kind in deterministic_builds
            else OpenAICompletionClient(config.model)
        )
        program = args.program or str(seed_program_path(args.seed_kind))
        result = build_memory_snapshot(
            role=args.role,
            program_path=program,
            program_version=args.program_version,
            review_paths=args.reviews,
            evaluation_paths=args.evaluations,
            output_path=args.output,
            seed_kind=args.seed_kind,
            embedding_client=_embedding_client(config),
            embedding_identity=config.embedding_identity(),
            completion_client=client,
            completion_identity=(config.model.identity() if client is not None else None),
            require_bubblewrap=config.require_bubblewrap,
        )
    elif args.stage == "review":
        client = OpenAICompletionClient(config.model)
        if args.final_mode:
            if args.no_s2:
                raise ValueError("--no-s2 is only a Specialist Review diagnostic")
            result = generate_final_review(
                paper_path=args.paper,
                review_paths=args.reviews,
                mode=args.final_mode,
                run_id=args.run_id,
                output_root=output_root,
                completion_client=client if args.final_mode == "meta" else None,
                model_identity=config.model.identity(),
            )
        else:
            for name in ("role", "program", "snapshot", "program_version"):
                if not getattr(args, name):
                    raise ValueError(f"Specialist review requires --{name.replace('_', '-')}")
            selected_s2_path = _s2_path_for_paper(config, args.paper)
            result = run_specialist_review(
                paper_path=args.paper,
                role=args.role,
                program_path=args.program,
                snapshot_path=args.snapshot,
                run_id=args.run_id,
                output_root=output_root,
                completion_client=client,
                model_identity=config.model.identity(),
                program_version=args.program_version,
                frozen_s2_path=selected_s2_path or None,
                s2_intervention=_diagnostic_s2_intervention(args.no_s2),
                embedding_client=_embedding_client(config),
                embedding_identity=config.embedding_identity(),
                require_bubblewrap=config.require_bubblewrap,
            )
    elif args.stage == "internal-eval":
        result = run_internal_evaluation(
            paper_path=args.paper,
            review_path=args.review,
            run_id=args.run_id,
            output_root=output_root,
            completion_client=OpenAICompletionClient(config.model),
            evaluator_identity=config.model.identity(),
        )
    elif args.stage == "evaluate-corpus":
        result = run_evaluation_batch(
            output_root=output_root,
            options=EvaluationBatchOptions(
                review_run_id=args.review_run_id,
                run_id=args.run_id,
                max_workers=args.max_workers,
                limit=args.limit,
            ),
            completion_client=OpenAICompletionClient(config.model),
            evaluator_identity=config.model.identity(),
            progress=lambda event: print(
                json.dumps(event, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
                flush=True,
            ),
        )
    elif args.stage == "evolve-step":
        pool = _load_pool(args.pool, args.role)
        if args.action == "init":
            if (
                not args.seed_evaluations
                or not args.seed_snapshots
                or not args.seed_reviews
            ):
                raise ValueError(
                    "init requires --seed-evaluations, --seed-snapshots, "
                    "and --seed-reviews JSON"
                )
            result = initialize_pool(
                role=args.role,
                seed_static_evaluations=read_json(args.seed_evaluations),
                seed_memory_snapshots=read_json(args.seed_snapshots),
                seed_static_reviews=read_json(args.seed_reviews),
                pool_path=args.pool,
            ).to_dict()
        elif args.action == "propose":
            if args.attempt <= 0:
                raise ValueError("propose requires --attempt > 0")
            result = propose_candidate(
                role=args.role,
                pool=pool,
                evaluation_paths=args.evaluations,
                review_paths=args.reviews,
                attempt=args.attempt,
                run_id=args.run_id,
                output_root=output_root,
                completion_client=OpenAICompletionClient(config.model),
                completion_identity=config.model.identity(),
                require_bubblewrap=config.require_bubblewrap,
            )
        else:
            if not args.attempt_artifact or not args.memory_snapshot:
                raise ValueError(
                    "commit requires --attempt-artifact and --memory-snapshot"
                )
            result = asdict(commit_candidate(
                pool=pool,
                attempt_path=args.attempt_artifact,
                static_review_paths=args.reviews,
                static_evaluation_paths=args.evaluations,
                pool_path=args.pool,
                memory_snapshot_path=args.memory_snapshot,
            ))
    elif args.stage == "run-evolution":
        result = _run_evolution_spec(config, args.spec)
    elif args.stage == "formal-evolution":
        if args.action == "materialize-attempt":
            required = {
                "role": args.role,
                "pool": args.pool,
                "canonical-evaluation-summary": args.canonical_evaluation_summary,
                "static-split": args.static_split,
                "rotating-split": args.rotating_split,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "materialize-attempt requires " + ", ".join(missing)
                )
            result = materialize_role_attempt(
                role=args.role,
                run_id=args.run_id,
                attempt=args.attempt,
                pool_path=args.pool,
                canonical_evaluation_summary_path=args.canonical_evaluation_summary,
                static_split_path=args.static_split,
                rotating_split_path=args.rotating_split,
                output_root=output_root,
            )
        elif args.action == "advance-attempt":
            required = {"pool": args.pool, "work": args.work}
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("advance-attempt requires " + ", ".join(missing))
            result = advance_role_attempt(
                work_path=args.work,
                pool_path=args.pool,
                output_root=output_root,
                completion_client=OpenAICompletionClient(config.model),
                completion_identity=config.model.identity(),
                rotating_review_paths=args.rotating_reviews,
                rotating_evaluation_paths=args.rotating_evaluations,
                candidate_memory_snapshot_path=(
                    args.candidate_memory_snapshot or None
                ),
                candidate_static_review_paths=(
                    args.candidate_static_reviews or None
                ),
                candidate_static_evaluation_paths=(
                    args.candidate_static_evaluations or None
                ),
                require_bubblewrap=config.require_bubblewrap,
            )
        else:
            required = {
                "canonical-evaluation-summary": args.canonical_evaluation_summary,
                "canonical-split": args.canonical_split,
                "static-split": args.static_split,
                "rotating-split": args.rotating_split,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"{args.action} requires " + ", ".join(missing))
            common_inputs = {
                "run_id": args.run_id,
                "canonical_evaluation_summary_path": args.canonical_evaluation_summary,
                "canonical_split_path": args.canonical_split,
                "static_split_path": args.static_split,
                "rotating_split_path": args.rotating_split,
                "frozen_s2_path": config.frozen_s2_path,
                "output_root": output_root,
                "model_identity": config.model.identity(),
                "embedding_identity": config.embedding_identity(),
                "require_bubblewrap": config.require_bubblewrap,
            }
            if args.action in {"preflight-all", "run-all-roles"}:
                if args.role or args.pool:
                    raise ValueError(
                        f"{args.action} derives all role-local pool paths; "
                        "omit --role and --pool"
                    )
                if args.action == "preflight-all":
                    result = {
                        role: preflight_formal_role_evolution(
                            **common_inputs,
                            role=role,
                            pool_path=(
                                output_root
                                / "evolution"
                                / role
                                / args.run_id
                                / "pool.json"
                            ),
                        )
                        for role in ROLES
                    }
                else:
                    result = run_formal_evolution_roles(
                        **common_inputs,
                        completion_client=OpenAICompletionClient(config.model),
                        embedding_client=_embedding_client(config),
                        max_workers=config.model.max_num_seqs,
                        progress=lambda event: print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            file=sys.stderr,
                            flush=True,
                        ),
                    )
            else:
                if not args.role or not args.pool:
                    raise ValueError(
                        f"{args.action} requires role and pool"
                    )
                common = {
                    **common_inputs,
                    "role": args.role,
                    "pool_path": args.pool,
                }
                if args.action == "preflight-role":
                    result = preflight_formal_role_evolution(**common)
                else:
                    result = run_formal_role_evolution(
                        **common,
                        completion_client=OpenAICompletionClient(config.model),
                        embedding_client=_embedding_client(config),
                        max_workers=config.model.max_num_seqs,
                        progress=lambda event: print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            file=sys.stderr,
                            flush=True,
                        ),
                    )
    elif args.stage == "materialize-freeze-spec":
        result = materialize_formal_freeze_spec(
            config=config,
            formal_run_id=args.run_id,
            repository_root=Path(__file__).resolve().parents[3],
            output_path=args.output,
        )
    elif args.stage == "materialize-coco-scoring-spec":
        result = materialize_coco_scoring_spec(
            coco_root=config.coco_root,
            artifact_root=output_root,
            winner_run_id=args.winner_run_id,
            output_path=args.output,
            evaluation_python=args.evaluation_python,
            evaluation_proxy_url=args.evaluation_proxy_url,
        )
    elif args.stage == "freeze-system":
        result = freeze_formal_system(
            spec_path=args.spec,
            output_path=args.output,
            repository_root=Path(__file__).resolve().parents[3],
        )
    elif args.stage == "generate-coco-reviews":
        options = CocoReviewBatchOptions(
            run_id=args.run_id,
            max_workers=args.max_workers,
        )
        options.validate()
        validated_system = validate_frozen_system_transition(
            frozen_system_path=args.frozen_system,
            repository_root=Path(__file__).resolve().parents[3],
        )
        formal_binding = validated_system["reproducibility"]["formal_freeze_spec"]
        expected_frozen_identity = {
            "system_id": validated_system["system_id"],
            "system_sha256": file_sha256(args.frozen_system),
            "spec_id": formal_binding["spec_id"],
        }
        completion_client = OpenAICompletionClient(config.model)
        adapter = ResearchV2ReviewGenerationAdapter(
            completion_client=completion_client,
            embedding_client=_embedding_client(config),
            embedding_identity=config.embedding_identity(),
            require_bubblewrap=config.require_bubblewrap,
        )
        result = run_coco_review_batch(
            frozen_system_path=args.frozen_system,
            coco_manifest_path=(
                args.coco_manifest
                or str(output_root / "data" / "coco_1300" / "manifest.json")
            ),
            output_root=output_root,
            options=options,
            generation_adapter=adapter,
            expected_frozen_identity=expected_frozen_identity,
            progress=lambda event: print(
                json.dumps(event, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
                flush=True,
            ),
        )
    elif args.stage == "seal-coco-pre-score":
        result = seal_coco_pre_score(
            frozen_system_path=args.frozen_system,
            artifact_root=output_root,
            winner_run_id=args.winner_run_id,
            repository_root=Path(__file__).resolve().parents[3],
            output_path=args.output,
        )
    elif args.stage == "coco-eval":
        result = run_coco_evaluation(
            pre_score_seal_path=args.pre_score_seal,
            final_mode=args.final_mode,
            command=args.command,
        )
    elif args.stage == "audit-coco-final":
        result = audit_coco_final(
            pre_score_seal_path=args.pre_score_seal,
            repository_root=Path(__file__).resolve().parents[3],
            output_path=args.output,
        )
    elif args.stage == "validate-coco-final":
        result = validate_coco_final_audit(
            args.audit,
            require_live_service=args.live_service,
        )
    elif args.stage == "bootstrap-corpus":
        try:
            result = run_canonical_bootstrap(
                config=config,
                options=BootstrapCorpusOptions(
                    run_id=args.run_id,
                    limit=args.limit,
                    max_workers=args.max_workers,
                    s2_enabled=not args.no_s2,
                ),
                completion_client=OpenAICompletionClient(config.model),
                progress=lambda event: print(
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                    flush=True,
                ),
            )
        except BootstrapInfrastructureFailure as exc:
            print(
                json.dumps(
                    {
                        "stage": "bootstrap-corpus",
                        "status": "infrastructure_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            return 2
    else:
        raise AssertionError(args.stage)
    printable = result
    if args.stage == "prepare-coco-1300":
        printable = {
            "protocol": result["protocol"],
            "count": result["count"],
            "manifest_sha256": result["manifest_sha256"],
            "groups": {
                name: {
                    "available_bucket_counts": value["available_bucket_counts"],
                    "quota_by_bucket": value["quota_by_bucket"],
                }
                for name, value in result["groups"].items()
            },
            **({"frozen_s2": result["frozen_s2"]} if "frozen_s2" in result else {}),
        }
    print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
