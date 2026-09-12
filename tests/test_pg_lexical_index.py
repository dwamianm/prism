"""Tests for PgLexicalIndex against a real PostgreSQL instance.

Requires PRME_TEST_DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import uuid

import pytest

from prme.models.nodes import MemoryNode
from prme.types import NodeType, Scope

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_pool():
    import asyncpg

    url = os.environ["PRME_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def lexical_index(pg_pool):
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.pg.lexical_index import PgLexicalIndex

    await initialize_pg_database(pg_pool)
    return PgLexicalIndex(pg_pool)


@pytest.fixture
async def graph_store(pg_pool):
    from prme.storage.pg.graph_store import PgGraphStore
    return PgGraphStore(pg_pool)


async def test_search_nodes(lexical_index, graph_store):
    """Nodes with content_tsv GENERATED column should be searchable."""
    uid = f"user-{uuid.uuid4().hex[:8]}"
    node = MemoryNode(
        user_id=uid, node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        content="PostgreSQL supports full-text search natively",
    )
    await graph_store.create_node(node)

    results = await lexical_index.search("full-text search", uid, limit=5)
    assert len(results) >= 1
    assert any("full-text" in r["content"].lower() or "search" in r["content"].lower() for r in results)


async def test_index_non_node_content(lexical_index):
    """Non-node content should be indexed in lexical_documents table."""
    uid = f"user-{uuid.uuid4().hex[:8]}"
    fake_node_id = str(uuid.uuid4())

    await lexical_index.index(
        fake_node_id, "Machine learning algorithms are fascinating", uid, "event"
    )

    results = await lexical_index.search("machine learning", uid, limit=5)
    assert len(results) >= 1


async def test_delete_by_node_id(lexical_index):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    fake_node_id = str(uuid.uuid4())

    await lexical_index.index(fake_node_id, "deletable content", uid, "note")
    await lexical_index.delete_by_node_id(fake_node_id)

    results = await lexical_index.search("deletable", uid, limit=5)
    matching = [r for r in results if r["node_id"] == fake_node_id]
    assert len(matching) == 0


async def test_duplicate_documents_do_not_consume_candidate_limit(lexical_index, graph_store):
    """A previously indexed source may later also have a durable graph row."""
    uid = f"user-{uuid.uuid4()}"
    first = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL,
                       content="cobalt cobalt cobalt cobalt")
    second = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL,
                        content="cobalt")
    # This order creates a legitimate source/index copy before graph publication.
    await lexical_index.index(str(first.id), first.content, uid, "note", "personal")
    await graph_store.create_node(first)
    await graph_store.create_node(second)
    for owner, scope in [(uid + "-other", Scope.PERSONAL), (uid, Scope.PROJECT)]:
        await graph_store.create_node(MemoryNode(user_id=owner, scope=scope, node_type=NodeType.NOTE,
                                                 content="cobalt " * 20))
    rows = await lexical_index.search("cobalt", uid, limit=2, scope=["personal"], node_type="note")
    assert [row["node_id"] for row in rows] == [str(first.id), str(second.id)]
    assert len(await lexical_index.search("cobalt", uid, limit=1, scope=["personal"])) == 1


async def test_tied_lexical_candidates_use_identity_before_limit(lexical_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    nodes = [MemoryNode(user_id=uid, node_type=NodeType.NOTE, content="identical telescope") for _ in range(3)]
    # Insert in the opposite order to the required stable identity tie-break.
    for node in sorted(nodes, key=lambda node: str(node.id), reverse=True):
        await graph_store.create_node(node)
    rows = await lexical_index.search("telescope", uid, limit=2)
    assert [row["node_id"] for row in rows] == sorted(str(node.id) for node in nodes)[:2]
