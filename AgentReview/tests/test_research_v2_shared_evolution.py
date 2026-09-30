from pathlib import Path
from types import SimpleNamespace

import pytest

from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_evolution import SharedEvolution, select_parent
from agentreview.research_v2.shared_experiment import CandidateContractError
from agentreview.research_v2.failure_policy import FailureController


def protocol(attempts=2, **limits):
    return SimpleNamespace(protocol_sha256="frozen", document={
        "failure_policy": {"candidate_repairs": 1, "round_repairs": 10, "candidate_failures": 5, **limits},
        "evolution": {"attempts": attempts, "rotating_sample_size": 1, "random_seed": 4, "parent_temperature": 1.0}},
        records=lambda split: [{"paper_id": split + "-one"}, {"paper_id": split + "-two"}])


class Backend:
    def __init__(self):
        self.calls = []
        self.proposals = []
        self.fail = None
        self.invalid = set()
        self.report_failure = False

    def preflight(self):
        pass

    def seed(self, name):
        return {"candidate_id": "seed-" + name, "seed": name, "seed_kind": name, "revision": 0, "lineage": []}

    def native_identity(self):
        return {"candidate_id": "coco-native", "seed": None}

    def assess(self, recipe, *, epoch):
        key = recipe["key"]
        self.calls.append((key, epoch, recipe["candidate"].get("revision", 0)))
        if self.fail and self.fail(key, epoch):
            raise OSError("Shared scoring unavailable")
        if key in self.invalid:
            raise CandidateContractError("KnowledgeBase read contract")
        return {"status": "complete", "static_score": 1.0 if recipe["kind"] == "candidate" else 4.0,
                "evaluation_epoch": epoch, "paper_ids": recipe["paper_ids"], "key": key}

    def validate(self, result, *, recipe, epoch):
        assert result["status"] == "complete"
        assert result["evaluation_epoch"] == epoch
        assert result["paper_ids"] == recipe["paper_ids"]
        assert result["key"] == recipe["key"]

    def propose(self, *, parent, feedback, history, directory):
        self.proposals.append((parent["candidate_id"], feedback, str(directory)))
        return {"seed": parent["seed"], "seed_kind": "evolved", "change": "new memory schema"}

    def freeze(self, winner, state):
        return {"status": "complete", "candidate_id": winner["candidate_id"]}

    def write_report_input(self, state):
        if self.report_failure:
            raise OSError("report index failed")


def run(tmp_path, backend, config=None, evidence=None):
    return SharedEvolution(protocol=config or protocol(), output_root=tmp_path, backend=backend).run(repair_evidence=evidence)


def test_shared_pool_retains_valid_low_score_children_and_scores_all_seed_controls(tmp_path):
    backend = Backend()
    result = run(tmp_path, backend)
    assert result["status"] == "complete"
    assert len(result["pool"]) == 5
    assert result["results"]["candidate-0001"]["static_score"] == 1
    assert len([name for name in result["recipes"] if name.startswith("no-memory-")]) == 3
    assert "coco-native" in result["results"]
    assert [key for key, epoch, revision in backend.calls if key.startswith("feedback-")] == ["feedback-0001-e0", "feedback-0002-e0"]
    assert len(backend.proposals) == 2
    calls = list(backend.calls)
    assert run(tmp_path, backend)["status"] == "complete"
    assert backend.calls == calls


def test_exhausted_upstream_repairs_do_not_restart_an_outer_repair_loop(tmp_path):
    from agentreview.research_v2.mstar_adapter import ReflectionFailure
    backend = Backend()
    calls = []
    def exhausted(**kwargs):
        calls.append(kwargs)
        raise ReflectionFailure("Native compile-fix loop exhausted", reflection={"path": "trace.json", "sha256": "trace"})
    backend.propose = exhausted
    result = run(tmp_path, backend, protocol(attempts=1))
    assert result["status"] == "complete"
    assert result["results"]["candidate-0001"]["status"] == "candidate_failed"
    assert len(calls) == 1
    assert result["results"]["candidate-0001"]["failure"]["reflection"]["path"] == "trace.json"
    assert read_json(tmp_path / "failure-policy.json")["repair_budget"]["used"] == 0


def test_candidate_failure_is_bounded_and_preserves_other_population(tmp_path):
    backend = Backend()
    backend.invalid.add("candidate-0001")
    result = run(tmp_path, backend, protocol(attempts=1))
    assert result["status"] == "complete"
    assert result["attempts"]["1"]["status"] == "candidate_failed"
    assert "candidate-0001" not in result["pool"]
    assert len([call for call in backend.calls if call[0] == "candidate-0001"]) == 2
    policy = read_json(tmp_path / "failure-policy.json")
    assert policy["repair_budget"]["used"] == 1
    assert policy["candidate_failure_budget"]["used"] == 1


def test_shared_fault_pauses_and_uniformly_reevaluates_candidates_and_controls(tmp_path):
    backend = Backend()
    backend.fail = lambda key, epoch: key == "candidate-0001" and epoch == 0
    result = run(tmp_path, backend, protocol(attempts=1))
    assert result["status"] == "paused"
    assert result["champion"] is not None
    assert (tmp_path / "winner.json").exists() is False
    before = list(backend.calls)
    assert run(tmp_path, backend, protocol(attempts=1))["status"] == "paused"
    assert backend.calls == before
    evidence = atomic_write_json(tmp_path / "repair.json", {"cause": "endpoint", "repair": "restored", "validation": "checked"})
    result = run(tmp_path, backend, protocol(attempts=1), evidence)
    assert result["status"] == "complete"
    static_keys = {key for key, recipe in result["recipes"].items() if recipe["split"] == "static"}
    assert static_keys <= {key for key, epoch, revision in backend.calls if epoch == 1}
    assert {result["results"][key]["evaluation_epoch"] for key in result["pool"]} == {1}


def test_round_budget_ends_without_publishing_new_winner(tmp_path):
    backend = Backend()
    backend.invalid.add("candidate-0001")
    result = run(tmp_path, backend, protocol(round_repairs=1))
    assert result["status"] == "ended"
    assert result["champion"] is not None
    assert result["winner"] is None
    assert (tmp_path / "winner.json").exists() is False


def test_recovery_budget_stops_later_model_dispatch(tmp_path):
    backend = Backend()
    backend.fail = lambda key, epoch: key == "candidate-0001" and epoch == 0
    settings = protocol(attempts=1, round_repairs=1)
    assert run(tmp_path, backend, settings)["status"] == "paused"
    calls = list(backend.calls)
    evidence = atomic_write_json(tmp_path / "repair.json", {"cause": "endpoint", "repair": "restored", "validation": "checked"})
    assert run(tmp_path, backend, settings, evidence)["status"] == "ended"
    assert backend.calls == calls


def test_incomplete_receipt_never_enters_pool(tmp_path):
    backend = Backend()
    original = backend.assess
    def incomplete(recipe, *, epoch):
        result = original(recipe, epoch=epoch)
        result["paper_ids"] = recipe["paper_ids"][:1]
        return result
    backend.assess = incomplete
    result = run(tmp_path, backend)
    assert result["status"] == "paused"
    assert result["pool"] == []
    assert result["winner"] is None


def test_report_index_failure_preserves_completed_experiment(tmp_path):
    backend = Backend()
    backend.report_failure = True
    assert run(tmp_path, backend, protocol(attempts=0))["status"] == "complete"
    assert (tmp_path / "winner.json").is_file()
    assert (tmp_path / "report-index.failure.json").is_file()


def test_softmax_includes_low_fitness_and_validates_scores():
    entries = [{"static_score": 1, "id": "low"}, {"static_score": 2, "id": "high"}]
    assert {select_parent(entries, temperature=1, random_seed=i)["id"] for i in range(100)} == {"low", "high"}
    with pytest.raises(ValueError):
        select_parent([{"static_score": float("nan")}], temperature=1, random_seed=0)


def pause_all(tmp_path):
    state = read_json(tmp_path / "search.json")
    keys = [key for key, recipe in state["recipes"].items() if recipe["split"] == "static"]
    FailureController(tmp_path / "failure-policy.json").pause(operation_id="controlled-pause", detail={"category": "shared_evaluation"}, affected=keys)


def test_pause_during_receipt_validation_keeps_candidate_out_of_pool(tmp_path):
    backend = Backend()
    original = backend.validate
    paused = False
    def validate(result, **kwargs):
        nonlocal paused
        original(result, **kwargs)
        if paused is False:
            paused = True
            pause_all(tmp_path)
    backend.validate = validate
    result = run(tmp_path, backend)
    assert result["status"] == "paused"
    assert result["pool"] == [] and result["champion"] is None
    assert (tmp_path / "winner.json").exists() is False


def test_pause_during_freeze_blocks_publication_and_preserves_champion(tmp_path):
    backend = Backend()
    original = backend.freeze
    def freeze(winner, state):
        pause_all(tmp_path)
        return original(winner, state)
    backend.freeze = freeze
    result = run(tmp_path, backend, protocol(attempts=0))
    assert result["status"] == "paused" and result["champion"] is not None
    assert result["winner"] is None and (tmp_path / "winner.json").exists() is False


def test_completed_resume_checks_controls_and_archives_invalidated_winner(tmp_path):
    backend = Backend()
    settings = protocol(attempts=0)
    first = run(tmp_path, backend, settings)
    original = backend.validate
    def validate(result, **kwargs):
        original(result, **kwargs)
        if kwargs["recipe"]["kind"] == "no_memory":
            raise ValueError("Frozen control scoring file changed")
    backend.validate = validate
    second = run(tmp_path, backend, settings)
    assert second["status"] == "paused" and second["winner"] is None
    assert second["winner_history"] == [first["winner"]]
    assert second["champion"] == first["champion"]


def test_repeated_proposal_contract_errors_consume_candidate_budget(tmp_path):
    backend = Backend()
    def propose(**kwargs):
        backend.proposals.append(kwargs)
        raise CandidateContractError("Reflection output requires a full MemoryProgram")
    backend.propose = propose
    result = run(tmp_path, backend, protocol(attempts=1))
    assert result["status"] == "complete"
    assert result["attempts"]["1"]["status"] == "candidate_failed"
    assert len(backend.proposals) == 2
    policy = read_json(tmp_path / "failure-policy.json")
    assert policy["repair_budget"]["used"] == 1
    assert policy["candidate_failure_budget"]["used"] == 1
    assert "candidate-0001" not in result["pool"]


def test_shared_proposal_service_error_pauses_without_candidate_repair(tmp_path):
    backend = Backend()
    def propose(**kwargs):
        raise TimeoutError("Local reflection service timeout")
    backend.propose = propose
    result = run(tmp_path, backend, protocol(attempts=1))
    policy = read_json(tmp_path / "failure-policy.json")
    assert result["status"] == "paused"
    assert policy["repair_budget"]["used"] == policy["candidate_failure_budget"]["used"] == 0


def test_recovery_history_keeps_the_original_recipe_before_runtime_repair(tmp_path):
    backend = Backend()
    settings = protocol(attempts=1)
    first = run(tmp_path, backend, settings)
    original = backend.assess
    def assess(recipe, *, epoch):
        if recipe["kind"] == "candidate" and epoch == 1 and recipe["candidate"]["revision"] == 0:
            raise CandidateContractError("KnowledgeBase read contract")
        return original(recipe, epoch=epoch)
    backend.assess = assess
    pause_all(tmp_path)
    evidence = atomic_write_json(tmp_path / "repair.json", {"cause": "input", "repair": "verified new source", "validation": "checked"})
    second = run(tmp_path, backend, settings, evidence)
    assert second["status"] == "complete"
    history = [item for item in second["assessment_history"] if item["recipe"]["key"] == "candidate-0001"]
    assert len(history) == 1
    assert history[0]["recipe"]["candidate"]["revision"] == 0
    assert history[0]["result"]["evaluation_epoch"] == 0
    assert second["recipes"]["candidate-0001"]["candidate"]["revision"] == 1
    assert (tmp_path / "winner.e0.json").exists() and (tmp_path / "winner.e1.json").exists()


def test_recovery_policy_commit_crash_replays_population_with_same_evidence(tmp_path, monkeypatch):
    backend = Backend()
    settings = protocol(attempts=1)
    run(tmp_path, backend, settings)
    pause_all(tmp_path)
    evidence = atomic_write_json(tmp_path / "repair.json", {"cause": "input", "repair": "restored", "validation": "checked"})
    original = SharedEvolution._apply_recovery_commit
    interrupted = False
    def interrupt(self):
        nonlocal interrupted
        if self.controller.value["status"] == "running" and self.epoch == 1 and interrupted is False:
            interrupted = True
            raise KeyboardInterrupt("Process interrupted after policy commit")
        return original(self)
    monkeypatch.setattr(SharedEvolution, "_apply_recovery_commit", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, backend, settings, evidence)
    calls = list(backend.calls)
    result = run(tmp_path, backend, settings, evidence)
    assert result["status"] == "complete" and result["recovery_applied_epoch"] == 1
    assert backend.calls == calls
    assert read_json(tmp_path / "failure-policy.json")["repair_budget"]["used"] == 1
