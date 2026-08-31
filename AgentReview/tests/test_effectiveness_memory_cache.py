import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.reviewer_runtime.program_loader import verify_promoted_program
from effectiveness_screening.arms import ARM_SCHEMA_VERSION
from effectiveness_screening.memory_cache import (
    _episodes_by_task,
    compile_cache,
    freeze_programs,
)


class EffectivenessMemoryCacheTest(unittest.TestCase):
    def test_program_freeze_reads_specialist_and_meta_lineage_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evolved_root = root / "evolved"
            lineage = "vector_search"
            embedding = {
                "mode": "frozen",
                "adapter": "memory_evolution.embedding_adapter:create_qwen3_embedding",
                "model_name": "Qwen/Qwen3-Embedding-8B",
                "revision": "revision-1",
                "fingerprint": "fingerprint-1",
            }

            def promotion(task_id):
                source = evolved_root / "source" / task_id / "best_program.py"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"TASK = {task_id!r}\n", encoding="utf-8")
                source_run = evolved_root / "runs" / task_id
                source_run.mkdir(parents=True, exist_ok=True)
                (source_run / "config.json").write_text(
                    json.dumps(
                        {
                            "embedding_model_name": embedding["model_name"],
                            "embedding_revision": embedding["revision"],
                            "embedding_fingerprint": embedding["fingerprint"],
                        }
                    ),
                    encoding="utf-8",
                )
                return {
                    "program": source.relative_to(evolved_root).as_posix(),
                    "program_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "source_run": source_run.relative_to(evolved_root).as_posix(),
                }

            specialist_tasks = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
            specialist_manifest = {
                "promoted_by_lineage": {
                    task_id: {lineage: promotion(task_id)}
                    for task_id in specialist_tasks
                }
            }
            meta_manifest = {
                "promoted_by_lineage": {"META": {lineage: promotion("META")}}
            }
            evolved_root.mkdir(parents=True, exist_ok=True)
            (evolved_root / "suite_manifest.json").write_text(
                json.dumps(specialist_manifest), encoding="utf-8"
            )
            (evolved_root / "meta_manifest.json").write_text(
                json.dumps(meta_manifest), encoding="utf-8"
            )
            config = {
                "output_root": str(root / "output"),
                "evolved_program_root": str(evolved_root),
                "memory_embedding": embedding,
                "experiment": {
                    "arms": ["evolved"],
                    "lineages": [lineage],
                },
            }

            manifest = freeze_programs(config)

            arm = manifest["arms"]["evolved_from_vector_search"]
            self.assertEqual(set(arm), {*specialist_tasks, "META"})
            for task_id, entry in arm.items():
                self.assertEqual(
                    hashlib.sha256(Path(entry["program"]).read_bytes()).hexdigest(),
                    entry["program_sha256"],
                )
            arm_root = root / "output" / "programs" / "evolved_from_vector_search"
            self.assertTrue((arm_root / "suite_manifest.json").is_file())
            self.assertTrue((arm_root / "meta_manifest.json").is_file())
            for task_id in (*specialist_tasks, "META"):
                verify_promoted_program(
                    str(arm_root),
                    task_id,
                    Path(arm[task_id]["program"]),
                    embedding["model_name"],
                    embedding["revision"],
                    embedding["fingerprint"],
                )

    def test_history_is_split_by_reviewer_task(self):
        record = {
            "sample_id": "paper-1",
            "paper_title": "A Paper",
            "retrieved_papers": [],
            "reviews": {
                "Reviewer QUAL": "quality review",
                "Final Meta-Reviewer": "meta review",
            },
        }
        grouped = _episodes_by_task([record])
        self.assertEqual(len(grouped["QUAL"]), 1)
        self.assertEqual(len(grouped["META"]), 1)
        self.assertEqual(len(grouped["CLAR"]), 0)

    def test_cache_compiler_uses_canonical_lineage_arm_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "output_root": str(Path(temporary) / "output"),
                "evolved_program_root": str(Path(temporary) / "evolved"),
                "experiment": {
                    "arms": ["no_memory", "seed", "evolved"],
                    "lineages": [
                        "vector_search",
                        "llm_summarizer",
                        "experience_learner",
                    ],
                },
            }
            with self.assertRaisesRegex(FileNotFoundError, "memory library"):
                compile_cache(config, "seed_vector_search")
            with self.assertRaisesRegex(ValueError, "Unknown arm ID"):
                compile_cache(config, "seed_experience")

    def test_program_freeze_preserves_all_lineage_arm_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source_program.py"
            source.write_text("def build():\n    return None\n", encoding="utf-8")
            config = {
                "output_root": str(root / "output"),
                "evolved_program_root": str(root / "evolved"),
                "memory_embedding": {
                    "mode": "frozen",
                    "adapter": "fake_embedding:create",
                    "model_name": "fake-embedding",
                    "revision": "fake-revision",
                    "fingerprint": "fake-fingerprint",
                },
                "experiment": {
                    "arms": ["no_memory", "seed", "evolved"],
                    "lineages": [
                        "vector_search",
                        "llm_summarizer",
                        "experience_learner",
                    ],
                },
            }
            with (
                patch("effectiveness_screening.memory_cache.ALL_TASKS", ("QUAL",)),
                patch(
                    "effectiveness_screening.memory_cache._program_source",
                    return_value=source,
                ),
            ):
                manifest = freeze_programs(config)

            expected = {
                "seed_vector_search",
                "seed_llm_summarizer",
                "seed_experience_learner",
                "evolved_from_vector_search",
                "evolved_from_llm_summarizer",
                "evolved_from_experience_learner",
            }
            self.assertEqual(set(manifest["arms"]), expected)
            self.assertEqual(set(manifest["arm_identities"]), expected)
            self.assertEqual(manifest["arm_schema_version"], ARM_SCHEMA_VERSION)
            for arm_id in expected:
                self.assertEqual(manifest["arm_identities"][arm_id]["arm_id"], arm_id)
                self.assertTrue(
                    Path(manifest["arms"][arm_id]["QUAL"]["program"]).is_file()
                )


if __name__ == "__main__":
    unittest.main()
