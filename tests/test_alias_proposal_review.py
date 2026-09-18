"""Alias proposals have an audited review inbox and explicit decision path."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from prme import (
    AliasProposalReviewConflict,
    MemoryEngine,
    StaleAliasProposal,
)
from prme.types import EdgeType, NodeType, Scope
from prme.api.app import create_app
from prme.config import APIConfig
from tests import test_durable_ingestion as fixtures


config = fixtures.config
user = fixtures.user


async def _proposal(engine: MemoryEngine, user_id: str, *, scope=Scope.PERSONAL):
    nodes = []
    for name in ("Acme Product Pro", "ACME Product Professional"):
        receipt = await engine.store_with_receipt(
            name,
            user_id=user_id,
            node_type=NodeType.ENTITY,
            scope=scope,
            metadata={"entity_type": "product"},
        )
        nodes.append(receipt.node)
    nodes.sort(key=lambda node: str(node.id))
    proposal = await engine._graph_store.propose_alias(
        *(str(node.id) for node in nodes),
        user_id=user_id,
        alias_type="semantic",
        score=0.91,
    )
    assert proposal is not None and proposal.applied and proposal.operation_id
    return nodes, proposal


async def _review_rows(engine: MemoryEngine, user_id: str):
    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            return graph._conn.execute(
                "SELECT op_type,payload FROM operations "
                "WHERE op_type IN ('ALIAS_PROPOSAL_ACCEPTED',"
                "'ALIAS_PROPOSAL_REJECTED') AND actor_id=? ORDER BY id",
                [user_id],
            ).fetchall()
    async with graph._pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT op_type,payload FROM operations "
            "WHERE op_type IN ('ALIAS_PROPOSAL_ACCEPTED',"
            "'ALIAS_PROPOSAL_REJECTED') AND actor_id=$1 ORDER BY id",
            user_id,
        )
    return [(row["op_type"], row["payload"]) for row in rows]


async def test_accept_publishes_verified_link_without_retiring_entities(config, user):
    async with MemoryEngine.open(config) as engine:
        nodes, proposal = await _proposal(engine, user)
        ids = [str(node.id) for node in nodes]
        pending = await engine.list_alias_proposals(user_id=user)
        assert len(pending) == 1 and pending[0].status == "pending"
        assert str(pending[0].proposal.operation_id) == proposal.operation_id
        assert await engine._graph_store.find_shortest_path(*ids) is None

        accepted = await engine.review_alias_proposal(
            proposal.operation_id,
            user_id=user,
            decision="accepted",
            reviewer_id="human:alice",
            reason="Catalog records describe the same licensed product.",
        )
        assert accepted.applied and accepted.verified_edge_id
        assert await engine._graph_store.find_shortest_path(*ids) == ids
        active = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
        assert {str(node.id) for node in active} == set(ids)

        edges = await engine._graph_store.get_edges(
            node_ids=ids, edge_type=EdgeType.RELATES_TO
        )
        assert len(edges) == 2
        verified = [edge for edge in edges if edge.metadata["identity_verified"]]
        assert len(verified) == 1
        assert verified[0].metadata["proposal_operation_id"] == proposal.operation_id

        inbox = await engine.list_alias_proposals(
            user_id=user, status="accepted"
        )
        assert len(inbox) == 1 and inbox[0].status == "accepted"
        assert inbox[0].review is not None
        assert inbox[0].review.reviewer_id == "human:alice"
        assert inbox[0].review.verified_edge == verified[0]
        assert await engine.list_alias_proposals(user_id=user, status="pending") == []

        replay = await engine.review_alias_proposal(
            proposal.operation_id,
            user_id=user,
            decision="accepted",
            reviewer_id="human:alice",
            reason="Catalog records describe the same licensed product.",
        )
        assert not replay.applied and replay.operation_id == accepted.operation_id
        with pytest.raises(
            AliasProposalReviewConflict, match="different review decision"
        ):
            await engine.review_alias_proposal(
                proposal.operation_id,
                user_id=user,
                decision="rejected",
                reviewer_id="human:alice",
                reason="Changed decision",
            )

    async with MemoryEngine.open(config) as reopened:
        inbox = await reopened.list_alias_proposals(user_id=user)
        assert len(inbox) == 1 and inbox[0].status == "accepted"
        assert await reopened._graph_store.find_shortest_path(*ids) == ids


async def test_reject_closes_inbox_without_joining_graph(config, user):
    async with MemoryEngine.open(config) as engine:
        nodes, proposal = await _proposal(engine, user, scope=Scope.PROJECT)
        ids = [str(node.id) for node in nodes]
        rejected = await engine.review_alias_proposal(
            proposal.operation_id,
            user_id=user,
            decision="rejected",
            reviewer_id="service:catalog-review",
            reason="Different platform editions.",
        )
        assert rejected.applied and rejected.verified_edge_id is None
        assert await engine._graph_store.find_shortest_path(*ids) is None
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1
        assert await engine.list_alias_proposals(
            user_id=user, scope=Scope.PERSONAL
        ) == []
        inbox = await engine.list_alias_proposals(
            user_id=user, scope="project", status="rejected"
        )
        assert len(inbox) == 1 and inbox[0].review is not None
        assert inbox[0].review.reason == "Different platform editions."


async def test_accept_rechecks_original_node_snapshots(config, user):
    async with MemoryEngine.open(config) as engine:
        nodes, proposal = await _proposal(engine, user)
        await engine._graph_store.update_node(
            str(nodes[0].id), metadata={"entity_type": "product", "sku": "new"}
        )
        with pytest.raises(StaleAliasProposal, match="assess the pair again"):
            await engine.review_alias_proposal(
                proposal.operation_id,
                user_id=user,
                decision="accepted",
                reviewer_id="human:alice",
            )
        assert await _review_rows(engine, user) == []
        assert len(await engine.list_alias_proposals(user_id=user, status="pending")) == 1


async def test_review_is_owner_scoped_and_rejection_requires_reason(config, user):
    async with MemoryEngine.open(config) as engine:
        _, proposal = await _proposal(engine, user)
        assert await engine.list_alias_proposals(user_id=user + "-other") == []
        with pytest.raises(ValueError, match="unavailable"):
            await engine.review_alias_proposal(
                proposal.operation_id,
                user_id=user + "-other",
                decision="accepted",
                reviewer_id="human:mallory",
            )
        with pytest.raises(ValueError, match="require a reason"):
            await engine.review_alias_proposal(
                proposal.operation_id,
                user_id=user,
                decision="rejected",
                reviewer_id="human:alice",
            )


@pytest.mark.parametrize("stage", ["validated", "edge", "journal"])
async def test_accept_failure_rolls_back_verified_edge_and_review(
    config, user, stage, monkeypatch
):
    from prme.storage import alias_review

    async with MemoryEngine.open(config) as engine:
        nodes, proposal = await _proposal(engine, user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored alias review fault")

        with monkeypatch.context() as patch:
            patch.setattr(alias_review, "_checkpoint", fail)
            with pytest.raises(RuntimeError, match="Authored alias review fault"):
                await engine.review_alias_proposal(
                    proposal.operation_id,
                    user_id=user,
                    decision="accepted",
                    reviewer_id="human:alice",
                )
        edges = await engine._graph_store.get_edges(
            node_ids=[str(node.id) for node in nodes]
        )
        assert len(edges) == 1 and edges[0].metadata["identity_verified"] is False
        assert await _review_rows(engine, user) == []


async def test_concurrent_matching_reviews_publish_once(config, user):
    async with MemoryEngine.open(config) as engine:
        _, proposal = await _proposal(engine, user)

        async def accept():
            return await engine.review_alias_proposal(
                proposal.operation_id,
                user_id=user,
                decision="accepted",
                reviewer_id="human:alice",
            )

        results = await asyncio.gather(accept(), accept())
        assert sorted(result.applied for result in results) == [False, True]
        assert len(await _review_rows(engine, user)) == 1


async def test_http_review_inbox_is_authenticated_and_typed(config, user):
    config.api = APIConfig(
        user_keys={user: "owner-token", user + "-other": "other-token"}
    )
    async with MemoryEngine.open(config) as engine:
        _, proposal = await _proposal(engine, user)
        app = create_app(config)
        app.state.engine = engine
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer owner-token"},
        ) as client:
            pending = await client.get("/v1/alias-proposals", params={"status": "pending"})
            assert pending.status_code == 200
            assert pending.json()[0]["proposal"]["operation_id"] == proposal.operation_id

            accepted = await client.post(
                f"/v1/alias-proposals/{proposal.operation_id}/review",
                json={
                    "decision": "accepted",
                    "reviewer_id": "human:alice",
                    "reason": "Reviewed source catalog.",
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["verified_edge_id"]
            reviewed = await client.get(
                "/v1/alias-proposals", params={"status": "accepted"}
            )
            assert reviewed.json()[0]["status"] == "accepted"

            foreign = await client.get(
                "/v1/alias-proposals",
                params={"user_id": user},
                headers={"Authorization": "Bearer other-token"},
            )
            assert foreign.status_code == 403


async def test_mcp_review_helpers_preserve_owner_and_serialization(config, user):
    from prme.mcp.server import (
        memory_list_alias_proposals,
        memory_review_alias_proposal,
    )

    async with MemoryEngine.open(config) as engine:
        _, proposal = await _proposal(engine, user)
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context={"engine": engine})
        )
        pending = json.loads(
            await memory_list_alias_proposals(user_id=user, ctx=ctx)
        )
        assert pending[0]["status"] == "pending"
        accepted = json.loads(
            await memory_review_alias_proposal(
                proposal.operation_id,
                "accepted",
                "human:alice",
                user_id=user,
                ctx=ctx,
            )
        )
        assert accepted["verified_edge_id"]
        reviewed = json.loads(
            await memory_list_alias_proposals(
                user_id=user, status="accepted", ctx=ctx
            )
        )
        assert reviewed[0]["review"]["reviewer_id"] == "human:alice"
