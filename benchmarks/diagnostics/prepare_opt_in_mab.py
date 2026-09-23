"""Prepare pinned MemoryAgentBench inputs without any inference or scoring."""
from __future__ import annotations

import json
from pathlib import Path
import argparse

import yaml

from benchmarks.diagnostics.opt_in_successor import PRIVATE, ROOT, REPORTS
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench as registrar


TASKS = {
    'banking': ('Test_Time_Learning/ICL/ICL_banking77.yaml', 'numeric-label-v1', 'exact_match', 'banking-strict'),
    'eventqa': ('Accurate_Retrieval/EventQA/Eventqa_64k.yaml', 'upstream', 'substring_exact_match', 'eventqa-session'),
    'conflict': ('Conflict_Resolution/Factconsolidation_mh_6k.yaml', 'answer-only-v1', 'substring_exact_match', 'conflict-answeronly'),
    'detective': ('Long_Range_Understanding/Detective_QA.yaml', 'choice-only-v1', 'exact_match', 'detective-choice'),
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    args=parser.parse_args()
    destination=PRIVATE/args.name
    destination.mkdir(exist_ok=False)
    upstream=PRIVATE/'MemoryAgentBench'
    preprocessing=registrar._preprocessing_identity()
    original=json.loads((ROOT/'benchmarks/results/research/2026-09-14/memoryagentbench-banking-strict-dev20-prme-verification.json').read_text())
    if preprocessing!=original['source']['preprocessing']:
        raise RuntimeError('Prepared input dependencies differ from registered baselines')
    if registrar._git(upstream,'rev-parse','HEAD')!=adapter.UPSTREAM_REVISION:
        raise RuntimeError('Upstream revision differs')
    summaries={}
    for name,(relative,contract,metric,prior_name) in TASKS.items():
        config=yaml.safe_load((upstream/'configs/data_conf'/relative).read_text())
        agent=yaml.safe_load((ROOT/'benchmarks/integrations/memoryagentbench_config.yaml').read_text())
        agent.update(model='deepseek-v4.1-flash:cloud',temperature=0,reader_seed=42,reader_output_contract=contract)
        with registrar._upstream_imports(upstream):
            from conversation_creator import ConversationCreator
            from utils.templates import get_template
            creator=ConversationCreator(agent,config)
            chunks=creator.get_chunks()
            groups=creator.get_query_and_answers()
            system=get_template(config['sub_dataset'],'system',agent['agent_name'])
        # Reproduce the prior first-20 development input contract before any
        # candidate inference. Additional source families remain separate.
        identity,n=registrar._registered_contexts(chunks,groups,max_chunk_chars=6000,
                                                  sub_dataset=config['sub_dataset'],max_queries=20)
        prepared={'task':name,'dataset_config':config,'agent_config':agent,'metric':metric,
                  'system_message':system,'chunks':chunks,'query_groups':groups,
                  'development_first20_identity':identity,'development_questions':n,
                  'preprocessing':preprocessing,'upstream_revision':adapter.UPSTREAM_REVISION,
                  'dataset_revision':adapter.DATASET_REVISION}
        path=destination/f'{name}.json'
        write_new(path,prepared)
        prior=json.loads((ROOT/f'benchmarks/results/research/2026-09-14/memoryagentbench-{prior_name}-dev20-prme-verification.json').read_text())
        summaries[name]={'prepared_sha256':file_sha(path),'contexts':len(chunks),
            'questions_per_context':[len(g) for g in groups],
            'development_questions':n,'prior_development_task':prior['task'],
            'development_input_identity_sha256':sha(identity),
            'source_family_sha256':[sha(c) for c in chunks],
            'context_source_chunks':[len(c) for c in chunks],
            'metric':metric,'output_contract':contract,'dataset_config':config}
        print(json.dumps({'task':name,'contexts':len(chunks),'questions_per_context':[len(g) for g in groups]}),flush=True)
    write_new(REPORTS/f'{args.name}-input-preparation.json',{
        'kind':'outcome-free-input-preparation-not-execution-registration',
        'preprocessing':preprocessing,'tasks':summaries,
        'source_sha256':file_sha(Path(__file__)),
        'inference_calls':0,'confirmation_eligibility':'Requires separate prior-exposure audit; no unused status inferred here.'})


if __name__=='__main__':
    main()
