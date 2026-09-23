"""Post-hoc complete-cohort audit of the frozen anchored-rank primary comparison."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    private = repair / 'data/opt-in-study'
    public = repair / 'benchmarks/results/research/2026-09-22'
    name = 'opt-in-anchored-rank-v1'
    cases = study.base._load_dataset(study.DATASET)
    expected = [c['question_id'] for c in cases]
    answer_path = public / f'{name}-answer-result.json'
    primary = json.loads(answer_path.read_text())
    if not primary['complete']:
        raise RuntimeError('Complete primary comparison required')
    inputs = {}
    rows = {}
    for arm, suffix in [('source', ''), ('control', '-reader-control'), ('candidate', '-reader-candidate')]:
        root = private / (name + suffix)
        result_path = public / (name + ('-source' if arm == 'source' else suffix) + '-result.json')
        result = json.loads(result_path.read_text())
        if (not result['complete'] or result['verified_cases'] != 500
                or file_sha(root / 'execution.json') != result['execution_sha256']):
            raise RuntimeError('Complete authenticated execution required')
        rows[arm] = json.loads((root / 'execution.json').read_text())['rows']
        if [r['question_id'] for r in rows[arm]] != expected:
            raise RuntimeError('Cohort or ordering changed')
        study.old.validate_complete(expected, rows[arm])
        inputs[arm] = file_sha(result_path)
    paired = study.old.paired_stats(rows['control'], rows['candidate'], cases)
    if paired != primary['paired']:
        raise RuntimeError('Primary statistics differ')
    summaries = defaultdict(Counter)
    context_groups = defaultdict(Counter)
    transitions = []
    for case, source, control, candidate in zip(cases, rows['source'], rows['control'], rows['candidate']):
        qid = case['question_id']
        path = private / name / 'cases' / f'{qid}.json'
        if file_sha(path) != source['case_sha256']:
            raise RuntimeError('Source case changed')
        record = json.loads(path.read_text())
        before, after = (record['arms'][a] for a in ('baseline', 'anchored_rank'))
        providers = {}
        for arm, row in [('control', control), ('candidate', candidate)]:
            providers[arm] = {}
            folder = private / f'{name}-reader-{arm}' / 'execution' / qid
            for kind in ('reader', 'judge'):
                p = folder / f'{kind}.json'
                if file_sha(p) != row[f'{kind}_sha256']:
                    raise RuntimeError('Provider record changed')
                providers[arm][kind] = json.loads(p.read_text())
        changed = before['context'] != after['context']
        request_changed = providers['control']['reader']['request'] != providers['candidate']['reader']['request']
        if changed != request_changed:
            raise RuntimeError('Reader request identity differs from context identity')
        a, b = before['evidence'], after['evidence']
        delta = int(candidate['correct']) - int(control['correct'])
        complete_delta = (int(b['complete_source_literal_recall'])
                          - int(a['complete_source_literal_recall'])) if a['applicable'] else None
        group = ('no_annotations' if complete_delta is None else
                 'complete_source_gain' if complete_delta > 0 else
                 'complete_source_loss' if complete_delta < 0 else
                 'complete_sources_both' if a['complete_source_literal_recall'] else
                 'incomplete_sources_both')
        counts = dict(total=1, wins=delta > 0, losses=delta < 0,
                      control_correct=control['correct'], candidate_correct=candidate['correct'])
        summaries[group].update(**counts)
        context_groups['changed' if changed else 'identical'].update(**counts)
        if delta:
            transitions.append({'question_id': qid, 'category': case['question_type'],
                'changed_context': changed, 'control_correct': control['correct'],
                'candidate_correct': candidate['correct'], 'source_group': group,
                'question': case['question'], 'reference': case['answer'],
                'control_answer': providers['control']['reader']['response']['message']['content'],
                'candidate_answer': providers['candidate']['reader']['response']['message']['content'],
                'control_judgment': providers['control']['judge']['response']['message']['content'],
                'candidate_judgment': providers['candidate']['judge']['response']['message']['content']})
    output = {'kind': 'post-hoc-complete-anchored-rank-answer-audit', 'at': study.utc(),
        'cases': 500, 'inputs': inputs, 'primary_result_sha256': file_sha(answer_path),
        'script_sha256': file_sha(Path(__file__)), 'paired_primary': paired,
        'source_groups': dict(summaries), 'context_groups': dict(context_groups),
        'all_answer_transitions': transitions, 'new_model_calls': 0, 'default_changes': False,
        'limits': 'Post-hoc mechanism associations on development data; no relabeling, reruns, selected responses or causal attribution. Source annotations do not guarantee semantic sufficiency.'}
    output['analysis_sha256'] = sha(output)
    write_new(study.REPORTS / 'opt-in-anchored-answer-audit-v1.json', output)
    print(json.dumps({k:v for k,v in output.items() if k != 'all_answer_transitions'}))


if __name__ == '__main__':
    main()
