import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import cli as cli_module
from agentreview.research_v2 import formal_transition as transition_module
from agentreview.research_v2.coco_review_batch import CocoReviewBatchOptions
from agentreview.research_v2.config import ResearchConfig
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.evolution import (
    MAX_MUTATION_ATTEMPTS,
    EvolutionPool,
    PoolEntry,
    feedback_packet,
)
from agentreview.research_v2.formal_evolution import (
    FORMAL_ATTEMPT_WORK_PROTOCOL,
    FORMAL_ALL_ROLES_PROTOCOL,
    FORMAL_AUTONOMOUS_S2_INTERVENTION,
    FORMAL_ROLE_EVOLUTION_PROTOCOL,
    FORMAL_ROTATING_BATCH_SIZE,
    FORMAL_RUN_SPEC_PROTOCOL,
    FORMAL_STATIC_SIZE,
)
from agentreview.research_v2.formal_transition import (
    FREEZE_ARGUMENT_FIELDS,
    build_formal_freeze_arguments,
    freeze_formal_system,
    load_freeze_arguments,
    materialize_formal_freeze_spec,
    validate_frozen_system_transition,
)
from agentreview.research_v2.io import (
    atomic_write_json,
    file_sha256,
    read_json,
    value_sha256,
)
from agentreview.research_v2.memory import SEED_NAMES, seed_program_path
from agentreview.research_v2.program_checks import program_capability_manifest
from agentreview.research_v2.s2 import (
    S2_RECORD_BUILDER_SHA256,
    _expected_record_identity,
)


class FormalTransitionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _write_jsonl(path: Path, values: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
                for value in values
            ),
            encoding="utf-8",
        )

    def _s2_fixture(
        self,
        *,
        path: Path,
        targets: list[dict],
        pool_version: str,
    ) -> None:
        records = []
        for target in targets:
            record = _expected_record_identity(
                target=target,
                paper_id=target["paper_id"],
                title=target["canonical_query_title"],
                pool_version=pool_version,
                raw_limit=50,
                top_k=5,
                builder_sha256=S2_RECORD_BUILDER_SHA256,
            )
            record.update(
                {
                    "retrieved_papers": [],
                    "result_sha256": value_sha256([]),
                    "rejected": [],
                }
            )
            record["record_sha256"] = value_sha256(record)
            records.append(record)
        self._write_jsonl(path, records)

    def _review_evaluation_fixture(
        self,
        *,
        root: Path,
        role: str,
        paper_id: str,
        review_run_id: str,
        evaluation_run_id: str,
        program_version: str,
        memory_snapshot_id: str,
        review_quality: float,
    ) -> tuple[Path, Path]:
        review_identity = {
            "run_id": review_run_id,
            "paper_id": paper_id,
            "role": role,
            "program_version": program_version,
            "memory_snapshot_id": memory_snapshot_id,
        }
        review_id = value_sha256(review_identity)
        review_path = (
            root / "reviews" / review_run_id / paper_id / f"{role}.json"
        )
        atomic_write_json(
            review_path,
            {
                **review_identity,
                "review_id": review_id,
                "review_text": f"Complete {role} review for {paper_id}",
                "retrieval_provenance": {
                    "memory": {
                        "seed_kind": "vector_search",
                        "query": paper_id,
                        "logs": [],
                    },
                    "s2": {
                        "capability_available": True,
                        "intervention": "evolved_program_autonomous",
                        "lookup_count": 0,
                    },
                    "assembly": {},
                },
            },
        )
        evaluation_identity = {
            "run_id": evaluation_run_id,
            "review_id": review_id,
            "paper_id": paper_id,
            "role": role,
            "review_quality": review_quality,
        }
        evaluation_path = (
            root
            / "evaluations"
            / evaluation_run_id
            / paper_id
            / f"{role}.json"
        )
        atomic_write_json(
            evaluation_path,
            {
                **evaluation_identity,
                "evaluation_id": value_sha256(evaluation_identity),
                "major_missing_points": [],
                "factual_evidence_issues": [],
                "improvement_suggestions": [],
            },
        )
        return review_path.resolve(), evaluation_path.resolve()

    def _formal_fixture(self, root: Path) -> tuple[ResearchConfig, dict[str, Path]]:
        root.mkdir(parents=True, exist_ok=True)
        development_s2 = root / "s2" / "development.jsonl"
        coco_s2 = root / "s2" / "coco-1300.jsonl"
        development_targets = [
            {
                "paper_id": f"development-{index:04d}",
                "canonical_query_title": f"Development title {index:04d}",
            }
            for index in range(275)
        ]
        coco_targets = [
            {
                "paper_id": f"coco-{index:04d}",
                "canonical_query_title": f"CoCo title {index:04d}",
            }
            for index in range(1300)
        ]
        self._s2_fixture(
            path=development_s2,
            targets=development_targets,
            pool_version="frozen_s2_external_development_v1",
        )
        self._s2_fixture(
            path=coco_s2,
            targets=coco_targets,
            pool_version="frozen_s2_coco1300_v1",
        )
        config = ResearchConfig(
            artifact_root=str(root),
            frozen_s2_path=str(development_s2),
            frozen_coco_s2_path=str(coco_s2),
            embedding_cache_path=str(root / "cache" / "embedding.sqlite3"),
            require_bubblewrap=True,
        )
        development_manifest = root / "data" / "manifest.json"
        coco_manifest = root / "data" / "coco_1300" / "manifest.json"
        development_manifest_value = {
            "protocol": "research-v2-development-split-v1",
            "seed": 0,
            "splits": {
                "canonical_review_corpus": {
                    "count": 125,
                    "paper_ids": [
                        value["paper_id"] for value in development_targets[:125]
                    ],
                },
                "evolution_static": {
                    "count": 25,
                    "paper_ids": [
                        value["paper_id"] for value in development_targets[125:150]
                    ],
                },
                "evolution_rotating_pool": {
                    "count": 125,
                    "paper_ids": [
                        value["paper_id"] for value in development_targets[150:]
                    ],
                },
            },
        }
        development_manifest_value["manifest_sha256"] = value_sha256(
            development_manifest_value
        )
        atomic_write_json(development_manifest, development_manifest_value)
        coco_manifest_value = {
            "protocol": "coco_1300_protocol_compatible_resample_v1",
            "count": 1300,
            "records": [
                {"paper_id": value["paper_id"]} for value in coco_targets
            ],
        }
        coco_manifest_value["manifest_sha256"] = value_sha256(coco_manifest_value)
        atomic_write_json(coco_manifest, coco_manifest_value)
        development_query_path = root / "data" / "s2_title_queries.jsonl"
        coco_query_path = root / "data" / "coco_1300" / "s2_title_queries.jsonl"
        self._write_jsonl(development_query_path, development_targets)
        self._write_jsonl(coco_query_path, coco_targets)

        run_id = "formal-evolution-v1"
        resume_audit = root / "runtime" / "formal_evolution_v1" / "resume_test.json"
        atomic_write_json(
            resume_audit,
            {
                "protocol": "research-v2-formal-resume-audit-v1",
                "recorded_at": "2026-09-01T23:06:25+08:00",
                "run_id": run_id,
                "source_checkpoints": {
                    "original_process_commit": "1" * 40,
                    "pre_fix_checkpoint": "2" * 40,
                    "context_fix_commit": "3" * 40,
                    "probe_commit": "4" * 40,
                },
            },
        )
        canonical_evaluation = root / "runs" / "canonical" / "summary.json"
        atomic_write_json(
            canonical_evaluation,
            {
                "schema_version": "research-v2-internal-evaluation-batch-v1",
                "status": "complete",
                "task_count": 625,
                "failed": 0,
                "completed": 625,
                "resumed": 0,
                "run_id": "canonical-evaluation",
                "review_run_id": "canonical-reviews",
            },
        )
        paper_records = []
        for target in development_targets:
            paper_path = root / "data" / "papers" / f"{target['paper_id']}.json"
            atomic_write_json(
                paper_path,
                {
                    "paper_id": target["paper_id"],
                    "title": target["canonical_query_title"],
                },
            )
            paper_records.append(
                {"paper_id": target["paper_id"], "path": str(paper_path.resolve())}
            )
        records_by_split = {
            "canonical": paper_records[:125],
            "static": paper_records[125:150],
            "rotating": paper_records[150:],
        }
        split_bindings = {}
        for name, records in records_by_split.items():
            split_path = root / "data" / "splits" / f"{name}.json"
            split_sha256 = value_sha256(
                {"split": name, "paper_ids": [value["paper_id"] for value in records]}
            )
            atomic_write_json(
                split_path,
                {
                    "count": len(records),
                    "records": records,
                    "split_sha256": split_sha256,
                },
            )
            paper_assets = []
            for record in records:
                paper_path = Path(record["path"])
                paper = read_json(paper_path)
                paper_assets.append(
                    {
                        "paper_id": record["paper_id"],
                        "path": str(paper_path),
                        "sha256": file_sha256(paper_path),
                        "title": paper["title"],
                    }
                )
            split_bindings[name] = {
                "path": str(split_path.resolve()),
                "sha256": file_sha256(split_path),
                "split_sha256": split_sha256,
                "paper_asset_set_sha256": value_sha256(paper_assets),
            }
        seed_programs = {}
        for name in SEED_NAMES:
            seed_path = seed_program_path(name).resolve()
            seed_programs[name] = {
                "path": str(seed_path),
                "sha256": file_sha256(seed_path),
                "capability_manifest": program_capability_manifest(path=seed_path),
            }
        role_summaries = {}
        for role in ROLES:
            role_root = root / "evolution" / role / run_id
            pool_path = role_root / "pool.json"
            canonical_artifacts = []
            for record in records_by_split["canonical"]:
                paper_id = record["paper_id"]
                review_path = (
                    root / "reviews" / "canonical-reviews" / paper_id / f"{role}.json"
                )
                evaluation_path = (
                    root
                    / "evaluations"
                    / "canonical-evaluation"
                    / paper_id
                    / f"{role}.json"
                )
                atomic_write_json(review_path, {"paper_id": paper_id, "role": role})
                atomic_write_json(
                    evaluation_path,
                    {"paper_id": paper_id, "role": role},
                )
                canonical_artifacts.append(
                    {
                        "review_path": str(review_path.resolve()),
                        "review_sha256": file_sha256(review_path),
                        "evaluation_path": str(evaluation_path.resolve()),
                        "evaluation_sha256": file_sha256(evaluation_path),
                    }
                )
            canonical_binding = {
                "path": str(canonical_evaluation.resolve()),
                "sha256": file_sha256(canonical_evaluation),
                "run_id": "canonical-evaluation",
                "review_run_id": "canonical-reviews",
            }
            run_spec = {
                "protocol": FORMAL_RUN_SPEC_PROTOCOL,
                "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
                "role": role,
                "run_id": run_id,
                "pool_path": str(pool_path.resolve()),
                "model_identity": config.model.identity(),
                "embedding_identity": config.embedding_identity(),
                "require_bubblewrap": True,
                "s2_intervention": dict(FORMAL_AUTONOMOUS_S2_INTERVENTION),
                "canonical_evaluation": canonical_binding,
                "canonical_artifacts_sha256": value_sha256(canonical_artifacts),
                "splits": split_bindings,
                "seed_programs": seed_programs,
                "search_budget": {
                    "seed_count": len(SEED_NAMES),
                    "mutation_attempts": MAX_MUTATION_ATTEMPTS,
                    "static_batch_size": FORMAL_STATIC_SIZE,
                    "rotating_batch_size": FORMAL_ROTATING_BATCH_SIZE,
                    "rotating_schedule": "frozen-record-order-disjoint-5-v1",
                },
                "frozen_s2": {
                    "path": str(development_s2.resolve()),
                    "sha256": file_sha256(development_s2),
                    "pool_version": "frozen_s2_external_development_v1",
                    "paper_count": 275,
                },
            }
            run_spec["run_spec_id"] = value_sha256(run_spec)
            atomic_write_json(role_root / "run-spec.json", run_spec)

            seed_scores = {
                "vector_search": 4.2,
                "llm_summarizer": 4.0,
                "experience_learner": 4.0,
            }
            pool = EvolutionPool(role)
            for seed_name in SEED_NAMES:
                program_path = seed_program_path(seed_name).resolve()
                program_sha256 = file_sha256(program_path)
                capability_manifest = program_capability_manifest(
                    path=program_path
                )
                program_version = (
                    f"{run_id}-{role.lower()}-seed-{seed_name}"
                )
                snapshot = {
                    "snapshot_protocol": "research-v2-memory-snapshot-v1",
                    "role": role,
                    "program_path": str(program_path),
                    "program_sha256": program_sha256,
                    "program_version": program_version,
                    "seed_kind": seed_name,
                    "source_review_ids": [],
                    "source_evaluation_ids": [],
                    "items": [],
                    "snapshot": {},
                    "capability_manifest": capability_manifest,
                    "embedding_identity": config.embedding_identity(),
                }
                snapshot["snapshot_id"] = value_sha256(snapshot)
                snapshot_path = (
                    root
                    / "memories"
                    / role
                    / program_version
                    / "snapshot.json"
                )
                atomic_write_json(snapshot_path, snapshot)
                review_run_id = f"{program_version}-static-review"
                evaluation_run_id = f"{program_version}-static-eval"
                for record in records_by_split["static"]:
                    self._review_evaluation_fixture(
                        root=root,
                        role=role,
                        paper_id=record["paper_id"],
                        review_run_id=review_run_id,
                        evaluation_run_id=evaluation_run_id,
                        program_version=program_version,
                        memory_snapshot_id=snapshot["snapshot_id"],
                        review_quality=seed_scores[seed_name],
                    )
                pool.add(
                    PoolEntry(
                        candidate_id=f"seed-{seed_name}",
                        role=role,
                        program_path=str(program_path),
                        program_sha256=program_sha256,
                        parent_id=None,
                        static_score=seed_scores[seed_name],
                        seed_kind=seed_name,
                        memory_snapshot_path=str(snapshot_path.resolve()),
                        memory_snapshot_sha256=file_sha256(snapshot_path),
                        memory_snapshot_id=snapshot["snapshot_id"],
                        capability_manifest=capability_manifest,
                        lineage=[
                            {
                                "candidate_id": f"seed-{seed_name}",
                                "parent_id": None,
                                "change": {
                                    "title": f"Initial {seed_name} seed",
                                    "details": [],
                                },
                                "capability_manifest": capability_manifest,
                            }
                        ],
                    )
                )
            atomic_write_json(pool_path, pool.to_dict())

            coordinator_attempts = {}
            for attempt in range(1, MAX_MUTATION_ATTEMPTS + 1):
                key = f"{attempt:03d}"
                parent = EvolutionPool.from_dict(read_json(pool_path)).select_parent(
                    seed=attempt
                )
                parent_snapshot = read_json(parent.memory_snapshot_path)
                start = (attempt - 1) * FORMAL_ROTATING_BATCH_SIZE
                batch_records = records_by_split["rotating"][
                    start : start + FORMAL_ROTATING_BATCH_SIZE
                ]
                prefix = f"{run_id}-{role.lower()}-a{key}"
                artifact_run_ids = {
                    "rotating_reviews": f"{prefix}-rotating-review",
                    "rotating_evaluations": f"{prefix}-rotating-eval",
                    "candidate_static_reviews": f"{prefix}-static-review",
                    "candidate_static_evaluations": f"{prefix}-static-eval",
                }
                work = {
                    "protocol": FORMAL_ATTEMPT_WORK_PROTOCOL,
                    "role_run_protocol": FORMAL_ROLE_EVOLUTION_PROTOCOL,
                    "role": role,
                    "run_id": run_id,
                    "attempt": attempt,
                    "pool_before": {
                        "path": str(pool_path.resolve()),
                        "sha256": file_sha256(pool_path),
                    },
                    "parent": {
                        "candidate_id": parent.candidate_id,
                        "program_path": parent.program_path,
                        "program_sha256": parent.program_sha256,
                        "program_version": parent_snapshot["program_version"],
                        "memory_snapshot_path": parent.memory_snapshot_path,
                        "memory_snapshot_sha256": parent.memory_snapshot_sha256,
                        "memory_snapshot_id": parent.memory_snapshot_id,
                        "seed_kind": parent.seed_kind,
                    },
                    "canonical_evaluation": canonical_binding,
                    "static_split": {
                        "path": split_bindings["static"]["path"],
                        "sha256": split_bindings["static"]["sha256"],
                        "split_sha256": split_bindings["static"][
                            "split_sha256"
                        ],
                        "paper_ids": [
                            record["paper_id"]
                            for record in records_by_split["static"]
                        ],
                    },
                    "rotating_split": {
                        "path": split_bindings["rotating"]["path"],
                        "sha256": split_bindings["rotating"]["sha256"],
                        "split_sha256": split_bindings["rotating"][
                            "split_sha256"
                        ],
                    },
                    "rotating_batch": {
                        "schedule": "frozen-record-order-disjoint-5-v1",
                        "paper_ids": [
                            record["paper_id"] for record in batch_records
                        ],
                        "paper_paths": [
                            record["path"] for record in batch_records
                        ],
                        "records_sha256": value_sha256(batch_records),
                    },
                    "artifact_run_ids": artifact_run_ids,
                }
                work["work_id"] = value_sha256(work)
                work_path = role_root / f"attempt-{key}.work.json"
                atomic_write_json(work_path, work)
                rotating_reviews = []
                rotating_evaluations = []
                for record in batch_records:
                    review_path, evaluation_path = (
                        self._review_evaluation_fixture(
                            root=root,
                            role=role,
                            paper_id=record["paper_id"],
                            review_run_id=artifact_run_ids[
                                "rotating_reviews"
                            ],
                            evaluation_run_id=artifact_run_ids[
                                "rotating_evaluations"
                            ],
                            program_version=parent_snapshot["program_version"],
                            memory_snapshot_id=parent.memory_snapshot_id,
                            review_quality=3.0,
                        )
                    )
                    rotating_reviews.append(review_path)
                    rotating_evaluations.append(evaluation_path)
                feedback = feedback_packet(
                    parent=parent,
                    review_paths=rotating_reviews,
                    evaluation_paths=rotating_evaluations,
                )
                feedback_sha256 = value_sha256(feedback)
                candidate_id = hashlib.sha256(
                    (
                        f"{run_id}\n{role}\n{attempt}\n"
                        f"{parent.candidate_id}"
                    ).encode("utf-8")
                ).hexdigest()[:24]
                plan = {
                    "protocol": "research-v2-evolution-attempt-plan-v1",
                    "run_id": run_id,
                    "attempt": attempt,
                    "role": role,
                    "parent_id": parent.candidate_id,
                    "parent_program_sha256": parent.program_sha256,
                    "candidate_id": candidate_id,
                    "feedback_sha256": feedback_sha256,
                    "completion_identity": config.model.identity(),
                }
                atomic_write_json(role_root / f"attempt-{key}.plan.json", plan)
                plan_sha256 = value_sha256(plan)
                reflector_output = "invalid fixture patch"
                repair_output = "still invalid fixture patch"
                context_budget = {
                    "protocol": transition_module.REFLECTOR_CONTEXT_BUDGET_PROTOCOL,
                    "method": "unmeasured-test-client",
                    "input_tokens": None,
                    "max_model_len": None,
                    "requested_max_tokens": transition_module.REFLECTOR_MAX_TOKENS,
                    "headroom_tokens": (
                        transition_module.REFLECTOR_CONTEXT_HEADROOM_TOKENS
                    ),
                    "max_tokens": transition_module.REFLECTOR_MAX_TOKENS,
                }
                reflector_messages = [
                    {
                        "role": "user",
                        "content": transition_module._reflector_prompt(
                            Path(parent.program_path).read_text(encoding="utf-8"),
                            feedback,
                        ),
                    }
                ]
                response_identity = {
                    "plan_sha256": plan_sha256,
                    "completion_identity": config.model.identity(),
                    "prompt_protocol": transition_module.REFLECTOR_PROMPT_PROTOCOL,
                    "context_budget": context_budget,
                    "created_at": "2026-09-01T00:00:00+00:00",
                }
                atomic_write_json(
                    role_root / f"attempt-{key}.reflector.json",
                    {
                        **response_identity,
                        "request_sha256": value_sha256(reflector_messages),
                        "component": "reflector",
                        "raw_model_output": reflector_output,
                    },
                )
                atomic_write_json(
                    role_root / f"attempt-{key}.repair.json",
                    {
                        **response_identity,
                        "request_sha256": value_sha256(
                            [
                                {
                                    "role": "user",
                                    "content": "synthetic repair request",
                                }
                            ]
                        ),
                        "component": "reflector_repair",
                        "raw_model_output": repair_output,
                    },
                )
                attempt_path = role_root / f"attempt-{key}.json"
                atomic_write_json(
                    attempt_path,
                    {
                        "protocol": "research-v2-evolution-attempt-v2",
                        "run_id": run_id,
                        "attempt": attempt,
                        "role": role,
                        "candidate_id": candidate_id,
                        "parent_id": parent.candidate_id,
                        "status": "failed",
                        "repair_used": True,
                        "program_path": "",
                        "program_sha256": "",
                        "capability_manifest": {},
                        "parent_capability_manifest": (
                            parent.capability_manifest
                        ),
                        "change": {"title": "", "details": []},
                        "errors": ["synthetic fixture mutation failure"],
                        "raw_reflector_output": (
                            reflector_output
                            + "\n\n--- EXPLICIT REPAIR ---\n\n"
                            + repair_output
                        ),
                        "feedback_sha256": feedback_sha256,
                        "completion_identity": config.model.identity(),
                        "plan_sha256": plan_sha256,
                        "created_at": "2026-09-01T00:00:00+00:00",
                    },
                )
                coordinator_attempts[key] = {
                    "status": "failed",
                    "attempt_path": str(attempt_path.resolve()),
                    "candidate_id": candidate_id,
                }

            winner = pool.best(require_snapshot=True)
            winner_payload = {
                "protocol": "research-v2-formal-role-winner-v1",
                "role": role,
                "run_id": run_id,
                "pool_path": str(pool_path.resolve()),
                "pool_sha256": file_sha256(pool_path),
                "winner": next(
                    entry
                    for entry in pool.to_dict()["entries"]
                    if entry["candidate_id"] == winner.candidate_id
                ),
            }
            winner_payload["winner_id"] = value_sha256(winner_payload)
            atomic_write_json(role_root / "winner.json", winner_payload)
            coordinator_path = role_root / "coordinator.json"
            atomic_write_json(
                coordinator_path,
                {
                    "protocol": "research-v2-formal-role-evolution-v1",
                    "role": role,
                    "run_id": run_id,
                    "status": "complete",
                    "winner_id": winner_payload["winner_id"],
                    "attempts": coordinator_attempts,
                },
            )
            role_summaries[role] = {
                "status": "complete",
                "winner_id": winner_payload["winner_id"],
                "coordinator_path": str(coordinator_path.resolve()),
                "pool_path": str(pool_path.resolve()),
            }
        summary_path = root / "runs" / run_id / "formal-evolution.json"
        atomic_write_json(
            summary_path,
            {
                "protocol": FORMAL_ALL_ROLES_PROTOCOL,
                "run_id": run_id,
                "role_order": list(ROLES),
                "roles": role_summaries,
                "status": "complete",
            },
        )
        return config, {
            "development_s2": development_s2,
            "coco_s2": coco_s2,
            "development_manifest": development_manifest,
            "coco_manifest": coco_manifest,
            "summary": summary_path,
            "resume_audit": resume_audit,
            "qual_run_spec": (
                root / "evolution" / "QUAL" / run_id / "run-spec.json"
            ),
        }

    def test_materialized_spec_round_trips_into_a_frozen_system(self):
        config, _ = self._formal_fixture(self.root)
        spec_path = self.root / "formal-freeze-spec.json"
        with patch(
            "agentreview.research_v2.formal_transition.clean_git_head",
            return_value="a" * 40,
        ):
            spec = materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=spec_path,
            )
            arguments = load_freeze_arguments(
                spec_path,
                repository_root=self.root,
            )
            frozen = freeze_formal_system(
                spec_path=spec_path,
                output_path=self.root / "frozen-system.json",
                repository_root=self.root,
            )
            validated_frozen = validate_frozen_system_transition(
                frozen_system_path=self.root / "frozen-system.json",
                repository_root=self.root,
            )

        self.assertEqual(set(arguments), FREEZE_ARGUMENT_FIELDS)
        self.assertEqual(spec["formal_run_id"], "formal-evolution-v1")
        self.assertEqual(
            spec["spec_id"],
            value_sha256({name: value for name, value in spec.items() if name != "spec_id"}),
        )
        self.assertTrue(arguments["primary_meta_enabled"])
        self.assertTrue(arguments["require_bubblewrap"])
        self.assertEqual(
            spec["evolution_code_provenance"]["mode"],
            "audited_multi_commit_resume",
        )
        self.assertEqual(
            arguments["model_identity"],
            config.model.identity(),
        )
        self.assertEqual(
            arguments["embedding_identity"],
            config.embedding_identity(),
        )
        self.assertEqual(set(arguments["programs"]), set(ROLES))
        self.assertEqual(set(arguments["memory_snapshots"]), set(ROLES))
        self.assertEqual(
            arguments["evolution_run_ids"],
            {role: "formal-evolution-v1" for role in ROLES},
        )
        self.assertEqual(frozen["protocol"], "research-v2-frozen-system-v2")
        self.assertEqual(
            frozen["system_id"],
            value_sha256(
                {name: value for name, value in frozen.items() if name != "system_id"}
            ),
        )
        self.assertTrue(frozen["review_runtime"]["primary_meta"]["enabled"])
        self.assertTrue(frozen["review_runtime"]["require_bubblewrap"])
        self.assertEqual(
            frozen["reproducibility"]["transition_code_commit"],
            "a" * 40,
        )
        self.assertEqual(validated_frozen, frozen)
        formal_binding = frozen["reproducibility"]["formal_freeze_spec"]
        self.assertEqual(formal_binding["spec_id"], spec["spec_id"])
        self.assertEqual(
            formal_binding["source_bindings"],
            spec["source_bindings"],
        )
        for role in ROLES:
            self.assertIn(
                f"{role}.data.paper.static.development-0125",
                spec["source_bindings"]["files"],
            )
        self.assertIn(
            "QUAL.evidence.seed.vector_search.review.development-0125/QUAL.json",
            spec["source_bindings"]["files"],
        )
        self.assertIn(
            "QUAL.evidence.attempt.001.rotating_evaluation."
            "development-0150/QUAL.json",
            spec["source_bindings"]["files"],
        )

    def test_missing_rotating_evidence_prevents_materialization(self):
        config, _ = self._formal_fixture(self.root)
        missing = (
            self.root
            / "evaluations"
            / "formal-evolution-v1-qual-a001-rotating-eval"
            / "development-0150"
            / "QUAL.json"
        )
        missing.unlink()
        output_path = self.root / "system-must-not-exist.json"

        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="a" * 40,
            ),
            self.assertRaisesRegex(ValueError, "primary evidence count"),
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=output_path,
            )

        self.assertFalse(output_path.exists())

    def test_failed_candidate_partial_review_is_snapshot_bound(self):
        role = "QUAL"
        run_id = "formal-evolution-v1"
        candidate_id = "candidate-partial"
        program_version = f"{run_id}-qual-{candidate_id}"
        program_path = seed_program_path("vector_search").resolve()
        capability_manifest = program_capability_manifest(path=program_path)
        snapshot = {
            "snapshot_protocol": "research-v2-memory-snapshot-v1",
            "role": role,
            "program_path": str(program_path),
            "program_sha256": file_sha256(program_path),
            "program_version": program_version,
            "seed_kind": "vector_search",
            "source_review_ids": [],
            "source_evaluation_ids": [],
            "items": [],
            "snapshot": {},
            "capability_manifest": capability_manifest,
        }
        snapshot["snapshot_id"] = value_sha256(snapshot)
        snapshot_path = (
            self.root
            / "memories"
            / role
            / program_version
            / "snapshot.json"
        )
        atomic_write_json(snapshot_path, snapshot)
        review_path, _ = self._review_evaluation_fixture(
            root=self.root,
            role=role,
            paper_id="development-0125",
            review_run_id="partial-static-review",
            evaluation_run_id="unused-partial-evaluation",
            program_version=program_version,
            memory_snapshot_id=snapshot["snapshot_id"],
            review_quality=4.0,
        )
        bindings = {"partial-review": review_path}

        memory_paths = transition_module._validate_failed_candidate_evidence(
            artifact_root=self.root,
            role=role,
            formal_run_id=run_id,
            candidate_id=candidate_id,
            parent_seed_kind="vector_search",
            program_path=program_path,
            phase="candidate_static_review",
            static_review_bindings=bindings,
            static_reviews=[review_path],
            static_evaluation_bindings={},
            static_evaluations=[],
        )
        self.assertIn(
            f"{role}.evidence.failed_candidate.{candidate_id}.memory.snapshot.json",
            memory_paths,
        )

        review = read_json(review_path)
        review["memory_snapshot_id"] = "different-snapshot"
        atomic_write_json(review_path, review, immutable=False)
        with self.assertRaisesRegex(ValueError, "partial candidate Review"):
            transition_module._validate_failed_candidate_evidence(
                artifact_root=self.root,
                role=role,
                formal_run_id=run_id,
                candidate_id=candidate_id,
                parent_seed_kind="vector_search",
                program_path=program_path,
                phase="candidate_static_review",
                static_review_bindings=bindings,
                static_reviews=[review_path],
                static_evaluation_bindings={},
                static_evaluations=[],
            )

    def test_formal_run_id_must_be_one_path_safe_name(self):
        config, _ = self._formal_fixture(self.root)

        for run_id in ("", ".", "..", "nested/run"):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(ValueError, "path-safe"):
                    build_formal_freeze_arguments(
                        config=config,
                        formal_run_id=run_id,
                        code_commit="a" * 40,
                    )

    def test_materializer_fails_closed_on_authoritative_identity_drift(self):
        cases = (
            "incomplete_all_role_summary",
            "different_role_winner",
            "different_model_identity",
            "changed_development_s2",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / case
                config, paths = self._formal_fixture(case_root)
                if case == "incomplete_all_role_summary":
                    summary = read_json(paths["summary"])
                    summary["status"] = "running"
                    atomic_write_json(paths["summary"], summary, immutable=False)
                elif case == "different_role_winner":
                    summary = read_json(paths["summary"])
                    summary["roles"]["QUAL"]["winner_id"] = "forged-winner"
                    atomic_write_json(paths["summary"], summary, immutable=False)
                elif case == "different_model_identity":
                    run_spec = read_json(paths["qual_run_spec"])
                    run_spec["model_identity"] = {"model": "different-model"}
                    run_spec["run_spec_id"] = value_sha256(
                        {
                            name: value
                            for name, value in run_spec.items()
                            if name != "run_spec_id"
                        }
                    )
                    atomic_write_json(paths["qual_run_spec"], run_spec, immutable=False)
                elif case == "changed_development_s2":
                    paths["development_s2"].write_text(
                        '{"changed":true}\n',
                        encoding="utf-8",
                    )
                output_path = case_root / "formal-freeze-spec.json"

                with (
                    patch(
                        "agentreview.research_v2.formal_transition.clean_git_head",
                        return_value="a" * 40,
                    ),
                    self.assertRaises(ValueError),
                ):
                    materialize_formal_freeze_spec(
                        config=config,
                        formal_run_id="formal-evolution-v1",
                        repository_root=case_root,
                        output_path=output_path,
                    )

                self.assertFalse(output_path.exists())

    def test_formal_roles_must_share_exact_data_sources(self):
        config, paths = self._formal_fixture(self.root)
        run_spec = read_json(paths["qual_run_spec"])
        source_split = Path(run_spec["splits"]["static"]["path"])
        alternate_split = self.root / "data" / "splits" / "qual-static-copy.json"
        atomic_write_json(alternate_split, read_json(source_split))
        run_spec["splits"]["static"]["path"] = str(alternate_split.resolve())
        run_spec["splits"]["static"]["sha256"] = file_sha256(alternate_split)
        run_spec["run_spec_id"] = value_sha256(
            {
                name: value
                for name, value in run_spec.items()
                if name != "run_spec_id"
            }
        )
        atomic_write_json(paths["qual_run_spec"], run_spec, immutable=False)
        output_path = self.root / "system-must-not-exist.json"

        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="a" * 40,
            ),
            self.assertRaisesRegex(
                ValueError,
                "share the exact canonical evaluation and splits",
            ),
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=output_path,
            )

        self.assertFalse(output_path.exists())

    def test_tampered_formal_spec_never_reaches_freeze_system(self):
        config, _ = self._formal_fixture(self.root)
        spec_path = self.root / "formal-freeze-spec.json"
        with patch(
            "agentreview.research_v2.formal_transition.clean_git_head",
            return_value="a" * 40,
        ):
            spec = materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=spec_path,
            )
        spec["freeze_arguments"]["primary_meta_enabled"] = False
        spec["spec_id"] = value_sha256(
            {name: value for name, value in spec.items() if name != "spec_id"}
        )
        tampered_path = self.root / "tampered-formal-freeze-spec.json"
        atomic_write_json(tampered_path, spec)

        output_path = self.root / "system-must-not-exist.json"
        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="a" * 40,
            ),
            self.assertRaisesRegex(ValueError, "primary Meta"),
        ):
            freeze_formal_system(
                spec_path=tampered_path,
                output_path=output_path,
                repository_root=self.root,
            )

        self.assertFalse(output_path.exists())

    def test_raw_legacy_spec_is_rejected(self):
        legacy_path = self.root / "legacy.json"
        atomic_write_json(legacy_path, {"programs": {}})

        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="a" * 40,
            ),
            self.assertRaisesRegex(ValueError, "formal freeze spec envelope"),
        ):
            load_freeze_arguments(
                legacy_path,
                repository_root=self.root,
            )

    def test_bound_source_change_prevents_final_system_commit(self):
        config, paths = self._formal_fixture(self.root)
        spec_path = self.root / "formal-freeze-spec.json"
        output_path = self.root / "system-must-not-exist.json"
        with patch(
            "agentreview.research_v2.formal_transition.clean_git_head",
            return_value="a" * 40,
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=spec_path,
            )
            paths["coco_s2"].write_text(
                paths["coco_s2"].read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "source binding changed"):
                freeze_formal_system(
                    spec_path=spec_path,
                    output_path=output_path,
                    repository_root=self.root,
                )

        self.assertFalse(output_path.exists())

    def test_materializer_revalidates_semantics_after_source_binding(self):
        config, paths = self._formal_fixture(self.root)
        output_path = self.root / "system-must-not-exist.json"
        materialize_bindings = transition_module._materialize_source_bindings
        replaced = False

        def replace_s2_before_binding(**kwargs):
            nonlocal replaced
            if not replaced:
                paths["coco_s2"].write_text(
                    '{"invalid":true}\n',
                    encoding="utf-8",
                )
                replaced = True
            return materialize_bindings(**kwargs)

        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="a" * 40,
            ),
            patch.object(
                transition_module,
                "_materialize_source_bindings",
                side_effect=replace_s2_before_binding,
            ),
            self.assertRaises(ValueError),
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=output_path,
            )

        self.assertFalse(output_path.exists())

    def test_transition_validator_rejects_rehashed_winner_substitution(self):
        config, _ = self._formal_fixture(self.root)
        spec_path = self.root / "formal-freeze-spec.json"
        frozen_path = self.root / "frozen-system.json"
        with patch(
            "agentreview.research_v2.formal_transition.clean_git_head",
            return_value="a" * 40,
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=spec_path,
            )
            frozen = freeze_formal_system(
                spec_path=spec_path,
                output_path=frozen_path,
                repository_root=self.root,
            )
            frozen["reproducibility"]["evolution_winners"]["QUAL"][
                "static_score"
            ] = 999.0
            frozen["system_id"] = value_sha256(
                {
                    name: value
                    for name, value in frozen.items()
                    if name != "system_id"
                }
            )
            atomic_write_json(frozen_path, frozen, immutable=False)
            with self.assertRaisesRegex(ValueError, "re-derived"):
                validate_frozen_system_transition(
                    frozen_system_path=frozen_path,
                    repository_root=self.root,
                )

    def test_git_head_change_rejects_materialize_and_load(self):
        config, _ = self._formal_fixture(self.root)
        changed_spec = self.root / "changed-head-spec.json"
        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                side_effect=("a" * 40, "b" * 40),
            ),
            self.assertRaisesRegex(RuntimeError, "Git HEAD changed"),
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=changed_spec,
            )
        self.assertFalse(changed_spec.exists())

        stable_spec = self.root / "stable-spec.json"
        with patch(
            "agentreview.research_v2.formal_transition.clean_git_head",
            return_value="a" * 40,
        ):
            materialize_formal_freeze_spec(
                config=config,
                formal_run_id="formal-evolution-v1",
                repository_root=self.root,
                output_path=stable_spec,
            )
        with (
            patch(
                "agentreview.research_v2.formal_transition.clean_git_head",
                return_value="b" * 40,
            ),
            self.assertRaisesRegex(ValueError, "code commit differs"),
        ):
            load_freeze_arguments(
                stable_spec,
                repository_root=self.root,
            )

    def test_materialize_freeze_cli_binds_repository_and_formal_run(self):
        config = ResearchConfig(artifact_root=str(self.root))
        output_path = self.root / "formal-freeze-spec.json"
        rendered = io.StringIO()
        with (
            patch.object(cli_module.ResearchConfig, "load", return_value=config),
            patch.object(
                cli_module,
                "materialize_formal_freeze_spec",
                return_value={"protocol": "research-v2-formal-freeze-spec-v1"},
            ) as materialize,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "--config",
                    "formal.json",
                    "materialize-freeze-spec",
                    "--run-id",
                    "formal-evolution-v1",
                    "--output",
                    str(output_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        materialize.assert_called_once_with(
            config=config,
            formal_run_id="formal-evolution-v1",
            repository_root=Path(cli_module.__file__).resolve().parents[3],
            output_path=str(output_path),
        )

    def test_freeze_cli_uses_the_source_revalidating_transition(self):
        config = ResearchConfig(artifact_root=str(self.root))
        spec_path = self.root / "formal-freeze-spec.json"
        output_path = self.root / "frozen-system.json"
        rendered = io.StringIO()
        with (
            patch.object(cli_module.ResearchConfig, "load", return_value=config),
            patch.object(
                cli_module,
                "freeze_formal_system",
                return_value={"protocol": "research-v2-frozen-system-v2"},
            ) as freeze,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "freeze-system",
                    "--spec",
                    str(spec_path),
                    "--output",
                    str(output_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        freeze.assert_called_once_with(
            spec_path=str(spec_path),
            output_path=str(output_path),
            repository_root=Path(cli_module.__file__).resolve().parents[3],
        )

    def test_generate_coco_reviews_cli_wires_the_production_adapter_at_eight(self):
        config = ResearchConfig(
            artifact_root=str(self.root),
            require_bubblewrap=True,
        )
        completion_client = object()
        embedding_client = object()
        adapter = object()
        frozen_system = self.root / "frozen-system.json"
        rendered = io.StringIO()
        with (
            patch.object(cli_module.ResearchConfig, "load", return_value=config),
            patch.object(
                cli_module,
                "OpenAICompletionClient",
                return_value=completion_client,
            ) as completion_factory,
            patch.object(
                cli_module,
                "_embedding_client",
                return_value=embedding_client,
            ) as embedding_factory,
            patch.object(
                cli_module,
                "ResearchV2ReviewGenerationAdapter",
                return_value=adapter,
            ) as adapter_factory,
            patch.object(
                cli_module,
                "validate_frozen_system_transition",
                return_value={
                    "system_id": "system-id",
                    "reproducibility": {
                        "formal_freeze_spec": {"spec_id": "spec-id"}
                    },
                },
            ) as validate_frozen,
            patch.object(
                cli_module,
                "file_sha256",
                return_value="system-sha256",
            ) as hash_frozen,
            patch.object(
                cli_module,
                "run_coco_review_batch",
                return_value={"status": "complete"},
            ) as run_batch,
            redirect_stdout(rendered),
        ):
            exit_code = cli_module.main(
                [
                    "--config",
                    "formal.json",
                    "generate-coco-reviews",
                    "--frozen-system",
                    str(frozen_system),
                    "--run-id",
                    "formal-coco-v1",
                ]
            )

        self.assertEqual(exit_code, 0)
        validate_frozen.assert_called_once_with(
            frozen_system_path=str(frozen_system),
            repository_root=Path(cli_module.__file__).resolve().parents[3],
        )
        hash_frozen.assert_called_once_with(str(frozen_system))
        completion_factory.assert_called_once_with(config.model)
        embedding_factory.assert_called_once_with(config)
        adapter_factory.assert_called_once_with(
            completion_client=completion_client,
            embedding_client=embedding_client,
            embedding_identity=config.embedding_identity(),
            require_bubblewrap=True,
        )
        call = run_batch.call_args
        self.assertEqual(call.kwargs["frozen_system_path"], str(frozen_system))
        self.assertEqual(
            call.kwargs["coco_manifest_path"],
            str(self.root / "data" / "coco_1300" / "manifest.json"),
        )
        self.assertEqual(call.kwargs["output_root"], self.root.resolve())
        self.assertEqual(
            call.kwargs["options"],
            CocoReviewBatchOptions(run_id="formal-coco-v1", max_workers=8),
        )
        self.assertIs(call.kwargs["generation_adapter"], adapter)
        self.assertEqual(
            call.kwargs["expected_frozen_identity"],
            {
                "system_id": "system-id",
                "system_sha256": "system-sha256",
                "spec_id": "spec-id",
            },
        )
        self.assertTrue(callable(call.kwargs["progress"]))

    def test_generate_coco_reviews_rejects_workers_outside_one_to_eight(self):
        config = ResearchConfig(artifact_root=str(self.root))
        with (
            patch.object(cli_module.ResearchConfig, "load", return_value=config),
            patch.object(cli_module, "OpenAICompletionClient") as completion_factory,
            patch.object(cli_module, "_embedding_client") as embedding_factory,
            patch.object(cli_module, "ResearchV2ReviewGenerationAdapter") as adapter_factory,
            patch.object(
                cli_module,
                "validate_frozen_system_transition",
            ) as validate_frozen,
            patch.object(cli_module, "run_coco_review_batch") as run_batch,
        ):
            for max_workers in (0, 9):
                with self.subTest(max_workers=max_workers):
                    with self.assertRaisesRegex(ValueError, r"\[1, 8\]"):
                        cli_module.main(
                            [
                                "generate-coco-reviews",
                                "--frozen-system",
                                str(self.root / "frozen-system.json"),
                                "--run-id",
                                "formal-coco-v1",
                                "--max-workers",
                                str(max_workers),
                            ]
                        )

        completion_factory.assert_not_called()
        embedding_factory.assert_not_called()
        adapter_factory.assert_not_called()
        validate_frozen.assert_not_called()
        run_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
