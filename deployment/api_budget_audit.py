"""Offline full-cohort budget accounting; no generation, truncation or scoring."""
from pathlib import Path
import argparse
import json
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO / "AgentReview"))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.models import Paper
from agentreview.research_v2.review_preparation import frozen_paper_context
from agentreview.research_v2.shared_experiment import bound
from agentreview.reviewer_identity import build_meta_review_question, load_meta_synthesizer, SPECIALIST_TASKS


def audit(*, protocol_path, output_path):
    from agentreview.research_v2.config import ModelConfig
    from agentreview.research_v2.token_accounting import count_chat_tokens, text_tokenizer, tokenizer_identity
    config = read_json(protocol_path)
    model = ModelConfig(**config["model"])
    tokenizer = text_tokenizer()
    synth = load_meta_synthesizer()
    rows = []
    for split, ref in config['splits'].items():
        for row in read_json(bound(ref))['records']:
            paper = Paper.from_dict(read_json(bound(row['assets']['paper'])))
            body = len(tokenizer.encode(paper.text,add_special_tokens=False).ids)
            # Empty role bodies here establish prompt overhead only. This is
            # never a generated review, functional acceptance or score input.
            messages = [{'role':'system','content':synth.system_prompt},{'role':'user','content':build_meta_review_question(
                paper_id=paper.paper_id,paper_context=frozen_paper_context(paper,'META'),
                specialist_reviews={role:'' for role in SPECIALIST_TASKS})}]
            meta_base = count_chat_tokens(messages, model)["input_tokens"]
            # Role bodies do not exist during this offline audit. Their actual
            # combined input is measured at request time; do not invent a fixed
            # per-role cap or reserve five theoretical maximum outputs here.
            remaining = model.max_model_len - meta_base
            rows.append({'paper_id':paper.paper_id,'split':split,'paper_asset':row['assets']['paper'],
                'complete_paper_tokens':body,'meta_prompt_without_role_bodies_tokens':meta_base,
                'remaining_context_for_role_bodies_and_meta_output':remaining,
                'model_maximum_output':model.max_output_tokens,
                'model_context':config['model']['max_model_len'],
                'complete_base_prompt_fits':remaining > 0})
    failed = [row for row in rows if not row['complete_base_prompt_fits']]
    result = {'protocol':'api-full-cohort-capacity-audit-v3','status':'blocked' if failed else 'base_prompt_capacity_checked',
        'token_accounting':tokenizer_identity(),'model':model.identity(),'split_bindings':config['splits'],
        'runtime_service_capacity_verified':False,'records':rows,'violations':failed,
        'scope':'Offline base prompts only, excluding generated role bodies. Does not certify the eventual assembled Meta request. Every actual request uses min(model maximum output, context minus complete input). No papers trimmed or removed.'}
    path = atomic_write_json(output_path,result,immutable=False)
    print(json.dumps({'artifact':str(path),'papers':len(rows),'violations':len(failed),
        'maximum_meta_base_input':max(row['meta_prompt_without_role_bodies_tokens'] for row in rows)},indent=2))
    return path


if __name__ == '__main__':
    from pilot_0930 import load_run
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=REPO / 'config/active-pilot.json')
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run = load_run(args.profile)
    audit(protocol_path=args.protocol or run.protocol_path,
          output_path=args.output or run.root / 'audit/capacity.json')
