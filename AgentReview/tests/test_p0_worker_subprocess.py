"""Real production evaluation subprocess + SDK SSE, with synthetic scorer data.

This is a transport/entrypoint regression, not real-model CoCo acceptance.
The unchanged upstream LLMClient is used; tiny scorer fixtures avoid model loads.
"""
import json
from pathlib import Path

import pytest

from test_research_v2_development_coco import experiment, fixture_integrity, binding
from test_streaming_transport import endpoint, event
from agentreview.research_v2 import development_coco as coco
from agentreview.research_v2.config import ModelConfig
from agentreview.research_v2.io import atomic_write_json, read_json, write_immutable_jsonl


SCORER = '''
import json
IDS = ['paper-a', 'paper-b']
def load_benchmark_records(ids, paths): return {p: {} for p in ids}
def load_human_complete_category_sets(path, ids): return {p: set() for p in ids}
def load_human_full_text_data(records, paths): return None, {p: 'gold' for p in records}
def load_human_review_data(records, paths): return {p: {'category_correct': {}} for p in records}
def load_ai_full_text_data(path): return {p: 'review' for p in IDS}
def load_ai_review_data(path): return {p: {} for p in IDS}
def load_ai_paper_level_data(path): return {p: 'review' for p in IDS}
def compute_full_text_metrics(ai, refs): return {'BLEU': 4}, {'BLEU': [4, 4]}
def evaluate_categories_batch(human, ai, client, **kwargs):
    return {p: {'category_scores': {}} for p in IDS}
def evaluate_full_judge_batch(human, ai, client, **kwargs):
    result = {}
    for pid in IDS:
        key = client.submit_task('score ' + pid, max_completion_tokens=64)
        score = json.loads(client.get_result(key)['content'])['score']
        result[pid] = {'judge_scores': {'success': True, 'score': score}}
    return result
def build_table_1_summary(name, full, category, paper, ai, human):
    from agentreview.research_v2.development_coco import COLUMNS
    score = sum(p['judge_scores']['score'] for p in paper.values()) / len(paper)
    return {'absolute_scores': dict.fromkeys(COLUMNS, score),
            'paper_counts': dict.fromkeys(COLUMNS, len(paper))}
'''


@pytest.mark.parametrize('valid', [True, False])
def test_production_evaluation_subprocess_stream_and_receipt(experiment, tmp_path, monkeypatch, valid):
    root = tmp_path / 'scorer'
    evaluation = root / 'src/evaluation'
    evaluation.mkdir(parents=True)
    for name in ('_2_split.py', '_3_classify.py'):
        (evaluation / name).write_text('# Other stages supplied as fixtures.\n')
    (evaluation / '_4_evaluation.py').write_text(SCORER)
    upstream = Path(__file__).resolve().parents[3] / 'CoCoReviewBench_official_2580687/src/utils.py'
    (root / 'src/utils.py').write_bytes(upstream.read_bytes())
    calls = []
    original_run_stage = coco._run_stage

    def stage(**kwargs):
        name, output = kwargs['stage'], kwargs['output']
        calls.append(name)
        if name == 'evaluation':
            return original_run_stage(**kwargs)
        artifact = write_immutable_jsonl(output / f'{name}.jsonl', [{'id': 'paper-a'}, {'id': 'paper-b'}])
        result = atomic_write_json(output / f'{name}.json', {'request': binding(kwargs['request_path']),
            'artifact': binding(artifact), 'integrity': fixture_integrity(output, name, ['paper-a', 'paper-b'])})
        atomic_write_json(output / 'stages' / name / 'state.json', {'status': 'complete', 'result': binding(result)})
        return result

    monkeypatch.setattr(coco, '_run_stage', stage)
    def send(handler, attempt):
        event(handler, content=json.dumps({'score': 4}) if valid else 'invalid JSON', finish='stop')
        handler.wfile.write(b'data: [DONE]\n\n')

    with endpoint(monkeypatch, send) as (url, requests):
        config = read_json(experiment['scoring_config_path'])
        config.update(coco_root=str(root), source_files=coco._source_identity(root), timeout_seconds=30,
            services={'judge': ModelConfig(base_url=url, model='test-model', response_model='test-model').protocol_config()},
            request_settings={'judge': {'max_workers': 1}})
        atomic_write_json(experiment['scoring_config_path'], config, immutable=False)
        if valid:
            receipt = coco.evaluate_final_reviews(**experiment)
            coco.validate_assessment_receipt(receipt)
            assert receipt['complete_count'] == 2 and receipt['mean_score'] == 4
            assert coco.evaluate_final_reviews(**experiment) == receipt
            assert len(requests) == 2  # Receipt replay must not issue new requests.
        else:
            with pytest.raises(coco.DevelopmentCoCoFailure):
                coco.evaluate_final_reviews(**experiment)
            assert read_json(Path(experiment['output_root']) / 'assessment.json')['status'] == 'paused'
            assert requests  # Must reach the actual SDK call, not fail at import.
    assert calls == ['split', 'classify', 'evaluation']
    assert all(request['stream'] for request in requests)
    progress = list(Path(experiment['output_root']).rglob('attempt-1.progress.json'))
    assert progress and all(read_json(p)['status'] == 'complete' for p in progress)
