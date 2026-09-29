"""Concrete shared backend, real memory/reviews and complete CoCo receipts."""

from contextlib import contextmanager
from pathlib import Path
import copy
import json
import threading

import pytest

import test_research_v2_experiment_protocol as protocol_fixtures
import test_research_v2 as review_fixtures
from test_research_v2_development_coco import fixture_integrity
from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.io import atomic_write_json, read_json, write_immutable_jsonl
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.shared_experiment import LocalSharedBackend, binding


def test_composed_receipt_checks_child_evidence_and_rejects_rehashed_tampering(backend, monkeypatch):
    from agentreview.research_v2.cohort_receipt import compose
    from agentreview.research_v2.io import value_sha256
    install_model_scores(monkeypatch)
    candidate_recipe = recipe(backend)
    result = backend.assess(candidate_recipe, epoch=0)
    child = read_json(result["assessment"]["path"])
    receipt = compose(children=[result["assessment"]], review_manifest=Path(child["inputs"]["reviews"]["path"]),
        candidate_identity=result["identity"], output=backend.root / "composed")
    coco.validate_assessment_receipt(receipt, expected_paper_ids=candidate_recipe["paper_ids"])
    for mutate in (
        lambda value: value.update(mean_score=4),
        lambda value: value["candidate_identity"].update(memory_enabled=True),
        lambda value: value.update(children=[]),
        lambda value: value["rows"][0]["metrics"].update({"Paper.": 5}),
    ):
        invalid = copy.deepcopy(receipt)
        mutate(invalid)
        invalid["receipt_id"] = value_sha256({k:v for k,v in invalid.items() if k != "receipt_id"})
        with pytest.raises(ValueError):
            coco.validate_assessment_receipt(invalid)


def test_two_papers_overlap_coco_without_sharing_role_state(backend, monkeypatch):
    from agentreview.research_v2 import shared_experiment as shared
    row = backend.protocol.records("static")[0]
    second = copy.deepcopy(row)
    second["paper_id"] = "second-paper"
    paper = read_json(row["assets"]["paper"]["path"])
    paper.update(paper_id="second-paper", title="Second independent paper")
    second["assets"]["paper"] = binding(atomic_write_json(backend.root / "second.json", paper))
    ids = [row["paper_id"], second["paper_id"]]
    benchmark = write_immutable_jsonl(backend.root / "benchmark.jsonl", [{"id": pid} for pid in ids])
    human = write_immutable_jsonl(backend.root / "human.jsonl", [{"id": pid} for pid in ids])
    selection = atomic_write_json(backend.root / "selection.json", dict.fromkeys(ids, "Reviewer 1"))
    reference = atomic_write_json(backend.root / "references.json", {"protocol": coco.REFERENCE_MANIFEST_PROTOCOL,
        "partition": "static", "paper_ids": ids, "benchmark_files": [binding(benchmark)],
        "complete_human_classify_file": binding(human), "reviewer_selection_file": binding(selection)})
    for record in (row, second):
        record["assets"]["coco_reference"] = binding(reference)
    backend.protocol.splits["static"] = [row, second]
    backend.execution = {"paper_workers": 2}
    original_review, original_meta = shared.run_specialist_review, shared.generate_final_review
    barrier = threading.Barrier(2)
    first_scored = threading.Event()
    calls = install_model_scores(monkeypatch)
    original_stage = coco._run_stage
    def review(**kwargs):
        if kwargs["role"] == "QUAL":
            barrier.wait(timeout=10)
        return original_review(**kwargs)
    def meta(**kwargs):
        if read_json(kwargs["paper_path"])["paper_id"] == "second-paper":
            assert first_scored.wait(timeout=20), "CoCo must start before all paper generation completes"
        return original_meta(**kwargs)
    def stage(**kwargs):
        result = original_stage(**kwargs)
        if kwargs["stage"] == "evaluation":
            first_scored.set()
        return result
    monkeypatch.setattr(shared, "run_specialist_review", review)
    monkeypatch.setattr(shared, "generate_final_review", meta)
    monkeypatch.setattr(coco, "_run_stage", stage)
    candidate_recipe = recipe(backend)
    candidate_recipe["paper_ids"] = ids
    result = backend.assess(candidate_recipe, epoch=0)
    receipt = backend.validate(result, recipe=candidate_recipe, epoch=0)
    assert len(receipt["children"]) == 2
    assert calls == [(0, "final"), (0, "final")]
    for role in ROLES:
        refs = result["identity"]["role_reviews"][role]
        assert [ref["paper_id"] for ref in refs] == ids
        assert len({ref["path"] for ref in refs}) == 2
        assert len({read_json(ref["path"])["memory_snapshot_id"] for ref in refs}) == 1


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
        model_dir = fixture.root / "model"
        model_dir.mkdir()
        (model_dir / "weights").write_bytes(b"test frozen model")
        from tokenizers import Tokenizer, models, pre_tokenizers
        tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.save(str(model_dir / "tokenizer.json"))
        fixture.config["model"]["tokenizer_sha256"] = binding(model_dir / "tokenizer.json")["sha256"]
        atomic_write_json(scoring, {"source_files": [], "coco_root": "/data0/xyj/shengao/CoCoReviewBench",
            "bert_score_model": coco._model_identity(model_dir),
            "services": {"judge": {"model_identity": coco._model_identity(model_dir)}}}, immutable=False)
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
            "integrity": fixture_integrity(output, stage, request["paper_ids"]),
            "papers": {paper: {"table_1_summary": {"absolute_scores": metrics, "paper_counts": counts},
                "paper_judge_scores": {"success": True, "average": score}, "category_judge_scores": {}}
                for paper in request["paper_ids"]}}
        return atomic_write_json(output / f"{stage}.raw.json", raw)
    monkeypatch.setattr(coco, "_run_stage", stage)
    return calls


def recipe(backend):
    return {"key": "seed-vector_search", "candidate": backend.seed("vector_search"), "kind": "seed", "split": "static",
            "paper_ids": ["static-paper"], "memory_enabled": False}


def test_shared_backend_scores_only_final_review_with_independent_role_memory(backend, monkeypatch):
    calls = install_model_scores(monkeypatch)
    candidate_recipe = recipe(backend)
    result = backend.assess(candidate_recipe, epoch=0)
    receipt = backend.validate(result, recipe=candidate_recipe, epoch=0)
    assert result["static_score"] == 2.0
    assert receipt["review_target"] == "final"
    assert result["role_diagnostics"] == {}
    assert calls == [(0, "final")]
    assert set(result["identity"]["role_reviews"]) == set(ROLES)
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
    assert len(calls) == 2
    with pytest.raises(ValueError, match="epoch"):
        backend.validate(first, recipe=candidate_recipe, epoch=1)


def test_role_scoring_is_never_called(backend, monkeypatch):
    calls = install_model_scores(monkeypatch, fail_role="role:QUAL")
    candidate_recipe = recipe(backend)
    result = backend.assess(candidate_recipe, epoch=0)
    assert backend.validate(result, recipe=candidate_recipe, epoch=0)["mean_score"] == 2.0
    assert calls == [(0, "final")]
    assert not list(backend.root.rglob("role-coco"))


@pytest.mark.parametrize("rotating_count", [1, 5])
def test_current_parent_feedback_uses_its_existing_role_memory(backend, monkeypatch, rotating_count):
    install_model_scores(monkeypatch)
    template = backend.protocol.records("rotating")[0]
    ids = [f"rotating-paper-{index}" for index in range(rotating_count)]
    rows = []
    reference_root = backend.root / "five-rotating"
    benchmark = write_immutable_jsonl(reference_root / "benchmark.jsonl", [{"id": pid} for pid in ids])
    human = write_immutable_jsonl(reference_root / "human.jsonl", [{"id": pid} for pid in ids])
    selection = atomic_write_json(reference_root / "selection.json", dict.fromkeys(ids, "Reviewer 1"))
    reference = atomic_write_json(reference_root / "references.json", {"protocol": coco.REFERENCE_MANIFEST_PROTOCOL,
        "partition":"rotating", "paper_ids":ids, "benchmark_files":[binding(benchmark)],
        "complete_human_classify_file":binding(human), "reviewer_selection_file":binding(selection)})
    for pid in ids:
        row = copy.deepcopy(template)
        row["paper_id"] = pid
        paper = read_json(template["assets"]["paper"]["path"])
        paper["paper_id"] = pid
        row["assets"].update(paper=binding(atomic_write_json(reference_root / f"{pid}.json", paper)), coco_reference=binding(reference))
        rows.append(row)
    backend.protocol.splits["rotating"] = rows
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
    rotating = {**static, "key": "feedback-0001-e0", "kind": "parent_feedback", "split": "rotating", "paper_ids": ids}
    second = backend.assess(rotating, epoch=0)
    backend.validate(second, recipe=rotating, epoch=0)
    assert first["identity"]["snapshots"] == second["identity"]["snapshots"]
    assert first["identity"]["role_reviews"] != second["identity"]["role_reviews"]
    assert first["assessment"] != second["assessment"]
    prompts = []
    class Reflector:
        def count_chat_tokens(self, messages):
            return {"input_tokens": len(messages[0]["content"]) // 4, "max_model_len": 131072}
        def complete(self, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return json.dumps({"change": "Use rotating diagnosis in the memory instruction.",
                "program": seed_program_path("vector_search").read_text().replace(
                    "Ground every judgment in the Frozen Paper.", "Verify each recalled heuristic against the Frozen Paper.")})
    @contextmanager
    def reflector(directory):
        yield Reflector()
    monkeypatch.setattr(backend, "client", reflector)
    backend.generation["input_max_tokens"] = 114688
    selected = ids[:min(2, rotating_count)]
    backend.config["evolution"]["detailed_reflection_cases"] = len(selected)
    proposal = backend.propose(parent=static["candidate"], feedback={**second,
        "reflection_case_ids":selected, "reflection_selection":{"rule":"frozen test tie-break"}},
        history={"0001": {"change": "COMPLETE_PRIOR_CHANGE"}}, directory=backend.root / "reflection")
    assert len(prompts) == 1 and "COMPLETE_PRIOR_CHANGE" in prompts[0]
    reflection_input = read_json(backend.root / "reflection/reflection-input.json")
    full_feedback = read_json(backend.root / "reflection/feedback.json")
    assert len(full_feedback["current_rotating_feedback"]) == rotating_count
    assert len(reflection_input["rotating_overview"]) == rotating_count
    assert reflection_input["required_case_ids"] == selected
    assert [row["paper_id"] for row in reflection_input["current_rotating_feedback"]] == selected
    assert reflection_input["current_rotating_feedback"][0]["metrics"]["Paper."] == 2.0
    for role in ROLES:
        assert len(full_feedback["trajectories"][role]) == rotating_count
        assert [row["paper_id"] for row in reflection_input["trajectories"][role]] == selected
        review = read_json(second["identity"]["role_reviews"][role][0]["path"])
        assert review["review_text"] == reflection_input["trajectories"][role][0]["review_text"]
    assert "Verify each recalled heuristic" in Path(proposal["program"]["path"]).read_text()
    with pytest.raises(ValueError, match="rotating"):
        backend.propose(parent=static["candidate"], feedback=first, history={}, directory=backend.root / "rejected-static")
