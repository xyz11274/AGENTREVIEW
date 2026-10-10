"""Prepare the reviewable small-sample plan; never launch a model request."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'AgentReview'))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.shared_experiment import binding, bound, runtime_identity
from agentreview.research_v2.experiment_protocol import load_experiment_protocol
from agentreview.research_v2.mstar_prompts import MSTAR_PROTOCOL
from agentreview.research_v2.runtime_limits import validate_run_budget
from prepare_runtime_0930 import revision

CONFIG = REPO / 'config/0930-smallflow-20261009-v2'
ROOT = REPO / 'artifacts/smallflow-0930-20261009-v2'


def prepare():
    source = REPO / 'config/0930-pilot-streaming-resume/protocol.frozen.json'
    base = load_experiment_protocol(source)
    value = revision(base.document, experiment_id='0930-smallflow-20261009-v2', root=ROOT,
                     launcher='pilot_0930.py')
    value['implementation'].update(preparer=binding(Path(__file__)),
        runtime_launcher=binding(REPO / 'deployment/run_budgeted_0930.py'),
        completion_launcher=binding(REPO / 'deployment/run_smallflow_0930.py'),
        chain_validator=binding(REPO / 'deployment/validate_evolution_runtime.py'),
        environment_launcher=binding(REPO / 'deployment/run_runtime_0930.sh'))
    value['evolution']['attempts'] = 3
    value['runtime_revision'].update(protocol='0930-smallflow-runtime-v4', mstar_protocol=MSTAR_PROTOCOL,
        request_timeout='Opt-in 600-second queue/connect/stream wall deadline; SDK idle timeout unchanged',
        run_timeout='Supervisor interrupts before the deadline and hard-stops at the same deadline',
        state_inheritance=False)
    value['runtime_budget'] = validate_run_budget({'wall_seconds': 43200,
        'http_attempts': {'cloud': 2000, 'local': 10000}, 'request_wall_seconds': 600,
        'scope': 'One seed, three attempts, seven initial arms, eight development final arms and valid-child trajectory; includes every HTTP retry'})
    value['claim_scope'] = ('Exploratory small-sample development effect and full runtime validation; '
        'existing pilot cohort, historical exposure retained; no unseen-test or efficacy claim')
    value['source_protocol'] = binding(source)
    value['evaluation_design']['comparison_units'] = (
        'One independent search seed 0; seven complete initial arms; three attempts; '
        'eight final development arms and every valid child trajectory; report failures without zero imputation')
    value['evaluation_design']['inherit_small_run_state'] = False
    value['historical_exposure_manifests'].append(binding(REPO / 'metadata/recovery-20261008/development-exposure.json'))
    assert value['splits'] == base.document['splits']
    assert value['model'] == base.document['model']
    assert value['scoring_config'] == base.document['scoring_config']
    protocol_path = atomic_write_json(CONFIG / 'protocol.frozen.json', value)
    protocol = load_experiment_protocol(protocol_path)
    counts = {name: len(rows) for name, rows in protocol.splits.items()}
    assert counts == {'build': 4, 'static': 3, 'rotating': 5, 'sealed_test': 3}
    atomic_write_json(ROOT / 'protocol.frozen.json', protocol.document)
    profile_path = atomic_write_json(CONFIG / 'profile.json', {
        'protocol': 'protocol.frozen.json', 'output_root': str(ROOT),
        'approval_state': 'awaiting_user_start', 'automatic_formal_launch': False})
    copied = []
    paths = protocol.document['implementation']['runtime']
    paths = [*paths, *[ref for key, ref in value['implementation'].items()
                     if key != 'runtime' and isinstance(ref, dict)]]
    for ref in paths:
        path = bound(ref)
        name = path.relative_to(REPO) if path.is_relative_to(REPO) else Path('external-source') / (ref['sha256'] + '-' + path.name)
        target = ROOT / 'frozen-code' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise ValueError('Prepared source freeze must retain its original bytes')
        if not target.exists():
            shutil.copy2(path, target)
        copied.append({'source': ref, 'copy': binding(target)})
    atomic_write_json(ROOT / 'code-freeze.json', {'files': copied, 'credentials_copied': False})
    paper_list = [{'split': name, 'paper_id': row['paper_id'], 'title': row['title'],
                   'work_family': row['work_family'], 'version_ids': row['version_ids'],
                   'development_label': 'development_holdout' if name == 'sealed_test' else name}
                  for name, rows in protocol.splits.items() for row in rows]
    plan = {'status': 'prepared_awaiting_user_start', 'experiment_id': value['experiment_id'],
        'protocol': binding(protocol_path), 'profile': binding(profile_path), 'root': str(ROOT),
        'counts': counts, 'random_seed': 0, 'attempts': 3, 'runtime_budget': value['runtime_budget'],
        'model_calls_this_preparation': 0, 'automatic_formal_launch': False,
        'inherit_candidates_scores_or_checkpoints': False, 'paper_list': paper_list,
        'start_command': f'deployment/run_runtime_0930.sh small pilot --profile {profile_path}',
        'completion': ['all declared comparison paper sets complete', 'full identity chain and original CoCo receipts',
                       'candidate admission and Champion validated', 'durable checkpoint replay',
                       'eight final development arms and valid-child heldout trajectory', 'paired effect and timing reports']}
    atomic_write_json(ROOT / 'plan.json', plan)
    return {'status': plan['status'], 'profile': str(profile_path), 'output_root': str(ROOT), 'counts': counts,
            'model_calls': 0, 'frozen_files': len(copied)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(prepare())
