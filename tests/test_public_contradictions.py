"""Conflict lifecycle is complete through the public engine API."""

import pytest

from prme import MemoryEngine
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _fact(engine, content, owner, scope=Scope.PERSONAL):
    event_id = await engine.store(
        content, user_id=owner, node_type=NodeType.FACT, scope=scope
    )
    return (await engine.get_event_nodes(event_id, user_id=owner))[0]


async def test_public_conflict_roundtrip_is_scoped_and_retry_safe(config, user):
    async with MemoryEngine.open(config) as engine:
        first = await _fact(engine, "Atlas uses east.", user)
        second = await _fact(engine, "Atlas uses west.", user)
        contested = await engine.contradict(
            str(first.id), str(second.id), user_id=user, actor_id="reviewer"
        )
        assert [node.lifecycle_state for node in contested] == [
            LifecycleState.CONTESTED, LifecycleState.CONTESTED,
        ]
        assert await engine.contradict(
            str(first.id), str(second.id), user_id=user, actor_id="reviewer"
        ) == contested

        winner, loser = await engine.resolve_contradiction(
            str(second.id), str(first.id), user_id=user,
            resolver_actor_id="reviewer",
        )
        assert winner.lifecycle_state == LifecycleState.STABLE
        assert loser.lifecycle_state == LifecycleState.DEPRECATED
        replay = await engine.resolve_contradiction(
            str(second.id), str(first.id), user_id=user,
            resolver_actor_id="reviewer",
        )
        assert replay == (winner, loser)
        result = await engine.retrieve("Atlas uses", user_id=user)
        assert first.id not in {item.node.id for item in result.results}


async def test_public_conflicts_reject_foreign_nodes_and_evidence(config, user):
    async with MemoryEngine.open(config) as engine:
        own = await _fact(engine, "Atlas uses east.", user)
        other = await _fact(engine, "Atlas uses west.", user + "-other")
        before = await engine.get_node(str(own.id), user_id=user)
        with pytest.raises(ValueError, match="not found"):
            await engine.contradict(
                str(own.id), str(other.id), user_id=user
            )
        assert await engine.get_node(str(own.id), user_id=user) == before
