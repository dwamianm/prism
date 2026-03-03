"""Tests for PgVectorIndex against a real PostgreSQL instance.

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


class MockEmbeddingProvider:
    """Deterministic embedding provider for tests."""

    model_name = "mock-model"
    model_version = "mock-v1"
    dimension = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        results = []
        for text in texts:
            h = hashlib.sha256(text.encode()).digest()
            vec = [float(b) / 255.0 for b in h] * (384 // 32)
            results.append(vec[:384])
        return results


@pytest.fixture
async def pg_pool():
    import asyncpg

    url = os.environ["PRME_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def vector_index(pg_pool):
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.pg.vector_index import PgVectorIndex

    await initialize_pg_database(pg_pool, embedding_dim=384)
    return PgVectorIndex(pg_pool, MockEmbeddingProvider())


@pytest.fixture
async def graph_store(pg_pool):
    from prme.storage.pg.graph_store import PgGraphStore
    return PgGraphStore(pg_pool)


async def test_index_and_search(vector_index, graph_store):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    node = MemoryNode(
        user_id=uid, node_type=NodeType.FACT,
        scope=Scope.PERSONAL, content="The capital of France is Paris",
    )
    await graph_store.create_node(node)
    await vector_index.index(str(node.id), node.content, uid)

    results = await vector_index.search("capital of France", uid, k=5)
    assert len(results) >= 1
    assert results[0]["node_id"] == str(node.id)


async def test_search_empty(vector_index):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    results = await vector_index.search("nonexistent query", uid, k=5)
    assert results == []
