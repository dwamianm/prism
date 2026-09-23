"""Complete-cohort descriptive source intervals for the frozen anchor policy."""
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    root = repair / 'data/opt-in-study/opt-in-anchored-rank-v1'
    reports = repair / 'benchmarks/results/research/2026-09-22'
    name = 'opt-in-anchored-rank-v1'
    reg = json.loads((reports / f'{name}-registration.json').read_text())
    result = json.loads((reports / f'{name}-source-result.json').read_text())
    execution = json.loads((root / 'execution.json').read_text())
    if not result['complete'] or not execution['complete'] or file_sha(root/'execution.json') != result['execution_sha256']:
        raise ValueError('Complete authenticated source trial required')
    if reg['registration_sha256'] != sha({k:v for k,v in reg.items() if k!='registration_sha256'}):
        raise ValueError('Registration differs')
    if file_sha(study.DATASET) != reg['dataset_sha256']:
        raise ValueError('Dataset differs')
    cases = study.base._load_dataset(study.DATASET)
    study.old.validate_complete(reg['ordered_question_ids'], execution['rows'])
    if [c['question_id'] for c in cases] != reg['ordered_question_ids']:
        raise ValueError('Case order differs')
    values = defaultdict(list)
    for source, row in zip(cases, execution['rows']):
        path = root / 'cases' / f"{row['question_id']}.json"
        if file_sha(path) != row['case_sha256']: raise ValueError('Source case differs')
        record = json.loads(path.read_text())
        for arm, captured in record['arms'].items():
            if literal_coverage(source, captured) != captured['evidence']:
                raise ValueError('Source coverage differs')
            values[arm].append({'question_id': source['question_id'], 'category': source['question_type'],
                'evidence': captured['evidence'], 'context_sha256': captured['context_sha256']})
    comparisons = {}
    for control in ('baseline', 'rank_envelope'):
        pairs = [(a,b) for a,b in zip(values[control],values['anchored_rank']) if a['evidence']['applicable']]
        complete_delta, fraction_delta = [], []
        gains, losses = [], []
        categories = defaultdict(lambda: {'total':0,'control_complete':0,'candidate_complete':0})
        for a,b in pairs:
            x,y = a['evidence'],b['evidence']
            delta = int(y['complete_source_literal_recall'])-int(x['complete_source_literal_recall'])
            complete_delta.append(delta)
            fraction_delta.append(y['required_source_literals_present']/y['required_turns']-x['required_source_literals_present']/x['required_turns'])
            if delta>0:gains.append(a['question_id'])
            if delta<0:losses.append(a['question_id'])
            categories[a['category']]['total'] += 1
            categories[a['category']]['control_complete'] += int(x['complete_source_literal_recall'])
            categories[a['category']]['candidate_complete'] += int(y['complete_source_literal_recall'])
        metrics = {}
        for metric, data in [('complete_source',complete_delta),('mean_source_fraction',fraction_delta)]:
            v = np.array(data)
            rng = np.random.default_rng(20260922)
            boot = v[rng.integers(0,len(v),size=(10000,len(v)))].mean(axis=1)
            metrics[metric] = {'difference':float(v.mean()),'ci95':np.quantile(boot,[.025,.975]).tolist()}
        comparisons[control] = {'annotated_cases':len(pairs),'metrics':metrics,'complete_source_gains':gains,
            'complete_source_losses':losses,'categories':dict(categories),
            'changed_contexts':sum(a['context_sha256']!=b['context_sha256'] for a,b in zip(values[control],values['anchored_rank']))}
    report = {'kind':'complete-anchored-source-descriptive-analysis','at':study.utc(),'complete':True,
        'source_registration_sha256':reg['registration_sha256'],
        'source_result_sha256':file_sha(reports/f'{name}-source-result.json'),
        'execution_sha256':file_sha(root/'execution.json'),'compared':comparisons,
        'advance_to_answers':result['advance_to_answers'],
        'method':'All 500 case hashes and all three literal-coverage evaluations authenticated. Intervals use all 470 annotated cases, 10000 paired seed-20260922 bootstrap draws, and are descriptive additions to the unchanged fixed source gate. No model calls, source selection, arm selection or promotion rule changes.',
        'new_hosted_or_cross_encoder_calls':0,'default_changes':False}
    report['sha256'] = sha(report)
    for folder in (study.REPORTS,reports):
        write_new(folder/'opt-in-anchored-source-paired-v1.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
