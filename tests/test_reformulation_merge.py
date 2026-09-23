from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from prme.retrieval import pipeline as policy
from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.types import NodeType, Scope


def candidate(index, paths, semantic=0, lexical=0):
    stamp = datetime(2026, 9, 22, tzinfo=timezone.utc)
    node = MemoryNode(id=UUID(int=index), user_id='authored', node_type=NodeType.FACT,
        scope=Scope.PERSONAL, content='The telescope is blue.', created_at=stamp,
        updated_at=stamp, valid_from=stamp, last_reinforced_at=stamp)
    return RetrievalCandidate(node=node, paths=paths, path_count=len(paths),
                              semantic_score=semantic, lexical_score=lexical)


def test_overlap_changes_signals_without_counting_queries_as_backends():
    base = candidate(1, ['VECTOR'], .3)
    alt = candidate(1, ['LEXICAL'], 0, .9)
    merged, trace = policy.merge_reformulation_signals([base], [[alt], [alt]])
    assert merged[0].paths == ['LEXICAL', 'VECTOR']
    assert merged[0].path_count == 2
    assert (merged[0].semantic_score, merged[0].lexical_score) == (.3, .9)
    assert trace['changed_existing_ids'] == [str(base.node.id)]
    assert trace['added_ids'] == []
    assert base.paths == ['VECTOR'] and base.lexical_score == 0


def test_same_backend_repeat_never_creates_multipath_bonus():
    base = candidate(1, ['VECTOR'], .3)
    alt = candidate(1, ['VECTOR'], .8)
    merged, _ = policy.merge_reformulation_signals([base], [[alt], [alt]])
    assert merged[0].path_count == 1 and merged[0].semantic_score == .8


@pytest.mark.parametrize('field,value', [('content', 'different claim'), ('user_id', 'foreign'), ('scope', Scope.PROJECT)])
def test_snapshot_owner_or_scope_collision_fails_atomically(field, value):
    base = candidate(1, ['VECTOR'], .3)
    other = candidate(1, ['LEXICAL'], 0, .9)
    setattr(other.node, field, value)
    before = base.model_dump(mode='json')
    with pytest.raises(ValueError, match='source snapshots'):
        policy.merge_reformulation_signals([base], [[candidate(2, ['VECTOR'], .9), other]])
    assert base.model_dump(mode='json') == before


def test_new_candidates_preserve_source_and_repeated_merge_is_idempotent():
    base = candidate(1, ['VECTOR'], .3)
    alt = candidate(2, ['LEXICAL'], 0, .9)
    merged, trace = policy.merge_reformulation_signals([base], [[alt]])
    repeated, _ = policy.merge_reformulation_signals(merged, [[alt]])
    assert trace['added_ids'] == [str(alt.node.id)]
    assert merged[1].node.model_dump(mode='json') == alt.node.model_dump(mode='json')
    assert [c.model_dump(mode='json') for c in merged] == [c.model_dump(mode='json') for c in repeated]


def test_nonfinite_signal_fails_without_mutation():
    base = candidate(1, ['VECTOR'], .3)
    other = candidate(1, ['LEXICAL'], 0, float('nan'))
    with pytest.raises(ValueError, match='Non-finite'):
        policy.merge_reformulation_signals([base], [[other]])
    assert base.semantic_score == .3 and base.lexical_score == 0


async def test_alternate_backend_failure_does_not_commit_partial_merge(monkeypatch):
    monkeypatch.setattr('prme.retrieval.reformulation.reformulate_query', AsyncMock(return_value=['telescopes']))
    monkeypatch.setattr(policy, 'analyze_query', AsyncMock(return_value=object()))
    async def failure(*args, **kwargs):
        kwargs['diagnostics'].backend_failures['VECTOR'] = 'backend_error'
        return [candidate(2, ['LEXICAL'], 0, .9)], {}
    monkeypatch.setattr(policy, 'generate_candidates', failure)
    engine = SimpleNamespace(_query_reformulation_provider='ollama', _query_reformulation_model='authored',
        _query_reformulation_count=2, _temporal_languages=['en'], _graph_store=None,
        _vector_index=None, _lexical_index=None, _query_reformulation_merge_policy="max_signals")
    values = [candidate(1, ['VECTOR'], .3)]
    with pytest.raises(RuntimeError, match='backend failed'):
        await policy.RetrievalPipeline._expand_reformulated_queries(engine, 'telescope', candidates=values, user_id='authored',
            scope=[Scope.PERSONAL], time_from=None, time_to=None, retrieval_mode=None, config=None)
    assert len(values) == 1
