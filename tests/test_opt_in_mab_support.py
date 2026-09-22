from benchmarks.diagnostics import opt_in_mab_support as support
import pytest

pytestmark = pytest.mark.skipif(not support.UPSTREAM.exists(), reason='Pinned external MAB checkout required')


def task(name, contract, metric):
    return {'dataset_config': {'sub_dataset': name}, 'agent_config': {'reader_output_contract': contract},
            'system_message': 'Original system.', 'metric': metric}


def test_exact_output_contract_does_not_change_retrieval_query():
    prepared = task('icl_banking77_5900shot_balance', 'numeric-label-v1', 'exact_match')
    query = 'Question: My card was lost.\n\nlabel:'
    assert support.adapter._retrieval_query(query) == 'My card was lost.'
    messages = support.reader_messages(prepared, 'Stored examples.', query)
    assert messages[0] == {'role': 'system', 'content': 'Original system.'}
    assert messages[1]['content'].startswith('Stored examples.\n' + query)
    assert 'only ASCII digits' in messages[1]['content']


def test_official_banking_and_eventqa_parsing_remains_in_force():
    banking = task('icl_banking77_5900shot_balance', 'numeric-label-v1', 'exact_match')
    assert support.score(banking, 'Answer: 7', ['7'])['correct']
    assert not support.score(banking, '17', ['7'])['correct']
    eventqa = task('eventqa_65536', 'upstream', 'substring_exact_match')
    answer = support.score(eventqa, 'It happened in Bath.', ['Bath'])
    assert answer['correct'] and answer['metrics']['eventqa_recall'] == 1


def test_budget_and_messages_are_bound_to_request():
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}]
    body = support.request_body(messages, 10)
    assert body['messages'] == messages
    assert body['options']['num_predict'] == 10
    assert body['options']['seed'] == 42
    assert body['think'] is False
