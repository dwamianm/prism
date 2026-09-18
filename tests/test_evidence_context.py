"""Direct evidence projection preserves complete sources with replayable scores."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.evidence_context import (
    augment_evidence_context,
    project_evidence_context,
)
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.types import EpistemicType, LifecycleState, NodeType, RetrievalMode, Scope


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def node(
    number: int,
    content: str,
    *,
    node_type: NodeType,
    evidence: tuple[UUID, ...] = (),
    owner: str = "owner",
    scope: Scope = Scope.PROJECT,
    created_at: datetime = NOW,
) -> MemoryNode:
    return MemoryNode(
        id=UUID(int=number),
        user_id=owner,
        scope=scope,
        node_type=node_type,
        content=content,
        evidence_refs=evidence,
        epistemic_type=EpistemicType.OBSERVED,
        lifecycle_state=LifecycleState.STABLE,
        confidence=0.8,
        confidence_base=0.8,
        salience=0.5,
        salience_base=0.5,
        created_at=created_at,
        updated_at=created_at,
        last_reinforced_at=created_at,
        event_time=created_at,
    )


@pytest.mark.asyncio
async def test_projects_derived_siblings_to_their_complete_direct_source():
    source_id = UUID(int=1)
    source = node(
        1,
        "Flask 2.3.1, SQLAlchemy 3.0.3, and Bootstrap 5.3 are required.",
        node_type=NodeType.NOTE,
        evidence=(source_id,),
    )
    short = RetrievalCandidate(
        node=node(2, "Flask is required.", node_type=NodeType.FACT, evidence=(source_id,)),
        semantic_score=0.9,
        lexical_score=0.8,
        paths=["VECTOR", "LEXICAL"],
        path_count=2,
    )
    sibling = RetrievalCandidate(
        node=node(3, "Bootstrap is required.", node_type=NodeType.FACT, evidence=(source_id,)),
        semantic_score=0.8,
        lexical_score=0.7,
        paths=["VECTOR"],
        path_count=1,
    )
    scored, _ = score_and_rank([short, sibling], now=NOW)
    anchor = scored[0]
    store = SimpleNamespace(get_nodes=lambda _ids: None)

    async def get_nodes(ids):
        assert ids == [str(source_id)]
        return [source]

    store.get_nodes = get_nodes
    projected = await project_evidence_context(
        scored,
        graph_store=store,
        user_id="owner",
        config=PackingConfig(evidence_projection_top_k=1),
        scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None,
    )

    assert [candidate.node.id for candidate in projected] == [source_id]
    result = projected[0]
    assert result.node.content == source.content
    assert result.composite_score == anchor.composite_score
    assert result.paths == ["EVIDENCE_CONTEXT"]
    assert result.score_provenance is not None
    operation = result.score_provenance.adjustments[-1]
    assert operation.kind == "evidence_projection"
    assert operation.source_node_id == anchor.node.id
    assert result.score_provenance.replay_score() == result.composite_score


@pytest.mark.asyncio
async def test_projection_leaves_group_unchanged_without_an_eligible_source():
    source_id = UUID(int=1)
    derived = RetrievalCandidate(
        node=node(2, "Scoped fact.", node_type=NodeType.FACT, evidence=(source_id,)),
        semantic_score=0.9,
    )
    scored, _ = score_and_rank([derived], now=NOW)
    foreign = node(
        1,
        "Another tenant's source.",
        node_type=NodeType.NOTE,
        evidence=(source_id,),
        owner="other",
    )

    async def get_nodes(_ids):
        return [foreign]

    projected = await project_evidence_context(
        scored,
        graph_store=SimpleNamespace(get_nodes=get_nodes),
        user_id="owner",
        config=PackingConfig(evidence_projection_top_k=1),
        scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None,
    )

    assert projected is scored


@pytest.mark.asyncio
async def test_projection_respects_the_ingestion_cutoff_before_replacing_a_group():
    source_id = UUID(int=1)
    source = node(
        1,
        "Future source.",
        node_type=NodeType.NOTE,
        evidence=(source_id,),
        created_at=NOW,
    )
    derived = RetrievalCandidate(
        node=node(2, "Earlier claim.", node_type=NodeType.FACT, evidence=(source_id,)),
        semantic_score=0.9,
    )
    scored, _ = score_and_rank([derived], now=NOW)

    async def get_nodes(_ids):
        return [source]

    projected = await project_evidence_context(
        scored,
        graph_store=SimpleNamespace(get_nodes=get_nodes),
        user_id="owner",
        config=PackingConfig(evidence_projection_top_k=1),
        scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None,
        knowledge_at=NOW - timedelta(days=1),
    )

    assert projected is scored


def test_packing_reserves_projected_source_context():
    source_id = UUID(int=1)
    source = RetrievalCandidate(
        node=node(
            1,
            "Complete direct source detail. " * 12,
            node_type=NodeType.NOTE,
            evidence=(source_id,),
        ),
        paths=["EVIDENCE_CONTEXT"],
        path_count=1,
        composite_score=0.2,
    )
    competitor = RetrievalCandidate(
        node=node(2, "Broad derived detail. " * 12, node_type=NodeType.FACT),
        paths=["VECTOR", "LEXICAL"],
        path_count=2,
        composite_score=0.9,
    )
    base = PackingConfig(token_budget=10_000, overhead_tokens=0, min_fidelity="full")
    source_only = pack_context([source], base)
    packed = pack_context(
        [competitor, source],
        base.model_copy(update={"token_budget": source_only.tokens_used}),
    )
    included = {
        item.node.id for values in packed.sections.values() for item in values
    }
    assert included == {source_id}


@pytest.mark.asyncio
async def test_augmentation_keeps_claims_and_adds_a_replayable_direct_source():
    source_id = UUID(int=1)
    source = node(
        1,
        "The complete schedule ends transaction work January 15 and deploys March 15.",
        node_type=NodeType.NOTE,
        evidence=(source_id,),
    )
    derived = RetrievalCandidate(
        node=node(
            2,
            "Transaction work ends January 15.",
            node_type=NodeType.FACT,
            evidence=(source_id,),
        ),
        semantic_score=0.9,
        lexical_score=0.8,
    )
    scored, _ = score_and_rank([derived], now=NOW)

    async def get_nodes(_ids):
        return [source]

    augmented = await augment_evidence_context(
        scored,
        graph_store=SimpleNamespace(get_nodes=get_nodes),
        user_id="owner",
        config=PackingConfig(
            evidence_augmentation_top_k=1,
            evidence_augmentation_score_decay=0.9,
        ),
        scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None,
    )

    assert {candidate.node.id for candidate in augmented} == {derived.node.id, source_id}
    direct = next(candidate for candidate in augmented if candidate.node.id == source_id)
    assert direct.composite_score == scored[0].composite_score * 0.9
    assert direct.score_provenance is not None
    assert direct.score_provenance.adjustments[-1].kind == "evidence_augmentation"
    assert direct.score_provenance.replay_score() == direct.composite_score


@pytest.mark.asyncio
async def test_non_entity_augmentation_skips_entity_routes_and_backfills_claims():
    entity_source_id = UUID(int=1)
    fact_source_id = UUID(int=2)
    sources = {
        str(entity_source_id): node(
            1,
            "A code sample contains YOUR_API_KEY but establishes no key ownership.",
            node_type=NodeType.NOTE,
            evidence=(entity_source_id,),
        ),
        str(fact_source_id): node(
            2,
            "The deployment completed after the final security review.",
            node_type=NodeType.NOTE,
            evidence=(fact_source_id,),
        ),
    }
    entity = RetrievalCandidate(
        node=node(
            3,
            "YOUR_API_KEY",
            node_type=NodeType.ENTITY,
            evidence=(entity_source_id,),
        ),
        semantic_score=0.95,
    )
    fact = RetrievalCandidate(
        node=node(
            4,
            "Deployment followed security review.",
            node_type=NodeType.FACT,
            evidence=(fact_source_id,),
        ),
        semantic_score=0.9,
    )
    scored, _ = score_and_rank([entity, fact], now=NOW)

    async def get_nodes(ids):
        return [sources[source_id] for source_id in ids]

    augmented = await augment_evidence_context(
        scored,
        graph_store=SimpleNamespace(get_nodes=get_nodes),
        user_id="owner",
        config=PackingConfig(
            evidence_augmentation_top_k=1,
            evidence_augmentation_anchor_policy="non_entity",
        ),
        scopes=[Scope.PROJECT],
        retrieval_mode=RetrievalMode.DEFAULT,
        unverified_confidence_threshold=None,
    )

    augmented_ids = {candidate.node.id for candidate in augmented}
    assert fact_source_id in augmented_ids
    assert entity_source_id not in augmented_ids


def test_projection_and_augmentation_are_mutually_exclusive():
    with pytest.raises(ValueError, match="cannot both be enabled"):
        PackingConfig(
            evidence_projection_top_k=1,
            evidence_augmentation_top_k=1,
        )
