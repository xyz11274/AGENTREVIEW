"""Offline full-cohort budget accounting; no generation, truncation or scoring."""
from pathlib import Path
import hashlib
import json
import sys

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/paired-benchmark-20260917"
sys.path.insert(0,str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review_preparation import frozen_paper_context
from agentreview.research_v2.shared_experiment import binding, bound
from agentreview.reviewer_identity import build_meta_review_question, load_meta_synthesizer, SPECIALIST_TASKS


def audit():
    from transformers import AutoTokenizer
    config = read_json(ROOT / "protocol.pending.json")
    model_root = Path('/data/xyj/models/Qwen3.8-27B')
    token_sha = hashlib.sha256((model_root / 'tokenizer.json').read_bytes()).hexdigest()
    if token_sha != config['model']['tokenizer_sha256']:
        raise ValueError('Token counts require the frozen tokenizer bytes')
    tokenizer = AutoTokenizer.from_pretrained(model_root,local_files_only=True)
    synth = load_meta_synthesizer()
    generation = config['generation']
    rows = []
    for split, ref in config['splits'].items():
        for row in read_json(bound(ref))['records']:
            paper = Paper.from_dict(read_json(bound(row['assets']['paper'])))
            body = len(tokenizer.encode(paper.text,add_special_tokens=False))
            # Empty role bodies here establish prompt overhead only. This is
            # never a generated review, functional acceptance or score input.
            messages = [{'role':'system','content':synth.system_prompt},{'role':'user','content':build_meta_review_question(
                paper_id=paper.paper_id,paper_context=frozen_paper_context(paper,'META'),
                specialist_reviews={role:'' for role in SPECIALIST_TASKS})}]
            meta_token_ids = tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,
                enable_thinking=config['model']['enable_thinking'], return_dict=False)
            if not isinstance(meta_token_ids, list) or not all(type(token) is int for token in meta_token_ids):
                raise TypeError('Budget audit requires one flat list of token IDs, not a mapping or batch')
            meta_base = len(meta_token_ids)
            # Reserve five full role outputs, plus a boundary allowance. Actual
            # assembled requests are always tokenized again before submission.
            meta_input = meta_base + 5*generation['review_max_tokens'] + 1024
            rows.append({'paper_id':paper.paper_id,'split':split,'paper_asset':row['assets']['paper'],
                'complete_paper_tokens':body,'meta_prompt_without_role_bodies_tokens':meta_base,
                'meta_input_capacity_estimate_with_five_full_role_outputs':meta_input,
                'input_limit':generation['input_max_tokens'],'meta_output_reserved':generation['meta_max_tokens'],
                'model_context':config['model']['max_model_len'],
                'fits_configured_capacity':meta_input<=generation['input_max_tokens'] and meta_input+generation['meta_max_tokens']<=config['model']['max_model_len']})
    failed = [row for row in rows if not row['fits_configured_capacity']]
    result = {'protocol':'paired-full-cohort-capacity-audit-v1','status':'blocked' if failed else 'configured_capacity_checked',
        'tokenizer_sha256':token_sha,'config':binding(ROOT/'protocol.pending.json'),'split_bindings':config['splits'],
        'runtime_service_capacity_verified':False,'records':rows,'violations':failed,
        'scope':'Offline capacity calculation only; every actual role, memory, Meta and reflection request must pass token counting and complete-output checks. No papers trimmed or removed.'}
    path = atomic_write_json(ROOT/'audit/proposed-capacity-budget.json',result,immutable=False)
    print(json.dumps({'artifact':str(path),'papers':len(rows),'violations':len(failed),
        'maximum_estimated_meta_input':max(row['meta_input_capacity_estimate_with_five_full_role_outputs'] for row in rows)},indent=2))
    return path


if __name__=='__main__':
    audit()
