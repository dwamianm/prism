from unittest.mock import Mock

import pytest

from prme.retrieval.reranker import CrossEncoderReranker, original_anchor
from prme.retrieval.config import PackingConfig
from prme.retrieval.execution import reranker_identity
from prme.retrieval.packing import pack_context
from prme.types import NodeType
from tests.test_reranker_envelope import inputs, receipt


async def test_preserves_original_anchor_through_real_balanced_packing_and_replay():
    rows = inputs()
    before = [r.model_dump() for r in rows]
    old, new = CrossEncoderReranker(policy="score_envelope"), CrossEncoderReranker(policy="anchored_score_envelope")
    old._predict_sync = new._predict_sync = Mock(return_value=[.001, .99])
    prior = await old.rerank('evidence', rows, top_k=2)
    current = await new.rerank('evidence', rows, top_k=2)
    one = pack_context([rows[0]], PackingConfig(token_budget=5000, overhead_tokens=0, min_fidelity='full'))
    config = PackingConfig(token_budget=one.tokens_used, overhead_tokens=0, min_fidelity='full')
    packed_ids = lambda bundle: [c.node.id for section in bundle.sections.values() for c in section]
    assert packed_ids(pack_context(prior, config)) == [rows[1].node.id]
    bundle = pack_context(current, config)
    assert packed_ids(bundle) == [rows[0].node.id]
    assert sorted(c.composite_score for c in current[:2]) == sorted(c.composite_score for c in rows[:2])
    assert current[2] == rows[2]
    assert [r.model_dump() for r in rows] == before
    saved = receipt(current, bundle, config)
    assert saved.schema_version == 13
    assert saved.replay_ranking() == tuple(c.node.id for c in current)
    assert reranker_identity(old) != reranker_identity(new)


@pytest.mark.parametrize('kind', ['instruction', 'pinned', 'salience', 'task', 'single_path'])
def test_mandatory_and_single_path_records_are_not_ordinary_anchors(kind):
    rows = inputs()
    if kind == 'instruction': rows[0].node = rows[0].node.model_copy(update={'node_type': NodeType.INSTRUCTION})
    if kind == 'pinned': rows[0].node = rows[0].node.model_copy(update={'pinned': True})
    if kind == 'salience': rows[0].node = rows[0].node.model_copy(update={'salience': 1.0})
    if kind == 'task': rows[0].node = rows[0].node.model_copy(update={'node_type': NodeType.TASK})
    if kind == 'single_path': rows[0].path_count = 1
    assert original_anchor(rows) == rows[1].node.id


async def test_absent_or_out_of_prefix_anchor_retains_previous_policy():
    rows = inputs()
    for c in rows[:2]: c.path_count = 1
    old, new = CrossEncoderReranker(policy="score_envelope"), CrossEncoderReranker(policy="anchored_score_envelope")
    old._predict_sync = new._predict_sync = Mock(return_value=[.001, .99])
    assert await new.rerank('x', rows, top_k=2) == await old.rerank('x', rows, top_k=2)
    rows[2].path_count = 1
    assert original_anchor(rows) is None
    assert await new.rerank('x', rows, top_k=2) == await old.rerank('x', rows, top_k=2)


async def test_zero_empty_invalid_and_repeat_boundaries():
    new = CrossEncoderReranker(policy="anchored_score_envelope")
    new._predict_sync = Mock(return_value=[.99, .001])
    assert await new.rerank('x', []) == []
    rows = inputs()
    assert await new.rerank('x', rows, top_k=0) == rows
    assert await new.rerank('x', rows, top_k=2) == await new.rerank('x', rows, top_k=2)
    with pytest.raises(ValueError): await new.rerank('x', rows, top_k=-1)
    with pytest.raises(ValueError): await new.rerank('x', rows, prior_weight=2)
