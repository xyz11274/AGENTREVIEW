"""Freeze an exposed-data acceptance or a fresh revision of the confirmed study."""
import argparse
import json
import time
from copy import deepcopy
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json, value_sha256
from agentreview.research_v2.shared_experiment import binding, bound, runtime_identity
from agentreview.research_v2.experiment_protocol import load_experiment_protocol

CONFIG = REPO / "config/0930-runtime-20261008"
ACCEPTANCE = REPO / "artifacts/evolution-acceptance-20261008"
FORMAL = REPO / "artifacts/study-0930-runtime-20261009"


def materialize_reference_subset(reference_path, paper_ids, output):
    """Retain exact selected JSONL lines and reviewer assignments in new assets."""
    source = read_json(reference_path)
    wanted = set(paper_ids)
    if len(wanted) != len(paper_ids) or not wanted <= set(source['paper_ids']):
        raise ValueError('Reference subset requires unique existing paper IDs')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    def rows(refs, filename):
        selected, ids = [], []
        for ref in refs:
            for line in bound(ref).read_bytes().splitlines(keepends=True):
                if not line.strip():
                    continue
                identity = str(json.loads(line)['id'])
                if identity in wanted:
                    selected.append(line)
                    ids.append(identity)
        if len(ids) != len(wanted) or set(ids) != wanted:
            raise ValueError(f'{filename} requires exactly the selected reference IDs')
        path = output / filename
        content = b''.join(selected)
        if path.exists() and path.read_bytes() != content:
            raise ValueError('Existing subset reference requires its original bytes')
        path.write_bytes(content)
        return binding(path)
    value = deepcopy(source)
    value['paper_ids'] = list(paper_ids)
    value['benchmark_files'] = [rows(source['benchmark_files'], 'benchmark.jsonl')]
    value['complete_human_classify_file'] = rows([source['complete_human_classify_file']], 'human.jsonl')
    selection = read_json(bound(source['reviewer_selection_file']))
    value['reviewer_selection_file'] = binding(atomic_write_json(output / 'selection.json',
                                              {pid: selection[pid] for pid in paper_ids}))
    return atomic_write_json(output / 'references.json', value)


def implementation(launcher):
    return {"runtime": runtime_identity(), "launcher": binding(REPO / "deployment" / launcher),
            "reporter": binding(REPO / "deployment/evolution_round_report.py"),
            "paired_reporter": binding(REPO / "deployment/paired_benchmark_report.py"),
            "timing_reporter": binding(REPO / "deployment/runtime_report.py")}


def revision(base, *, experiment_id, root, launcher):
    value = deepcopy(base)
    for name in ("recovery", "local_deployment", "launch_policy", "transport_revision"):
        value.pop(name, None)
    value["experiment_id"] = experiment_id
    value["implementation"] = implementation(launcher)
    value["service_configuration"] = value["services_config"] = binding(CONFIG / "services.json")
    execution = read_json(REPO / "config/0930/execution.json")
    execution["admission_root"] = str(root / "api-admission")
    execution_path = atomic_write_json(root / "execution.frozen.json", execution)
    value["execution_profile"] = binding(execution_path)
    value["embedding"]["cache_path"] = str(root / "embedding.sqlite3")
    value["runtime_revision"] = {"protocol": "0930-runtime-repair-v3", "mstar_protocol": "mstar-program-evolution-v3",
        "mstar_repairs": "initial response plus at most three fixes, including format errors; Python fence unchanged",
        "coco_semantics": "unchanged upstream prompts/parsers/recovery/scoring; HTTP observer preserves upstream retries and SDK defaults",
        "gpu_adaptation": {"split": [4, 5], "classify": [6, 7]},
        "state_inheritance": False}
    return value


def acceptance():
    base = read_json(REPO / "config/0930-pilot-streaming-resume/protocol.frozen.json")
    value = revision(base, experiment_id="0930-runtime-acceptance-20261008", root=ACCEPTANCE,
                     launcher="validate_evolution_runtime.py")
    # Select before looking at scores. Full original paper/reference bytes stay bound.
    for split in ("build", "static", "rotating"):
        manifest = read_json(bound(base["splits"][split]))
        manifest["records"] = manifest["records"][:1]
        if split != "build":
            ref_path = materialize_reference_subset(bound(manifest["records"][0]["assets"]["coco_reference"]),
                [manifest["records"][0]["paper_id"]], ACCEPTANCE / "inputs" / split)
            manifest["records"][0]["assets"]["coco_reference"] = binding(ref_path)
        manifest_path = atomic_write_json(ACCEPTANCE / "inputs" / split / "manifest.json", manifest)
        value["splits"][split] = binding(manifest_path)
    value["evolution"].update(attempts=1, rotating_sample_size=1, detailed_reflection_cases=1,
                              detailed_case_rule="One fully evaluated exposed rotating paper")
    value["evaluation_design"]["expected_counts"].update(build=1, static=1, rotating=1)
    value["evaluation_design"]["development_split_bindings"] = {
        name: value["splits"][name] for name in ("build", "static", "rotating")}
    value["evaluation_design"]["comparison_units"] = "Runtime acceptance: all seven initial arms and one real mutation, complete one-paper static cohort"
    value["runtime_acceptance_budget"] = {"wall_seconds": 7200, "http_attempts": {"cloud": 250, "local": 1500},
        "scope": "One attempt; production fixed baselines, build, reflection, final-review CoCo and interruption/reload; no sealed evaluation"}
    value["claim_scope"] = "Runtime correctness only, exposed development samples; no claim about fitness improvement"
    value["source_protocol"] = binding(REPO / "config/0930-pilot-streaming-resume/protocol.frozen.json")
    audit = {"status": "complete", "method": "Ordered subsets of already audited pilot partitions; loader rechecks identity/family/content disjointness",
             "source_audit": base["deduplication"]["family_audit"],
             "split_manifest_sha256": {name: ref["sha256"] for name, ref in value["splits"].items()}}
    audit_path = atomic_write_json(ACCEPTANCE / "inputs/family-audit.json", audit)
    value["deduplication"]["family_audit"] = binding(audit_path)
    path = atomic_write_json(CONFIG / "acceptance.protocol.frozen.json", value)
    protocol = load_experiment_protocol(path)
    atomic_write_json(ACCEPTANCE / "protocol.frozen.json", protocol.document)
    return path


def recover_acceptance():
    target = REPO / 'artifacts/evolution-acceptance-20261009'
    old_path = CONFIG / 'acceptance.protocol.frozen.json'
    old = load_experiment_protocol(old_path)
    state_ref = binding(ACCEPTANCE / 'search/search.json')
    policy_ref = binding(ACCEPTANCE / 'search/failure-policy.json')
    budget_ref = binding(ACCEPTANCE / 'call-budget.json')
    state, policy = read_json(bound(state_ref)), read_json(bound(policy_ref))
    assert state['status'] == policy['status'] == 'paused'
    assert not state['results'] and not state['attempts'] and not state['pool']
    assert old.document['implementation']['runtime'] == runtime_identity()
    document = deepcopy(old.document)
    document['implementation'] = implementation('validate_evolution_runtime.py')
    document['experiment_id'] = '0930-runtime-acceptance-recovery-20261009'
    document['embedding']['cache_path'] = str(target / 'embedding.sqlite3')
    execution = read_json(bound(document['execution_profile']))
    execution['admission_root'] = str(target / 'api-admission')
    document['execution_profile'] = binding(atomic_write_json(target / 'execution.frozen.json', execution))
    validation = {}
    from agentreview.research_v2.development_coco import _validate_inputs
    for split in ('static', 'rotating'):
        manifest = read_json(bound(document['splits'][split]))
        original = read_json(REPO / f'data/pilot-0930/{split}/scoring-only/references.json')
        original_path = REPO / f'data/pilot-0930/{split}/scoring-only/references.json'
        ref_path = materialize_reference_subset(original_path,
            [r['paper_id'] for r in manifest['records']], target / 'inputs' / split)
        for row in manifest['records']:
            row['assets']['coco_reference'] = binding(ref_path)
        document['splits'][split] = binding(atomic_write_json(target / 'inputs' / split / 'manifest.json', manifest))
        validation[split] = {'references': binding(ref_path), 'original_references': binding(original_path),
                             'paper_ids': [r['paper_id'] for r in manifest['records']]}
        if split == 'static':
            review_manifest = next((ACCEPTANCE / 'search/assessments').glob('**/parts/*/reviews.json'))
            _validate_inputs(review_manifest, ref_path, split)
            validation[split]['saved_final_review_manifest'] = binding(review_manifest)
    document['evaluation_design']['development_split_bindings'] = {
        name: document['splits'][name] for name in ('build','static','rotating')}
    audit_path = atomic_write_json(target / 'inputs/family-audit.json', {
        'status':'complete', 'method':'Same selected identities, closed subset reference assets; loader rechecks disjointness',
        'source_audit': old.document['deduplication']['family_audit'],
        'split_manifest_sha256':{name:ref['sha256'] for name,ref in document['splits'].items()}})
    document['deduplication']['family_audit'] = binding(audit_path)
    validation_path = atomic_write_json(target / 'reference-closure-validation.json', validation)
    evidence_path = atomic_write_json(target / 'repair-evidence.json', {
        'cause':'Acceptance reference IDs were reduced to one paper while benchmark/human/selection assets retained three IDs; failed before any CoCo sends.',
        'repair':'Materialize exact selected reference rows and assignments; keep original CoCo closure checks; resume with the existing controller in epoch 1.',
        'validation':'Original saved final review passes the real _validate_inputs with new references; subset regression and unchanged scoring tests pass.',
        'validation_record':binding(validation_path), 'tests':binding(REPO/'metadata/recovery-20261008/subset-tests.log'),
        'source_protocol':binding(old_path), 'source_search':state_ref,'source_policy':policy_ref,'source_budget':budget_ref,
        'regeneration':'New epoch rebuilds memories and final reviews; old generated reviews, requests and failed receipts remain at their original paths.'})
    document['recovery'] = {'evidence':binding(evidence_path),'source_protocol':binding(old_path),
                            'search_checkpoint':state_ref,'failure_policy':policy_ref}
    protocol_path = atomic_write_json(CONFIG / 'acceptance.recovery-20261009.frozen.json', document)
    new = load_experiment_protocol(protocol_path)
    atomic_write_json(target / 'protocol.frozen.json', new.document)
    migrated = deepcopy(state)
    migrated['identity']['experiment_protocol_sha256'] = new.protocol_sha256
    assert {k:v for k,v in migrated.items() if k!='identity'} == {k:v for k,v in state.items() if k!='identity'}
    atomic_write_json(target / 'search/search.json', migrated)
    atomic_write_json(target / 'search/failure-policy.json', policy)
    carried = read_json(bound(budget_ref))
    original_budget = deepcopy(carried)
    carried['source_budget'] = budget_ref
    carried['previous_deadline_at'] = carried['deadline_at']
    carried['deadline_at'] = time.time() + document['runtime_acceptance_budget']['wall_seconds']
    carried['resume_authorization'] = 'Explicit user request on 2026-10-09 to restart CoCo and continue; cumulative attempt limits unchanged'
    atomic_write_json(target / 'call-budget.json', carried)
    assert carried['used'] == original_budget['used'] and carried['limits'] == original_budget['limits']
    atomic_write_json(target / 'migration.json', {'source_protocol':binding(old_path),'source_search':state_ref,
        'source_policy':policy_ref,'source_budget':budget_ref,'target_protocol':binding(protocol_path),
        'repair_evidence':binding(evidence_path),'inherited_http_attempts':carried['used'],
        'rng_and_failure_budgets_preserved':True,'candidate_grades_inherited':0})
    return protocol_path


def formal(acceptance_root=ACCEPTANCE):
    acceptance_path = Path(acceptance_root) / "acceptance.json"
    evidence = read_json(acceptance_path)
    if evidence.get("status") != "complete" or evidence.get("runtime") != runtime_identity():
        raise ValueError("Formal freeze requires successful real acceptance of the final code")
    bound(evidence["checkpoint"])
    old_study = read_json(REPO / "config/0930/study.frozen.json")
    searches = []
    for old in old_study["searches"]:
        base = read_json(bound(old["protocol"]))
        seed = old["seed"]
        value = revision(base, experiment_id=f"0930-runtime-20261009-search-{seed}", root=FORMAL,
                         launcher="three_search_study.py")
        value["study"]["freeze_manifest"] = str(FORMAL / "all-systems-frozen.json")
        value["source_protocol"] = old["protocol"]
        value["runtime_acceptance"] = binding(acceptance_path)
        value["historical_exposure_manifests"].append(binding(REPO / "metadata/recovery-20261008/development-exposure.json"))
        value["runtime_revision"]["exposure_audit"] = binding(REPO / "metadata/recovery-20261008/formal-data-identity-audit.json")
        value["implementation"]["preparer"] = binding(Path(__file__))
        assert value["evolution"] == base["evolution"] and value["splits"] == base["splits"]
        assert value["model"] == base["model"] and value["scoring_config"] == base["scoring_config"]
        path = atomic_write_json(CONFIG / f"formal.seed-{seed}.protocol.frozen.json", value)
        protocol = load_experiment_protocol(path)
        assert {name: len(rows) for name, rows in protocol.splits.items()} == {"build":125,"static":25,"rotating":125,"sealed_test":1300}
        atomic_write_json(FORMAL / "runs" / f"seed-{seed}" / "protocol.frozen.json", protocol.document)
        searches.append({"seed": seed, "protocol": binding(path), "run_root": str(FORMAL / "runs" / f"seed-{seed}")})
    study = {**old_study, "searches": searches, "source_study": binding(REPO / "config/0930/study.frozen.json"),
             "output_root": str(FORMAL), "runtime_acceptance": binding(acceptance_path),
             "implementation": {"launcher": binding(REPO / "deployment/three_search_study.py"),
                                "reporter": binding(REPO / "deployment/paired_benchmark_report.py")},
             "inherit_small_run_state": False}
    study.pop("study_id", None)
    study["study_id"] = value_sha256(study)
    return atomic_write_json(CONFIG / "study.frozen.json", study)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("acceptance", "recover-acceptance", "formal"))
    parser.add_argument('--acceptance-root', type=Path, default=ACCEPTANCE)
    args = parser.parse_args()
    print(acceptance() if args.action == "acceptance" else recover_acceptance() if args.action == 'recover-acceptance' else formal(args.acceptance_root))
