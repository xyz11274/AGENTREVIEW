"""The prepared small development flow plus zero-request completion verification."""
import argparse
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'AgentReview'))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import APISharedBackend, binding, bound
from agentreview.research_v2.shared_evolution import SharedEvolution
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from pilot_0930 import load_run
from validate_evolution_runtime import verify_chain


def verify_completed_run(run):
    protocol, root = load_experiment_protocol(run.protocol_path), run.root
    checkpoint = read_json(root / 'search/search.json')
    policy = read_json(root / 'search/failure-policy.json')
    budget = read_json(root / 'call-budget.json')
    expected = protocol.document['evolution']['attempts']
    assert len(checkpoint['attempts']) == expected, 'All declared attempts must settle for this small-flow test'
    valid = [a['candidate_key'] for a in checkpoint['attempts'].values() if a['status'] == 'complete']
    assert valid, 'No valid evolved candidate: preserve failed attempts; full candidate flow not accepted'
    chains = [verify_chain(protocol, root, checkpoint, key=key) for key in valid]
    final = read_json(root / 'final/final-test.json')
    assert final['status'] == 'complete'
    assert set(final['results']) == set(protocol.document['evaluation_design']['arms'])
    assert read_json(root / 'round-test/final-rounds.json')['status'] == 'complete'
    replay = SharedEvolution(protocol=protocol, output_root=root / 'search',
                             backend=APISharedBackend(protocol=protocol, output_root=root / 'search')).run()
    assert replay == checkpoint == read_json(root / 'search/search.json')
    assert policy == read_json(root / 'search/failure-policy.json')
    assert budget == read_json(root / 'call-budget.json'), 'Replay must not dispatch or double-charge'
    value = {'status': 'complete', 'protocol': binding(run.protocol_path),
        'checkpoint': binding(root / 'search/search.json'), 'valid_candidate_count': len(valid),
        'candidate_chains': chains, 'zero_http_checkpoint_replay': True,
        'call_budget': budget, 'formal_study_started': False,
        'claim_scope': protocol.document['claim_scope']}
    atomic_write_json(root / 'completion-check.json', value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args()
    run = load_run(args.profile)
    protocol = load_experiment_protocol(run.protocol_path)
    bound(protocol.document['implementation']['completion_launcher'])
    state = run.run()
    if state['status'] != 'complete':
        raise SystemExit(2)
    result = verify_completed_run(run)
    print({'status': result['status'], 'valid_candidates': result['valid_candidate_count']}, flush=True)
