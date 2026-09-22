import json

import pytest

from benchmarks.diagnostics import opt_in_mab_matrix as study
from benchmarks.diagnostics.register_opt_in_interactions import clean_config, sha, write_new


def arm(name='baseline', fresh=False):
    config = clean_config()
    config.update(db_path='{pack}/memory.duckdb', vector_path='{pack}/vectors.usearch',
                  lexical_path='{pack}/lexical_index', duckdb_threads=1,
                  organizer={**config['organizer'], 'opportunistic_enabled': False})
    return {'id': name, 'config': config, 'stratum': 'fresh' if fresh else 'historical'}


async def test_ingestion_refuses_paths_outside_its_private_pack(tmp_path):
    unsafe = arm()
    unsafe['config']['db_path'] = str(tmp_path / 'outside.duckdb')
    with pytest.raises(study.study.old.ResearchFailure, match='escaped its private pack'):
        await study.ingest(['authored source'], 'authored', 0, unsafe, tmp_path / 'private', None, 'authored')
    assert not (tmp_path / 'outside.duckdb').exists()


def test_source_partition_and_scope_do_not_depend_on_retrieval_flag():
    items = study.source_items(['First source.\n\n', 'Second source.'], 'eventqa_65536', 4)
    assert len(items) == 2
    assert {i.session_id for i in items} == {'eventqa_65536:context:4'}
    assert all(i.role == 'tool' and i.scope == study.Scope.PROJECT for i in items)
    assert [i.metadata['source_chunk_index'] for i in items] == [0, 1]


def test_no_between_source_interval_is_invented_for_one_context():
    cases = [{'context_id': 0}, {'context_id': 0}]
    stats = study.paired([{'correct': False}, {'correct': True}], [{'correct': True}, {'correct': True}], cases)
    assert stats['difference'] == .5
    assert stats['source_context_cluster_ci95'] is None


async def test_native_and_direct_store_controls_authenticate_without_dataset_answers(tmp_path):
    chunks = ['The telescope is blue.\n\n', 'The notebook is blue.']
    native = await study.ingest(chunks, 'authored', 0, arm(), tmp_path / 'native', None, 'authored')
    with study.matched_admission(), study.worker.observed_features():
        control = await study.ingest(chunks, 'authored', 0, arm('fresh_baseline', True), tmp_path / 'control', None, 'authored')
        qa = arm('qa_pairing', True)
        qa['config']['enable_qa_pairing'] = True
        treatment = await study.ingest(chunks, 'authored', 0, qa, tmp_path / 'qa', None, 'authored')
    assert native['inventory']['events'] == control['inventory']['events'] == treatment['inventory']['events'] == 2
    assert control['graph_identity'] == treatment['graph_identity']
    assert treatment['inventory']['qa_pairs'] == 0  # Tool documents are not a dialogue.


@pytest.mark.skipif(not study.support.UPSTREAM.exists(), reason='Pinned external MAB checkout required')
async def test_complete_arm_records_and_reverifies_native_reader_contract(tmp_path, monkeypatch):
    prepared = {'chunks': [['The telescope is blue.']],
        'query_groups': [[['What color is the telescope?', ['blue'], 'authored-1']]],
        'dataset_config': {'sub_dataset': 'factconsolidation_authored', 'generation_max_length': 10},
        'agent_config': {'reader_output_contract': 'answer-only-v1'},
        'system_message': 'You are a helpful assistant.', 'metric': 'substring_exact_match'}
    masters = tmp_path / 'masters'
    masters.mkdir()
    await study.ingest(prepared['chunks'][0], prepared['dataset_config']['sub_dataset'], 0,
                       arm(), masters / 'context-000', None, 'authored')
    async def fake_chat(client, semaphore, messages, limit, path):
        request = study.support.request_body(messages, limit)
        artifact = {'request': request, 'request_sha256': sha(request), 'status': 'complete',
                    'attempts': [{'http_status': 200}], 'response': {'message': {'content': 'blue'},
                        'done_reason': 'stop', 'prompt_eval_count': 10, 'eval_count': 2}}
        write_new(path, artifact)
        return 'blue', artifact
    monkeypatch.setattr(study.support, 'chat', fake_chat)
    await study.run_arm('authored', prepared, arm(), tmp_path, masters, None)
    execution = json.loads((tmp_path / 'baseline/execution.json').read_text())
    assert execution['complete'] and execution['rows'][0]['correct']
    assert json.loads((tmp_path / 'baseline/verification.json').read_text())['receipts'] == 2


@pytest.mark.skipif(not study.support.UPSTREAM.exists(), reason='Pinned external MAB checkout required')
async def test_terminal_reader_failure_never_scores_partial_arm(tmp_path, monkeypatch):
    prepared = {'chunks': [['The telescope is blue.']],
        'query_groups': [[['What color?', ['blue'], 'a'], ['What color again?', ['blue'], 'b']]],
        'dataset_config': {'sub_dataset': 'factconsolidation_authored', 'generation_max_length': 10},
        'agent_config': {'reader_output_contract': 'answer-only-v1'},
        'system_message': 'You are a helpful assistant.', 'metric': 'substring_exact_match'}
    masters = tmp_path / 'masters'
    masters.mkdir()
    await study.ingest(prepared['chunks'][0], prepared['dataset_config']['sub_dataset'], 0,
                       arm(), masters / 'context-000', None, 'authored')
    async def fail(*args, **kwargs):
        raise study.study.old.ResearchFailure('Injected provider failure')
    monkeypatch.setattr(study.support, 'chat', fail)
    await study.run_arm('authored', prepared, arm(), tmp_path, masters, None)
    execution = json.loads((tmp_path / 'baseline/execution.json').read_text())
    assert not execution['complete']
    assert not (tmp_path / 'baseline/verification.json').exists()
    assert all(row['status'] != 'complete' for row in execution['rows'])
