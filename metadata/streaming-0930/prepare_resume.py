"""Prepare a provenance-bound continuation through the existing recovery epoch."""
from __future__ import annotations

import copy
import fcntl
from pathlib import Path

from agentreview.research_v2.development_coco import validate_assessment_receipt
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound, runtime_identity


def main():
    repo = Path(__file__).resolve().parents[2]
    source = repo / 'artifacts/pilot-0930-local'
    target = repo / 'artifacts/pilot-0930-streaming-resume'
    metadata = repo / 'metadata/streaming-0930/resume'
    config = repo / 'config/0930-pilot-streaming-resume'
    original_path = repo / 'config/0930-pilot-local/protocol.frozen.json'
    prepared_path = repo / 'config/0930-pilot-streaming/protocol.frozen.json'
    original = load_experiment_protocol(original_path)
    prepared = load_experiment_protocol(prepared_path)
    with (source / 'pilot.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_ref = binding(source / 'search/search.json')
        policy_ref = binding(source / 'search/failure-policy.json')
        state = read_json(bound(state_ref))
        policy = read_json(bound(policy_ref))
        assert read_json(source / 'pilot-state.json')['status'] == state['status'] == policy['status'] == 'paused'
        assert state['identity']['experiment_protocol_sha256'] == original.protocol_sha256
        assert state['attempts']['4']['status'] == 'pending'
        assert prepared.document['implementation']['runtime'] == runtime_identity()
        settings = ('model', 'embedding', 'generation', 'splits', 'evolution', 'failure_policy',
                    'scoring_config', 'initial_memory_programs', 'non_memory_conditions',
                    'evaluation_design', 'execution_profile')
        for key in settings:
            assert original.document[key] == prepared.document[key], key
        scoring = read_json(bound(original.document['scoring_config']))
        for ref in scoring['source_files']:
            bound(ref)
        receipts = []
        for key, result in state['results'].items():
            if result['status'] == 'complete':
                recipe = state['recipes'][key]
                receipt = validate_assessment_receipt(bound(result['assessment']),
                    expected_paper_ids=recipe['paper_ids'], expected_epoch=result['evaluation_epoch'],
                    expected_partition=recipe['split'], candidate_identity=result['identity'])
                assert receipt['mean_score'] == result['static_score']
                receipts.append({'key': key, 'assessment': result['assessment'], 'score': result['static_score']})
        for recipe in state['recipes'].values():
            candidate = recipe['candidate']
            if 'program' in candidate:
                assert binding(bound(candidate['program']))['sha256'] == candidate['program_sha256']
        evidence_path = atomic_write_json(metadata / 'repair-evidence.json', {
            'cause': 'Round 4 nonstreaming reflection exhausted three 600-second read timeouts.',
            'repair': 'Receive Ark generation and Judge with SSE; retain full input, model capacity and official CoCo semantics. Continue through the existing recovery epoch with the original candidates, attempts and RNG states.',
            'validation': '318 regression tests and 101 subtests; 18 final streaming tests; 30 resume tests. Real generation and official CoCo Judge streaming probes returned STREAM_OK. Historical complete receipts verified before migration.',
            'validation_record': binding(repo / 'metadata/streaming-0930/validation.json'),
            'resume_tests': binding(repo / 'metadata/streaming-0930/resume-tests.log'),
            'source_protocol': binding(original_path), 'source_search': state_ref,
            'source_failure_policy': policy_ref, 'reevaluate': policy['pending_candidates'],
            'retained_failure_policy': policy['candidate_failure_budget'],
            'historical_receipts': receipts, 'scientific_settings_preserved': list(settings),
        })
        document = copy.deepcopy(prepared.document)
        document['experiment_id'] = '0930-pilot-streaming-resume-five-rounds-seed-0'
        for name in ('launcher', 'reporter', 'paired_reporter', 'timing_reporter'):
            document['implementation'][name] = binding(document['implementation'][name]['path'])
        document['recovery'] = {'evidence': binding(evidence_path), 'source_protocol': binding(original_path),
                                'search_checkpoint': state_ref, 'failure_policy': policy_ref}
        protocol_path = atomic_write_json(config / 'protocol.frozen.json', document)
        resumed = load_experiment_protocol(protocol_path)
        migrated = copy.deepcopy(state)
        migrated['identity']['experiment_protocol_sha256'] = resumed.protocol_sha256
        assert {key: value for key, value in migrated.items() if key != 'identity'} == {
            key: value for key, value in state.items() if key != 'identity'}
        atomic_write_json(target / 'search/search.json', migrated)
        atomic_write_json(target / 'search/failure-policy.json', policy)
        assert binding(target / 'search/failure-policy.json')['sha256'] == policy_ref['sha256']
        atomic_write_json(target / 'protocol.frozen.json', document)
        atomic_write_json(config / 'profile.json', {
            'protocol': 'protocol.frozen.json', 'output_root': '../../artifacts/pilot-0930-streaming-resume'})
        manifest = {'source_protocol': binding(original_path), 'source_search': state_ref,
                    'source_policy': policy_ref, 'target_protocol': binding(protocol_path),
                    'initial_target_search': binding(target / 'search/search.json'),
                    'repair_evidence': binding(evidence_path), 'historical_receipts_verified': len(receipts),
                    'rng_states_preserved': True, 'attempts_preserved': True,
                    'existing_artifacts': 'Historical programs and receipts remain bound to their original paths.',
                    'execution': 'Existing recovery allocates a new comparison epoch and reevaluates all affected candidates.'}
        atomic_write_json(metadata / 'migration.json', manifest)
        bound(state_ref)
        bound(policy_ref)
        print({'status': 'prepared', 'receipts_verified': len(receipts), 'reevaluate': len(policy['pending_candidates']),
               'profile': str(config / 'profile.json')})


if __name__ == '__main__':
    main()
