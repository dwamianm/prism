"""Complete scoped enumeration must not silently stop at retrieval's top k."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from prme import MemoryClient, MemoryEngine
from prme.models import MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_enumeration_crosses_many_pages_with_tied_dates_and_tenant_filters(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        expected = []
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Unique random UUID prefix per tenant avoids collisions in shared PG tests.
        from uuid import uuid4
        prefix = uuid4().int & ~((1 << 16) - 1)
        for i in range(253):
            node = MemoryNode(id=UUID(int=prefix + i), user_id=user if i % 5 else user + "-other",
                              scope=Scope.PERSONAL if i % 3 else Scope.PROJECT,
                              node_type=NodeType.FACT, content=f"A complete source {i}",
                              created_at=timestamp, updated_at=timestamp)
            await engine._graph_store.create_node(node)
            if node.user_id == user and node.scope == Scope.PERSONAL:
                expected.append(node.id)
        assert len(expected) > 100
        nodes = [n async for n in engine.iter_nodes(user_id=user, scope=Scope.PERSONAL,
                                                    node_type=NodeType.FACT, batch_size=7)]
        assert [n.id for n in nodes] == expected
        first = await engine.scan_nodes(user_id=user, scope=Scope.PERSONAL, limit=7)
        second = await engine.scan_nodes(user_id=user, scope=Scope.PERSONAL,
                                         after_id=str(first[-1].id), limit=7)
        assert [n.id for n in first + second] == expected[:14]


async def test_enumeration_lifecycle_filters_and_empty_filter(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for state in (LifecycleState.TENTATIVE, LifecycleState.ARCHIVED):
            await engine._graph_store.create_node(MemoryNode(
                user_id=user, node_type=NodeType.FACT, content=state.value, lifecycle_state=state,
            ))
        active = [n async for n in engine.iter_nodes(user_id=user, batch_size=1)]
        assert [n.lifecycle_state for n in active] == [LifecycleState.TENTATIVE]
        all_nodes = [n async for n in engine.iter_nodes(user_id=user, lifecycle_states=list(LifecycleState), batch_size=1)]
        assert len(all_nodes) == 2
        assert [n async for n in engine.iter_nodes(user_id=user, lifecycle_states=[])] == []


async def test_enumeration_rejects_missing_tenant_and_invalid_page_size(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for kwargs in ({"user_id": ""}, {"user_id": user, "batch_size": 0}, {"user_id": user, "batch_size": -1}):
            with pytest.raises(ValueError):
                _ = [n async for n in engine.iter_nodes(**kwargs)]
        with pytest.raises(ValueError):
            await engine.scan_nodes(user_id=user, after_id="not-a-cursor")


def test_sync_client_supports_complete_iteration_and_explicit_pages(config, user):  # noqa: F811
    with MemoryClient(config=config) as client:
        for i in range(5):
            client.store(f"Memory number {i}", user_id=user, node_type=NodeType.NOTE)
        client.store("Other tenant's memory", user_id=user + "-other", node_type=NodeType.NOTE)
        nodes = list(client.iter_nodes(user_id=user, node_type=NodeType.NOTE, batch_size=2))
        assert len(nodes) == 5 and len({n.id for n in nodes}) == 5
        assert all(n.user_id == user for n in nodes)
        assert [n.id for n in client.scan_nodes(user_id=user, node_type=NodeType.NOTE, limit=2)] == [n.id for n in nodes[:2]]
