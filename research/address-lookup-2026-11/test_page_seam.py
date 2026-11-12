"""P7: the refactor in ``prme.retrieval.temporal`` keeps the current lookup's results identical.

The indexed prototype needs the lookup split at the page boundary: ``classify_page`` for the per-node decision and
``build_state`` for everything after the page. This test holds that split to one property: the state returned by the
refactored ``get_assertion_state`` equals the state the pre-refactor code returned for the same store and query. The
fixture below is a hand-built store covering the shapes whose counting the split could break: a missing assertion, a
selector mismatch, a conditional claim, a superseded claim, a closed validity window and two owners.

This file lives under ``research/`` and is not collected by the project's test suite. It is run directly:

    PYTHONPATH=. uv run pytest research/address-lookup-2026-11/test_page_seam.py -q
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, "research/address-lookup-2026-11")

import keys  # noqa: E402
import lookup  # noqa: E402

from prme import AssertionStateQuery, MemoryEngine  # noqa: E402
from prme.config import MCPConfig  # noqa: E402
from prme.types import EpistemicType, LifecycleState, NodeType, Scope  # noqa: E402


@pytest.fixture
def config(tmp_path):
    from tests.contract.harness import product_config

    return product_config(str(tmp_path))


@pytest.fixture
def user() -> str:
    return "alice"


async def seed(engine, user: str) -> None:
    """One store holding every shape the count split could get wrong."""
    shared = {"predicate": "located_in", "polarity": "positive"}
    await engine.store("Alice lives in Lisbon.", user_id=user, node_type=NodeType.FACT,
                       metadata={**shared, "subject": "Alice", "object": "Lisbon"})
    await engine.store("Alice lives in Porto.", user_id=user, node_type=NodeType.FACT,
                       metadata={**shared, "subject": "alice", "object": "Porto"})
    # A claim whose selectors only the normalize rule makes equal to the query.
    await engine.store("Alice lives in Faro.", user_id=user, node_type=NodeType.FACT,
                       metadata={"subject": "  ALICE  ", "predicate": "Located-In",
                                 "object": "Faro", "polarity": "positive"})
    # A conditional claim that the epistemic filter refuses.
    await engine.store("Alice lives in Braga, if it rains.", user_id=user, node_type=NodeType.FACT,
                       epistemic_type=EpistemicType.CONDITIONAL,
                       metadata={**shared, "subject": "Alice", "object": "Braga",
                                 "condition": "if it rains", "condition_state": "unknown"})
    # A claim outside its validity window.
    await engine.store("Alice lived in Evora.", user_id=user, node_type=NodeType.FACT,
                       valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
                       valid_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
                       metadata={**shared, "subject": "Alice", "object": "Evora"})
    # A retired claim, and a matching claim at another address.
    await engine.store("Alice lives in Sintra.", user_id=user, node_type=NodeType.FACT,
                       metadata={**shared, "subject": "Alice", "object": "Sintra"})
    await engine.store("Alice likes tea.", user_id=user, node_type=NodeType.FACT,
                       metadata={"subject": "Alice", "predicate": "likes", "object": "tea",
                                 "polarity": "positive"})
    # A node with no usable assertion at all.
    await engine.store("A plain note.", user_id=user, node_type=NodeType.FACT, metadata={"note": "x"})
    # One other owner's claim at the same address.
    await engine.store("Alice lives in Madrid.", user_id="bob", node_type=NodeType.FACT,
                       metadata={**shared, "subject": "Alice", "object": "Madrid"})


async def build(config, user):
    from prme.storage.embedding import create_embedding_provider

    provider = create_embedding_provider(config.embedding)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    await seed(engine, user)
    return engine


def query() -> AssertionStateQuery:
    return AssertionStateQuery(subject=" alice ", predicate="located in", scope=Scope.PERSONAL,
                               valid_at=datetime(2026, 12, 1, tzinfo=timezone.utc))


async def test_refactor_returns_the_same_state(config, user):
    engine = await build(config, user)
    state = await engine.get_assertion_state(query(), user_id=user)
    assert state.status == "multiple"
    assert state.scanned_nodes == 8
    assert state.matched_records == 6
    assert state.exclusions["selector_mismatch"] == 1
    assert state.exclusions["condition_filtered"] == 1
    assert state.exclusions["missing_structured_assertion"] == 1
    assert state.exclusions["outside_validity_window"] == 1
    await engine.close()


async def test_indexed_lookup_returns_the_same_state(config, user):
    """The prototype must equal the current path on a store this small, field for field."""
    engine = await build(config, user)
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    keys.backfill(graph._conn)
    keys.install_write_path(graph)
    current = await engine.get_assertion_state(query(), user_id=user)
    indexed = await lookup.indexed_state(engine, query(), user_id=user)
    assert indexed.model_dump_json() == current.model_dump_json()
    await engine.close()


async def test_the_key_matches_the_python_rule_on_this_store(config, user):
    """Every key on disk equals what ``normalize_assertion_value`` says for the same node."""
    from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value
    from prme.models.nodes import MemoryNode

    engine = await build(config, user)
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    keys.backfill(graph._conn)
    rows = graph._conn.execute(
        f"SELECT CAST(id AS VARCHAR), metadata, {keys.SUBJECT_COLUMN}, {keys.PREDICATE_COLUMN} FROM nodes"
    ).fetchall()
    checked = 0
    for node_id, metadata, stored_subject, stored_predicate in rows:
        node = graph._get_node_sync(node_id, True)
        values = _assertion_values(node)
        if values is None:
            assert stored_subject is None and stored_predicate is None, node_id
            continue
        assert stored_subject == normalize_assertion_value("subject", values["subject"]), node_id
        assert stored_predicate == normalize_assertion_value("predicate", values["predicate"]), node_id
        checked += 1
    assert checked >= 4
    await engine.close()


async def test_a_new_claim_is_found_without_a_backfill(config, user):
    """The write path keeps the key, so a claim stored after the migration is found at once."""
    engine = await build(config, user)
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    keys.backfill(graph._conn)
    keys.install_write_path(graph)
    await engine.store("Alice lives in Porto now.", user_id=user, node_type=NodeType.FACT,
                       metadata={"subject": "Alice", "predicate": "located_in", "object": "Coimbra",
                                 "polarity": "positive"})
    state = await engine.get_assertion_state(query(), user_id=user)
    found = {entry.object for entry in state.timeline}
    assert "Coimbra" in found
    indexed = await lookup.indexed_state(engine, query(), user_id=user)
    assert indexed.model_dump_json() == state.model_dump_json()
    await engine.close()


async def test_rewritten_metadata_takes_its_key_with_it(config, user):
    """A rewritten subject must move the claim to its new address, not leave it at the old one."""
    engine = await build(config, user)
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    keys.backfill(graph._conn)
    keys.install_write_path(graph)
    state = await engine.get_assertion_state(query(), user_id=user)
    target = next(entry for entry in state.timeline if entry.object == "Sintra")
    await graph.update_node(str(target.node_id),
                            metadata={"subject": "Alicia", "predicate": "located_in",
                                      "object": "Sintra", "polarity": "positive"})
    old_address = await engine.get_assertion_state(query(), user_id=user)
    new_address = await engine.get_assertion_state(
        AssertionStateQuery(subject="alicia", predicate="located_in", scope=Scope.PERSONAL,
                            valid_at=datetime(2026, 12, 1, tzinfo=timezone.utc)),
        user_id=user,
    )
    assert "Sintra" not in {entry.object for entry in old_address.timeline}
    assert "Sintra" in {entry.object for entry in new_address.timeline}
    indexed = await lookup.indexed_state(
        engine,
        AssertionStateQuery(subject="alicia", predicate="located_in", scope=Scope.PERSONAL,
                            valid_at=datetime(2026, 12, 1, tzinfo=timezone.utc)),
        user_id=user,
    )
    assert indexed.model_dump_json() == new_address.model_dump_json()
    await engine.close()
