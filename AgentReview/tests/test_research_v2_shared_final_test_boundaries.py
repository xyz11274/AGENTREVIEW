"""Exercise final-test boundaries through the concrete backend and artifacts."""

from contextlib import contextmanager
import json

import pytest

import test_research_v2 as review_fixtures
from test_research_v2_shared_coco_integration import backend, install_model_scores, recipe
from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2 import shared_experiment as shared
from agentreview.research_v2.constants import ROLES
from agentreview.research_v2.experiment_protocol import FrozenExperimentProtocol
from agentreview.research_v2.experiment_report import generate_experiment_report
from agentreview.research_v2.failure_policy import FailureController, EvolutionPaused, EvolutionBudgetExhausted
from agentreview.research_v2.io import atomic_write_json, file_sha256, read_json


def freeze_candidate(backend, monkeypatch):
    """Construct real populated role-local development Memory snapshots."""
    install_model_scores(monkeypatch)
    final_client(backend, monkeypatch)
    candidate_recipe = recipe(backend)
    candidate_recipe["memory_enabled"] = True
    development = backend.assess(candidate_recipe, epoch=0)
    backend.validate(development, recipe=candidate_recipe, epoch=0)
    snapshots = development["identity"]["snapshots"]
    assert set(snapshots) == set(ROLES)
    assert all(len(read_json(item["path"])["items"]) > 0 for item in snapshots.values())
    frozen = atomic_write_json(backend.root / "fixture-frozen.json", {"snapshots": snapshots})
    backend.frozen_system = shared.binding(frozen)
    backend.frozen_snapshots[candidate_recipe["candidate"]["candidate_id"]] = snapshots
    final_recipe = {**candidate_recipe, "key": "champion", "kind": "champion", "split": "sealed_test", "paper_ids": ["sealed_test-paper"]}
    return final_recipe, snapshots


def forbid_memory_build(backend, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Final test attempted to rebuild development Memory")

    monkeypatch.setattr(backend, "_bootstrap", forbidden)
    monkeypatch.setattr(shared, "build_memory_snapshot", forbidden)


def final_client(backend, monkeypatch):
    class MemoryCompletion(review_fixtures.FakeCompletion):
        def complete(self, messages, *, max_tokens, component):
            if component == "memory_summarizer" and "The Query must conform to this schema" in messages[-1]["content"]:
                self.calls.append({"messages": messages, "max_tokens": max_tokens, "component": component})
                return json.dumps({"query_text": "complete paper review experience", "reviewer_dimension": ""})
            return super().complete(messages, max_tokens=max_tokens, component=component)

    client = MemoryCompletion()

    @contextmanager
    def cached_client(directory):
        yield client

    monkeypatch.setattr(backend, "client", cached_client)
    return client


def test_sealed_assessment_requires_frozen_system_before_generation(backend, monkeypatch):
    calls = install_model_scores(monkeypatch)
    candidate_recipe = {**recipe(backend), "split": "sealed_test", "paper_ids": ["sealed_test-paper"]}
    with pytest.raises(ValueError, match="verified frozen system"):
        backend.assess(candidate_recipe, epoch=0)
    assert calls == []
    assert (backend.root / "frozen-test-generation").exists() is False


def test_sealed_reviews_reuse_all_five_frozen_memory_libraries_without_build(backend, monkeypatch):
    final_recipe, snapshots = freeze_candidate(backend, monkeypatch)
    forbid_memory_build(backend, monkeypatch)
    client = final_client(backend, monkeypatch)
    calls = install_model_scores(monkeypatch)
    before = {role: file_sha256(value["path"]) for role, value in snapshots.items()}
    result = backend.assess(final_recipe, epoch=0)
    backend.validate(result, recipe=final_recipe, epoch=0)
    assert result["identity"]["snapshots"] == snapshots
    assert {role: file_sha256(value["path"]) for role, value in snapshots.items()} == before
    assert len({value["path"] for value in snapshots.values()}) == 5
    assert calls == [(0, "final"), *((0, f"role:{role}") for role in ROLES)]
    assert len([call for call in client.calls if call["component"].startswith("specialist_review") or call["component"] == "meta_synthesis"]) == 6


def test_scoring_recovery_reuses_complete_frozen_test_reviews_in_fresh_epoch(backend, monkeypatch):
    final_recipe, snapshots = freeze_candidate(backend, monkeypatch)
    forbid_memory_build(backend, monkeypatch)
    client = final_client(backend, monkeypatch)
    install_model_scores(monkeypatch, fail_role="final")
    with pytest.raises(coco.DevelopmentCoCoFailure, match="Controlled judge"):
        backend.assess(final_recipe, epoch=0)
    generation = backend.root / "frozen-test-generation" / "champion"
    final_path = generation / "final_reviews/champion/sealed_test-paper.json"
    score_failure = backend.root / "assessments/e0/champion/r0/coco/assessment.json"
    assert read_json(score_failure)["status"] == "paused"
    generated_hash = file_sha256(final_path)
    calls_before = len(client.calls)
    review_paths = {role: generation / "reviews/champion/sealed_test-paper" / f"{role}.json" for role in ROLES}
    review_hashes = {role: file_sha256(path) for role, path in review_paths.items()}
    calls = install_model_scores(monkeypatch)
    repaired = backend.assess(final_recipe, epoch=1)
    backend.validate(repaired, recipe=final_recipe, epoch=1)
    assert len(client.calls) == calls_before
    assert file_sha256(final_path) == generated_hash
    assert {role: file_sha256(path) for role, path in review_paths.items()} == review_hashes
    assert repaired["identity"]["snapshots"] == snapshots
    assert repaired["evaluation_epoch"] == 1
    assert calls == [(1, "final"), *((1, f"role:{role}") for role in ROLES)]
    receipt = read_json(repaired["assessment"]["path"])
    assert receipt["rows"][0]["review"]["sha256"] == generated_hash
    assert read_json(score_failure)["status"] == "paused"


@pytest.mark.parametrize("policy_update", [
    {"status": "paused"},
    {"status": "ended"},
    {"pending_candidates": ["awaiting-reassessment"]},
    {"evaluation_epoch": 1},
])
def test_live_development_promotion_gate_precedes_every_sealed_asset_read(backend, monkeypatch, policy_update):
    controller = FailureController(backend.root / "failure-policy.json")
    frozen = backend.freeze({"key": "winner", "evaluation_epoch": 0}, {"recipes": {}, "results": {}})
    frozen_path = atomic_write_json(backend.root / "winner.json", frozen)
    controller.value.update(policy_update)
    controller.save()

    def forbidden(*args, **kwargs):
        raise AssertionError("Promotion failure opened sealed assets")

    monkeypatch.setattr(FrozenExperimentProtocol, "paper_paths", forbidden)
    monkeypatch.setattr(FrozenExperimentProtocol, "validate_final_test_assets", forbidden)
    with pytest.raises((EvolutionPaused, EvolutionBudgetExhausted, ValueError), match="Promotion|current verified"):
        shared.run_shared_final_test(protocol_path=backend.protocol.path,
            output_root=backend.root.parent / "final-test", frozen_system_path=frozen_path)


def test_concrete_backend_report_index_generates_seed_champion_and_role_tables(backend, monkeypatch):
    install_model_scores(monkeypatch)
    candidate_recipe = recipe(backend)
    result = backend.assess(candidate_recipe, epoch=0)
    winner = {**candidate_recipe["candidate"], **result, "key": candidate_recipe["key"]}
    state = {"recipes": {candidate_recipe["key"]: candidate_recipe}, "results": {candidate_recipe["key"]: result}}
    state["winner"] = backend.freeze(winner, state)
    backend.write_report_input(state)
    manifest = backend.root / "report-input.json"
    report_result = generate_experiment_report(manifest, backend.root.parent / "independent-report")
    report = read_json(report_result["paths"]["report.json"])
    assert {row["kind"] for row in report["variants"]} == {"seed", "champion"}
    assert {row["purpose"] for row in report["variants"]} == {"selection"}
    assert len(report["role_diagnostics"]) == 10
    assert all(row["aggregate"]["absolute_scores"]["Paper."] == 2.0 for row in report["variants"])
    assert all(row["original_comparison"]["status"] == "equal" for row in report["variants"])
