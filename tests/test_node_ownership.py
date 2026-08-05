"""Tests for engine-level node ownership checks (issue #35).

Node-ID operations were unscoped: knowing an ID was enough to read or mutate
another user's memory. Passing ``user_id`` now makes a foreign node behave
exactly like a missing one, so a caller cannot use the difference between
"denied" and "not found" to learn that an ID exists (RFC-0004 S6).

Omitting ``user_id`` keeps the previous single-user behaviour, which the
organizer and CLI rely on.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import NodeType


@pytest_asyncio.fixture
async def engine():
    with tempfile.TemporaryDirectory(prefix="prme_owner_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        eng = await MemoryEngine.create(PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
        ))
        yield eng
        await eng.close()


async def _node_id_for(engine: MemoryEngine, user_id: str) -> str:
    nodes = await engine.query_nodes(user_id=user_id, limit=1)
    assert nodes, f"expected a stored node for {user_id}"
    return str(nodes[0].id)


@pytest_asyncio.fixture
async def alice_node(engine):
    await engine.store(
        "Alice's private salary figure is 123456.",
        user_id="alice", node_type=NodeType.FACT,
    )
    return await _node_id_for(engine, "alice")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_get_node_returns_own_node(engine, alice_node):
    node = await engine.get_node(alice_node, user_id="alice")
    assert node is not None and node.user_id == "alice"


async def test_get_node_hides_another_users_node(engine, alice_node):
    assert await engine.get_node(alice_node, user_id="mallory") is None


async def test_get_node_hides_another_users_archived_node(engine, alice_node):
    await engine.archive(alice_node)
    assert await engine.get_node(
        alice_node, include_superseded=True, user_id="mallory"
    ) is None


async def test_get_node_without_user_id_is_unchanged(engine, alice_node):
    node = await engine.get_node(alice_node)
    assert node is not None and node.user_id == "alice"


async def test_missing_and_foreign_nodes_are_indistinguishable(engine, alice_node):
    missing = await engine.get_node(
        "00000000-0000-0000-0000-000000000000", user_id="mallory"
    )
    foreign = await engine.get_node(alice_node, user_id="mallory")
    assert missing is foreign is None


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def test_promote_rejects_another_users_node(engine, alice_node):
    with pytest.raises(ValueError, match="not found"):
        await engine.promote(alice_node, user_id="mallory")

    node = await engine.get_node(alice_node)
    assert node.lifecycle_state.value == "tentative"


async def test_archive_rejects_another_users_node(engine, alice_node):
    with pytest.raises(ValueError, match="not found"):
        await engine.archive(alice_node, user_id="mallory")

    node = await engine.get_node(alice_node, include_superseded=True)
    assert node.lifecycle_state.value != "archived"


async def test_reinforce_rejects_another_users_node(engine, alice_node):
    before = await engine.get_node(alice_node)

    with pytest.raises(ValueError, match="not found"):
        await engine.reinforce(alice_node, user_id="mallory")

    after = await engine.get_node(alice_node)
    assert after.reinforcement_boost == before.reinforcement_boost


async def test_supersede_rejects_a_cross_user_pair(engine, alice_node):
    await engine.store("Mallory's own note.", user_id="mallory")
    mallory_node = await _node_id_for(engine, "mallory")

    with pytest.raises(ValueError, match="not found"):
        await engine.supersede(alice_node, mallory_node, user_id="mallory")

    node = await engine.get_node(alice_node, include_superseded=True)
    assert node.lifecycle_state.value != "superseded"


async def test_owner_can_still_mutate(engine, alice_node):
    await engine.promote(alice_node, user_id="alice")
    node = await engine.get_node(alice_node)
    assert node.lifecycle_state.value == "stable"

    await engine.reinforce(alice_node, user_id="alice")
    reinforced = await engine.get_node(alice_node)
    assert reinforced.reinforcement_boost > 0

    await engine.archive(alice_node, user_id="alice")
    archived = await engine.get_node(alice_node, include_superseded=True)
    assert archived.lifecycle_state.value == "archived"


async def test_mutations_without_user_id_are_unchanged(engine, alice_node):
    await engine.promote(alice_node)
    node = await engine.get_node(alice_node)
    assert node.lifecycle_state.value == "stable"
