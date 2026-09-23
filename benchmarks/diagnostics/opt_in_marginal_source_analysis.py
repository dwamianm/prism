"""Complete-cohort post hoc intervals for every frozen marginal source policy."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics import opt_in_rank_source_analysis as stats
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    name = 'opt-in-marginal-packing-v2'
    root = study.PRIVATE / name
    source_path = study.REPORTS / f'{name}-source-result.json'
    source = json.loads(source_path.read_text())
    reg = json.loads((study.REPORTS / f'{name}-registration.json').read_text())
    execution = json.loads((root / 'execution.json').read_text())
    if (not source['complete'] or source['verified_cases'] != 500
            or file_sha(root / 'execution.json') != source['execution_sha256']):
        raise RuntimeError('A complete authenticated source assay is required')
    study.old.validate_complete(reg['ordered_question_ids'], execution['rows'])
    baseline_path = study.REPORTS / 'opt-in-successor-v2-analysis-01-complete.json'
    errors = {r['question_id']: r['diagnostic'] for r in json.loads(baseline_path.read_text())['arms']['baseline']['errors']}
    values = {n: {'complete': [], 'fraction': [], 'gains': [], 'losses': [],
                   'by_baseline_error': defaultdict(Counter)} for n in reg['policies']}
    for row in execution['rows']:
        path = root / 'cases' / f"{row['question_id']}.json"
        if file_sha(path) != row['case_sha256']:
            raise RuntimeError('Source case identity differs')
        case = json.loads(path.read_text())
        if file_sha(root / 'inputs' / path.name) != case['inputs_sha256']:
            raise RuntimeError('Candidate snapshot identity differs')
        control = case['arms']['control']['evidence']
        group = errors.get(row['question_id'], 'baseline_correct')
        for policy, metrics in values.items():
            current = case['arms'][policy]
            counts = metrics['by_baseline_error'][group]
            counts['total'] += 1
            counts['changed_context'] += current['context'] != case['arms']['control']['context']
            if not control['applicable']:
                continue
            candidate = current['evidence']
            if not candidate['applicable'] or candidate['required_turns'] != control['required_turns']:
                raise RuntimeError('Source applicability differs')
            complete = int(candidate['complete_source_literal_recall']) - int(control['complete_source_literal_recall'])
            fraction = (candidate['required_source_literals_present'] - control['required_source_literals_present']) / control['required_turns']
            metrics['complete'].append(complete)
            metrics['fraction'].append(fraction)
            counts['complete_source_gain'] += complete > 0
            counts['complete_source_loss'] += complete < 0
            counts['more_source_turns'] += fraction > 0
            counts['fewer_source_turns'] += fraction < 0
            if complete > 0:
                metrics['gains'].append(row['question_id'])
            elif complete < 0:
                metrics['losses'].append(row['question_id'])
    comparisons = {}
    for policy, metrics in values.items():
        if len(metrics['complete']) != 470:
            raise RuntimeError('Full applicable source cohort required')
        comparisons[policy] = {**metrics,
            'complete': stats.interval(metrics['complete']), 'fraction': stats.interval(metrics['fraction']),
            'by_baseline_error': dict(metrics['by_baseline_error'])}
    output = {'kind': 'post-hoc-complete-marginal-source-intervals', 'at': study.utc(),
        'source_result_sha256': file_sha(source_path), 'baseline_analysis_sha256': file_sha(baseline_path),
        'script_sha256': file_sha(Path(__file__)), 'interval_helper_sha256': file_sha(Path(stats.__file__)),
        'total_cases': 500, 'applicable_source_cases': 470, 'comparisons': comparisons,
        'selected_policy_unchanged': source['selected_policy'], 'new_model_calls': 0,
        'limits': 'All three registered policies and all source cases retained. Unadjusted paired question-bootstrap intervals, 10000 draws, seed 20260922, inspected after source completion. Source outcomes and baseline-error associations are not repaired answer scores or confirmation. No selection gate, source context or baseline score changes.'}
    output['analysis_sha256'] = sha(output)
    write_new(study.REPORTS / 'opt-in-marginal-source-intervals-v1.json', output)
    print(json.dumps({p: {k: v[k] for k in ('complete', 'fraction', 'by_baseline_error')} for p, v in comparisons.items()}))


if __name__ == '__main__':
    main()
