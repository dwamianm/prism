"""Post-hoc all-case source/answer audit of the completed rank repair child.

This cannot restore the failed primary control repeat. No new inference,
relabeling, response selection, or candidate tuning occurs here.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    repair = Path('/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22')
    private = repair / 'data/opt-in-study'
    public = repair / 'benchmarks/results/research/2026-09-22'
    source_root = private / 'opt-in-rank-envelope-v2'
    reader_root = private / 'opt-in-rank-envelope-v2-reader-candidate'
    source_path = public / 'opt-in-rank-envelope-v2-source-result.json'
    answer_path = public / 'opt-in-rank-envelope-v2-reader-candidate-result.json'
    source, answer = [json.loads(p.read_text()) for p in (source_path, answer_path)]
    for result, root in ((source, source_root), (answer, reader_root)):
        if (not result['complete'] or result['verified_cases'] != 500
                or file_sha(root / 'execution.json') != result['execution_sha256']):
            raise RuntimeError('Complete authenticated input required')
    source_rows = json.loads((source_root / 'execution.json').read_text())['rows']
    outcomes = json.loads((reader_root / 'execution.json').read_text())['rows']
    cases = study.base._load_dataset(study.DATASET)
    expected = [c['question_id'] for c in cases]
    if [r['question_id'] for r in source_rows] != expected or [r['question_id'] for r in outcomes] != expected:
        raise RuntimeError('Cohort or order differs')
    study.old.validate_complete(expected, outcomes)
    historical = study.PRIVATE / 'opt-in-successor-v2'
    controls = {}
    for arm in ('baseline', 'reranker'):
        root = historical / arm
        verification = json.loads((root / 'verification.json').read_text())
        if (not verification['complete'] or verification['cases'] != 500
                or file_sha(root / 'execution.json') != verification['execution_sha256']):
            raise RuntimeError('Missing complete-arm verification')
        controls[arm] = json.loads((root / 'execution.json').read_text())['rows']
        study.old.validate_complete(expected, controls[arm])
    summaries = defaultdict(Counter)
    transitions = []
    source_changes = []
    for src, candidate, original in zip(source_rows, outcomes, controls['baseline']):
        qid = src['question_id']
        path = source_root / 'cases' / f'{qid}.json'
        if file_sha(path) != src['case_sha256']:
            raise RuntimeError('Source case identity differs')
        record = json.loads(path.read_text())
        before, after = (record['arms'][a] for a in ('baseline', 'rank_envelope'))
        folder = reader_root / 'execution' / qid
        for kind in ('reader', 'judge'):
            if file_sha(folder / f'{kind}.json') != candidate[f'{kind}_sha256']:
                raise RuntimeError('Reader/judge identity differs')
        changed = before['context'] != after['context']
        delta = int(candidate['correct']) - int(original['correct'])
        a, b = before['evidence'], after['evidence']
        complete_delta = (int(b['complete_source_literal_recall'])
                          - int(a['complete_source_literal_recall'])) if a['applicable'] else None
        fraction_delta = (b['required_source_literals_present']
                          - a['required_source_literals_present']) if a['applicable'] else None
        group = ('no_annotations' if complete_delta is None else
                 'complete_source_gain' if complete_delta > 0 else
                 'complete_source_loss' if complete_delta < 0 else
                 'complete_sources_both' if a['complete_source_literal_recall'] else
                 'incomplete_sources_both')
        summaries[group].update(total=1, wins=delta > 0, losses=delta < 0,
                                baseline_correct=original['correct'],
                                candidate_correct=candidate['correct'], changed_context=changed)
        row = {'question_id': qid, 'category': record['question_type'],
               'changed_context': changed, 'baseline_correct': original['correct'],
               'candidate_correct': candidate['correct'], 'source_group': group,
               'complete_source_delta': complete_delta, 'source_turn_delta': fraction_delta}
        if delta:
            transitions.append(row)
        if fraction_delta:
            source_changes.append(row)
    if sum(r['total'] for r in summaries.values()) != 500:
        raise RuntimeError('Incomplete audit')
    output = {'kind': 'post-hoc-complete-rank-repair-answer-diagnostics', 'at': study.utc(),
              'source_result_sha256': file_sha(source_path), 'answer_result_sha256': file_sha(answer_path),
              'script_sha256': file_sha(Path(__file__)), 'cases': 500,
              'primary_comparison_status': 'unavailable_failed_control_repeat',
              'secondary_original_control': answer['paired'],
              'post_hoc_original_broken_reranker': study.old.paired_stats(controls['reranker'], outcomes, cases),
              'source_groups': dict(summaries), 'all_answer_transitions': transitions,
              'all_source_turn_changes': source_changes,
              'limits': 'Whole-cohort post-hoc mechanism associations only. The original baseline is not replaced. Restoring a broken opt-in arm does not establish improvement over production. Source literal coverage does not prove semantic sufficiency; changed context does not prove causation in a stochastic reader/judge.',
              'model_calls': 0, 'default_changes': False}
    output['analysis_sha256'] = sha(output)
    write_new(study.REPORTS / 'opt-in-rank-answer-diagnostics-v1.json', output)
    print(json.dumps({k: v for k, v in output.items() if k not in ('all_answer_transitions', 'all_source_turn_changes')}))


if __name__ == '__main__':
    main()
