"""Describe all repaired source changes against frozen baseline error classes."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    root = repair / 'data/opt-in-study/opt-in-rank-envelope-v2'
    public = repair / 'benchmarks/results/research/2026-09-22'
    source_path = public / 'opt-in-rank-envelope-v2-source-result.json'
    source = json.loads(source_path.read_text())
    execution = json.loads((root / 'execution.json').read_text())
    if (not source['complete'] or source['verified_cases'] != 500
            or file_sha(root / 'execution.json') != source['execution_sha256']):
        raise RuntimeError('Complete authenticated repair source result required')
    analysis_path = study.REPORTS / 'opt-in-successor-v2-analysis-01-complete.json'
    baseline = json.loads(analysis_path.read_text())['arms']['baseline']
    errors = {r['question_id']: r['diagnostic'] for r in baseline['errors']}
    summaries = defaultdict(Counter)
    changes = []
    for row in execution['rows']:
        path = root / 'cases' / f"{row['question_id']}.json"
        if file_sha(path) != row['case_sha256']:
            raise RuntimeError('Source case identity differs')
        case = json.loads(path.read_text())
        before, after = [case['arms'][a] for a in ('baseline', 'rank_envelope')]
        group = errors.get(case['question_id'], 'baseline_correct')
        summaries[group]['total'] += 1
        changed = before['context'] != after['context']
        summaries[group]['changed_context'] += changed
        a, b = before['evidence'], after['evidence']
        if not a['applicable']:
            continue
        summaries[group]['source_applicable'] += 1
        delta = b['required_source_literals_present'] - a['required_source_literals_present']
        complete_delta = int(b['complete_source_literal_recall']) - int(a['complete_source_literal_recall'])
        summaries[group]['complete_source_gain'] += complete_delta > 0
        summaries[group]['complete_source_loss'] += complete_delta < 0
        summaries[group]['more_source_turns'] += delta > 0
        summaries[group]['fewer_source_turns'] += delta < 0
        if delta:
            changes.append({'question_id': case['question_id'], 'category': case['question_type'],
                'baseline_error_class': group, 'annotated_turns': a['required_turns'],
                'baseline_source_turns': a['required_source_literals_present'],
                'repair_source_turns': b['required_source_literals_present'],
                'complete_source_delta': complete_delta})
    if sum(r['total'] for r in summaries.values()) != 500:
        raise RuntimeError('Incomplete cohort')
    output = {'kind': 'post-hoc-complete-source-versus-baseline-error-description',
        'at': study.utc(), 'source_result_sha256': file_sha(source_path),
        'baseline_analysis_sha256': file_sha(analysis_path), 'script_sha256': file_sha(Path(__file__)),
        'cases': 500, 'summaries': dict(summaries), 'all_source_turn_changes': changes,
        'limits': 'Associations with the original fixed baseline answers, not repaired answer outcomes. Full source literals are neither necessary nor sufficient for answer correctness. No source selection, contexts, gates or scores changed; the frozen full-cohort answer trial remains required.',
        'model_calls': 0, 'default_changes': False}
    output['analysis_sha256'] = sha(output)
    write_new(study.REPORTS / 'opt-in-repair-source-error-bridge-v1.json', output)
    print(json.dumps(output['summaries']))


if __name__ == '__main__':
    main()
