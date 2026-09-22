"""Causal controls and full-cohort eligibility of the successor study."""
import asyncio
from copy import deepcopy
import json

import duckdb
import pytest

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import clean_config
from benchmarks.diagnostics.run_opt_in_interactions import config_for


async def test_concurrent_admission_identity_is_arm_invariant_and_case_scoped():
    import prme.storage.engine as module
    original = module.MemoryNode
    async def one(qid):
        token=study.FRESH_TURN.set(dict(question_id=qid,position=0,index=0,ordinal=0))
        try:
            await asyncio.sleep(0)
            node=module.MemoryNode(user_id='authored',node_type='fact',content='same source')
            pair=module.MemoryNode(user_id='authored',node_type='fact',content='paired source',metadata={'qa_pair':True})
            return node,pair
        finally:
            study.FRESH_TURN.reset(token)
    with study.matched_admission():
        a,b,c=await asyncio.gather(one('same'),one('other'),one('same'))
    assert a[0].id == c[0].id != b[0].id
    assert a[0].created_at == c[0].created_at == b[0].created_at
    assert a[0].id != a[1].id
    assert a[0].content == 'same source'
    assert module.MemoryNode is original


async def test_fresh_control_and_qa_execute_store_hooks_with_same_source_identity(tmp_path):
    case=dict(question_id='authored-only',haystack_session_ids=['session'],haystack_dates=['2024/01/01 (Mon) 12:00'],
        haystack_sessions=[[dict(role='user',content='Which color do I prefer?'),dict(role='assistant',content='You prefer blue.')]])
    configs=[]
    for enabled in (False,True):
        folder=tmp_path/str(enabled)
        (folder/'pack').mkdir(parents=True)
        config=clean_config()
        config.update(db_path='{pack}/memory.duckdb',vector_path='{pack}/vectors.usearch',lexical_path='{pack}/lexical_index',enable_qa_pairing=enabled)
        configs.append((folder,config_for({'config':config},folder/'pack',None)))
    with study.matched_admission():
        results=await asyncio.gather(*(study.fresh_ingest(deepcopy(case),cfg,folder) for folder,cfg in configs))
    assert results[0]['inventory']['qa_pairs']==0
    assert results[1]['inventory']['qa_pairs']==1
    assert results[0]['inventory']['events']==results[1]['inventory']['events']==2
    source_rows=[]
    for folder,_ in configs:
        with duckdb.connect(str(folder/'pack/memory.duckdb'),read_only=True) as db:
            source_rows.append(db.execute("SELECT id,content,created_at,valid_from FROM nodes WHERE json_extract(metadata,'$.qa_pair') IS NULL ORDER BY id").fetchall())
    assert source_rows[0]==source_rows[1]


def test_explicit_fields_are_not_overwritten_by_admission_fixture():
    token=study.FRESH_TURN.set(dict(question_id='authored',position=0,index=0,ordinal=0))
    try:
        result=study.fixture_identity('source',{'id':'caller-id','created_at':'caller-clock','valid_from':'caller-validity'})
    finally:
        study.FRESH_TURN.reset(token)
    assert result['id']=='caller-id'
    assert result['created_at']=='caller-clock'
    assert result['valid_from']=='caller-validity'


def test_analysis_refuses_partial_arm_even_with_valid_positive_rows(tmp_path):
    from benchmarks.diagnostics.analyze_opt_in_successor import load_complete
    (tmp_path/'execution.json').write_text(json.dumps({'complete':False,'rows':[{'question_id':'a','status':'complete','correct':True}]}))
    with pytest.raises(study.old.ResearchFailure):
        load_complete(tmp_path,['a','b'])


def test_holm_counts_failed_registered_comparisons_in_family():
    from benchmarks.diagnostics.analyze_opt_in_successor import holm
    assert holm({'complete_arm':0.01},family_size=10)['complete_arm']==pytest.approx(0.1)
