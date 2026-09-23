"""Post-hoc source-coverage diagnostics; never inputs to retrieval or judging.

Annotation retention is not semantic answerability. Missing annotations can have
equivalent evidence elsewhere; retained turns can still require interpretation.
Only run after both complete cohorts have passed the separate result verifier.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path

import duckdb

from benchmarks.integrations import run_gpt54_comparison as s
from benchmarks.integrations.gpt54_budget import digest, write_new


def summarize(rows):
    counts = Counter()
    categories = defaultdict(Counter)
    for row in rows:
        state = row['coverage']
        outcome = 'correct' if row['correct'] else 'incorrect'
        key = f'{state}_{outcome}'
        counts[key] += 1
        categories[row['question_type']][key] += 1
    return {'questions':len(rows), 'coverage_by_answer':dict(counts),
            'categories':{key:dict(value) for key,value in sorted(categories.items())},
            'annotation_totals':{key:sum(row.get(key,0) for row in rows)
                                 for key in ['required_turns','annotated_turns_returned',
                                             'annotated_turns_packed','missing_at_packing']},
            'rows':rows}


def longmem_rows(result):
    rows = []
    questions = {q['question_id']:q for q in s.question_rows('longmemeval')}
    for row in result['rows']:
        capture = json.loads((s.PRIVATE/'longmemeval/contexts'/(row['question_id']+'.json')).read_text())
        evidence = capture['evidence']
        state = 'abstention'
        metrics = {}
        if evidence['applicable']:
            state = 'all_annotated_turns_packed' if evidence['complete_turn_recall'] else 'annotated_turns_missing'
            question = questions[row['question_id']]
            wanted = {(sid,pos,index)
                      for pos,(sid,session) in enumerate(zip(question['haystack_session_ids'],
                                                             question['haystack_sessions'],strict=True))
                      for index,turn in enumerate(session) if turn.get('has_answer') is True}
            historical = json.loads(Path(capture['source_capture']).read_text())['retrievals'][0]
            def identity(item):
                return (item['source_session_id'],item['source_session_position'],item['source_turn_index'])
            returned = {identity(item) for item in historical['returned']}
            packed = {identity(item) for item in historical['packed']}
            if (len(wanted) != evidence['required_turns']
                or len(wanted & packed) != evidence['retrieved_required_turns']):
                raise ValueError('Historical annotation metrics differ')
            metrics = {'required_turns':len(wanted), 'annotated_turns_returned':len(wanted & returned),
                       'annotated_turns_packed':len(wanted & packed),
                       'missing_at_packing':len((wanted & returned)-packed)}
        rows.append({key:row[key] for key in ['question_id','question_type','correct']} |
                    {'coverage':state,'annotation_metrics':evidence,**metrics})
    return rows


def locomo_rows(result):
    prepared = json.loads((s.PRIVATE/'locomo/prepared.json').read_text())
    source_ids = {}
    for record in prepared['packs']:
        with duckdb.connect(record['config']['db_path'], read_only=True) as con:
            records = con.execute('SELECT id, metadata FROM nodes').fetchall()
        mapped = {}
        for node_id, raw in records:
            metadata = json.loads(raw) if isinstance(raw,str) else raw
            dialog_id = (metadata or {}).get('source_dialog_id')
            if dialog_id:
                mapped[str(node_id)] = dialog_id
        source_ids[record['conversation_id']] = mapped
    questions = {q['question_id']:q for q in s.question_rows('locomo')}
    rows = []
    for row in result['rows']:
        qid = row['question_id']
        question = questions[qid]
        mapped = source_ids[question['conversation_id']]
        capture = json.loads((s.PRIVATE/'locomo/contexts'/(qid+'.json')).read_text())
        candidates = capture['receipt']['candidates']
        returned = {mapped[c['node_id']] for c in candidates if c['node_id'] in mapped}
        packed = {mapped[c['node_id']] for c in candidates if c['in_context'] and c['node_id'] in mapped}
        wanted = set(question.get('evidence',[]))
        unresolved = wanted - set(mapped.values())
        if not wanted:
            state = 'no_annotations'
        elif unresolved:
            state = 'unresolved_annotations'
        else:
            state = 'all_annotated_turns_packed' if wanted <= packed else 'annotated_turns_missing'
        rows.append({key:row[key] for key in ['question_id','question_type','correct']} |
                    {'coverage':state, 'required_turns':len(wanted),
                     'annotated_turns_returned':len(wanted & returned),
                     'annotated_turns_packed':len(wanted & packed),
                     'missing_at_packing':len((wanted & returned)-packed),
                     'unresolved_annotations':sorted(unresolved)})
    return rows


def main():
    verification_path = s.PUBLIC/'gpt54-comparison-v1-verification.json'
    verification = json.loads(verification_path.read_text())
    if not verification['complete']:
        raise ValueError('Both full cohorts must be authenticated before diagnosis')
    output = {'kind':'post-hoc-annotation-retention', 'created_at':s.utc(),
              'verification_sha256':digest(verification_path), 'source_sha256':digest(Path(__file__)),
              'limits':'Diagnostic only, not a registered endpoint, causal test or semantic entailment assessment. '
                       'LoCoMo requires exact annotation/source dialog identities; unresolved annotations stay visible.',
              'benchmarks':{}}
    for benchmark, analyze in [('longmemeval',longmem_rows),('locomo',locomo_rows)]:
        path = s.PUBLIC/f'gpt54-{benchmark}-v1-result.json'
        if digest(path) != verification['benchmarks'][benchmark]['result_sha256']:
            raise ValueError('Authenticated result changed')
        result = json.loads(path.read_text())
        output['benchmarks'][benchmark] = summarize(analyze(result))
    write_new(s.PUBLIC/'gpt54-posthoc-evidence-diagnostics.json',output)
    lines = ['# GPT-5.4 post-hoc evidence-retention diagnostics', '',
             'These diagnostics use completed, authenticated runs. Dataset evidence annotations '
             'were used only after execution and did not influence ingestion, retrieval or judging. '
             'Retention is not a semantic answerability test: equivalent evidence can exist elsewhere, '
             'and retained evidence can still be misinterpreted.', '',
             '| Benchmark | Annotated turns | In returned candidates | In packed context | Lost at packing |',
             '|---|---:|---:|---:|---:|']
    for benchmark, summary in output['benchmarks'].items():
        totals = summary['annotation_totals']
        lines.append(f'| {benchmark} | {totals["required_turns"]} | '
                     f'{totals["annotated_turns_returned"]} | {totals["annotated_turns_packed"]} | '
                     f'{totals["missing_at_packing"]} |')
    for benchmark, summary in output['benchmarks'].items():
        lines += ['', f'## {benchmark}', '', '| Evidence state | Correct | Incorrect |', '|---|---:|---:|']
        counts = summary['coverage_by_answer']
        states = sorted({row['coverage'] for row in summary['rows']})
        for state in states:
            lines.append(f'| {state.replace("_", " ")} | {counts.get(state+"_correct",0)} | '
                         f'{counts.get(state+"_incorrect",0)} |')
    lines += ['', 'Counts are question/annotation incidences, so the same source turn can occur '
              'in several questions. Unresolved LoCoMo annotation identities are reported separately '
              'and never silently treated as missing retrieved evidence.', '',
              'Next experiments should distinguish candidate discovery from packing loss, then test '
              'answer-blind evidence selection on complete registered cohorts. Errors with retained '
              'annotations need a separate interpretation/conflict/temporal audit. These diagnostics '
              'do not authorize default changes or establish that a particular fix will work.', '',
              '[Full diagnostics and per-question records](gpt54-posthoc-evidence-diagnostics.json).', '']
    with (s.PUBLIC/'GPT54-EVIDENCE-DIAGNOSTICS.md').open('x') as handle:
        handle.write('\n'.join(lines))
    print(json.dumps({key:{k:v for k,v in value.items() if k!='rows'}
                      for key,value in output['benchmarks'].items()},indent=2))


if __name__=='__main__':
    main()
