"""Post hoc description of every completed identical-input control repeat."""
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    prior_path = study.REPORTS/'opt-in-complete-control-repeats-v1.json'
    prior = json.loads(prior_path.read_text())
    configurations = {name:(study.PRIVATE/'opt-in-successor-v2'/name, False)
                      for name in ('baseline','evidence_augmentation','evidence_projection')}
    configurations['marginal_control_repeat'] = (study.PRIVATE/'opt-in-marginal-packing-v2-reader-control',True)
    configurations['anchor_control_repeat'] = (repair/'data/opt-in-study/opt-in-anchored-rank-v1-reader-control',True)
    result_path = repair/'benchmarks/results/research/2026-09-22/opt-in-anchored-rank-v1-reader-control-result.json'
    latest = json.loads(result_path.read_text())
    if not latest['complete'] or latest['verified_cases'] != 500:raise ValueError('Complete new control required')
    expected = json.loads((study.REPORTS/'opt-in-successor-v2-registration.json').read_text())['longmemeval_s']['ordered_question_ids']
    rows, checksums, requests = {}, {}, {}
    for name,(root,nested) in configurations.items():
        execution = json.loads((root/'execution.json').read_text())
        checksum = file_sha(root/'execution.json')
        required = latest['execution_sha256'] if name=='anchor_control_repeat' else prior['execution_sha256'][name]
        if not execution['complete'] or checksum != required:raise ValueError('Authenticated control changed')
        study.old.validate_complete(expected,execution['rows'])
        checksums[name] = checksum
        rows[name] = execution['rows']
        requests[name] = []
        for row in execution['rows']:
            folder = root/('execution' if nested else '')/row['question_id']
            reader = None
            for role in ('reader','judge'):
                path = folder/f'{role}.json'
                data = json.loads(path.read_text())
                if (file_sha(path)!=row[f'{role}_sha256'] or data['status']!='complete'
                    or data['request_sha256']!=sha(data['request']) or data['response']['done_reason']!='stop'):
                    raise ValueError('Provider artifact differs')
                if role=='reader':reader=data
                elif row['correct'] != ('yes' in data['response']['message']['content'].lower()):
                    raise ValueError('Recorded score differs')
            requests[name].append(reader['request'])
    differing = [qid for i,qid in enumerate(expected)
                 if any(requests[name][i]!=requests['baseline'][i] for name in configurations)]
    if differing:raise ValueError('Reader requests are not identical')
    totals = [sum(rows[name][i]['correct'] for name in configurations) for i in range(500)]
    report = {'kind':'five-complete-identical-control-repeat-description','at':study.utc(),
        'previous_four_control_report_sha256':file_sha(prior_path),
        'anchor_control_result_sha256':file_sha(result_path),'execution_sha256':checksums,
        'full_questions':500,'request_identity_gate':True,'differing_requests':differing,
        'correct_per_complete_arm':{name:sum(r['correct'] for r in values) for name,values in rows.items()},
        'success_count_histogram':dict(Counter(totals)),
        'variable_question_ids':[qid for qid,total in zip(expected,totals) if 0<total<5],
        'pairwise_disagreements':{f'{a}__{b}':sum(x['correct']!=y['correct'] for x,y in zip(rows[a],rows[b]))
                                 for a,b in combinations(configurations,2)},
        'anchor_control_vs_original':study.old.paired_stats(rows['baseline'],rows['anchor_control_repeat'],study.base._load_dataset(study.DATASET)),
        'additional_failed_control_repeat':prior['additional_failed_control_repeat'],
        'limits':'Five complete controls with exact reader requests. Descriptive variation, not new feature comparisons, independent holdouts, replacement baselines or a pooled primary score. The earlier failed rank control stays unscored. The registered anchor candidate still uses its own prospectively fixed new control for its primary comparison, and the original baseline only as secondary. This audit changes no pending policy or threshold. Paired question-bootstrap intervals do not capture every source of hosted-provider drift.',
        'new_model_calls':0,'default_changes':False}
    report['sha256']=sha(report)
    write_new(study.REPORTS/'opt-in-complete-control-repeats-v2.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='variable_question_ids'},indent=2))


if __name__=='__main__':main()
