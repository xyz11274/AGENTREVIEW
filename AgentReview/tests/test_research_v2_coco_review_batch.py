import fcntl
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentreview.reviewer_identity import load_meta_synthesizer, load_reviewer_suite
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
from agentreview.research_v2.models import Paper
from agentreview.research_v2.program_checks import (
    REVIEW_PLAN_PROTOCOL,
    program_capability_manifest,
)
from agentreview.research_v2.review_preparation import RETRIEVAL_PROVENANCE_PROTOCOL


class _UnusedGenerationAdapter:
    def __init__(self):
        self.calls = 0

    def generate_specialist(self, invocation):
        self.calls += 1
        raise AssertionError("generation must not start during invalid preflight")

    def generate_final(self, invocation):
        self.calls += 1
        raise AssertionError("generation must not start during invalid preflight")


class _Completion:
    def complete(self, messages, *, max_tokens, component):
        if component.startswith("specialist_review:"):
            return f"Complete {component} output."
        if component == "meta_synthesis":
            return "Synthesis across all five roles.\n\nFinal recommendation: Accept"
        raise AssertionError(component)


class _OnePaperGenerationAdapter:
    def __init__(self):
        self.delegate = ResearchV2ReviewGenerationAdapter(
            completion_client=_Completion(),
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


class CocoReviewBatchTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
