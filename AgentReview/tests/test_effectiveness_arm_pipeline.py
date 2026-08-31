import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreview.reviewer_identity import META_REVIEWER_NAME
from effectiveness_screening.arms import ARM_SCHEMA_VERSION
from effectiveness_screening.evaluate_proxy import (
    blind_judge_outputs,
    build_blind_pairs,
    classify_outputs,
    judge_outputs,
)
from effectiveness_screening.prepare import DESIGN_VERSION, iter_jsonl, write_jsonl
from effectiveness_screening.summarize import PRIMARY_METRICS, summarize

LINEAGES = ("vector_search", "llm_summarizer", "experience_learner")
ARM_CASES = (
    ("no_memory", "no_memory", None),
    ("seed_vector_search", "seed", "vector_search"),
    ("seed_llm_summarizer", "seed", "llm_summarizer"),
    ("seed_experience_learner", "seed", "experience_learner"),
    ("evolved_from_vector_search", "evolved", "vector_search"),
    ("evolved_from_llm_summarizer", "evolved", "llm_summarizer"),
    ("evolved_from_experience_learner", "evolved", "experience_learner"),
)


def screening_config(output_root: Path):
    return {
        "arm_schema_version": ARM_SCHEMA_VERSION,
        "output_root": str(output_root),
        "frozen_s2_pool": str(output_root / "frozen_s2_temporal_v2.jsonl"),
        "frozen_s2_pool_version": "frozen-s2-temporal-v2",
        "memory_embedding": {
            "mode": "frozen",
            "adapter": "fake_embedding:create",
            "model_name": "fake-embedding",
            "revision": "fake-revision",
            "fingerprint": "fake-fingerprint",
        },
        "external_roots": {"cocoreviewbench": str(output_root / "coco")},
        "generation": {
            "model": "gpt-5-mini",
            "temperature": 0.3,
            "review_max_tokens": 8192,
            "review_retry_max_tokens": 16384,
            "reviewer_reasoning_effort": "low",
            "meta_review_max_tokens": 4096,
        },
        "experiment": {
            "arms": ["no_memory", "seed", "evolved"],
            "lineages": list(LINEAGES),
            "replicates": [1, 2],
            "replicate_run_seeds": {"1": 101, "2": 202},
        },
        "metrics": {
            "review_split_revision": "split-revision",
            "review_classify_revision": "classify-revision",
            "judge_model": "judge-model",
        },
        "sampling": {"papers_per_stratum": 5},
        "screening_gate": {
            "delta_CoCo_Average_proxy_min": 0.1,
            "positive_papers_min": 12,
            "paired_bootstrap_confidence": 0.8,
            "paired_bootstrap_lower_bound_min": 0.0,
            "delta_Correctness_proxy_min": 0.0,
            "delta_Reference_category_recall_pp_min": -2.0,
            "delta_Complete_official_pp_min": -2.0,
            "delta_META_Average_min": -0.1,
            "blind_judge_evolved_win_rate_min": 0.6,
            "blind_judge_decisive_pairs_min": 20,
        },
    }


class EffectivenessArmPipelineTest(unittest.TestCase):
    def test_classification_writes_one_file_per_lineage_arm(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            write_jsonl(
                output_root / "effectiveness_validation.jsonl",
                [{"paper_id": "paper-1"}],
            )
            with (
                patch("effectiveness_screening.evaluate_proxy.FrozenCoCoAdapter"),
                patch(
                    "effectiveness_screening.evaluate_proxy._load_reviews",
                    return_value={"reviewer": "review"},
                ),
                patch(
                    "effectiveness_screening.evaluate_proxy._build_candidate_categories",
                    return_value={"QUAL-MET": ["opinion"]},
                ),
            ):
                result = classify_outputs(config)

            self.assertEqual(result["classified_cells"], 14)
            for replicate in (1, 2):
                classified_root = (
                    output_root / "evaluation" / "classified" / f"replicate_{replicate}"
                )
                self.assertEqual(
                    {path.stem for path in classified_root.glob("*.jsonl")},
                    {case[0] for case in ARM_CASES},
                )
                for arm_id, arm_kind, lineage in ARM_CASES:
                    record = next(iter_jsonl(classified_root / f"{arm_id}.jsonl"))
                    self.assertEqual(record["arm_id"], arm_id)
                    self.assertEqual(record["arm_kind"], arm_kind)
                    self.assertEqual(record["lineage"], lineage)
                    self.assertEqual(
                        record["classifier_provenance"],
                        {
                            "review_split_revision": "split-revision",
                            "review_classify_revision": "classify-revision",
                        },
                    )

    def test_judging_writes_one_file_per_lineage_arm(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            write_jsonl(
                output_root / "evaluation_references_proxy.jsonl",
                [
                    {
                        "paper_id": "paper-1",
                        "metric_status": "CoCo-style proxy reference",
                        "category_reference_by_tag": {"QUAL-MET": "reference opinion"},
                        "meta_reference": "reference",
                    }
                ],
            )
            for replicate in (1, 2):
                for arm_id, arm_kind, lineage in ARM_CASES:
                    write_jsonl(
                        output_root
                        / "evaluation"
                        / "classified"
                        / f"replicate_{replicate}"
                        / f"{arm_id}.jsonl",
                        [
                            {
                                "design_version": DESIGN_VERSION,
                                "arm_schema_version": ARM_SCHEMA_VERSION,
                                "paper_id": "paper-1",
                                "replicate": replicate,
                                "arm_id": arm_id,
                                "arm_kind": arm_kind,
                                "lineage": lineage,
                                "review_package_sha256": f"sha-{arm_id}",
                                "candidate_category_texts": {
                                    "QUAL-MET": ["candidate opinion"]
                                },
                            }
                        ],
                    )

            class Judge:
                def llm_completion(self, *_args, **_kwargs):
                    if _kwargs.get("agent_name") == "CoCo Proxy Judge":
                        return (
                            '{"claim_extraction": "ok", "actionability": 4, '
                            '"grounding_specificity": 4, "verifiability": 4, '
                            '"alignment": 4, "completeness": 4, "clarity": 4}'
                        )
                    return (
                        '{"adjudication_clarity": 4, "groundedness": 4, '
                        '"verifiability": 4, "conflict_coverage": 4}'
                    )

            with (
                patch(
                    "effectiveness_screening.evaluate_proxy._judge_backend",
                    return_value=Judge(),
                ),
                patch(
                    "effectiveness_screening.evaluate_proxy._official_judge_prompt",
                    return_value=("prompt", "prompt-sha"),
                ),
                patch(
                    "effectiveness_screening.evaluate_proxy._load_reviews",
                    return_value={META_REVIEWER_NAME: "review"},
                ),
                patch(
                    "effectiveness_screening.evaluate_proxy._meta_prompt",
                    return_value="meta prompt",
                ),
            ):
                result = judge_outputs(config)

            self.assertEqual(result["judged_cells"], 14)
            for replicate in (1, 2):
                judged_root = (
                    output_root / "evaluation" / "judged" / f"replicate_{replicate}"
                )
                self.assertEqual(
                    {path.stem for path in judged_root.glob("*.jsonl")},
                    {case[0] for case in ARM_CASES},
                )
                for arm_id, _, _ in ARM_CASES:
                    record = next(iter_jsonl(judged_root / f"{arm_id}.jsonl"))
                    self.assertEqual(record["arm_id"], arm_id)
                    self.assertEqual(
                        record["evaluated_review_package_sha256"],
                        f"sha-{arm_id}",
                    )

    def test_blind_pairs_cover_each_lineage_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            write_jsonl(
                output_root / "effectiveness_validation.jsonl",
                [{"paper_id": "paper-1"}, {"paper_id": "paper-2"}],
            )

            def load_reviews(output_dir, paper_id):
                return {
                    "source": (f"{output_dir.parent.name}/{output_dir.name}/{paper_id}")
                }

            with patch(
                "effectiveness_screening.evaluate_proxy._load_reviews",
                side_effect=load_reviews,
            ):
                result = build_blind_pairs(config)

            self.assertEqual(result["pair_count"], 12)
            public = list(iter_jsonl(output_root / "evaluation" / "blind_pairs.jsonl"))
            sealed = list(
                iter_jsonl(output_root / "evaluation" / "blind_pairs_key.jsonl")
            )
            self.assertEqual(len({item["pair_id"] for item in public}), 12)
            self.assertEqual({item["lineage"] for item in public}, set(LINEAGES))
            self.assertEqual({item["lineage"] for item in sealed}, set(LINEAGES))
            for item in public:
                self.assertEqual(item["seed_arm_id"], f"seed_{item['lineage']}")
                self.assertEqual(
                    item["evolved_arm_id"], f"evolved_from_{item['lineage']}"
                )
                self.assertIn("/holdout/", item["review_package_A"]["source"])
                self.assertIn("/holdout/", item["review_package_B"]["source"])

    def test_blind_judge_writes_strict_traceable_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            write_jsonl(
                output_root / "evaluation" / "blind_pairs.jsonl",
                [
                    {
                        "arm_schema_version": ARM_SCHEMA_VERSION,
                        "pair_id": "pair-1",
                        "comparison_id": (
                            "evolved_from_vector_search_minus_seed_vector_search"
                        ),
                        "paper_id": "paper-1",
                        "replicate": 1,
                        "lineage": "vector_search",
                        "seed_arm_id": "seed_vector_search",
                        "evolved_arm_id": "evolved_from_vector_search",
                        "review_package_A": {"Reviewer QUAL": "A"},
                        "review_package_B": {"Reviewer QUAL": "B"},
                        "judge_instruction": "Blindly compare A and B.",
                    }
                ],
            )

            class Judge:
                def llm_completion(self, messages, **kwargs):
                    self.messages = messages
                    self.agent_name = kwargs["agent_name"]
                    return '{"winner":"A","justification":"A is more grounded."}'

            judge = Judge()
            with patch(
                "effectiveness_screening.evaluate_proxy._judge_backend",
                return_value=judge,
            ):
                result = blind_judge_outputs(config)

            self.assertEqual(result["judged_pairs"], 1)
            record = next(
                iter_jsonl(output_root / "evaluation" / "blind_judge_results.jsonl")
            )
            self.assertEqual(
                set(record),
                {
                    "design_version",
                    "arm_schema_version",
                    "pair_id",
                    "comparison_id",
                    "paper_id",
                    "replicate",
                    "lineage",
                    "blind_input_sha256",
                    "judge_model",
                    "judge_temperature",
                    "winner",
                    "justification",
                },
            )
            self.assertEqual(record["winner"], "A")
            self.assertEqual(judge.agent_name, "Effectiveness Blind Judge")

    def test_blind_judge_rejects_non_strict_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            write_jsonl(
                output_root / "evaluation" / "blind_pairs.jsonl",
                [
                    {
                        "arm_schema_version": ARM_SCHEMA_VERSION,
                        "pair_id": "pair-1",
                        "comparison_id": "comparison",
                        "paper_id": "paper-1",
                        "replicate": 1,
                        "lineage": "vector_search",
                        "seed_arm_id": "seed_vector_search",
                        "evolved_arm_id": "evolved_from_vector_search",
                        "review_package_A": {},
                        "review_package_B": {},
                        "judge_instruction": "Compare.",
                    }
                ],
            )

            class Judge:
                def llm_completion(self, *_args, **_kwargs):
                    return '{"winner":"seed","justification":"guess"}'

            with (
                patch(
                    "effectiveness_screening.evaluate_proxy._judge_backend",
                    return_value=Judge(),
                ),
                self.assertRaisesRegex(ValueError, "winner"),
            ):
                blind_judge_outputs(config)

    def test_summary_preserves_distinct_lineage_deltas(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            config = screening_config(output_root)
            papers = [f"paper-{index:02d}" for index in range(20)]
            write_jsonl(
                output_root / "effectiveness_validation.jsonl",
                [{"paper_id": paper_id} for paper_id in papers],
            )
            values = {
                "no_memory": 1.0,
                "seed_vector_search": 2.0,
                "evolved_from_vector_search": 3.0,
                "seed_llm_summarizer": 2.0,
                "evolved_from_llm_summarizer": 4.0,
                "seed_experience_learner": 3.0,
                "evolved_from_experience_learner": 2.0,
            }
            for replicate in (1, 2):
                for arm_id, arm_kind, lineage in ARM_CASES:
                    metric_value = values[arm_id]
                    write_jsonl(
                        output_root
                        / "evaluation"
                        / "judged"
                        / f"replicate_{replicate}"
                        / f"{arm_id}.jsonl",
                        [
                            {
                                "design_version": DESIGN_VERSION,
                                "arm_schema_version": ARM_SCHEMA_VERSION,
                                "metric_status": (
                                    "CoCo-style proxy screening; not official benchmark"
                                ),
                                "paper_id": paper_id,
                                "replicate": replicate,
                                "arm_id": arm_id,
                                "arm_kind": arm_kind,
                                "lineage": lineage,
                                "paper_metrics": {
                                    metric: metric_value for metric in PRIMARY_METRICS
                                },
                            }
                            for paper_id in papers
                        ],
                    )

            keys = []
            results = []
            for lineage in LINEAGES:
                for replicate in (1, 2):
                    for paper_id in papers:
                        pair_id = f"{lineage}-{replicate}-{paper_id}"
                        keys.append(
                            {
                                "arm_schema_version": ARM_SCHEMA_VERSION,
                                "pair_id": pair_id,
                                "comparison_id": (
                                    f"evolved_from_{lineage}_minus_seed_{lineage}"
                                ),
                                "paper_id": paper_id,
                                "replicate": replicate,
                                "lineage": lineage,
                                "seed_arm_id": f"seed_{lineage}",
                                "evolved_arm_id": f"evolved_from_{lineage}",
                                "evolved_position": "A",
                            }
                        )
                        results.append({"pair_id": pair_id, "winner": "A"})
            write_jsonl(output_root / "evaluation" / "blind_pairs_key.jsonl", keys)
            write_jsonl(
                output_root / "evaluation" / "blind_judge_results.jsonl", results
            )

            report = summarize(config)

            self.assertEqual(set(report["arm_summary"]), set(values))
            expected_deltas = {
                "vector_search": 1.0,
                "llm_summarizer": 2.0,
                "experience_learner": -1.0,
            }
            for lineage, expected in expected_deltas.items():
                lineage_report = report["lineages"][lineage]
                self.assertEqual(
                    lineage_report["paired_deltas"]["evolved_minus_seed"][
                        "CoCo_Average_proxy"
                    ]["mean_delta"],
                    expected,
                )
                self.assertEqual(lineage_report["blind_judge"]["expected_pairs"], 40)


if __name__ == "__main__":
    unittest.main()
