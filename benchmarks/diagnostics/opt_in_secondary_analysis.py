"""Registered secondary interaction contrasts and unchanged-input variation."""
import argparse
from collections import Counter
import itertools
import json

import numpy as np

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import load_complete
from benchmarks.diagnostics.register_opt_in_interactions import sha, write_new


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    plan=json.loads((study.REPORTS/'opt-in-successor-secondary-analysis-registration.json').read_text())
    registration=json.loads((study.REPORTS/f'{args.name}-registration.json').read_text())
    ids=registration['longmemeval_s']['ordered_question_ids']
    root=study.PRIVATE/args.name
    rows={}
    for arm in registration['arms']:
        folder=root/arm['id']
        if (folder/'verification.json').exists():
            rows[arm['id']]=load_complete(folder,ids)
    output={'kind':'secondary-opt-in-analysis','primary_estimates_unchanged':True,
            'plan_sha256':sha(plan),'registration_sha256':registration['registration_sha256'],
            'interactions':{},'reader_variation':None}
    for name,names in plan['interactions'].items():
        if not all(n in rows for n in names):
            output['interactions'][name]={'status':'unavailable_incomplete_arms'}
            continue
        y=[np.array([r['correct'] for r in rows[n]],dtype=int) for n in names]
        delta=y[3]-y[1]-y[2]+y[0]
        rng=np.random.default_rng(20260922)
        boot=[float(rng.choice(delta,size=len(delta),replace=True).mean()) for _ in range(10000)]
        output['interactions'][name]={'status':'complete','contrast':float(delta.mean()),
            'ci95':np.quantile(boot,[.025,.975]).tolist(),'questions':len(ids),'classification':'exploratory interaction estimate'}
    repeat_names=plan['reader_variation']['candidate_repeat_arms']
    if all(n in rows for n in repeat_names):
        differences=[]
        operations=Counter()
        for qid in ids:
            requests=[]
            for name in repeat_names:
                folder=root/name/qid
                reader=json.loads((folder/'reader.json').read_text())
                capture=json.loads((folder/'capture.json').read_text())
                requests.append(reader['request'])
                for provenance in capture['retrievals'][0]['receipt']['score_provenance'].values():
                    operations.update(op['kind'] for op in provenance['adjustments'] if 'evidence' in op['kind'])
            if any(request!=requests[0] for request in requests[1:]):
                differences.append(qid)
        repeat={'input_gate_passed':not differences and not operations,
                'differing_request_questions':differences,'evidence_operations':dict(operations)}
        if repeat['input_gate_passed']:
            y=np.array([[int(r['correct']) for r in rows[n]] for n in repeat_names])
            repeat.update(arms=repeat_names,questions=len(ids),
                correct_per_arm={name:int(vector.sum()) for name,vector in zip(repeat_names,y)},
                pooled_secondary_accuracy=float(y.mean()),
                success_count_histogram={str(k):int((y.sum(axis=0)==k).sum()) for k in range(4)},
                pairwise_disagreements={f'{repeat_names[a]}__{repeat_names[b]}':int((y[a]!=y[b]).sum()) for a,b in itertools.combinations(range(3),2)},
                primary_baseline_replaced=False)
        output['reader_variation']=repeat
    output['analysis_sha256']=sha(output)
    write_new(study.REPORTS/args.output,output)
    print(json.dumps(output))


if __name__=='__main__': main()
