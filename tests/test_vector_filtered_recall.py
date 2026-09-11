"""Filtered vector recall must survive a corpus dominated by ineligible nodes.

Uses real DuckDB/USearch with fixed vectors; no model downloads or API calls.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from prme.models.nodes import MemoryNode
from prme.storage.vector_index import VectorIndex
from prme.types import LifecycleState, NodeType, Scope


class FixedEmbeddingProvider:
    model_name = "filtered-recall-test"
    model_version = "1"
    dimension = 2

    async def embed(self, texts):
        vectors = {
            "query": [1.0, 0.0],
            "distractor": [1.0, 0.0],
            "first": [0.8, 0.6],
            "second": [0.6, 0.8],
            "third": [0.0, 1.0],
        }
        return [vectors[text] for text in texts]


@pytest_asyncio.fixture(params=[True, False], ids=["exact", "approximate"])
async def vector_store(request, isolated_duckdb, isolated_graph_store, tmp_path):
    index = VectorIndex(
        isolated_duckdb,
        str(tmp_path / "vectors.usearch"),
        FixedEmbeddingProvider(),
        exact_search=request.param,
    )
    yield index, isolated_graph_store
    await index.close()


async def add_node(store, content, **kwargs):
    index, graph = store
    node = MemoryNode(
        content=content,
        user_id=kwargs.pop("user_id", "alice"),
        scope=kwargs.pop("scope", Scope.PERSONAL),
        node_type=NodeType.FACT,
        **kwargs,
    )
    await graph.create_node(node)
    await index.index(str(node.id), content, node.user_id)
    return str(node.id)


@pytest.mark.parametrize("excluded_by", ["user", "scope", "expired", "future", "archived"])
async def test_ineligible_neighbors_do_not_hide_matching_memories(vector_store, excluded_by):
    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    before = datetime(2024, 1, 1, tzinfo=timezone.utc)
    after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    distractor_args = {
        "user": {"user_id": "bob"},
        "scope": {"scope": Scope.PROJECT},
        "expired": {"valid_from": before, "valid_to": cutoff},
        "future": {"valid_from": after},
        "archived": {"lifecycle_state": LifecycleState.ARCHIVED},
    }[excluded_by]
    filters = {
        "scope": {"scope": [Scope.PERSONAL.value]},
        "expired": {"time_from": cutoff},
        "future": {"time_to": cutoff},
    }.get(excluded_by, {})

    # All 24 ineligible vectors are closer than any eligible vector.
    # k=2 previously inspected just six vectors and returned nothing.
    for _ in range(24):
        await add_node(vector_store, "distractor", **distractor_args)
    expected = [
        await add_node(vector_store, content, valid_from=before)
        for content in ("first", "second", "third")
    ]

    index, _ = vector_store
    results = await index.search("query", "alice", k=2, **filters)
    assert [result["node_id"] for result in results] == expected[:2]
    assert results[0]["score"] > results[1]["score"]


async def test_returns_all_available_matches_when_fewer_than_k(vector_store):
    for _ in range(40):
        await add_node(vector_store, "distractor", user_id="bob")
    expected = await add_node(vector_store, "first")
    index, _ = vector_store
    results = await index.search_by_vector([1.0, 0.0], "alice", k=10)
    assert [result["node_id"] for result in results] == [expected]
    assert await index.search("query", "unknown-user", k=10) == []


async def test_empty_index_and_zero_limit(vector_store):
    index, _ = vector_store
    assert await index.search("query", "alice", k=2) == []
    await add_node(vector_store, "first")
    assert await index.search("query", "alice", k=0) == []


async def test_duplicate_vectors_do_not_consume_result_slots(vector_store):
    first = await add_node(vector_store, "first")
    second = await add_node(vector_store, "second")
    index, _ = vector_store
    for _ in range(8):
        await index.index(first, "first", "alice")
    results = await index.search("query", "alice", k=2)
    assert [result["node_id"] for result in results] == [first, second]
