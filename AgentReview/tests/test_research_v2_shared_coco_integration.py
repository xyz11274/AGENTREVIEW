"""Concrete shared backend, real memory/reviews and complete CoCo receipts."""

from contextlib import contextmanager
from pathlib import Path

import pytest

import test_research_v2_experiment_protocol as protocol_fixtures
import test_research_v2 as review_fixtures
from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.io import atomic_write_json, read_json, write_immutable_jsonl
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding


@pytest.fixture
def backend(monkeypatch):
    fixture = protocol_fixtures.ExperimentProtocolTest()
    fixture.setUp()
    try:
        fixture.config["non_memory_conditions"]["review_policy_program"] = binding(seed_program_path("vector_search"))
        fixture.config["embedding"]["cache_path"] = str(fixture.root / "embedding.sqlite")
        for split in ("static", "rotating", "sealed_test"):
            paper_id = f"{split}-paper"
            root = fixture.root / split
            benchmark = write_immutable_jsonl(root / "benchmark.jsonl", [{"id": paper_id}])
            human = write_immutable_jsonl(root / "human.jsonl", [{"id": paper_id}])
            selection = atomic_write_json(root / "selection.json", {paper_id: "Reviewer 1"})
            reference = root / "reference.json"
            atomic_write_json(reference, {"protocol": coco.REFERENCE_MANIFEST_PROTOCOL, "partition": split,
                "paper_ids": [paper_id], "benchmark_files": [binding(benchmark)],
                "complete_human_classify_file": binding(human), "reviewer_selection_file": binding(selection)}, immutable=False)
            fixture.mutate_row(split, lambda row: row["assets"].update(coco_reference=binding(reference)))
        scoring = fixture.root / "scoring.json"
        atomic_write_json(scoring, {"source_files": [], "coco_root": "/data0/xyj/shengao/CoCoReviewBench"}, immutable=False)
        fixture.config["scoring_config"] = binding(scoring)
        fixture.rewrite_config()
        protocol = load_experiment_protocol(fixture.config_path)
        result = LocalSharedBackend(protocol=protocol, output_root=fixture.root / "run")
        result.require_bubblewrap = False
        result.embedding_client = lambda texts: [[1.0, 0.0] for _ in texts]
        @contextmanager
        def client(directory):
            yield review_fixtures.FakeCompletion()
        monkeypatch.setattr(result, "client", client)
        monkeypatch.setattr(coco, "_validate_config", lambda value: None)
        yield result
    finally:
        fixture.tearDown()


def install_model_scores(monkeypatch, *, fail_role=None):
    calls = []
    def stage(*, stage, request_path, output, config):
        request = read_json(request_path)
        if stage == "evaluation":
            calls.append((request["evaluation_epoch"], request["review_target"]))
        if stage == "evaluation" and request["review_target"] == fail_role:
            raise coco.DevelopmentCoCoFailure({"stage": "evaluation", "category": "shared_evaluation", "message": "Controlled judge transport failure"})
        score = 2.0 if request["review_target"] == "final" else 5.0
        metrics = dict.fromkeys(coco.COLUMNS, score)
        counts = dict.fromkeys(coco.COLUMNS, 1)
        rows = [{"status": "complete", "metrics": metrics, "metric_counts": counts} for _ in request["paper_ids"]]
        raw = {"request": binding(request_path), "paper_ids": request["paper_ids"], "table_1_summary": coco.aggregate_paper_scores(rows),
            "papers": {paper: {"table_1_summary": {"absolute_scores": metrics, "paper_counts": counts},
                "paper_judge_scores": {"success": True, "average": score}, "category_judge_scores": {}}
                for paper in request["paper_ids"]}}
        return atomic_write_json(output / f"{stage}.raw.json", raw)
    monkeypatch.setattr(coco, "_run_stage", stage)
    return calls


def recipe(backend):
    return {"key": "seed-vector_search", "candidate": backend.seed("vector_search"), "kind": "seed", "split": "static",
            "paper_ids": ["static-paper"], "memory_enabled": False}


def test_shared_backend_uses_final_paper_fitness_and_independent_role_receipts(backend, monkeypatch):
    calls = install_model_scores(monkeypatch)
    candidate_recipe = recipe(backend)
    result = backend.assess(candidate_recipe, epoch=0)
    receipt = backend.validate(result, recipe=candidate_recipe, epoch=0)
    assert result["static_score"] == 2.0
    assert receipt["review_target"] == "final"
    assert set(result["role_diagnostics"]) == set(ROLES)
    assert calls == [(0, "final"), *((0, f"role:{role}") for role in ROLES)]
    for role, artifact in result["role_diagnostics"].items():
        diagnostic = coco.validate_assessment_receipt(artifact["path"])
        assert diagnostic["mean_score"] == 5.0
        assert diagnostic["review_target"] == f"role:{role}"
    assert len({ref["path"] for ref in result["identity"]["snapshots"].values()}) == 5
    assert len({read_json(ref["path"])["program_sha256"] for ref in result["identity"]["snapshots"].values()}) == 1


def test_fresh_epoch_rebuilds_all_review_and_scoring_artifacts(backend, monkeypatch):
    calls = install_model_scores(monkeypatch)
    candidate_recipe = recipe(backend)
    first = backend.assess(candidate_recipe, epoch=0)
    second = backend.assess(candidate_recipe, epoch=1)
    backend.validate(second, recipe=candidate_recipe, epoch=1)
    assert first["assessment"]["path"] != second["assessment"]["path"]
    assert first["identity"]["snapshots"]["QUAL"]["path"] != second["identity"]["snapshots"]["QUAL"]["path"]
    assert len(calls) == 12
    with pytest.raises(ValueError, match="epoch"):
        backend.validate(first, recipe=candidate_recipe, epoch=1)


def test_role_scoring_failure_cannot_return_a_rankable_candidate(backend, monkeypatch):
    calls = install_model_scores(monkeypatch, fail_role="role:QUAL")
    candidate_recipe = recipe(backend)
    with pytest.raises(coco.DevelopmentCoCoFailure):
        backend.assess(candidate_recipe, epoch=0)
    main = backend.root / "assessments/e0/seed-vector_search/r0/coco/assessment.json"
    assert coco.validate_assessment_receipt(main)["mean_score"] == 2.0
    failed = read_json(backend.root / "assessments/e0/seed-vector_search/r0/role-coco/QUAL/assessment.json")
    assert failed["status"] == "paused" and failed["mean_score"] is None
    assert calls == [(0, "final"), (0, "role:QUAL")]


def test_current_parent_feedback_uses_its_existing_role_memory(backend, monkeypatch):
    install_model_scores(monkeypatch)
    class QueryCompletion(review_fixtures.FakeCompletion):
        def complete(self, messages, *, max_tokens, component, response_format=None):
            if response_format and response_format["json_schema"]["name"] == "Query":
                return '{"query_text":"current paper methods","reviewer_dimension":"QUAL"}'
            return super().complete(messages, max_tokens=max_tokens, component=component)
    @contextmanager
    def client(directory):
        yield QueryCompletion()
    monkeypatch.setattr(backend, "client", client)
    static = {**recipe(backend), "memory_enabled": True}
    first = backend.assess(static, epoch=0)
    rotating = {**static, "key": "feedback-0001-e0", "kind": "parent_feedback", "split": "rotating", "paper_ids": ["rotating-paper"]}
    second = backend.assess(rotating, epoch=0)
    backend.validate(second, recipe=rotating, epoch=0)
    assert first["identity"]["snapshots"] == second["identity"]["snapshots"]
    assert first["identity"]["role_reviews"] != second["identity"]["role_reviews"]
    assert first["assessment"] != second["assessment"]
