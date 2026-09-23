"""Post hoc error persistence audit over three complete, identical-input arms."""
from collections import Counter
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import load_complete
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    secondary_path = study.REPORTS / 'opt-in-successor-secondary-analysis-05-complete.json'
    analysis_path = study.REPORTS / 'opt-in-successor-v2-analysis-05-complete.json'
    secondary = json.loads(secondary_path.read_text())
    analysis = json.loads(analysis_path.read_text())
    for value in (secondary, analysis):
        if value['analysis_sha256'] != sha({k: v for k, v in value.items() if k != 'analysis_sha256'}):
            raise RuntimeError('Input analysis checksum differs')
    repeat = secondary['reader_variation']
    if not repeat['input_gate_passed'] or repeat['differing_request_questions'] or repeat['evidence_operations']:
        raise RuntimeError('Identical-input repeated-control gate did not pass')
    reg = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    ids = reg['longmemeval_s']['ordered_question_ids']
    arms = repeat['arms']
    rows = {a: load_complete(study.PRIVATE / 'opt-in-successor-v2' / a, ids) for a in arms}
    if any(sum(r['correct'] for r in rows[a]) != repeat['correct_per_arm'][a] for a in arms):
        raise RuntimeError('Repeated-control scores differ')
    errors = {r['question_id']: r for r in analysis['arms']['baseline']['errors']}
    groups = {'always_correct': [], 'always_incorrect': [], 'variable': []}
    mechanisms, categories = Counter(), Counter()
    persistent = []
    for index, qid in enumerate(ids):
        scores = {a: bool(rows[a][index]['correct']) for a in arms}
        count = sum(scores.values())
        group = 'always_correct' if count == len(arms) else 'always_incorrect' if count == 0 else 'variable'
        groups[group].append(qid)
        if group == 'always_incorrect':
            error = errors[qid]
            mechanisms[error['diagnostic']] += 1
            categories[error['category']] += 1
            persistent.append({k: error[k] for k in ('question_id', 'category', 'diagnostic')})
    if (len(ids) != 500 or sum(map(len, groups.values())) != len(ids)
            or len(groups['always_correct']) != repeat['success_count_histogram']['3']
            or len(groups['always_incorrect']) != repeat['success_count_histogram']['0']):
        raise RuntimeError('Complete-cohort partition differs')
    result = {'kind': 'post-hoc-identical-input-error-persistence-audit', 'at': study.utc(),
        'inputs': {p.name: file_sha(p) for p in (secondary_path, analysis_path)},
        'source_sha256': file_sha(Path(__file__)), 'questions': len(ids), 'arms': arms,
        'counts': {k: len(v) for k, v in groups.items()},
        'persistent_error_mechanisms': dict(mechanisms), 'persistent_error_categories': dict(categories),
        'persistent_errors': persistent, 'groups': groups,
        'interpretation': 'Descriptive development audit after outcomes were inspected. Three repeated inputs do not establish deterministic correctness or isolate reader from judge error. Source annotations classify coverage, not semantic sufficiency. All future registered comparisons retain the full cohort; this is not a selected evaluation subset.',
        'development_execution_note': 'Initial analysis invocation failed before writing output because a path string was supplied to the checksum helper. Corrected to pathlib.Path; no benchmark case or model call was run or replaced.',
        'new_model_calls': 0, 'rescores': 0, 'default_changes': False}
    result['analysis_sha256'] = sha(result)
    write_new(study.REPORTS / 'opt-in-stable-errors-v1-result.json', result)
    print(json.dumps({k: result[k] for k in ('counts', 'persistent_error_mechanisms', 'persistent_error_categories')}))


if __name__ == '__main__':
    main()
