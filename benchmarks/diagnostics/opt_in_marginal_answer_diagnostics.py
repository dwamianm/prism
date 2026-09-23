"""All-case post-hoc source/answer associations for the complete marginal trial."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    name = 'opt-in-marginal-packing-v2'
    root = study.PRIVATE / name
    source_file = study.REPORTS / f'{name}-source-result.json'
    answer_file = study.REPORTS / f'{name}-answer-result.json'
    source, primary = [json.loads(p.read_text()) for p in (source_file, answer_file)]
    if (not source['complete'] or not primary['complete']
            or file_sha(root/'execution.json') != source['execution_sha256']):
        raise RuntimeError('Complete authenticated trials required')
    cases = study.base._load_dataset(study.DATASET)
    expected = [c['question_id'] for c in cases]
    source_rows = json.loads((root/'execution.json').read_text())['rows']
    study.old.validate_complete(expected, source_rows)
    outcomes = {}
    for role, child in primary['child_results'].items():
        result = json.loads((study.REPORTS/f'{child}-result.json').read_text())
        execution = study.PRIVATE/child/'execution.json'
        if not result['complete'] or file_sha(execution) != result['execution_sha256']:
            raise RuntimeError('Complete child identity differs')
        outcomes[role] = json.loads(execution.read_text())['rows']
        study.old.validate_complete(expected, outcomes[role])
    summaries = defaultdict(Counter)
    groups = defaultdict(Counter)
    changes, source_changes = [], []
    for source_row, before, after in zip(source_rows, outcomes['control'], outcomes['candidate']):
        qid = source_row['question_id']
        path = root/'cases'/f'{qid}.json'
        if file_sha(path) != source_row['case_sha256']:
            raise RuntimeError('Source case changed')
        record = json.loads(path.read_text())
        a, b = (record['arms'][k] for k in ('control',primary['selected_policy']))
        evidence_a, evidence_b = a['evidence'], b['evidence']
        changed = a['context'] != b['context']
        delta = int(after['correct'])-int(before['correct'])
        if evidence_a['applicable']:
            complete_delta = int(evidence_b['complete_source_literal_recall'])-int(evidence_a['complete_source_literal_recall'])
            turn_delta = evidence_b['required_source_literals_present']-evidence_a['required_source_literals_present']
            group = ('complete_source_gain' if complete_delta > 0 else 'complete_source_loss' if complete_delta < 0
                     else 'complete_sources_both' if evidence_a['complete_source_literal_recall'] else 'incomplete_sources_both')
        else:
            complete_delta = turn_delta = None
            group = 'no_annotations'
        summaries[group].update(total=1, wins=delta>0, losses=delta<0, control_correct=before['correct'],
                                candidate_correct=after['correct'], changed_context=changed)
        groups['changed_context' if changed else 'unchanged_context'].update(total=1,wins=delta>0,losses=delta<0)
        for role, outcome in [('control',before),('candidate',after)]:
            folder = study.PRIVATE/primary['child_results'][role]/'execution'/qid
            for kind in ('reader','judge'):
                if file_sha(folder/f'{kind}.json') != outcome[f'{kind}_sha256']:
                    raise RuntimeError('Provider artifact changed')
        row = {'question_id':qid,'category':record['question_type'],'changed_context':changed,
               'control_correct':before['correct'],'candidate_correct':after['correct'],
               'source_group':group,'complete_source_delta':complete_delta,'source_turn_delta':turn_delta}
        if delta: changes.append(row)
        if turn_delta: source_changes.append(row)
    assert sum(v['total'] for v in summaries.values()) == 500
    assert sum(v['wins'] for v in summaries.values()) == primary['paired']['wins']
    assert sum(v['losses'] for v in summaries.values()) == primary['paired']['losses']
    report = {'kind':'post-hoc-complete-marginal-source-answer-association','at':study.utc(),
              'source_result_sha256':file_sha(source_file),'primary_result_sha256':file_sha(answer_file),
              'script_sha256':file_sha(Path(__file__)),'cases':500,'primary':primary['paired'],
              'source_groups':dict(summaries),'input_groups':dict(groups),
              'all_answer_transitions':changes,'all_source_turn_changes':source_changes,
              'new_model_calls':0,'answer_scores_changed':False,
              'limits':'Post-hoc associations on all 500 cases, not causal assignments. Literal source completeness is neither semantic sufficiency nor proof of reader fault. Every original and primary answer score remains unchanged.'}
    report['sha256'] = sha(report)
    write_new(study.REPORTS/'opt-in-marginal-answer-diagnostics-v1.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('all_answer_transitions','all_source_turn_changes')}))


if __name__ == '__main__': main()
