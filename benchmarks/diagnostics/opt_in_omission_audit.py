"""Descriptive, answer-blind mechanism audit of the fixed baseline's sources."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import describe, load_complete
from benchmarks.diagnostics.opt_in_source_coverage import parse_context, required_turns, source_key
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme.retrieval.tokenization import count_tokens


NAME = 'opt-in-baseline-omission-audit-v1'


def main():
    parent = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    folder = study.PRIVATE / 'opt-in-successor-v2/baseline'
    cases = study.base._load_dataset(study.DATASET)
    outcomes = load_complete(folder, parent['longmemeval_s']['ordered_question_ids'])
    plan = {'kind': 'post-hoc-baseline-source-mechanism-audit', 'registered_at': study.utc(),
        'baseline_already_inspected': True, 'cases': len(cases),
        'source_sha256': file_sha(Path(__file__)), 'dataset_sha256': file_sha(study.DATASET),
        'baseline_verification_sha256': file_sha(folder / 'verification.json'),
        'question': 'Are annotated omitted turns low in final rank, long, weak on lexical/semantic scores, or absent from all returned sources?',
        'method': 'All 500 authenticated baseline captures. Source annotations label required turns only after retrieval. Record full-source presence, final returned rank, content tokens (not serialized cost), original roles, score components and same-session packed-record counts. Summarize all required turns and all incorrect annotated cases separately. No provider calls, candidate modification, rescore, significance test or promotion claim.'}
    plan['registration_sha256'] = sha(plan)
    write_new(study.REPORTS / f'{NAME}-registration.json', plan)
    groups = defaultdict(list)
    questions = []
    for case, outcome in zip(cases, outcomes):
        path = folder / case['question_id'] / 'capture.json'
        if file_sha(path) != outcome['capture_sha256']:
            raise study.old.ResearchFailure('Baseline capture changed')
        capture = json.loads(path.read_text())['retrievals'][0]
        packed = {record['id']: record for record in parse_context(capture['context'])}
        returned = {source_key(record): (rank, record) for rank, record in enumerate(capture['returned'], 1)}
        packed_by_session = Counter((r['source_session_id'], r['source_session_position']) for r in capture['packed'])
        rows = []
        for key, content in required_turns(case).items():
            rank, record = returned.get(key, (None, None))
            trace = capture['receipt']['score_provenance'][record['node_id']]['trace'] if record else {}
            present = bool(record and record['node_id'] in packed and content in packed[record['node_id']]['text'])
            row = {'source_key': list(key), 'returned_rank': rank, 'source_content_tokens': count_tokens(content, 'cl100k_base'),
                'role': case['haystack_sessions'][key[1]][key[2]]['role'], 'source_present': present,
                'semantic': trace.get('semantic_similarity'), 'lexical': trace.get('lexical_relevance'),
                'composite': record['composite_score'] if record else None,
                'packed_records_same_session': packed_by_session[key[:2]],
                'question_correct': outcome['correct'], 'category': case['question_type']}
            rows.append(row)
            groups['all_required_present' if present else 'all_required_missing'].append(row)
            if not outcome['correct']:
                groups['incorrect_required_present' if present else 'incorrect_required_missing'].append(row)
        questions.append({'question_id': case['question_id'], 'required_turns': rows})
    summaries = {}
    for name, rows in groups.items():
        summaries[name] = {'turns': len(rows), 'roles': dict(Counter(r['role'] for r in rows)),
            'categories': dict(Counter(r['category'] for r in rows)),
            'not_returned': sum(r['returned_rank'] is None for r in rows),
            'same_session_has_packed_record': sum(r['packed_records_same_session'] > 0 for r in rows),
            **{key: describe([r[key] for r in rows if r[key] is not None]) for key in
               ('returned_rank', 'source_content_tokens', 'semantic', 'lexical', 'composite', 'packed_records_same_session')}}
    result = {'kind': plan['kind'], 'complete': True, 'cases': len(questions),
        'registration_sha256': plan['registration_sha256'], 'summaries': summaries, 'questions': questions,
        'interpretation_limit': 'Descriptive associations over inspected development data. Whole annotated turns are not validated claims, minimal evidence spans, or proof of semantic sufficiency.',
        'scores_changed': False, 'production_changed': False}
    result['result_sha256'] = sha(result)
    write_new(study.REPORTS / f'{NAME}-result.json', result)
    print(json.dumps(summaries))


if __name__ == '__main__':
    main()
