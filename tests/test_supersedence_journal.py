"""Explicit corrections are atomic, auditable and safe to retry."""

import asyncio
from uuid import UUID

import pytest

from prme import MemoryEngine
from prme.storage.supersedence import read_record
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _claims(engine, owner):
    event_ids = [
        await engine.store(content, user_id=owner, node_type=NodeType.FACT)
        for content in ("Atlas uses east.", "Atlas uses west.")
    ]
    nodes = [
        (await engine.get_event_nodes(event_id, user_id=owner))[0]
        for event_id in event_ids
    ]
    return event_ids, nodes


async def _operation_count(graph, node_id):
    if hasattr(graph, "_pool"):
        async with graph._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM operations "
                "WHERE target_id=$1 AND op_type='SUPERSEDENCE_APPLIED'",
                node_id,
            )
    return graph._conn.execute(
        "SELECT count(*) FROM operations "
        "WHERE target_id=? AND op_type='SUPERSEDENCE_APPLIED'",
        [node_id],
    ).fetchone()[0]


async def test_explicit_correction_is_journaled_and_retry_safe(config, user):
    async with MemoryEngine.open(config) as engine:
        events, nodes = await _claims(engine, user)
        old_id, new_id = (str(node.id) for node in nodes)
        await engine.supersede(
            old_id, new_id, evidence_id=events[1], user_id=user, actor_id="reviewer",
        )
        retired = await engine.get_node(old_id, user_id=user, include_superseded=True)
        assert retired.lifecycle_state == LifecycleState.SUPERSEDED
        provenance = await engine.get_provenance(old_id, user_id=user)
        operations = [
            item for item in provenance.operations
            if item.op_type == "SUPERSEDENCE_APPLIED"
        ]
        assert len(operations) == 1
        record = read_record(operations[0].payload)
        assert record.before == nodes[0]
        assert record.after == retired
        assert record.replacement == nodes[1]
        assert record.actor_id == "reviewer"
        assert record.evidence_id == UUID(events[1])

    async with MemoryEngine.open(config) as engine:
        await engine.supersede(
            old_id.upper(), new_id.upper(), evidence_id=events[1],
            user_id=user, actor_id="reviewer",
        )
        assert await _operation_count(engine._graph_store, old_id) == 1
        assert len(await engine._graph_store.get_edges(
            target_id=old_id, edge_type=EdgeType.SUPERSEDES,
        )) == 1
        with pytest.raises(ValueError, match="different inputs"):
            await engine.supersede(
                old_id, new_id, evidence_id=events[1],
                user_id=user, actor_id="another-reviewer",
            )


async def test_concurrent_exact_corrections_publish_once(config, user):
    async with MemoryEngine.open(config) as engine:
        events, nodes = await _claims(engine, user)
        old_id, new_id = (str(node.id) for node in nodes)
        await asyncio.gather(*[
            engine.supersede(
                old_id, new_id, evidence_id=events[1],
                user_id=user, actor_id="reviewer",
            )
            for _ in range(4)
        ])
        assert await _operation_count(engine._graph_store, old_id) == 1
        assert len(await engine._graph_store.get_edges(
            target_id=old_id, edge_type=EdgeType.SUPERSEDES,
        )) == 1


async def test_journal_failure_rolls_back_correction(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        _, nodes = await _claims(engine, user)
        old_id, new_id = (str(node.id) for node in nodes)
        from prme.storage import supersedence

        def fail_after_journal(stage):
            if stage == "journaled":
                raise RuntimeError("injected journal failure")

        monkeypatch.setattr(supersedence, "_checkpoint", fail_after_journal)
        with pytest.raises(RuntimeError, match="injected"):
            await engine.supersede(old_id, new_id, user_id=user)
        assert await engine.get_node(old_id, user_id=user) == nodes[0]
        assert await engine._graph_store.get_edges(
            target_id=old_id, edge_type=EdgeType.SUPERSEDES,
        ) == []
        assert await _operation_count(engine._graph_store, old_id) == 0
