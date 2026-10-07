"""Behavioral controls for tickets #238, #239 and #241, on both backends."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.followup import followup_analysis
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.source_context import prepare_sources
from prme.retrieval.supersedence import suppress_replaced_claims
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType, QueryIntent, RepresentationLevel, RetrievalMode, Scope
from tests.test_evidence_context import NOW, node
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def candidate(number, text, kind=NodeType.FACT, **kwargs):
    return RetrievalCandidate(node=node(number, text, node_type=kind, **kwargs),
                              paths=['VECTOR', 'LEXICAL'], path_count=2, semantic_score=.8, lexical_score=.8)


def test_followup_is_bounded_uses_only_ranked_evidence_and_retains_request_analysis():
    analysis = QueryAnalysis(query='Which telescope?', intent=QueryIntent.FACTUAL, entities=['telescope'],
                             temporal_signals=[{'value': 'original'}], is_aggregation=True)
    first = candidate(1, 'Alice visited Paris.')
    first.node.metadata = {'predicate': 'visited_city', 'answer': 'SECRET_LABEL'}
    ranked = [first] + [candidate(i, f' The Name{i} text mentions London.') for i in range(2, 8)]
    new, observation = followup_analysis(analysis, ranked)
    assert new.query != analysis.query and 'visited city' in new.query and 'Paris' in new.query
    assert 'SECRET_LABEL' not in new.query
    assert new.temporal_signals == analysis.temporal_signals and new.intent == analysis.intent
    assert new.is_aggregation and new.request_id == analysis.request_id
    assert len(observation['anchor_ids']) == 5 and len(observation['signals']) <= 12
    assert followup_analysis(analysis, ranked) == (new, observation)
    assert followup_analysis(analysis, [candidate(1, 'no named entity')])[0] is None


async def test_source_resolution_is_batched_owner_scope_and_time_constrained():
    source = node(1, 'A complete source turn.', node_type=NodeType.NOTE, evidence=(UUID(int=1),))
    foreign = node(3, 'foreign', node_type=NodeType.NOTE, owner='foreign')
    late = node(4, 'future', node_type=NodeType.NOTE, created_at=NOW + timedelta(days=1))
    claim = candidate(2, 'A claim.', evidence=(source.id, foreign.id, late.id, UUID(int=5)))
    scored, _ = score_and_rank([claim], now=NOW)
    store = SimpleNamespace(get_nodes=AsyncMock(return_value=[source, foreign, late]))
    prepared, links = await prepare_sources(scored, graph_store=store, user_id='owner', scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT, unverified_confidence_threshold=None, knowledge_at=NOW,
        event_time_from=None, event_time_to=None, time_from=None, time_to=None)
    assert store.get_nodes.await_count == 1
    assert links == {claim.node.id: (source.id,)}
    added = next(c for c in prepared if c.node.id == source.id)
    assert added.paths == ['SOURCE_CONTEXT'] and added.composite_score == 0
    assert added.score_provenance.replay_score() == 0
    assert prepared[0].model_dump() == scored[0].model_dump()


@pytest.mark.parametrize('format', ['reader', 'auditable', 'compact'])
async def test_copacking_full_sources_budget_receipt_replay_and_old_version_rejection(format):
    source = node(1, 'I use a blue telescope. It needs a dark sky.', node_type=NodeType.NOTE,
                  evidence=(UUID(int=1),))
    claim = candidate(2, 'I use a blue telescope.', evidence=(source.id,))
    scored, _ = score_and_rank([claim], now=NOW)
    scored, links = await prepare_sources(scored, graph_store=SimpleNamespace(get_nodes=AsyncMock(return_value=[source])),
        user_id='owner', scopes=[Scope.PROJECT], retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None, knowledge_at=None, event_time_from=None, event_time_to=None,
        time_from=None, time_to=None)
    packing = PackingConfig(context_format=format, co_pack_sources=True, token_budget=2000, overhead_tokens=0)
    bundle = pack_context(scored, packing, _claim_sources=links)
    assert bundle.included_count == 2
    assert bundle.render().index('I use a blue telescope.') < bundle.render().index('It needs a dark sky.')
    assert bundle.tokens_used <= 2000
    receipt = make_receipt(request_id=UUID(int=99), user_id='owner', query='telescope', reference_time=NOW,
                          scopes=[Scope.PROJECT], scoring=ScoringWeights(), packing=packing,
                          candidates=scored, bundle=bundle,
                          execution=RetrievalExecution(parameters={}, features={}), ranking_policy='score_id')
    assert receipt.schema_version == 23
    assert receipt.replay_ranking() == tuple(c.node.id for c in scored)
    restored = RetrievalReceipt.model_validate_json(receipt.model_dump_json())
    assert restored.checksum == receipt.checksum
    bad = receipt.model_dump(mode='json')
    bad['schema_version'] = 12
    with pytest.raises(ValidationError):
        RetrievalReceipt.model_validate(bad)
    tight = pack_context(scored, packing.model_copy(update={'token_budget': 25}), _claim_sources=links)
    assert tight.tokens_used <= 25
    assert source.id not in {c.node.id for group in tight.sections.values() for c in group}
    repacked = pack_context(scored, packing)
    assert repacked.render() == bundle.render()
    reserved = pack_context(scored, packing, _required=[(source.id, RepresentationLevel.FULL)])
    assert source.id in {c.node.id for group in reserved.sections.values() for c in group}


def test_a_derived_record_cannot_fold_away_its_source_when_copacking():
    source = candidate(1, 'Blue telescope.', NodeType.NOTE, evidence=(UUID(int=1),))
    derived = candidate(2, 'Blue telescope. A derived addition.', evidence=(source.node.id,))
    source.composite_score = 1
    derived.composite_score = .9
    base = PackingConfig(context_format='reader', fold_repeated_text=True, token_budget=1000, overhead_tokens=0,
                         multipath_ordering='score')
    control = pack_context([source, derived], base)
    assert source.node.id in control.excluded_ids
    fixed = pack_context([source, derived], base.model_copy(update={'co_pack_sources': True}),
                         _claim_sources={derived.node.id: (source.node.id,)})
    assert source.node.id not in fixed.excluded_ids
    assert fixed.included_count == 2
    assert fixed.render().index('A derived addition') < fixed.render().rindex('Blue telescope.')


@pytest.mark.parametrize('invalid', ['foreign', 'scope', 'missing', 'archived', 'contested', 'future', 'expired',
                                     'hypothetical', 'type', 'cycle', 'foreign_edge'])
async def test_suppression_does_not_trust_unavailable_ineligible_or_ambiguous_replacements(invalid):
    old = candidate(1, 'blue telescope')
    new = node(2, 'green telescope', node_type=NodeType.FACT)
    edge = MemoryEdge(source_id=new.id, target_id=old.node.id, user_id='owner', edge_type=EdgeType.SUPERSEDES,
                      valid_from=NOW)
    old.node.superseded_by = new.id if invalid != 'foreign_edge' else None
    if invalid == 'foreign':
        new.user_id = 'foreign'
    if invalid == 'scope':
        new.scope = Scope.PERSONAL
    if invalid in {'archived', 'contested'}:
        new.lifecycle_state = LifecycleState(invalid)
    if invalid == 'future':
        new.valid_from = NOW + timedelta(days=1)
    if invalid == 'expired':
        new.valid_to = NOW
    if invalid == 'hypothetical':
        new.epistemic_type = EpistemicType.HYPOTHETICAL
    if invalid == 'type':
        new.node_type = NodeType.NOTE
    if invalid == 'cycle':
        new.superseded_by = old.node.id
    if invalid == 'foreign_edge':
        edge.user_id = 'foreign'
    store = SimpleNamespace(get_edges=AsyncMock(return_value=[edge]),
                            get_nodes=AsyncMock(return_value=[] if invalid == 'missing' else [new]))
    kept, excluded = await suppress_replaced_claims([old], graph_store=store, user_id='owner', reference_time=NOW)
    assert kept == [old] and not excluded


async def test_followup_config_receipt_and_failure_fallback(config, user, monkeypatch):
    enabled = config.model_copy(update={'enable_evidence_followup': True})
    async with MemoryEngine.open(enabled) as engine:
        await engine.store('My telescope was made in Paris.', user_id=user)
        await engine.store('Paris is a city in France.', user_id=user)
        response = await engine.retrieve('Which telescope do I use?', user_id=user, reference_time=NOW)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert receipt.execution.parameters['evidence_followup']['status'] == 'merged'
        assert receipt.replay_ranking() == tuple(c.node.id for c in response.results)
        from prme.retrieval import pipeline
        original = pipeline.generate_candidates
        async def fail_followup(*args, **kwargs):
            if kwargs.get('include_pinned') is False:
                kwargs['diagnostics'].backend_failures['VECTOR'] = 'backend_error'
                return [], {}
            return await original(*args, **kwargs)
        monkeypatch.setattr(pipeline, 'generate_candidates', fail_followup)
        fallback = await engine.retrieve('Which telescope do I use?', user_id=user, reference_time=NOW)
        receipt = await engine.get_retrieval_receipt(str(fallback.metadata.request_id), user_id=user)
        assert receipt.execution.parameters['evidence_followup']['status'] == 'backend_failed'
        engine._retrieval_pipeline._enable_evidence_followup = False
        control = await engine.retrieve('Which telescope do I use?', user_id=user, reference_time=NOW)
        assert fallback.bundle.render() == control.bundle.render()


async def test_supersedence_edges_suppress_current_claims_and_preserve_explicit_history(config, user):
    enabled = config.model_copy(update={'enable_read_supersedence': True})
    async with MemoryEngine.open(enabled) as engine:
        old = await engine.store('My current telescope color is blue.', user_id=user, node_type=NodeType.FACT,
                                 valid_from=NOW - timedelta(days=2))
        new = await engine.store('My current telescope color is green.', user_id=user, node_type=NodeType.FACT,
                                 valid_from=NOW - timedelta(days=1))
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        old = str(next(n.id for n in nodes if 'blue' in n.content))
        new = str(next(n.id for n in nodes if 'green' in n.content))
        await engine._graph_store.create_edge(MemoryEdge(source_id=UUID(new), target_id=UUID(old), user_id=user,
            edge_type=EdgeType.SUPERSEDES, valid_from=NOW - timedelta(days=1)))
        response = await engine.retrieve('What is my current telescope color?', user_id=user, reference_time=NOW)
        assert old not in {str(c.node.id) for c in response.results}
        assert new in {str(c.node.id) for c in response.results}
        assert any(c.reason == 'supersedence_filtered' for c in response.excluded)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert receipt.replay_ranking() == tuple(c.node.id for c in response.results)
        for query, kwargs in [('What was the telescope color previously?', {}),
                              ('What is my current telescope color?', {'retrieval_mode': RetrievalMode.EXPLICIT}),
                              ('What is my current telescope color?', {'knowledge_at': NOW + timedelta(days=100)})]:
            result = await engine.retrieve(query, user_id=user, reference_time=NOW, **kwargs)
            assert old in {str(c.node.id) for c in result.results}
        assert (await engine._graph_store.get_node(old)).lifecycle_state == LifecycleState.TENTATIVE


def test_options_are_explicit_and_absent_from_legacy_serialization():
    cfg = PRMEConfig()
    assert not cfg.enable_evidence_followup and not cfg.enable_read_supersedence and not cfg.packing.co_pack_sources
    assert 'enable_evidence_followup' not in cfg.model_dump()
    assert 'enable_read_supersedence' not in cfg.model_dump()
    assert 'co_pack_sources' not in cfg.packing.model_dump()


async def test_public_source_resolution_receipt_restart_and_selection_bounds(config, user, monkeypatch):
    enabled = config.model_copy(update={'packing': config.packing.model_copy(update={'co_pack_sources': True})})
    async with MemoryEngine.open(enabled) as engine:
        await engine.store('I use a telescope from Paris. The source also names France.', user_id=user)
        await engine.store('The telescope is from Paris.', user_id=user, node_type=NodeType.FACT)
        nodes = await engine.query_nodes(user_id=user)
        source = next(n for n in nodes if n.node_type == NodeType.NOTE)
        claim = next(n for n in nodes if n.node_type == NodeType.FACT)
        await engine._graph_store.update_node(str(claim.id), evidence_refs=[source.id])
        claim = await engine._graph_store.get_node(str(claim.id))
        async def only_claim(*args, **kwargs):
            return [RetrievalCandidate(node=claim, paths=['VECTOR'], path_count=1, semantic_score=.9)], {'VECTOR': 1}
        monkeypatch.setattr('prme.retrieval.pipeline.generate_candidates', only_claim)
        engine._retrieval_pipeline._packing_config.session_context_window = 0
        response = await engine.retrieve('telescope', user_id=user, reference_time=NOW)
        assert 'France' in response.bundle.render()
        assert response.metadata.receipt_persisted
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.schema_version == 23
        assert saved.replay_ranking() == tuple(c.node.id for c in response.results)
        limited = await engine.retrieve('telescope', user_id=user, reference_time=NOW, limit=1)
        assert len(limited.results) == 1 and 'France' not in limited.bundle.render()
    async with MemoryEngine.open(enabled) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum
        assert restored.replay_ranking() == saved.replay_ranking()


async def test_edge_only_replacement_with_a_successor_is_not_treated_as_current():
    old = candidate(1, 'old editor')
    replacement = node(2, 'intermediate editor', node_type=NodeType.FACT)
    first = MemoryEdge(source_id=replacement.id, target_id=old.node.id, user_id='owner',
                       edge_type=EdgeType.SUPERSEDES, valid_from=NOW)
    next_edge = MemoryEdge(source_id=UUID(int=3), target_id=replacement.id, user_id='owner',
                           edge_type=EdgeType.SUPERSEDES, valid_from=NOW)
    store = SimpleNamespace(get_edges=AsyncMock(side_effect=[[first], [first, next_edge]]),
                            get_nodes=AsyncMock(return_value=[replacement]))
    kept, excluded = await suppress_replaced_claims([old], graph_store=store, user_id='owner', reference_time=NOW)
    assert kept == [old] and not excluded
