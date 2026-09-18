"""Explicit reinforcement must preserve provenance boundaries and existing belief."""

from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.types import Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("scoped_caller", [True, False])
@pytest.mark.parametrize("invalid", ["foreign", "scope", "missing", "malformed"])
async def test_reinforcement_rejects_invalid_evidence_before_any_mutation(
    config, user, scoped_caller, invalid
):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An owned observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = "not-a-uuid" if invalid == "malformed" else str(uuid4())
        if invalid in ("foreign", "scope"):
            evidence = await engine.store(
                "Evidence outside the claim boundary",
                user_id=user + "-other" if invalid == "foreign" else user,
                scope=Scope.PROJECT if invalid == "scope" else Scope.PERSONAL,
            )
        with pytest.raises(ValueError, match="Evidence event not found in the node's owner and scope"):
            await engine.reinforce(
                str(node.id), evidence_id=evidence,
                user_id=user if scoped_caller else None,
            )
        after = await engine.get_node(str(node.id), user_id=user)
        assert after == node
    async with MemoryEngine.open(config) as engine:
        assert await engine.get_node(str(node.id), user_id=user) == node


async def test_owned_evidence_and_reinforcement_survive_restart(config, user):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An owned observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = await engine.store("A confirming observation", user_id=user)
        await engine.reinforce(str(node.id), evidence_id=evidence, user_id=user)
        updated = await engine.get_node(str(node.id), user_id=user)
        assert UUID(evidence) in updated.evidence_refs
        assert updated.reinforcement_boost == pytest.approx(.15)
        assert updated.confidence_base == pytest.approx(node.confidence_base + .05)
    async with MemoryEngine.open(config) as engine:
        assert await engine.get_node(str(node.id), user_id=user) == updated


@pytest.mark.parametrize("confidence,boost", [(0.99, 0.1), (0.5, 0.75)])
async def test_reinforcement_never_reduces_existing_values(config, user, confidence, boost):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("A strongly supported observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        await engine._graph_store.update_node(
            str(node.id), confidence_base=confidence, reinforcement_boost=boost,
        )
        await engine.reinforce(str(node.id), user_id=user)
        updated = await engine.get_node(str(node.id), user_id=user)
        assert updated.confidence_base >= confidence
        assert updated.reinforcement_boost >= boost
