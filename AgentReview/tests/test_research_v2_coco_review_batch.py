import fcntl
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.reviewer_identity import load_meta_synthesizer, load_reviewer_suite
from agentreview.research_v2 import coco_review_batch as batch_module
from agentreview.research_v2.coco_review_batch import (
    CocoReviewInfrastructureFailure,
    CocoReviewPaperFailure,
    CocoReviewBatchOptions,
    ResearchV2ReviewGenerationAdapter,
    run_coco_review_batch,
)
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.io import atomic_write_json, file_sha256, value_sha256
from agentreview.research_v2.memory import (
    DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY,
    build_memory_snapshot,
    seed_program_path,
)
from agentreview.research_v2.meta import _final_review_id
from agentreview.research_v2.models import Paper
from agentreview.research_v2.program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
)
from agentreview.research_v2.review_preparation import RETRIEVAL_PROVENANCE_PROTOCOL
from agentreview.research_v2.review import _review_id


class _UnusedGenerationAdapter:
    def __init__(self):
        self.calls = 0

    def generate_specialist(self, invocation):
        self.calls += 1
        raise AssertionError("generation must not start during invalid preflight")

    def generate_final(self, invocation):
        self.calls += 1
        raise AssertionError("generation must not start during invalid preflight")

    def validate_frozen_runtime(self, **_):
        return None


class _Completion:
    class Config:
        def __init__(self, identity):
            self._identity = dict(identity)

        def identity(self):
            return dict(self._identity)

    def __init__(self, model_identity=None):
        self.config = self.Config(
            model_identity
            or {
                "model": "mock-27b",
                "temperature": 0.0,
                "enable_thinking": False,
            }
        )
        self.calls = 0

    def complete(self, messages, *, max_tokens, component):
        self.calls += 1
        if component.startswith("specialist_review:"):
            return f"Complete {component} output."
        if component == "meta_synthesis":
            return "Synthesis across all five roles.\n\nFinal recommendation: Accept"
        raise AssertionError(component)


class _OnePaperGenerationAdapter:
    def __init__(self):
        self.delegate = ResearchV2ReviewGenerationAdapter(
            completion_client=_Completion(),
            embedding_identity=dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY),
            require_bubblewrap=False,
        )
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.specialist_calls = 0
        self.meta_started_after_specialist_calls = None
        self.saw_forbidden_manifest_field = False
        self.s2_store_ids = set()

    def _enter(self):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)

    def _exit(self):
        with self.lock:
            self.active -= 1

    def validate_frozen_runtime(self, **identities):
        self.delegate.validate_frozen_runtime(**identities)

    def generate_specialist(self, invocation):
        self._enter()
        try:
            with self.lock:
                self.specialist_calls += 1
                self.s2_store_ids.add(id(invocation.frozen_s2_store))
            self.saw_forbidden_manifest_field |= hasattr(invocation, "score_bracket")
            if invocation.paper_id != "paper-0000":
                time.sleep(0.0005)
                raise CocoReviewPaperFailure("test_paper", "intentional paper failure")
            return self.delegate.generate_specialist(invocation)
        finally:
            self._exit()

    def generate_final(self, invocation):
        self._enter()
        try:
            if invocation.mode == "meta":
                with self.lock:
                    self.meta_started_after_specialist_calls = self.specialist_calls
            return self.delegate.generate_final(invocation)
        finally:
            self._exit()


class _InfrastructureAdapter:
    def __init__(self):
        self.calls = 0

    def generate_specialist(self, invocation):
        self.calls += 1
        raise CocoReviewInfrastructureFailure("offline")

    def generate_final(self, invocation):
        raise AssertionError("no Final Review should be scheduled after outage")

    def validate_frozen_runtime(self, **_):
        return None


class CocoReviewBatchTest(unittest.TestCase):
    def test_specialist_review_id_keeps_the_existing_resume_protocol(self):
        values = "run\npaper\nQUAL\nprogram-v1\nsnapshot-v1\nplan-v1"
        self.assertEqual(
            _review_id(
                run_id="run",
                paper_id="paper",
                role="QUAL",
                program_version="program-v1",
                snapshot_id="snapshot-v1",
                review_plan_identity_sha256="plan-v1",
            ),
            hashlib.sha256(values.encode("utf-8")).hexdigest(),
        )

    def _formal_fixture(self, root):
        embedding_identity = dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY)
        model_identity = {
            "model": "mock-27b",
            "temperature": 0.0,
            "enable_thinking": False,
        }
        program_path = seed_program_path("vector_search").resolve()
        program_sha256 = file_sha256(program_path)
        capability_manifest = program_capability_manifest(path=program_path)
        programs = {}
        snapshots = {}
        for role in ROLES:
            version = f"{role.lower()}-winner-v1"
            snapshot_path = root / "snapshots" / f"{role}.json"
            snapshot = build_memory_snapshot(
                role=role,
                program_path=program_path,
                program_version=version,
                review_paths=[],
                evaluation_paths=[],
                output_path=snapshot_path,
                seed_kind="vector_search",
                embedding_identity=embedding_identity,
                require_bubblewrap=False,
            )
            programs[role] = {
                "path": str(program_path),
                "sha256": program_sha256,
                "version": version,
                "capability_manifest": capability_manifest,
            }
            snapshots[role] = {
                "path": str(snapshot_path.resolve()),
                "sha256": file_sha256(snapshot_path),
                "snapshot_id": snapshot["snapshot_id"],
            }
        paper_root = root / "papers"
        paper_root.mkdir(parents=True)
        records = []
        s2_lines = []
        for index in range(1300):
            paper_id = f"paper-{index:04d}"
            paper_value = Paper(
                paper_id=paper_id,
                title=f"Paper {index}",
                abstract="Frozen abstract.",
                text="Complete frozen paper text.",
                source={"dataset": "CoCoReviewBench/published-benchmark"},
            ).to_dict()
            paper_value["paper_asset_id"] = value_sha256(paper_value)
            paper_path = paper_root / f"{paper_id}.json"
            paper_path.write_text(
                json.dumps(paper_value, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            records.append(
                {
                    "paper_id": paper_id,
                    "paper_asset_id": paper_value["paper_asset_id"],
                    "path": str(paper_path.resolve()),
                    "venue_year": "ICLR_2020",
                    "score_bracket": "7-10",
                }
            )
            s2_lines.append(
                json.dumps(
                    {
                        "paper_id": paper_id,
                        "pool_version": "test-frozen-s2-v1",
                        "retrieved_papers": [],
                    },
                    sort_keys=True,
                )
            )
        s2_path = root / "coco-s2.jsonl"
        s2_path.write_text("\n".join(s2_lines) + "\n", encoding="utf-8")
        manifest_path = root / "manifest.json"
        manifest = {
            "protocol": "coco_1300_protocol_compatible_resample_v1",
            "count": 1300,
            "records": records,
        }
        manifest["manifest_sha256"] = value_sha256(manifest)
        atomic_write_json(manifest_path, manifest)
        system_path = root / "frozen-system.json"
        system = {
            "protocol": "research-v2-frozen-system-v2",
            "review_runtime": {
                "programs": programs,
                "memory_snapshots": snapshots,
                "review_plan": {
                    "protocol": REVIEW_PLAN_PROTOCOL,
                    "decision_authority": "role-local-evolved-program",
                    "s2_capability": {
                        "name": "retrieve_current_s2",
                        "scope": "current-paper-frozen-pool",
                        "network_access": False,
                        "llm_calls": 0,
                    },
                    "primary_intervention": None,
                },
                "retrieval_provenance": {
                    "protocol": RETRIEVAL_PROVENANCE_PROTOCOL,
                    "records_program_decision": True,
                    "records_memory_trace": True,
                    "records_s2_trace": True,
                },
                "specialist_identity": load_reviewer_suite().manifest_payload(),
                "model_identity": model_identity,
                "embedding_identity": embedding_identity,
                "require_bubblewrap": False,
                "frozen_s2_pools": {
                    "coco_1300": {
                        "path": str(s2_path.resolve()),
                        "sha256": file_sha256(s2_path),
                    }
                },
                "primary_meta": {
                    "enabled": True,
                    "identity": load_meta_synthesizer().identity(),
                },
                "concat_ablation": {
                    "enabled": True,
                    "role_order": list(ROLES),
                },
            },
            "reproducibility": {
                "evolution_run_ids": {role: f"formal-{role.lower()}" for role in ROLES},
                "evolution_winners": {
                    role: {
                        "program_sha256": programs[role]["sha256"],
                        "memory_snapshot_sha256": snapshots[role]["sha256"],
                        "memory_snapshot_id": snapshots[role]["snapshot_id"],
                    }
                    for role in ROLES
                },
            },
            "external_evaluation": {
                "coco_1300_manifest": {
                    "path": str(manifest_path.resolve()),
                    "sha256": file_sha256(manifest_path),
                    "protocol": manifest["protocol"],
                    "count": manifest["count"],
                    "manifest_sha256": manifest["manifest_sha256"],
                }
            },
        }
        system["system_id"] = value_sha256(system)
        atomic_write_json(system_path, system)
        return system_path, manifest_path

    def test_production_adapter_identity_mismatch_stops_before_generation(self):
        class EmbeddingClient:
            provider = "qwen3_embedding"
            model_name = "Qwen/Qwen3-Embedding-8B"
            revision = "1" * 40

            def __init__(self, cache_path):
                self.cache_path = cache_path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            fallback = dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY)
            embedding_cache = (root / "embedding.sqlite3").resolve()
            production_embedding = {
                "provider": "qwen3_embedding",
                "model": "Qwen/Qwen3-Embedding-8B",
                "revision": "1" * 40,
                "cache_path": str(embedding_cache),
            }
            missing_cache_client = EmbeddingClient(embedding_cache)
            del missing_cache_client.cache_path
            with self.assertRaisesRegex(
                CocoReviewInfrastructureFailure,
                "cache_path is required",
            ):
                ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(),
                    embedding_client=missing_cache_client,
                    embedding_identity={
                        **production_embedding,
                        "cache_path": str(Path.cwd()),
                    },
                    require_bubblewrap=False,
                )
            cases = {
                "model": ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(
                        {
                            "model": "different-27b",
                            "temperature": 0.0,
                            "enable_thinking": False,
                        }
                    ),
                    embedding_identity=fallback,
                    require_bubblewrap=False,
                ),
                "embedding": ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(),
                    embedding_client=EmbeddingClient(embedding_cache),
                    embedding_identity=production_embedding,
                    require_bubblewrap=False,
                ),
                "bubblewrap": ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(),
                    embedding_identity=fallback,
                    require_bubblewrap=True,
                ),
            }
            cases["model"].validate_frozen_runtime = lambda **_: None
            messages = {
                "model": "Completion client identity",
                "embedding": "Embedding client identity",
                "bubblewrap": "Bubblewrap requirement",
            }
            for name, adapter in cases.items():
                with self.subTest(name=name):
                    with self.assertRaisesRegex(
                        CocoReviewInfrastructureFailure,
                        messages[name],
                    ):
                        run_coco_review_batch(
                            frozen_system_path=system_path,
                            coco_manifest_path=manifest_path,
                            output_root=root,
                            options=CocoReviewBatchOptions(
                                run_id=f"identity-mismatch-{name}",
                            ),
                            generation_adapter=adapter,
                        )
                    self.assertEqual(adapter.completion_client.calls, 0)
                    self.assertFalse(
                        (root / "reviews" / f"identity-mismatch-{name}").exists()
                    )

            class ProxyWithoutIdentityGate:
                def __init__(self, delegate):
                    self.delegate = delegate

                def generate_specialist(self, invocation):
                    return self.delegate.generate_specialist(invocation)

                def generate_final(self, invocation):
                    return self.delegate.generate_final(invocation)

            proxied = ProxyWithoutIdentityGate(cases["bubblewrap"])
            with self.assertRaisesRegex(
                TypeError,
                "must validate the frozen runtime",
            ):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(run_id="identity-proxy"),
                    generation_adapter=proxied,
                )
            self.assertEqual(proxied.delegate.completion_client.calls, 0)

    def test_frozen_runtime_requires_complete_execution_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            base = json.loads(system_path.read_text(encoding="utf-8"))
            cases = {
                "model_identity": ({}, "model_identity is required"),
                "embedding_identity": ({}, "embedding_identity is required"),
                "require_bubblewrap": (None, "require_bubblewrap must be a boolean"),
            }
            for field, (replacement, message) in cases.items():
                with self.subTest(field=field):
                    value = json.loads(json.dumps(base))
                    if replacement is None:
                        value["review_runtime"].pop(field)
                    else:
                        value["review_runtime"][field] = replacement
                    value["system_id"] = value_sha256(
                        {
                            name: item
                            for name, item in value.items()
                            if name != "system_id"
                        }
                    )
                    invalid_path = root / f"invalid-{field}.json"
                    atomic_write_json(invalid_path, value)
                    adapter = _UnusedGenerationAdapter()
                    with self.assertRaisesRegex((TypeError, ValueError), message):
                        run_coco_review_batch(
                            frozen_system_path=invalid_path,
                            coco_manifest_path=manifest_path,
                            output_root=root,
                            options=CocoReviewBatchOptions(
                                run_id=f"invalid-{field}",
                            ),
                            generation_adapter=adapter,
                        )
                    self.assertEqual(adapter.calls, 0)

    def test_batch_rejects_system_changed_after_formal_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            system = json.loads(system_path.read_text(encoding="utf-8"))
            system["reproducibility"]["formal_freeze_spec"] = {
                "spec_id": "formal-spec-id"
            }
            system["system_id"] = value_sha256(
                {
                    name: value
                    for name, value in system.items()
                    if name != "system_id"
                }
            )
            atomic_write_json(system_path, system, immutable=False)
            expected = {
                "system_id": system["system_id"],
                "system_sha256": file_sha256(system_path),
                "spec_id": "formal-spec-id",
            }

            system["reproducibility"]["post_preflight_substitution"] = True
            system["system_id"] = value_sha256(
                {
                    name: value
                    for name, value in system.items()
                    if name != "system_id"
                }
            )
            atomic_write_json(system_path, system, immutable=False)
            adapter = _UnusedGenerationAdapter()
            with self.assertRaisesRegex(ValueError, "changed after formal"):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(run_id="formal-identity-race"),
                    generation_adapter=adapter,
                    expected_frozen_identity=expected,
                )
            self.assertEqual(adapter.calls, 0)

    def test_rejects_non_1300_manifest_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest = {
                "protocol": "coco_1300_protocol_compatible_resample_v1",
                "count": 1299,
                "records": [],
            }
            manifest["manifest_sha256"] = value_sha256(manifest)
            atomic_write_json(manifest_path, manifest)
            system_path = root / "frozen-system.json"
            system = {
                "protocol": "research-v2-frozen-system-v2",
                "review_runtime": {},
                "external_evaluation": {
                    "coco_1300_manifest": {
                        "path": str(manifest_path.resolve()),
                        "sha256": file_sha256(manifest_path),
                        "protocol": manifest["protocol"],
                        "count": manifest["count"],
                        "manifest_sha256": manifest["manifest_sha256"],
                    }
                },
            }
            system["system_id"] = value_sha256(system)
            atomic_write_json(system_path, system)
            adapter = _UnusedGenerationAdapter()

            with self.assertRaisesRegex(ValueError, "exactly 1,300"):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(run_id="formal-coco"),
                    generation_adapter=adapter,
                )

            lock_path = root / "runs" / "locked-run" / "coco-review-batch.lock"
            lock_path.parent.mkdir(parents=True)
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    run_coco_review_batch(
                        frozen_system_path=system_path,
                        coco_manifest_path=manifest_path,
                        output_root=root,
                        options=CocoReviewBatchOptions(run_id="locked-run"),
                        generation_adapter=adapter,
                    )

        self.assertEqual(adapter.calls, 0)

    def test_exact_batch_is_bounded_interleaves_meta_and_never_exposes_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            adapter = _OnePaperGenerationAdapter()

            result = run_coco_review_batch(
                frozen_system_path=system_path,
                coco_manifest_path=manifest_path,
                output_root=root,
                options=CocoReviewBatchOptions(
                    run_id="formal-coco",
                    max_workers=8,
                ),
                generation_adapter=adapter,
            )

            self.assertEqual(result["status"], "completed_with_failures")
            self.assertEqual(result["progress"]["specialist"]["completed"], 5)
            self.assertEqual(result["progress"]["specialist"]["failed"], 6495)
            self.assertEqual(result["progress"]["meta"]["completed"], 1)
            self.assertEqual(result["progress"]["meta"]["blocked"], 1299)
            self.assertEqual(result["progress"]["concat"]["completed"], 1)
            self.assertEqual(result["progress"]["concat"]["blocked"], 1299)
            self.assertTrue(
                (
                    root / "final_reviews" / "formal-coco-meta" / "paper-0000.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    root / "final_reviews" / "formal-coco-concat" / "paper-0000.json"
                ).is_file()
            )
            outage = _InfrastructureAdapter()
            with self.assertRaises(CocoReviewInfrastructureFailure):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="formal-coco",
                        max_workers=8,
                    ),
                    generation_adapter=outage,
                )
            interrupted = json.loads(
                (root / "runs" / "formal-coco" / "coco_review_batch.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(interrupted["status"], "infrastructure_failed")
            self.assertEqual(interrupted["progress"]["specialist"]["resumed"], 5)
            self.assertEqual(interrupted["progress"]["meta"]["resumed"], 1)
            self.assertEqual(interrupted["progress"]["concat"]["resumed"], 1)
            self.assertLessEqual(outage.calls, 8)
            review_path = root / "reviews" / "formal-coco" / "paper-0000" / "QUAL.json"
            corrupted = json.loads(review_path.read_text(encoding="utf-8"))
            corrupted["reviewer_identity"] = {"forged": True}
            review_path.write_text(
                json.dumps(corrupted, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            unused = _UnusedGenerationAdapter()
            with self.assertRaisesRegex(ValueError, "Specialist Review identity"):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="formal-coco",
                        max_workers=8,
                    ),
                    generation_adapter=unused,
                )
            self.assertEqual(unused.calls, 0)

        self.assertLessEqual(adapter.maximum_active, 8)
        self.assertGreater(adapter.maximum_active, 1)
        self.assertEqual(adapter.specialist_calls, 6500)
        self.assertLess(adapter.meta_started_after_specialist_calls, 6500)
        self.assertFalse(adapter.saw_forbidden_manifest_field)
        self.assertEqual(len(adapter.s2_store_ids), 1)

    def test_resume_rejects_review_text_tampering_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            cases = {
                "specialist": (
                    Path("reviews/formal-coco/paper-0000/QUAL.json"),
                    "Specialist Review content identity",
                ),
                "meta": (
                    Path("final_reviews/formal-coco-meta/paper-0000.json"),
                    "Final Review content identity",
                ),
                "concat": (
                    Path("final_reviews/formal-coco-concat/paper-0000.json"),
                    "Final Review content identity",
                ),
            }
            for name, (relative_path, message) in cases.items():
                with self.subTest(name=name):
                    output_root = root / name
                    run_coco_review_batch(
                        frozen_system_path=system_path,
                        coco_manifest_path=manifest_path,
                        output_root=output_root,
                        options=CocoReviewBatchOptions(run_id="formal-coco"),
                        generation_adapter=_OnePaperGenerationAdapter(),
                    )
                    artifact_path = output_root / relative_path
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    artifact["review_text"] += "\n\nFORGED RESUME TEXT"
                    if name == "specialist":
                        artifact["raw_model_output"] = artifact["review_text"]
                        provenance = artifact["retrieval_provenance"]
                        review_plan_identity = {
                            "intervention": provenance["s2"]["intervention"],
                            "capability_manifest": provenance["s2"][
                                "capability_manifest"
                            ],
                            "plan_sha256": provenance["s2"]["plan_sha256"],
                            "preparation_sha256": artifact["generation_config"][
                                "preparation_sha256"
                            ],
                        }
                        artifact["review_id"] = _review_id(
                            run_id=artifact["run_id"],
                            paper_id=artifact["paper_id"],
                            role=artifact["role"],
                            program_version=artifact["program_version"],
                            snapshot_id=artifact["memory_snapshot_id"],
                            review_plan_identity_sha256=value_sha256(
                                review_plan_identity
                            ),
                        )
                    else:
                        artifact["final_review_id"] = _final_review_id(
                            run_id=artifact["run_id"],
                            paper_id=artifact["paper_id"],
                            mode=artifact["mode"],
                            source_review_ids=artifact["source_review_ids"],
                            review_text=artifact["review_text"],
                        )
                    artifact_path.write_text(
                        json.dumps(artifact, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8",
                    )
                    unused = _UnusedGenerationAdapter()
                    with self.assertRaisesRegex(ValueError, message):
                        run_coco_review_batch(
                            frozen_system_path=system_path,
                            coco_manifest_path=manifest_path,
                            output_root=output_root,
                            options=CocoReviewBatchOptions(run_id="formal-coco"),
                            generation_adapter=unused,
                        )
                    self.assertEqual(unused.calls, 0)

    def test_source_drift_discards_staging_and_same_run_id_can_resume(self):
        class MutatingAdapter:
            def __init__(self):
                self.delegate = ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(),
                    embedding_identity=dict(
                        DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY
                    ),
                    require_bubblewrap=False,
                )
                self.mutated = False

            def validate_frozen_runtime(self, **identities):
                self.delegate.validate_frozen_runtime(**identities)

            def generate_specialist(self, invocation):
                if not self.mutated:
                    paper = json.loads(
                        invocation.paper_path.read_text(encoding="utf-8")
                    )
                    paper["title"] = "Changed after coordinator preflight"
                    invocation.paper_path.write_text(
                        json.dumps(paper, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8",
                    )
                    self.mutated = True
                return self.delegate.generate_specialist(invocation)

            def generate_final(self, invocation):
                return self.delegate.generate_final(invocation)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            paper_path = root / "papers" / "paper-0000.json"
            original_paper = paper_path.read_bytes()
            with self.assertRaises(CocoReviewInfrastructureFailure):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="source-drift",
                        max_workers=1,
                    ),
                    generation_adapter=MutatingAdapter(),
                )

            target = root / "reviews" / "source-drift" / "paper-0000"
            self.assertFalse((target / "QUAL.request.json").exists())
            self.assertFalse((target / "QUAL.raw.json").exists())
            self.assertFalse((target / "QUAL.json").exists())
            staging = root / "runs" / "source-drift" / ".staging"
            self.assertEqual(list(staging.iterdir()), [])

            paper_path.write_bytes(original_paper)
            outage = _InfrastructureAdapter()
            with self.assertRaises(CocoReviewInfrastructureFailure):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="source-drift",
                        max_workers=1,
                    ),
                    generation_adapter=outage,
                )
            self.assertEqual(outage.calls, 1)

    def test_crash_after_raw_promotion_resumes_without_a_second_llm_call(self):
        class ResumeThenOutageAdapter:
            def __init__(self):
                self.completion = _Completion()
                self.delegate = ResearchV2ReviewGenerationAdapter(
                    completion_client=self.completion,
                    embedding_identity=dict(
                        DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY
                    ),
                    require_bubblewrap=False,
                )
                self.specialist_calls = 0

            def validate_frozen_runtime(self, **identities):
                self.delegate.validate_frozen_runtime(**identities)

            def generate_specialist(self, invocation):
                self.specialist_calls += 1
                if self.specialist_calls > 1:
                    raise CocoReviewInfrastructureFailure("stop after resume")
                return self.delegate.generate_specialist(invocation)

            def generate_final(self, invocation):
                raise AssertionError("Final generation is not ready")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system_path, manifest_path = self._formal_fixture(root)
            original_promote = batch_module._promote_staged_json
            promotions = 0

            def crash_after_first_promotion(**kwargs):
                nonlocal promotions
                original_promote(**kwargs)
                promotions += 1
                if promotions == 1:
                    raise CocoReviewInfrastructureFailure(
                        "simulated process interruption"
                    )

            first_adapter = ResearchV2ReviewGenerationAdapter(
                completion_client=_Completion(),
                embedding_identity=dict(DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY),
                require_bubblewrap=False,
            )
            with (
                patch.object(
                    batch_module,
                    "_promote_staged_json",
                    side_effect=crash_after_first_promotion,
                ),
                self.assertRaises(CocoReviewInfrastructureFailure),
            ):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="promotion-crash",
                        max_workers=1,
                    ),
                    generation_adapter=first_adapter,
                )

            target = root / "reviews" / "promotion-crash" / "paper-0000"
            self.assertTrue((target / "QUAL.raw.json").is_file())
            self.assertFalse((target / "QUAL.request.json").exists())
            self.assertFalse((target / "QUAL.json").exists())

            resume_adapter = ResumeThenOutageAdapter()
            with self.assertRaises(CocoReviewInfrastructureFailure):
                run_coco_review_batch(
                    frozen_system_path=system_path,
                    coco_manifest_path=manifest_path,
                    output_root=root,
                    options=CocoReviewBatchOptions(
                        run_id="promotion-crash",
                        max_workers=1,
                    ),
                    generation_adapter=resume_adapter,
                )
            self.assertEqual(resume_adapter.completion.calls, 0)
            self.assertTrue((target / "QUAL.request.json").is_file())
            self.assertTrue((target / "QUAL.json").is_file())

    def test_unvalidated_staged_files_never_reach_canonical_paths(self):
        class TamperingAdapter:
            def __init__(self, target):
                self.target = target
                self.delegate = ResearchV2ReviewGenerationAdapter(
                    completion_client=_Completion(),
                    embedding_identity=dict(
                        DETERMINISTIC_FALLBACK_EMBEDDING_IDENTITY
                    ),
                    require_bubblewrap=False,
                )

            def validate_frozen_runtime(self, **identities):
                self.delegate.validate_frozen_runtime(**identities)

            def generate_specialist(self, invocation):
                value = self.delegate.generate_specialist(invocation)
                path = (
                    invocation.output_path
                    if self.target == "final"
                    else invocation.output_path.with_name(
                        f"{invocation.role}.request.json"
                    )
                )
                artifact = json.loads(path.read_text(encoding="utf-8"))
                artifact["tampered_after_return"] = True
                path.write_text(
                    json.dumps(artifact, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                return value

            def generate_final(self, invocation):
                raise AssertionError("Final generation is not ready")

        for target_kind in ("final", "request"):
            with (
                self.subTest(target_kind=target_kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                system_path, manifest_path = self._formal_fixture(root)
                with self.assertRaises(CocoReviewInfrastructureFailure):
                    run_coco_review_batch(
                        frozen_system_path=system_path,
                        coco_manifest_path=manifest_path,
                        output_root=root,
                        options=CocoReviewBatchOptions(
                            run_id=f"tampered-stage-{target_kind}",
                            max_workers=1,
                        ),
                        generation_adapter=TamperingAdapter(target_kind),
                    )
                canonical = (
                    root
                    / "reviews"
                    / f"tampered-stage-{target_kind}"
                    / "paper-0000"
                )
                self.assertFalse((canonical / "QUAL.request.json").exists())
                self.assertFalse((canonical / "QUAL.raw.json").exists())
                self.assertFalse((canonical / "QUAL.json").exists())


if __name__ == "__main__":
    unittest.main()
