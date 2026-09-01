"""Single stage-oriented CLI for Research Core v2."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .coco import run_coco_evaluation
from .coco_protocol import prepare_coco_1300
from .config import ResearchConfig
from .data import prepare_data
from .evaluation import run_internal_evaluation
from .evolution import EvolutionPool, commit_candidate, initialize_pool, propose_candidate
from .freeze import freeze_system
from .io import atomic_write_json, read_json, read_jsonl
from .llm import OpenAICompletionClient
from .memory import build_memory_snapshot, seed_program_path
from .meta import generate_final_review
from .review import run_specialist_review
from .s2 import SemanticScholarSearchClient, build_frozen_s2_pool


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
    review.add_argument("--no-s2", action="store_true")
    review.add_argument("--final-mode", choices=("meta", "concat"))
    review.add_argument("--reviews", nargs="*", default=[])

    evaluate = stages.add_parser("internal-eval")
    evaluate.add_argument("--paper", required=True)
    evaluate.add_argument("--review", required=True)
    evaluate.add_argument("--run-id", required=True)

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

    run_evolution = stages.add_parser("run-evolution")
    run_evolution.add_argument("--spec", required=True)

    freeze = stages.add_parser("freeze-system")
    freeze.add_argument("--spec", required=True)
    freeze.add_argument("--output", required=True)

    coco = stages.add_parser("coco-eval")
    coco.add_argument("--final-reviews", nargs="+", required=True)
    coco.add_argument("--frozen-system", required=True)
    coco.add_argument("--output", required=True)
    coco.add_argument("command", nargs=argparse.REMAINDER)

    bootstrap = stages.add_parser("bootstrap-corpus")
    bootstrap.add_argument("--run-id", default="canonical-bootstrap-v1")
    bootstrap.add_argument("--limit", type=int, default=0)
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
                completion_client=completion_client,
                require_bubblewrap=config.require_bubblewrap,
            )
        elif action == "review":
            s2_enabled = bool(item.get("s2_enabled", True))
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
                review_strategy=str(item.get("review_strategy") or ""),
                s2_enabled=s2_enabled,
                frozen_s2_path=(
                    _s2_path_for_paper(config, item["paper"])
                    if s2_enabled
                    else None
                ),
                embedding_client=_embedding_client(config),
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
                require_bubblewrap=config.require_bubblewrap,
            )
        elif action == "commit":
            pool_path = str(item["pool"])
            pool = _load_pool(pool_path, role)
            outcome = asdict(
                commit_candidate(
                    pool=pool,
                    attempt_path=item["attempt_artifact"],
                    static_evaluation_paths=list(item["static_evaluations"]),
                    pool_path=pool_path,
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
            completion_client=client,
            require_bubblewrap=config.require_bubblewrap,
        )
    elif args.stage == "review":
        client = OpenAICompletionClient(config.model)
        if args.final_mode:
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
                s2_enabled=not args.no_s2,
                frozen_s2_path=selected_s2_path or None,
                embedding_client=_embedding_client(config),
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
    elif args.stage == "evolve-step":
        pool = _load_pool(args.pool, args.role)
        if args.action == "init":
            if not args.seed_evaluations:
                raise ValueError("init requires --seed-evaluations JSON")
            result = initialize_pool(
                role=args.role,
                seed_static_evaluations=read_json(args.seed_evaluations),
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
                require_bubblewrap=config.require_bubblewrap,
            )
        else:
            if not args.attempt_artifact:
                raise ValueError("commit requires --attempt-artifact")
            result = asdict(commit_candidate(
                pool=pool,
                attempt_path=args.attempt_artifact,
                static_evaluation_paths=args.evaluations,
                pool_path=args.pool,
            ))
    elif args.stage == "run-evolution":
        result = _run_evolution_spec(config, args.spec)
    elif args.stage == "freeze-system":
        result = freeze_system(output_path=args.output, **read_json(args.spec))
    elif args.stage == "coco-eval":
        result = run_coco_evaluation(
            final_review_paths=args.final_reviews,
            frozen_system_path=args.frozen_system,
            coco_root=config.coco_root,
            output_root=args.output,
            command=args.command,
        )
    elif args.stage == "bootstrap-corpus":
        split = read_json(
            output_root / "data" / "splits" / "canonical_review_corpus.json"
        )
        records = list(split.get("records") or [])
        if args.limit > 0:
            records = records[: args.limit]
        client = OpenAICompletionClient(config.model)
        no_memory_program = str(seed_program_path("no_memory"))
        snapshots = {}
        for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
            snapshot_path = (
                output_root
                / "memories"
                / role
                / "canonical-no-memory-v1"
                / "snapshot.json"
            )
            build_memory_snapshot(
                role=role,
                program_path=no_memory_program,
                program_version="canonical-no-memory-v1",
                review_paths=[],
                evaluation_paths=[],
                output_path=snapshot_path,
                seed_kind="no_memory",
                require_bubblewrap=config.require_bubblewrap,
            )
            snapshots[role] = str(snapshot_path)
        completed, failed = [], []
        for record in records:
            for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
                try:
                    review = run_specialist_review(
                        paper_path=record["path"],
                        role=role,
                        program_path=no_memory_program,
                        snapshot_path=snapshots[role],
                        run_id=args.run_id,
                        output_root=output_root,
                        completion_client=client,
                        model_identity=config.model.identity(),
                        program_version="canonical-no-memory-v1",
                        s2_enabled=not args.no_s2,
                        frozen_s2_path=config.frozen_s2_path or None,
                        embedding_client=_embedding_client(config),
                        require_bubblewrap=config.require_bubblewrap,
                    )
                    completed.append(review["review_id"])
                except Exception as exc:
                    failed.append(
                        {
                            "paper_id": record["paper_id"],
                            "role": role,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        result = {
            "run_id": args.run_id,
            "completed_review_ids": completed,
            "failures": failed,
            "snapshots": snapshots,
        }
        atomic_write_json(
            output_root / "runs" / args.run_id / "run.json",
            result,
        )
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
