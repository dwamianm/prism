"""Post hoc paired source intervals after the complete registered repair assay."""
from pathlib import Path
import json

import numpy as np

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def interval(values):
    delta = np.asarray(values, dtype=float)
    rng = np.random.default_rng(20260922)
    draws = [float(rng.choice(delta, size=len(delta), replace=True).mean()) for _ in range(10000)]
    return {'difference': float(delta.mean()), 'ci95': np.quantile(draws, [.025, .975]).tolist(),
            'questions': len(delta), 'positive': int((delta > 0).sum()), 'negative': int((delta < 0).sum())}


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    name = 'opt-in-rank-envelope-v2'
    public = repair / 'benchmarks/results/research/2026-09-22'
    private = repair / 'data/opt-in-study' / name
    source = json.loads((public / f'{name}-source-result.json').read_text())
    execution = json.loads((private / 'execution.json').read_text())
    reg = json.loads((public / f'{name}-registration.json').read_text())
    if not source['complete'] or not execution['complete'] or source['verified_cases'] != 500:
        raise RuntimeError('A complete authenticated source assay is required')
    if file_sha(private / 'execution.json') != source['execution_sha256']:
        raise RuntimeError('Source execution identity differs')
    study.old.validate_complete(reg['ordered_question_ids'], execution['rows'])
    values, mismatches = [], []
    for outcome in execution['rows']:
        path = private / 'cases' / f"{outcome['question_id']}.json"
        if file_sha(path) != outcome['case_sha256']:
            raise RuntimeError('Source case changed')
        case = json.loads(path.read_text())
        arms = {key: value['evidence'] for key, value in case['arms'].items()}
        if not arms['baseline']['applicable']:
            continue
        if not all(a['applicable'] and a['required_turns'] == arms['baseline']['required_turns'] for a in arms.values()):
            raise RuntimeError('Applicability or source denominator differs')
        values.append({'question_id': case['question_id'], 'category': case['question_type'],
            'complete': {key: int(a['complete_source_literal_recall']) for key, a in arms.items()},
            'fraction': {key: a['required_source_literals_present']/a['required_turns'] for key, a in arms.items()}})
        if not arms['reranker']['complete_source_literal_recall']:
            original = study.PRIVATE / 'opt-in-successor-v2/reranker' / case['question_id'] / 'capture.json'
            if file_sha(original) != case['legacy_capture_sha256']:
                raise RuntimeError('Original reranker capture changed')
            captured = json.loads(original.read_text())['retrievals'][0]
            if captured['evidence']['complete_turn_recall']:
                mismatches.append({'question_id': case['question_id'],
                    'node_id_complete': True, 'source_literal_complete': False,
                    'required_turns': arms['reranker']['required_turns'],
                    'source_literals_present': arms['reranker']['required_source_literals_present']})
    if len(values) != 470:
        raise RuntimeError('Source applicability count differs')
    comparisons = {}
    for control in ('baseline', 'reranker'):
        comparison = {metric: interval([r[metric]['rank_envelope']-r[metric][control] for r in values])
                      for metric in ('complete', 'fraction')}
        comparison['complete_source_gains'] = [r['question_id'] for r in values if r['complete']['rank_envelope'] > r['complete'][control]]
        comparison['complete_source_losses'] = [r['question_id'] for r in values if r['complete']['rank_envelope'] < r['complete'][control]]
        comparisons[control] = comparison
    result = {'kind': 'post-hoc-complete-repair-source-intervals', 'at': study.utc(),
        'source_result_sha256': file_sha(public / f'{name}-source-result.json'),
        'source_execution_sha256': file_sha(private / 'execution.json'), 'script_sha256': file_sha(Path(__file__)),
        'total_cases': 500, 'applicable_source_cases': 470, 'comparisons': comparisons,
        'node_id_vs_literal_coverage_disagreements': mismatches,
        'inference_boundary': 'Unadjusted paired question-bootstrap intervals, 10000 draws, seed 20260922, after source outcomes were inspected. Source-gate selection remains the original registered count/mean rule. These are source-literal retention metrics, not answer scores, semantic sufficiency, independent confirmation or uncached serving latency.',
        'new_model_calls': 0, 'default_changes': False}
    result['analysis_sha256'] = sha(result)
    write_new(study.REPORTS / 'opt-in-rank-envelope-source-intervals-v1.json', result)
    print(json.dumps({k: {metric: v[metric] for metric in ('complete', 'fraction')} for k, v in comparisons.items()}))
    print(json.dumps({'coverage_metric_disagreements': mismatches}))


if __name__ == '__main__':
    main()
