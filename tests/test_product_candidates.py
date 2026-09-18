"""Product candidate generation is deterministic, broad, and non-mutating."""

from uuid import UUID

import pytest

from prme import (
    MemoryEngine,
    ProductCandidateEntity,
    rank_product_alignment_candidates,
)
from prme.types import NodeType, Scope
from tests import test_durable_ingestion as fixtures


config = fixtures.config
user = fixtures.user


def _record(identity: int, name: str, catalog: str):
    return ProductCandidateEntity(
        node_id=UUID(int=identity),
        catalog=catalog,
        product={"name": name, "manufacturer": "Adobe", "price": "299"},
    )


def test_candidate_ranker_is_deterministic_and_cross_catalog():
    records = [
        _record(1, "Adobe Acrobat Standard 7.0 Windows", "amazon"),
        _record(2, "Adobe Acrobat 7 Standard for Windows", "google"),
        _record(3, "Adobe Photoshop Elements 5", "google"),
        _record(4, "Adobe Acrobat Standard 7 Windows", "amazon"),
    ]
    first = rank_product_alignment_candidates(
        records, top_k=2, min_score=0.1, cross_catalog_only=True
    )
    second = rank_product_alignment_candidates(
        list(reversed(records)), top_k=2, min_score=0.1, cross_catalog_only=True
    )

    assert first == second
    intended = next(
        item
        for item in first
        if {item.left.node_id, item.right.node_id} == {UUID(int=1), UUID(int=2)}
    )
    assert intended.score > 0.3
    assert all(item.left.catalog != item.right.catalog for item in first)
    assert all(item.left.node_id < item.right.node_id for item in first)


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"top_k": 0}, "top_k"),
        ({"min_score": float("nan")}, "min_score"),
        ({"cross_catalog_only": True}, "catalog"),
    ],
)
def test_candidate_ranker_rejects_ambiguous_bounds(kwargs, error):
    records = [
        ProductCandidateEntity(node_id=UUID(int=1), product={"name": "One"}),
        ProductCandidateEntity(node_id=UUID(int=2), product={"name": "One Pro"}),
    ]
    with pytest.raises(ValueError, match=error):
        rank_product_alignment_candidates(records, **kwargs)


async def _node(engine, user_id, name, *, scope=Scope.PERSONAL):
    return (
        await engine.store_with_receipt(
            name,
            user_id=user_id,
            node_type=NodeType.ENTITY,
            scope=scope,
            metadata={"entity_type": "product"},
        )
    ).node


async def test_engine_validates_owned_nodes_and_provenance_before_ranking(config, user):
    async with MemoryEngine.open(config) as engine:
        left = await _node(engine, user, "Adobe Acrobat Standard 7.0 Windows")
        right = await _node(engine, user, "Adobe Acrobat 7 Standard for Windows")
        other_scope = await _node(
            engine, user, "Adobe Acrobat 7 for Windows", scope=Scope.PROJECT
        )
        records = [
            {
                "node_id": str(left.id),
                "catalog": "amazon",
                "product": {"name": left.content, "manufacturer": "Adobe"},
            },
            {
                "node_id": str(right.id),
                "catalog": "google",
                "product": {"name": right.content, "manufacturer": "Adobe"},
            },
            {
                "node_id": str(other_scope.id),
                "catalog": "google",
                "product": {
                    "name": other_scope.content,
                    "manufacturer": "Adobe",
                },
            },
        ]
        candidates = await engine.find_product_alignment_candidates(
            records, user_id=user, cross_catalog_only=True
        )
        assert len(candidates) == 1
        assert {candidates[0].left.node_id, candidates[0].right.node_id} == {
            left.id,
            right.id,
        }
        assert await engine.list_alias_proposals(user_id=user) == []
        assert await engine._graph_store.get_edges(
            node_ids=[str(left.id), str(right.id), str(other_scope.id)]
        ) == []

        with pytest.raises(ValueError, match="unavailable"):
            await engine.find_product_alignment_candidates(
                records, user_id=user + "-other"
            )
        changed = [dict(item) for item in records]
        changed[0] = {
            **changed[0],
            "product": {"name": "Changed product", "manufacturer": "Adobe"},
        }
        with pytest.raises(ValueError, match="unavailable"):
            await engine.find_product_alignment_candidates(changed, user_id=user)
