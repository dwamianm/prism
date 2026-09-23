from copy import deepcopy

import pytest

from benchmarks.integrations.run_gpt54_comparison import CATEGORIES, source_turns, verdict


def test_source_boundary_keeps_short_turns_and_excludes_annotations():
    conversation = {
        'session_1_date_time': '1:56 pm on 8 May, 2023',
        'session_1': [
            {'speaker':'A', 'text':'Yes.', 'dia_id':'D1:1'},
            {'speaker':'B', 'text':'Look.', 'dia_id':'D1:2', 'blip_caption':'a blue bird'},
        ],
        'qa': [{'answer':'FORBIDDEN'}], 'observation':'FORBIDDEN',
        'event_summary':'FORBIDDEN', 'session_summary':'FORBIDDEN',
    }
    rows = source_turns(conversation)
    assert len(rows) == 2
    assert rows[0]['content'].endswith('A: Yes.')
    assert rows[1]['content'].endswith('[Image: a blue bird]')
    assert 'FORBIDDEN' not in str(rows)
    assert rows[0]['event_time'].year == 2023
    assert rows[0]['event_time'].tzinfo is not None
    changed = deepcopy(conversation)
    changed['qa'] = [{'answer':'different'}]
    assert source_turns(changed) == rows


def test_upstream_categories_are_not_old_adapter_names():
    assert CATEGORIES == {1:'multi-hop', 2:'temporal', 3:'open-domain', 4:'single-hop'}


@pytest.mark.parametrize('text', ['yes and no', 'maybe yes', '', '{"yes": true}'])
def test_ambiguous_verdicts_fail_closed(text):
    with pytest.raises(ValueError):
        verdict(text)


def test_binary_verdict():
    assert verdict('Yes.') is True
    assert verdict('NO\n') is False
