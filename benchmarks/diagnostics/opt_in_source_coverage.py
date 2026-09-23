"""Source-literal coverage supplement; never changes registered primary scores."""
from __future__ import annotations

import argparse
from collections import Counter
import json

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import load_complete
from benchmarks.diagnostics.register_opt_in_interactions import sha, write_new


def parse_context(context):
    # splitlines() also splits Unicode paragraph separators inside JSON strings.
    lines = context.split('\n')
    records = [json.loads(line) for line in lines if line.startswith('{')]
    return records


def required_turns(case):
    if case['question_id'].endswith('_abs'):
        return {}
    return {(sid, position, index): turn['content']
        for position, (sid, session) in enumerate(zip(case['haystack_session_ids'], case['haystack_sessions']))
        for index, turn in enumerate(session) if turn.get('has_answer') is True}


def source_key(row):
    return (row['source_session_id'], row['source_session_position'], row['source_turn_index'])


def literal_coverage(case, capture):
    wanted = required_turns(case)
    if case['question_id'].endswith('_abs'):
        return {'applicable': False}
    records = {row['id']: row for row in parse_context(capture['context'])}
    returned = {source_key(row): row['node_id'] for row in capture['returned']}
    present = [key for key, content in wanted.items()
               if (row := records.get(returned.get(key))) is not None
               and bool(content) and content in row['text']]
    return {'applicable': True, 'required_turns': len(wanted),
            'complete_source_literal_recall': bool(wanted) and len(present) == len(wanted),
            'required_source_literals_present': len(present),
            'empty_annotation': not bool(wanted)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    reg = json.loads((study.REPORTS / f'{args.name}-registration.json').read_text())
    cases = study.base._load_dataset(study.DATASET)
    output = {'kind': 'source-literal-coverage-supplement', 'primary_metrics_unchanged': True,
              'registration_sha256': reg['registration_sha256'], 'arms': {},
              'limits': 'Verbatim annotated turns, not semantic entailment or exhaustive real-world evidence.'}
    for arm in reg['arms']:
        folder = study.PRIVATE / args.name / arm['id']
        if not (folder / 'verification.json').exists():
            continue
        rows = load_complete(folder, reg['longmemeval_s']['ordered_question_ids'])
        counts = Counter()
        disagreements = []
        outcomes = {}
        for case, row in zip(cases, rows):
            capture = json.loads((folder / case['question_id'] / 'capture.json').read_text())['retrievals'][0]
            literal = literal_coverage(case, capture)
            outcomes[case['question_id']] = literal
            if not literal['applicable']:
                continue
            counts['applicable'] += 1
            counts['complete_source_literals'] += literal['complete_source_literal_recall']
            if capture['evidence']['complete_turn_recall'] != literal['complete_source_literal_recall']:
                disagreements.append(case['question_id'])
            if not row['correct']:
                counts['incorrect_with_complete_source_literals'] += literal['complete_source_literal_recall']
                counts['incorrect_missing_source_literals'] += not literal['complete_source_literal_recall']
        output['arms'][arm['id']] = {'counts': dict(counts), 'node_presence_disagreements': disagreements,
                                     'outcomes': outcomes}
    output['sha256'] = sha(output)
    write_new(study.REPORTS / args.output, output)
    print(json.dumps({name: value['counts'] for name, value in output['arms'].items()}))


if __name__ == '__main__':
    main()
