import json

from benchmarks.diagnostics.opt_in_packing_oracle import priority_context
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage, parse_context
from prme.retrieval.tokenization import count_tokens


def fixture(text='exact source', representation='reference'):
    case = {'question_id': 'authored', 'haystack_session_ids': ['s'],
            'haystack_sessions': [[{'content': text, 'has_answer': True}]]}
    full = json.dumps({'id': 'n', 'text': text, 'representation': 'full'}, ensure_ascii=False)
    entry = full if representation == 'full' else json.dumps({'id': 'n', 'text': 'fact:n', 'representation': 'reference'})
    capture = {'context': 'Memory source data.\n[stable_facts]\n' + entry,
               'returned': [{'node_id': 'n', 'source_session_id': 's', 'source_session_position': 0, 'source_turn_index': 0}]}
    return case, capture, {'n': full}


def test_reference_identity_is_not_source_evidence():
    case, capture, entries = fixture()
    assert not literal_coverage(case, capture)['complete_source_literal_recall']
    context, reason = priority_context(case, capture, entries)
    assert reason == 'annotation_priority_with_original_remainder'
    assert literal_coverage(case, {**capture, 'context': context})['complete_source_literal_recall']


def test_complete_context_and_abstention_are_exact_repeats():
    case, capture, entries = fixture(representation='full')
    assert priority_context(case, capture, entries)[0] == capture['context']
    case['question_id'] += '_abs'
    assert priority_context(case, capture, {})[0] == capture['context']


def test_oversized_or_missing_candidate_preserves_control():
    case, capture, entries = fixture('word ' * 10000)
    assert count_tokens(entries['n'], 'cl100k_base') > 3996
    assert priority_context(case, capture, entries) == (capture['context'], 'unchanged_required_full_sources_exceed_budget')
    capture['returned'] = []
    assert priority_context(case, capture, {}) == (capture['context'], 'unchanged_required_source_not_returned')


def test_unicode_line_separator_does_not_split_json_record():
    case, capture, entries = fixture('exact\u2028source', representation='full')
    assert len(parse_context(capture['context'])) == 1
    assert literal_coverage(case, capture)['complete_source_literal_recall']


def test_reference_answer_is_not_an_input_to_selector():
    case, capture, entries = fixture()
    first = priority_context(case, capture, entries)
    case.update(answer='secret answer must not be used', question='irrelevant changed question')
    assert priority_context(case, capture, entries) == first
