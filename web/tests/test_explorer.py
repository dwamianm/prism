"""Real-backend contracts for inspection, pagination and tenant isolation."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from prme import MemoryEngine
from prme.config import APIConfig, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401
from web.server import create_explorer_app, explorer_config


async def make_node(engine, owner, **kwargs):
    node = MemoryNode(user_id=owner, node_type=NodeType.FACT, content="Stored claim", **kwargs)
    await engine._graph_store.create_node(node)
    return node


async def test_connections_filter_endpoints_before_paging(config, user):  # noqa: F811
    config.api = APIConfig(user_keys={user: "owner-key", user + "-other": "other-key"})
    async with MemoryEngine.open(config) as engine:
        root = await make_node(engine, user)
        retired = await make_node(engine, user, lifecycle_state=LifecycleState.ARCHIVED)
        neighbor = await make_node(engine, user)
        foreign = await make_node(engine, user + "-other")
        other_scope = await make_node(engine, user, scope=Scope.PROJECT)
        edges = [
            MemoryEdge(id=UUID(int=1), source_id=root.id, target_id=foreign.id, user_id=user, edge_type=EdgeType.RELATES_TO),
            MemoryEdge(id=UUID(int=2), source_id=other_scope.id, target_id=root.id, user_id=user, edge_type=EdgeType.RELATES_TO),
            MemoryEdge(id=UUID(int=3), source_id=root.id, target_id=neighbor.id, user_id=user + "-other", edge_type=EdgeType.RELATES_TO),
            MemoryEdge(id=UUID(int=4), source_id=retired.id, target_id=root.id, user_id=user, edge_type=EdgeType.SUPERSEDES,
                       valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc), valid_to=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            MemoryEdge(id=UUID(int=5), source_id=root.id, target_id=neighbor.id, user_id=user, edge_type=EdgeType.SUPPORTS),
            MemoryEdge(id=UUID(int=6), source_id=root.id, target_id=root.id, user_id=user, edge_type=EdgeType.RELATES_TO),
            MemoryEdge(id=UUID(int=7), source_id=root.id, target_id=neighbor.id, user_id=user, edge_type=EdgeType.RELATES_TO,
                       metadata={"relation": "alias", "identity_verified": False}),
        ]
        for edge in edges:
            await engine._graph_store.create_edge(edge)
        app = create_explorer_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", headers={"Authorization": "Bearer owner-key"}) as client:
            path = f"/v1/explorer/nodes/{root.id}/connections"
            first = await client.get(path, params={"limit": 1})
            assert first.status_code == 200
            assert first.headers["cache-control"] == "no-store"
            result = first.json()
            assert [e["id"] for e in result["edges"]] == [str(edges[3].id)]
            assert [n["id"] for n in result["nodes"]] == [str(retired.id)]
            assert result["nodes"][0]["lifecycle_state"] == "archived"
            assert result["edges"][0]["valid_to"] is not None
            assert result["has_more"] is True
            second = (await client.get(path, params={"after_id": result["next_cursor"], "limit": 2})).json()
            assert [e["id"] for e in second["edges"]] == [str(edges[4].id), str(edges[5].id)]
            assert not second["has_more"] and second["next_cursor"] is None
            complete = (await client.get(path)).json()
            assert [e["id"] for e in complete["edges"]] == [str(e.id) for e in edges[3:6]]
            opted_in = (await client.get(path, params={"include_proposals": True})).json()
            assert [e["id"] for e in opted_in["edges"]] == [str(e.id) for e in edges[3:]]
            for result in (complete, opted_in):
                encoded = str(result)
                assert str(foreign.id) not in encoded and str(other_scope.id) not in encoded
                assert user + "-other" not in encoded
            assert (await client.get(path, params={"user_id": user + "-other"})).status_code == 403
            for node_id in (foreign.id, uuid4()):
                assert (await client.get(f"/v1/explorer/nodes/{node_id}/connections")).status_code == 404
            assert (await client.get(path, headers={"Authorization": "Bearer wrong"})).status_code == 401
            for params in ({"limit": 0}, {"limit": 101}, {"after_id": "bad-id"}):
                assert (await client.get(path, params=params)).status_code == 422


async def test_operator_requires_owner_and_empty_graph_is_valid(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        node = await make_node(engine, user)
        app = create_explorer_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            path = f"/v1/explorer/nodes/{node.id}/connections"
            assert (await client.get(path)).status_code == 422
            assert (await client.get(path, params={"user_id": user + "-other"})).status_code == 404
            result = (await client.get(path, params={"user_id": user})).json()
            assert result["node"]["id"] == str(node.id)
            assert result["nodes"] == result["edges"] == []
            assert not result["has_more"]


async def test_browser_scan_and_provenance_contract(config, user):  # noqa: F811
    config.api = APIConfig(user_keys={user: "test-key"})
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store("Original source <script>unsafe()</script>", user_id=user)
        node = (await engine.scan_nodes(user_id=user))[0]
        await engine.archive(str(node.id), user_id=user)
        app = create_explorer_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", headers={"Authorization": "Bearer test-key"}) as client:
            response = await client.get("/v1/nodes/scan", params=[("state", s.value) for s in LifecycleState])
            assert response.status_code == 200
            assert [n["id"] for n in response.json()["nodes"]] == [str(node.id)]
            provenance = (await client.get(f"/v1/nodes/{node.id}/provenance", params={"operation_limit": 1})).json()
            assert provenance["evidence_events"][0]["id"] == event_id
            assert "<script>unsafe()</script>" in provenance["evidence_events"][0]["content"]


async def test_static_ui_is_served_without_exposing_source():
    app = create_explorer_app(PRMEConfig(api=APIConfig(user_keys={"alice": "test-key"})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for path in ("/", "/app.js", "/graph.js", "/styles.css", "/mark.svg", "/fonts/inter-latin-wght-normal.woff2"):
            result = await client.get(path)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
        assert (await client.get("/server.py")).status_code == 404
        assert (await client.get("/v1/nodes/scan")).status_code == 401
        assert (await client.get("/v1/health")).status_code == 200


@pytest.fixture
def pack_env(tmp_path, monkeypatch):
    """Isolate explorer_config from the repository .env and the caller's shell."""
    monkeypatch.chdir(tmp_path)
    for name in ("PRME_CHAT_DATA_DIR", "PRME_DB_PATH", "PRME_VECTOR_PATH", "PRME_LEXICAL_PATH", "PRME_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    pack = tmp_path / "pack"
    pack.mkdir()
    return pack


def test_chat_data_dir_supplies_unset_pack_paths(pack_env, monkeypatch):
    assert explorer_config().db_path == "./memory.duckdb"
    (pack_env.parent / ".env").write_text("PRME_CHAT_DATA_DIR=./pack\n")
    loaded = explorer_config()
    assert (loaded.db_path, loaded.vector_path, loaded.lexical_path) == (
        str(pack_env / "memory.duckdb"), str(pack_env / "vectors.usearch"), str(pack_env / "lexical_index"),
    )
    monkeypatch.setenv("PRME_DB_PATH", "/elsewhere/memory.duckdb")
    loaded = explorer_config()
    assert loaded.db_path == "/elsewhere/memory.duckdb"
    assert loaded.lexical_path == str(pack_env / "lexical_index")


def test_missing_chat_data_dir_is_an_error(pack_env, monkeypatch):
    monkeypatch.setenv("PRME_CHAT_DATA_DIR", str(pack_env / "typo"))
    with pytest.raises(FileNotFoundError, match="typo"):
        explorer_config()


async def test_demo_pack_is_connected_through_shared_entities(tmp_path, monkeypatch):
    from prme.types import LifecycleState
    from tests.test_durable_ingestion import MockEmbeddingProvider
    from web import demo

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    await demo.build(tmp_path / "pack")
    pack = tmp_path / "pack"
    pack_config = PRMEConfig(db_path=str(pack / "memory.duckdb"), vector_path=str(pack / "vectors.usearch"),
                        lexical_path=str(pack / "lexical_index"))
    async with MemoryEngine.open(pack_config) as engine:
        nodes = await engine.query_nodes(user_id=demo.OWNER, lifecycle_states=list(LifecycleState))
        entities = {n.content: n for n in nodes if n.node_type == NodeType.ENTITY}
        # One entity per name across every session.
        assert sorted(entities) == ["Avery", "Brightpath", "Jordan", "Northwind Health", "Priya", "Sparkle Cleaning"]
        current = [n for n in nodes if n.node_type == NodeType.FACT and n.lifecycle_state != LifecycleState.SUPERSEDED]
        employer = [n for n in current if n.metadata["predicate"] == "works_at" and n.metadata["subject"] == "Avery"]
        # The job change retired Northwind Health; the repeated Brightpath claim merged.
        assert [n.metadata["object"] for n in employer] == ["Brightpath"]
        assert len(employer[0].evidence_refs) == 2
        superseded = [n for n in nodes if n.lifecycle_state == LifecycleState.SUPERSEDED]
        assert len(superseded) == 4
        edges = await engine._graph_store.get_edges(source_id=str(entities["Avery"].id))
        assert {e.edge_type for e in edges} == {EdgeType.HAS_FACT}
