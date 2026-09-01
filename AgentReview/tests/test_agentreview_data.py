import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_evolution.agentreview_data import (
    _official_task_reference,
    assert_no_human_reference_leak,
    build_coco_evaluator_reference,
    prepare_held_out_test_data,
    prepare_openreview_data,
)
from memory_evolution.cli import _load_evaluator_validation
from memory_evolution.tasks import SPECIALIST_TASKS


class AgentReviewDataTest(unittest.TestCase):
    def test_prepare_d_uses_official_coco_left_out_references(self):
        with tempfile.TemporaryDirectory(prefix="mstar_d_official_refs_") as directory:
            root = Path(directory)
            coco_root = root / "coco"
            benchmark_root = coco_root / "benchmark"
            markdown_root = coco_root / "train" / "MD" / "d0"
            benchmark_root.mkdir(parents=True)
            markdown_root.mkdir(parents=True)
            (markdown_root / "md_content.md").write_text(
                "# D Paper\nMethod and experiments.", encoding="utf-8"
            )
            record = self._record("d0", "D Paper", "work-d")
            record.pop("opinions_md_by_category_dict")
            record["sentence_texts"] = ["Legacy opinion must not be selected."]
            record["opinions"] = []
            benchmark = benchmark_root / "benchmark_fixture.jsonl"
            benchmark.write_text(json.dumps(record) + "\n", encoding="utf-8")
            temporal = self._temporal_manifest(
                root,
                [],
                [],
                d_ids=["d0"],
                source_asset="benchmark_fixture.jsonl",
            )
            s2 = root / "s2.jsonl"
            s2.write_text("{}\n", encoding="utf-8")
            official = {
                "d0": {
                    "category_correct": {
                        task + "-FIX": [f"official {task} reference"]
                        for task in SPECIALIST_TASKS
                    }
                }
            }
            with (
                patch(
                    "memory_evolution.agentreview_data.load_official_coco_human_references",
                    return_value=(
                        official,
                        {"implementation": "official fixture", "requested_paper_count": 1},
                    ),
                ) as loader,
                patch(
                    "memory_evolution.agentreview_data._load_s2_cache",
                    return_value=({"d0": {}}, "a" * 64),
                ),
                patch(
                    "memory_evolution.agentreview_data.validate_frozen_s2_pool",
                    return_value={"passed": True},
                ),
            ):
                manifest = prepare_held_out_test_data(
                    str(coco_root),
                    str(root / "prepared"),
                    str(s2),
                    str(temporal),
                )

            loader.assert_called_once()
            for task in SPECIALIST_TASKS:
                self.assertEqual(manifest["tasks"][task]["count"], 1)
                row = json.loads(
                    (root / "prepared" / task / "test.jsonl")
                    .read_text(encoding="utf-8")
                    .strip()
                )
                self.assertEqual(row["answer"], f"[{{task}}-FIX]\nofficial {{task}} reference".format(task=task))
            self.assertEqual(
                manifest["evaluator_reference_source"]["implementation"],
                "official fixture",
            )

    def _temporal_manifest(
        self,
        root,
        a_ids,
        b_ids,
        c_ids=(),
        d_ids=(),
        titles=None,
        source_asset="",
    ):
        titles = titles or {}
        zones = {
            "historical_experience": list(a_ids),
            "evolution_validation": list(b_ids),
            "effectiveness_holdout": list(c_ids),
            "held_out_benchmark_test": list(d_ids),
        }
        payload = {
            "version": "coco-temporal-paper-split-v1",
            "source_pool_count": sum(map(len, zones.values())),
            "zones": {},
        }
        for zone, paper_ids in zones.items():
            path = root / f"{zone}.jsonl"
            values = [
                {
                    "paper_id": paper_id,
                    "title": titles.get(paper_id, paper_id),
                    "normalized_title": paper_id,
                    "paper_family": f"family-{paper_id}",
                    "venue": "iclr",
                    "year": 2024 if zone == "held_out_benchmark_test" else 2023,
                    "paper_review_cutoff": (
                        "2023-12-31"
                        if zone == "held_out_benchmark_test"
                        else "2022-12-31"
                    ),
                    "zone": zone,
                    "source_asset": source_asset,
                }
                for paper_id in paper_ids
            ]
            path.write_text(
                "".join(json.dumps(value) + "\n" for value in values),
                encoding="utf-8",
            )
            payload["zones"][zone] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "count": len(values),
            }
        manifest = root / "temporal.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return manifest

    def _record(self, paper_id, title, paperhash):
        return {
            "id": paper_id,
            "MD_path": f"train/MD/{paper_id}",
            "conference": "ICLR 2024",
            "decision": "Accept",
            "content": {"title": title, "paperhash": paperhash},
            "opinions_md_by_category_dict": {
                "QUAL-MET": [[], [], f"quality {paper_id}"],
                "CLAR-WRT": [[], [], f"clarity {paper_id}"],
                "SIGN-DOM": [[], [], f"significance {paper_id}"],
                "ORIG-MTH": [[], [], f"originality {paper_id}"],
                "POL-ETH": [[], [], f"policy {paper_id}"],
            },
            "metareview": [[["AC", {"comment": f"meta {paper_id}"}]]],
        }

    def test_prepare_isolates_work_families_and_test(self):
        with tempfile.TemporaryDirectory(prefix="mstar_data_test_") as directory:
            root = Path(directory)
            records = [
                self._record("p0", "Version Zero", "same-work"),
                self._record("p0-title-link", "Version Zero", "different-hash"),
                self._record("p1", "Paper One", "work-one"),
                self._record("p2", "Paper Two", "work-two"),
                self._record("p3", "Paper Three", "work-three"),
            ]
            for record in records:
                paper_dir = root / record["MD_path"]
                paper_dir.mkdir(parents=True)
                (paper_dir / "md_content.md").write_text(
                    f"# {record['content']['title']}\nMethod and experiments.",
                    encoding="utf-8",
                )
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(self._record("test-version", "Different title", "same-work"))
                + "\n",
                encoding="utf-8",
            )

            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(
                    self._temporal_manifest(
                        root, ["p1"], ["p2", "p3"], [], ["test-version"]
                    )
                ),
                validation_families=1,
                meta_validation_families=1,
                max_train_families=1,
                seed=0,
            )

            self.assertTrue(manifest["test_overlap_removed"])
            self.assertEqual(manifest["family_overlap"], 0)
            for task_id in SPECIALIST_TASKS:
                task = manifest["tasks"][task_id]
                self.assertEqual(task["episode_count"], 1)
                self.assertEqual(task["validation_count"], 1)
                combined = Path(task["episodes"]).read_text(encoding="utf-8") + Path(
                    task["validation"]
                ).read_text(encoding="utf-8")
                self.assertNotIn('"paper_id": "p0"', combined)
                self.assertNotIn('"paper_id": "p0-title-link"', combined)

    def test_frozen_s2_cache_is_optional_capability_and_hashed(self):
        with tempfile.TemporaryDirectory(prefix="mstar_s2_test_") as directory:
            root = Path(directory)
            records = [
                self._record("p1", "Paper One", "work-one"),
                self._record("p2", "Paper Two", "work-two"),
            ]
            for record in records:
                paper_dir = root / record["MD_path"]
                paper_dir.mkdir(parents=True)
                (paper_dir / "md_content.md").write_text(
                    f"# {record['content']['title']}\nMethod.",
                    encoding="utf-8",
                )
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(self._record("test", "Held Out", "held-out")) + "\n",
                encoding="utf-8",
            )
            cache = root / "s2.json"
            papers = [
                {
                    "title": "Frozen Neighbor",
                    "year": 2022,
                    "abstract": "Cached abstract.",
                }
            ]
            cache.write_text(
                json.dumps(
                    [
                        {
                            "paper_id": paper_id,
                            "target_identity": {"paper_id": paper_id},
                            "paper_review_cutoff": "2022-12-31",
                            "retrieval_method": "fixture",
                            "retrieval_version": "v1",
                            "top_k": 10,
                            "retrieval_config": {"top_k": 10},
                            "retrieved_papers": papers if paper_id == "p1" else [],
                            "result_sha256": hashlib.sha256(
                                json.dumps(
                                    papers if paper_id == "p1" else [],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode()
                            ).hexdigest(),
                            "pool_version": "frozen-s2-temporal-v1",
                        }
                        for paper_id in ("p1", "p2")
                    ]
                ),
                encoding="utf-8",
            )
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(self._temporal_manifest(root, ["p1"], ["p2"])),
                validation_families=1,
                meta_validation_families=0,
                max_train_families=1,
                s2_cache_path=str(cache),
            )
            self.assertTrue(manifest["formal_ready"])
            self.assertEqual(len(manifest["semantic_scholar_cache_sha256"]), 64)
            payload = "".join(
                Path(manifest["tasks"][task]["queries"]).read_text(encoding="utf-8")
                for task in SPECIALIST_TASKS
            )
            self.assertNotIn("FROZEN RELATED-WORK CACHE", payload)
            self.assertNotIn("Frozen Neighbor", payload)
            self.assertFalse(manifest["semantic_scholar_default_injection"])

    def _write_source(self, root, records):
        for record in records:
            paper_dir = root / record["MD_path"]
            paper_dir.mkdir(parents=True, exist_ok=True)
            (paper_dir / "md_content.md").write_text(
                f"# {record['content']['title']}\nMethod.",
                encoding="utf-8",
            )
        (root / "train_clean.jsonl").write_text(
            "\n".join(json.dumps(value) for value in records) + "\n",
            encoding="utf-8",
        )
        (root / "test.jsonl").write_text(
            json.dumps(self._record("held-out", "Held Out", "held-out")) + "\n",
            encoding="utf-8",
        )

    def _splits_by_paper(self, manifest):
        splits = {}
        for task_id in SPECIALIST_TASKS:
            for name in ("episodes", "validation", "queries"):
                for line in (
                    Path(manifest["tasks"][task_id][name])
                    .read_text(encoding="utf-8")
                    .splitlines()
                ):
                    item = json.loads(line)
                    paper_id = item["metadata"]["paper_id"]
                    splits.setdefault(paper_id, set()).add(item["metadata"]["split"])
        return splits

    def test_meta_validation_defaults_to_remaining_b_families(self):
        with tempfile.TemporaryDirectory(prefix="mstar_b_alloc_test_") as directory:
            root = Path(directory)
            b_ids = [f"b{index}" for index in range(5)]
            records = [self._record("a0", "Train Paper", "work-train")] + [
                self._record(paper_id, f"B Paper {paper_id}", f"work-{paper_id}")
                for paper_id in b_ids
            ]
            self._write_source(root, records)
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(self._temporal_manifest(root, ["a0"], b_ids)),
                validation_families=2,
                seed=0,
            )
            self.assertEqual(manifest["train_family_count"], 1)
            self.assertEqual(manifest["validation_family_count"], 2)
            self.assertEqual(manifest["meta_validation_family_count"], 3)
            self.assertEqual(manifest["family_overlap"], 0)
            splits = self._splits_by_paper(manifest)
            specialist = {
                paper_id
                for paper_id, values in splits.items()
                if "specialist_validation" in values
            }
            meta = {
                paper_id
                for paper_id, values in splits.items()
                if "meta_validation" in values
            }
            self.assertEqual(len(specialist), 2)
            self.assertEqual(len(meta), 3)
            self.assertFalse(specialist & meta)
            self.assertEqual(specialist | meta, set(b_ids))

    def test_b_allocation_fails_fast_when_b_is_smaller_than_requested(self):
        with tempfile.TemporaryDirectory(prefix="mstar_b_short_test_") as directory:
            root = Path(directory)
            b_ids = ["b0", "b1"]
            records = [self._record("a0", "Train Paper", "work-train")] + [
                self._record(paper_id, f"B Paper {paper_id}", f"work-{paper_id}")
                for paper_id in b_ids
            ]
            self._write_source(root, records)
            with self.assertRaisesRegex(ValueError, "frozen B contains 2"):
                prepare_openreview_data(
                    str(root),
                    str(root / "prepared"),
                    str(self._temporal_manifest(root, ["a0"], b_ids)),
                    validation_families=2,
                    meta_validation_families=1,
                    seed=0,
                )
            with self.assertRaisesRegex(ValueError, "frozen B contains 2"):
                prepare_openreview_data(
                    str(root),
                    str(root / "prepared"),
                    str(self._temporal_manifest(root, ["a0"], b_ids)),
                    validation_families=3,
                    seed=0,
                )

    def test_train_defaults_to_all_manifest_a_families(self):
        with tempfile.TemporaryDirectory(prefix="mstar_a_default_test_") as directory:
            root = Path(directory)
            a_ids = ["a0", "a1", "a2"]
            records = [
                self._record(paper_id, f"A Paper {paper_id}", f"work-{paper_id}")
                for paper_id in a_ids
            ] + [self._record("b0", "B Paper", "work-b0")]
            self._write_source(root, records)
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(self._temporal_manifest(root, a_ids, ["b0"])),
                validation_families=1,
                meta_validation_families=0,
                seed=0,
            )
            self.assertEqual(manifest["train_family_count"], 3)
            for task_id in SPECIALIST_TASKS:
                self.assertEqual(manifest["tasks"][task_id]["episode_count"], 3)

    def test_historical_episode_excludes_human_reference(self):
        with tempfile.TemporaryDirectory(prefix="mstar_boundary_test_") as directory:
            root = Path(directory)
            records = [
                self._record("p1", "Paper One", "work-one"),
                self._record("p2", "Paper Two", "work-two"),
            ]
            for record in records:
                paper_dir = root / record["MD_path"]
                paper_dir.mkdir(parents=True)
                (paper_dir / "md_content.md").write_text(
                    "# Paper\nMethod", encoding="utf-8"
                )
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(self._record("test", "Held Out", "held-out")) + "\n",
                encoding="utf-8",
            )
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(self._temporal_manifest(root, ["p1"], ["p2"])),
                validation_families=1,
                meta_validation_families=0,
                max_train_families=1,
            )
            episode = json.loads(
                Path(manifest["tasks"]["QUAL"]["episodes"])
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertNotIn("VERIFIED HUMAN REFERENCE", episode["text"])
            self.assertNotIn("quality p", episode["text"])
            self.assertEqual(
                episode["metadata"]["experience_protocol"],
                "self_generated_after_review",
            )

    def test_historical_task_needs_no_human_reference_or_category_adapter(self):
        with tempfile.TemporaryDirectory(prefix="mstar_unsupervised_a_") as directory:
            root = Path(directory)
            records = [
                self._record("a0", "Source A", "work-a"),
                self._record("b0", "Source B", "work-b"),
            ]
            for record in records:
                record.pop("opinions_md_by_category_dict")
                record.pop("metareview")
                record["sentence_texts"] = []
                record["opinions"] = []
            self._write_source(root, records)
            with patch(
                "memory_evolution.agentreview_data._category_reference",
                side_effect=AssertionError("legacy reference adapter must not gate A"),
            ):
                manifest = prepare_openreview_data(
                    str(root),
                    str(root / "prepared"),
                    str(self._temporal_manifest(root, ["a0"], ["b0"])),
                    validation_families=1,
                    meta_validation_families=0,
                )
            for task_id in SPECIALIST_TASKS:
                self.assertEqual(manifest["tasks"][task_id]["episode_count"], 1)
                self.assertEqual(manifest["tasks"][task_id]["validation_count"], 1)

    def test_coco_reference_is_evaluator_only_and_never_enters_candidate_input(self):
        with tempfile.TemporaryDirectory(prefix="mstar_evaluator_only_") as directory:
            root = Path(directory)
            records = [
                self._record("a0", "Source A", "work-a"),
                self._record("b0", "Source B", "work-b"),
            ]
            for record in records:
                record.pop("opinions_md_by_category_dict")
                record["sentence_texts"] = [
                    "Reviewer-specific quality concern long enough for leak audit.",
                    "Author rebuttal must not become the evaluator reference.",
                ]
                record["opinions"] = [
                    {
                        "category": ["QUAL-MET", "CLAR-WRT"],
                        "data": [
                            {"role": "Reviewer 1", "sentence_ids": [0]},
                            {"role": "Author", "sentence_ids": [1]},
                        ],
                    }
                ]
            self._write_source(root, records)
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(self._temporal_manifest(root, ["a0"], ["b0"])),
                validation_families=1,
                meta_validation_families=0,
            )
            qual = manifest["tasks"]["QUAL"]
            episode = json.loads(Path(qual["episodes"]).read_text().splitlines()[0])
            task = json.loads(Path(qual["validation"]).read_text().splitlines()[0])
            reference = json.loads(
                Path(qual["evaluator_references"]).read_text().splitlines()[0]
            )
            self.assertNotIn("answer", task)
            self.assertNotIn("categories", task["metadata"])
            self.assertNotIn(reference["answer"], task["question"])
            self.assertNotIn(reference["answer"], episode["text"])
            self.assertNotIn("Author rebuttal", reference["answer"])
            self.assertTrue(reference["access"]["candidate_forbidden"])
            joined = _load_evaluator_validation(root / "prepared", "QUAL")
            self.assertEqual(joined[0].answer, reference["answer"])
            self.assertNotIn(joined[0].answer, joined[0].question)
            self.assertEqual(joined[0].metadata["categories"], ["QUAL-MET"])

    def test_coco_opinions_adapter_reconstructs_qual_reference(self):
        reference, categories = build_coco_evaluator_reference(
            {
                "sentence_texts": ["Reviewer concern.", "Author reply."],
                "opinions": [
                    {
                        "category": ["QUAL-EXP", "ORIG-MTH"],
                        "data": [
                            {"role": "Reviewer 2", "sentence_ids": [0]},
                            {"role": "Author", "sentence_ids": [1]},
                        ],
                    }
                ],
            },
            "QUAL",
        )
        self.assertEqual(categories, ["QUAL-EXP"])
        self.assertIn("Reviewer concern.", reference)
        self.assertNotIn("Author reply.", reference)

    def test_leak_gate_rejects_field_and_reference_text_injection(self):
        reference = {
            "id": "b0::QUAL",
            "answer": "A human reference sentence that must remain evaluator-only.",
        }
        with self.assertRaisesRegex(ValueError, "Human Reference leak gate failed"):
            assert_no_human_reference_leak(
                {"validation": [{"id": "b0::QUAL", "human_reference": "leak"}]},
                [reference],
            )
        with self.assertRaisesRegex(ValueError, "Human Reference leak gate failed"):
            assert_no_human_reference_leak(
                {"validation": [{"id": "b0::QUAL", "notes": reference["answer"]}]},
                [reference],
            )

    def test_manifest_title_is_canonical_when_source_title_is_structured(self):
        with tempfile.TemporaryDirectory(prefix="mstar_canonical_title_") as directory:
            root = Path(directory)
            records = [
                self._record("a0", "Source A", "work-a"),
                self._record("b0", "Source B", "work-b"),
            ]
            records[0]["content"]["title"] = {"value": "Contaminated Source Title"}
            self._write_source(root, records)
            (root / records[0]["MD_path"] / "md_content.md").write_text(
                "# Paper body\nMethod.", encoding="utf-8"
            )
            manifest = prepare_openreview_data(
                str(root),
                str(root / "prepared"),
                str(
                    self._temporal_manifest(
                        root,
                        ["a0"],
                        ["b0"],
                        titles={"a0": "Canonical Manifest Title"},
                    )
                ),
                validation_families=1,
                meta_validation_families=0,
            )
            episode = json.loads(
                Path(manifest["tasks"]["QUAL"]["episodes"]).read_text().splitlines()[0]
            )
            self.assertEqual(
                episode["metadata"]["paper_title"], "Canonical Manifest Title"
            )
            self.assertIn("Canonical Manifest Title", episode["text"])
            self.assertNotIn("{'value':", episode["text"])

    def test_official_coco_reference_projection_does_not_repeat_selection_logic(self):
        reference, categories = _official_task_reference(
            {
                "category_correct": {
                    "QUAL-MET": ["Official retained quality concern."],
                    "ORIG-MTH": ["Official retained originality concern."],
                },
                "category_incorrect": {
                    "QUAL-EXP": ["Official rejected concern."],
                },
            },
            "QUAL",
        )
        self.assertIn("Official retained quality concern.", reference)
        self.assertNotIn("Official retained originality concern.", reference)
        self.assertNotIn("Official rejected concern.", reference)
        self.assertEqual(categories, ["QUAL-MET"])

    def test_leak_gate_whitelists_authorized_paper_lines(self):
        paper_line = (
            "Aleksander Madry et al. Towards deep learning models resistant "
            "to adversarial attacks. arXiv:1706.06083, 2017."
        )
        review_only = "This reviewer-only sentence never appears in the paper text."
        artifacts = {
            "queries": [
                {
                    "id": "p1::QUAL",
                    "question": f"Paper body.\n{paper_line}",
                }
            ]
        }
        references = [{"id": "p1::QUAL", "answer": paper_line}]
        # Same-paper reference text collides until the legitimate Markdown line
        # is authorized.
        with self.assertRaises(ValueError):
            assert_no_human_reference_leak(artifacts, references)
        assert_no_human_reference_leak(
            artifacts,
            references,
            authorized_fragments_by_paper={"p1": {paper_line}},
        )
        # A different paper no longer creates a cross-paper false positive.
        assert_no_human_reference_leak(
            artifacts, [{"id": "p9::QUAL", "answer": paper_line}]
        )
        references.append({"id": "p1::QUAL", "answer": review_only})
        artifacts["queries"][0]["question"] += "\n" + review_only
        with self.assertRaises(ValueError):
            assert_no_human_reference_leak(
                artifacts,
                references,
                authorized_fragments_by_paper={"p1": {paper_line}},
            )

    def test_prepare_fails_closed_when_markdown_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="mstar_missing_md_") as directory:
            root = Path(directory)
            records = [
                self._record("a0", "Source A", "work-a"),
                self._record("b0", "Source B", "work-b"),
            ]
            (root / "train_clean.jsonl").write_text(
                "\n".join(json.dumps(value) for value in records) + "\n",
                encoding="utf-8",
            )
            (root / "test.jsonl").write_text(
                json.dumps(self._record("held-out", "Held Out", "held-out")) + "\n",
                encoding="utf-8",
            )
            # No markdown directories are created on purpose.
            with self.assertRaisesRegex(ValueError, "no resolvable markdown"):
                prepare_openreview_data(
                    str(root),
                    str(root / "prepared"),
                    str(self._temporal_manifest(root, ["a0"], ["b0"])),
                    validation_families=1,
                    meta_validation_families=0,
                )
            self.assertFalse((root / "prepared" / "manifest.json").exists())

    def test_formal_coco_path_does_not_load_duplicate_openreview_exports(self):
        with tempfile.TemporaryDirectory(prefix="mstar_coco_source_") as directory:
            root = Path(directory)
            coco_root = root / "coco"
            benchmark_root = coco_root / "benchmark"
            markdown_root = benchmark_root / "MD"
            markdown_root.mkdir(parents=True)
            records = [
                self._record("a0", "Source A", "work-a"),
                self._record("b0", "Source B", "work-b"),
            ]
            for record in records:
                record["MD_path"] = f"benchmark/MD/{record['id']}.md"
                (markdown_root / f"{record['id']}.md").write_text(
                    f"# {record['id']}\nPaper body only.", encoding="utf-8"
                )
            benchmark_path = benchmark_root / "benchmark_fixture.jsonl"
            benchmark_path.write_text(
                "".join(json.dumps(value) + "\n" for value in records),
                encoding="utf-8",
            )
            manifest_path = self._temporal_manifest(
                root,
                ["a0"],
                ["b0"],
                source_asset="benchmark/benchmark_fixture.jsonl",
            )
            official = {
                "b0": {
                    "category_correct": {
                        task_id + "-FIX": [f"official {task_id} reference"]
                        for task_id in SPECIALIST_TASKS
                    }
                }
            }
            provenance = {
                "implementation": (
                    "CoCoReviewBench.src.evaluation._4_evaluation."
                    "load_human_review_data"
                ),
                "requested_paper_count": 1,
            }
            with patch(
                "memory_evolution.agentreview_data.load_official_coco_human_references",
                return_value=(official, provenance),
            ) as loader:
                prepared = prepare_openreview_data(
                    str(root / "nonexistent_openreview"),
                    str(root / "prepared"),
                    str(manifest_path),
                    coco_root=str(coco_root),
                    validation_families=1,
                    meta_validation_families=0,
                )
            loader.assert_called_once()
            self.assertEqual(prepared["source"], [str(benchmark_path.resolve())])
            self.assertEqual(
                prepared["candidate_paper_source"]["projection"],
                "manifest identity + paper Markdown only; no benchmark human "
                "field is copied or stripped",
            )


if __name__ == "__main__":
    unittest.main()
