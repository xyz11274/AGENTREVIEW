from pathlib import Path

import pytest

import evolution_round_report as rounds
import pilot_0930 as pilot
import pilot_data
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding


def score(mean, epoch=0, paper="p"):
    return {"mean": mean, "per_paper": {paper: mean}, "evaluation_epoch": epoch}


def test_paired_round_scores_require_same_papers_and_epoch():
    assert rounds.paired_delta(None, score(3)) is None
    assert rounds.paired_delta(score(2), score(3))["losses"] == 1
    with pytest.raises(ValueError):
        rounds.paired_delta(score(3, paper="other"), score(3))
    with pytest.raises(ValueError):
        rounds.paired_delta(score(3, epoch=1), score(3))


def test_recovery_report_pairs_committed_scores_until_new_epoch_is_published():
    parent_recipe = {'key': 'parent', 'split': 'static'}
    child_recipe = {'key': 'child', 'split': 'static'}
    parent = {'status': 'complete', 'evaluation_epoch': 0, 'static_score': 3.0}
    child = {'status': 'complete', 'evaluation_epoch': 0, 'static_score': 3.5}
    updated = {**child, 'evaluation_epoch': 1, 'static_score': 4.0}
    state = {'champion': {'evaluation_epoch': 0},
             'recipes': {'parent': parent_recipe, 'child': child_recipe},
             'results': {'parent': parent, 'child': updated},
             'assessment_history': [{'recipe': child_recipe, 'result': child}]}
    view = rounds.committed_comparison(state)
    assert view['results']['child'] == child
    assert state['results']['child'] == updated
    state['results']['parent'] = {**parent, 'evaluation_epoch': 1}
    state['champion']['evaluation_epoch'] = 1
    assert rounds.committed_comparison(state)['results']['child'] == updated


def test_failed_reflection_reports_actual_proposed_source(tmp_path, monkeypatch):
    atomic_write_json(tmp_path / 'protocol.frozen.json', {'evolution': {'attempts': 1}})
    old, proposed = tmp_path / 'old.py', tmp_path / 'proposed.py'
    old.write_text('VALUE = 1\n')
    proposed.write_text('VALUE = missing_name\n')
    raw = tmp_path / 'response.txt'
    raw.write_text('*** Begin Patch\n*** End Patch\n')
    trace = tmp_path / 'trace.json'
    atomic_write_json(trace, {'calls': [binding(raw)], 'events': [
        {'validation_source': str(proposed), 'program': binding(proposed), 'error': 'NameError'}]})
    failure = {'category': 'candidate', 'reflection': binding(trace)}
    state = {'status': 'running', 'pool': ['seed-a'], 'attempts': {'1': {
        'status': 'candidate_failed', 'parent_key': 'seed-a', 'candidate_key': 'candidate-0001'}},
        'recipes': {'seed-a': {'kind': 'seed', 'candidate': {'program': binding(old)}},
                    'candidate-0001': {'kind': 'candidate', 'candidate': {
                        'program': binding(old), 'proposal_failure': failure}}},
        'results': {'candidate-0001': {'status': 'candidate_failed', 'failure': failure}}}
    atomic_write_json(tmp_path / 'search/search.json', state)
    monkeypatch.setattr(rounds, 'scores_for', lambda state, key: score(3) if key == 'seed-a' else None)
    row = rounds.report(tmp_path)['rounds'][0]
    assert row['candidate_program']['path'] == str(proposed)
    assert row['candidate_program_status'] == 'validation_failed'
    assert 'missing_name' in Path(row['program_diff']['path']).read_text()
    assert row['candidate_static'] is None
    assert row['reflection_responses'] == [binding(raw)]


def test_report_does_not_promote_parent_improvement_below_best_or_impute_failure(tmp_path, monkeypatch):
    atomic_write_json(tmp_path / "protocol.frozen.json", {"evolution": {"attempts": 5}})
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("VALUE = 1\n")
    new.write_text("VALUE = 2\n")
    recipes = {}
    for key, kind, program in [("seed-a", "seed", old), ("seed-b", "seed", old),
                               ("candidate-0001", "candidate", new), ("candidate-0002", "candidate", old)]:
        recipes[key] = {"kind": kind, "candidate": {"program": binding(program)}}
    state = {"status": "running", "recipes": recipes, "results": {}, "pool": ["seed-a", "seed-b", "candidate-0001"],
        "attempts": {"1": {"status": "complete", "parent_key": "seed-a", "candidate_key": "candidate-0001", "change": "Change VALUE"},
                     "2": {"status": "candidate_failed", "parent_key": "seed-a", "candidate_key": "candidate-0002"}}}
    atomic_write_json(tmp_path / "search/search.json", state)
    scores = {"seed-a": score(3), "seed-b": score(4), "candidate-0001": score(3.5)}
    monkeypatch.setattr(rounds, "scores_for", lambda state, key: scores.get(key))
    report = rounds.report(tmp_path)
    first, failed, pending = report["rounds"][:3]
    assert first["delta_vs_parent"]["mean"] == 0.5
    assert first["became_champion"] is False
    assert first["best_after_round"] == 4
    assert first["added_lines"] == first["removed_lines"] == 1
    assert "VALUE = 2" in Path(first["program_diff"]["path"]).read_text()
    assert failed["candidate_static"] is failed["delta_vs_parent"] is None
    assert pending["status"] == "not_started"
    assert report["completed_rounds"] == 2
    assert report["final_comparisons"] is None


def test_report_rejects_score_that_differs_from_receipts(tmp_path, monkeypatch):
    atomic_write_json(tmp_path / "paper.json", {"metrics": {"Paper.": 2}})
    receipt = {"candidate_id": "x", "papers": [{"paper_id": "p", **binding(tmp_path / "paper.json")} ]}
    atomic_write_json(tmp_path / "receipt.json", receipt)
    state = {"recipes": {"x": {"key": "x", "candidate": {}, "paper_ids": ["p"], "split": "static"}},
             "results": {"x": {"status": "complete", "assessment": binding(tmp_path / "receipt.json"),
                                   "identity": {"candidate": {}}, "evaluation_epoch": 0, "static_score": 4}}}
    monkeypatch.setattr(rounds, "validate_assessment_receipt", lambda path, **kw: read_json(path))
    with pytest.raises(ValueError, match="differs from complete CoCo receipts"):
        rounds.scores_for(state, "x")
    state["results"]["x"]["static_score"] = 2
    assert rounds.scores_for(state, "x")["mean"] == 2
    state["recipes"]["x"]["candidate"] = {"different_program": True}
    with pytest.raises(ValueError, match="different candidate program"):
        rounds.scores_for(state, "x")
    state["recipes"]["x"]["candidate"] = {}
    (tmp_path / "receipt.json").write_text("{}")
    with pytest.raises(ValueError):
        rounds.scores_for(state, "x")


def test_prepare_preserves_full_text_and_distinct_paper_families(tmp_path):
    data = tmp_path / "data"
    source = pilot.REPO / "data/paired"
    result = pilot_data.prepare(source_root=source, data_root=data)
    assert result["counts"] == {"build": 4, "static": 3, "rotating": 5, "sealed_test": 3}
    assert pilot_data.prepare(source_root=source, data_root=data) == result
    all_keys = set()
    for split, ref in result["splits"].items():
        records = read_json(ref["path"])["records"]
        assert len(records) == result["counts"][split]
        for row in records:
            keys = pilot_data._identity_keys(row)
            assert all_keys.isdisjoint(keys)
            all_keys |= keys
            origin = "rotating" if split == "sealed_test" else split
            original = read_json(source / origin / "papers" / f"{row['paper_id']}.json")
            assert read_json(row["assets"]["paper"]["path"])["text"] == original["text"]
    full_test = read_json(source / "sealed_test/manifest.json")["records"]
    assert all_keys.isdisjoint({key for row in full_test for key in pilot_data._identity_keys(row)})


def test_round_holdout_reuses_champion_receipt_with_original_identity(tmp_path, monkeypatch):
    recipes = {key: {"key": key, "candidate": {"candidate_id": key}, "memory_enabled": True}
               for key in ("candidate-0001", "candidate-0002")}
    assessments = {key: {"evaluation_epoch": 0, "identity": {"snapshots": {}}} for key in recipes}
    frozen = {"freeze_id": "freeze", "winner": {"key": "candidate-0002"},
              "recipes": recipes, "assessments": assessments}
    atomic_write_json(tmp_path / "search/winner.json", frozen)
    calls = []
    class Host:
        def __init__(self, **kwargs):
            self.frozen_snapshots = {}
        def validate(self, result, *, recipe, epoch, generation_only=False):
            if not generation_only:
                assert result["receipt_candidate_id"] == recipe["key"]
        def assess(self, recipe, *, epoch):
            calls.append(recipe["key"])
            return {"receipt_candidate_id": recipe["key"]}
    class Protocol:
        def records(self, split):
            assert split == "sealed_test"
            return [{"paper_id": "p"}]
    monkeypatch.setattr(pilot, "APISharedBackend", Host)
    search = {"attempts": {str(i): {"ordinal": i, "candidate_key": f"candidate-{i:04d}",
              "status": "complete" if i < 3 else "candidate_failed"} for i in (1, 2, 3)}}
    final = {"freeze_id": "freeze", "recipes": {"champion": {**recipes["candidate-0002"], "key": "champion"}},
             "results": {"champion": {"receipt_candidate_id": "champion"}}}
    pilot.evaluate_rounds(Protocol(), search, final, root=tmp_path)
    assert calls == ["candidate-0001"]
    result = read_json(tmp_path / "round-test/final-rounds.json")
    assert result["status"] == "complete"
    assert len(result["results"]) == 2
    pilot.evaluate_rounds(Protocol(), search, final, root=tmp_path)
    assert calls == ["candidate-0001"]
    with pytest.raises(ValueError, match="same verified final freeze"):
        pilot.evaluate_rounds(Protocol(), search, {**final, "freeze_id": "other"}, root=tmp_path)
