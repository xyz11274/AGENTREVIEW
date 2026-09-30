"""User-frozen full-scale RNG, temperature, retry and terminal-event rules."""
import copy
from types import SimpleNamespace

import pytest

from test_research_v2_shared_evolution import Backend, protocol, run
from agentreview.research_v2.shared_evolution import select_parent
from agentreview.research_v2.failure_policy import FailureController, FailureLimits, EvolutionPaused, EvolutionBudgetExhausted
from agentreview.research_v2.io import atomic_write_json, read_json


def config(attempts=6, seed=0):
    p = protocol(attempts, consecutive_candidate_failures=3)
    p.document["evolution"].update(random_seed=seed, parent_temperature=0.6, rotating_sample_size=5,
        detailed_reflection_cases=2, random_streams_protocol="sha256-separated-python-random-v1")
    p.records = lambda split: [{"paper_id": f"{split}-{i}"} for i in range(10 if split == "rotating" else 2)]
    return p


class DetailedBackend(Backend):
    def reflection_scores(self, feedback):
        return [{"paper_id": pid, "score": 1 + index % 5} for index, pid in enumerate(feedback["paper_ids"])]


def test_raw_temperature_equivalence_without_double_scaling():
    class Capture:
        def choices(self, entries, *, weights, k):
            self.weights = weights
            return [entries[0]]
    raw, normalized = Capture(), Capture()
    scores = [1, 2.4, 3.8, 5]
    select_parent([{"static_score": score} for score in scores], temperature=0.6, rng=raw)
    select_parent([{"static_score": (score-1)/4} for score in scores], temperature=0.15, rng=normalized)
    assert raw.weights == pytest.approx(normalized.weights)


def test_three_streams_and_two_low_cases_are_persisted_and_replay_identical(tmp_path):
    backend = DetailedBackend()
    state = run(tmp_path, backend, config(2))
    assert state["status"] == "complete"
    streams = state["rng_streams"]
    assert len({value["derived_seed"] for value in streams.values()}) == 3
    assert all(value["draws"] == 2 for value in streams.values())
    for attempt in state["attempts"].values():
        assert len(attempt["rotating_paper_ids"]) == 5
        assert len(attempt["reflection_case_ids"]) == 2
        scores = {row["paper_id"]: row["score"] for row in attempt["reflection_selection"]["scores"]}
        assert sorted(scores[pid] for pid in attempt["reflection_case_ids"]) == [1, 2]
    before = copy.deepcopy(state)
    assert run(tmp_path, backend, config(2))["rng_streams"] == before["rng_streams"]
    other = run(tmp_path / "seed-1000", DetailedBackend(), config(2, 1000))
    assert other["rng_streams"]["parent"]["derived_seed"] != streams["parent"]["derived_seed"]


def test_three_failures_pause_then_five_total_end_with_frozen_champion(tmp_path):
    backend = DetailedBackend()
    backend.invalid = {f"candidate-{i:04d}" for i in (1,2,3,5,6)}
    state = run(tmp_path, backend, config())
    assert state["status"] == "paused_candidate"
    policy = read_json(tmp_path / "failure-policy.json")
    assert policy["candidate_failure_budget"]["used"] == 3
    assert policy["repair_budget"]["used"] == 3
    calls = len(backend.calls)
    assert run(tmp_path, backend, config())["status"] == "paused_candidate"
    assert len(backend.calls) == calls
    evidence = atomic_write_json(tmp_path / "diagnosis.json", {"cause":"candidate contracts", "repair":"verified caller repair", "validation":"regression passed"})
    state = run(tmp_path, backend, config(), evidence)
    assert state["status"] == "ended" and state["winner"]["status"] == "complete"
    policy = read_json(tmp_path / "failure-policy.json")
    assert policy["candidate_failure_budget"]["used"] == 5
    assert policy["repair_budget"]["used"] == 6  # five candidate repairs plus one diagnosed intervention
    assert policy["consecutive_failures"]["used"] == 2  # valid child four breaks the streak
    assert "candidate-0004" in state["pool"]
    assert all(f"candidate-{i:04d}" not in state["pool"] for i in (1,2,3,5,6))


def test_tenth_repair_can_finish_but_eleventh_is_never_reserved(tmp_path):
    c = FailureController(tmp_path / "policy.json", limits=FailureLimits(consecutive_candidate_failures=3))
    for i in range(10):
        assert c.reserve_repair(operation_id=str(i), candidate_id=str(i))
    c.require_running()
    with pytest.raises(EvolutionBudgetExhausted):
        c.reserve_repair(operation_id="eleven", candidate_id="eleven")
    assert read_json(c.path)["repair_budget"] == {"limit": 10, "used": 10}


def test_diagnosis_resume_keeps_counters_and_rejects_reusing_old_proof(tmp_path):
    from agentreview.research_v2.shared_evolution import SharedEvolution
    p = config()
    engine = SharedEvolution(protocol=p, output_root=tmp_path, backend=DetailedBackend())
    c = engine.controller
    for index in range(3):
        c.candidate_failed(operation_id=str(index), candidate_id=str(index), detail={})
    proof = atomic_write_json(tmp_path / "proof.json", {"cause":"invalid code", "repair":"verified correction", "validation":"regression passed"})
    engine._recover(proof)
    engine = SharedEvolution(protocol=p, output_root=tmp_path, backend=DetailedBackend())
    engine._recover(proof)  # Repeated controller launch is idempotent.
    c = engine.controller
    assert c.value["repair_budget"]["used"] == 1
    assert c.value["candidate_failure_budget"]["used"] == c.value["consecutive_failures"]["used"] == 3
    c.candidate_failed(operation_id="four", candidate_id="four", detail={})
    with pytest.raises(ValueError, match="new verified repair"):
        engine._recover(proof)
    assert c.value["status"] == "paused_candidate"
    assert c.value["repair_budget"]["used"] == 1


def test_shared_outage_never_increments_candidate_counters(tmp_path):
    backend = DetailedBackend()
    backend.fail = lambda key, epoch: key == "candidate-0001"
    assert run(tmp_path, backend, config())["status"] == "paused"
    policy = read_json(tmp_path / "failure-policy.json")
    assert policy["candidate_failure_budget"]["used"] == policy["consecutive_failures"]["used"] == 0


def test_prior_use_labels_allow_full_benchmark_but_never_cross_partition_overlap():
    from test_research_v2_experiment_protocol import ExperimentProtocolTest
    from agentreview.research_v2.experiment_protocol import load_experiment_protocol, ExperimentProtocolError
    fixture = ExperimentProtocolTest()
    fixture.setUp()
    try:
        sealed = read_json(fixture.manifest_paths["sealed_test"])
        history = fixture.root / "previous-use.json"
        atomic_write_json(history, {"records":sealed["records"]})
        fixture.config["historical_exposure_manifests"] = [fixture.ref(history)]
        fixture.rewrite_config()
        with pytest.raises(ExperimentProtocolError, match="historical exposure"):
            load_experiment_protocol(fixture.config_path)
        fixture.config["evaluation_design"] = {"historical_overlap_policy":"retain-label-no-test-feedback",
            "strictly_unseen_claim":False,"test_feedback_to_search":False,"all_arms_frozen_before_final":True,
            "original_evaluation_basis":fixture.ref(fixture.manifest_paths["sealed_test"]),
            "historical_use_annotations":fixture.ref(history)}
        fixture.rewrite_config()
        assert len(load_experiment_protocol(fixture.config_path).records("sealed_test")) == 1
        fixture.mutate_row("build", lambda row: row.update(work_family=sealed["records"][0]["work_family"]))
        with pytest.raises(ExperimentProtocolError, match="overlap"):
            load_experiment_protocol(fixture.config_path)
    finally:
        fixture.tearDown()


def test_final_gate_blocks_until_all_three_champions_are_frozen(tmp_path):
    from agentreview.research_v2.shared_experiment import validate_study_freeze
    path = tmp_path / "all-frozen.json"
    p = SimpleNamespace(document={"study":{"random_seeds":[0,1000,2000],"freeze_manifest":str(path)}})
    with pytest.raises(FileNotFoundError):
        validate_study_freeze(p)
    atomic_write_json(path, {"protocol":"three-search-freeze-v1","status":"all_frozen","random_seeds":[0,1000,2000],
        "systems":[{"seed":0},{"seed":1000}]})
    with pytest.raises(ValueError, match="omit"):
        validate_study_freeze(p)


def test_persistent_retry_owner_prevents_wrapper_amplification(tmp_path):
    from agentreview.research_v2.review import _call_with_identical_retry
    from agentreview.research_v2.experiment_requests import PersistentCompletionClient
    class Failing:
        def __init__(self): self.calls = 0
        def count_chat_tokens(self, messages): return {"input_tokens":1,"max_model_len":100}
        def complete(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("public service")
    inner = Failing()
    wrapper = PersistentCompletionClient(inner, tmp_path, identity={}, max_attempts=3)
    for _ in range(2):
        with pytest.raises(Exception):
            _call_with_identical_retry(wrapper, [{"role":"user","content":"unchanged"}], max_tokens=10, component="test")
    assert inner.calls == 3
