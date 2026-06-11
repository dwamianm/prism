"""Tests for the PRME HTTP API layer.

Uses FastAPI TestClient with a real MemoryEngine backed by
temporary DuckDB, vector, and lexical storage.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from prme.api.app import create_app
from prme.config import APIConfig, PRMEConfig
from prme.storage.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_api_") as d:
        yield d


@pytest.fixture
def config(tmp_dir):
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


@pytest.fixture
def app(config):
    """Create a FastAPI app with custom config."""
    return create_app(config)


@pytest.fixture
def client(app):
    """Create a TestClient with lifespan managed."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Health & Stats
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_stats_returns_counts(self, client):
        resp = client.get("/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "node_count" in data
        assert "backend" in data
        assert data["node_count"] >= 0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestStore:
    def test_store_basic(self, client):
        resp = client.post(
            "/v1/store",
            json={
                "content": "Paris is the capital of France",
                "user_id": "test-user",
                "role": "user",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "event_id" in data
        assert data["event_id"]  # non-empty

    def test_store_with_node_type(self, client):
        resp = client.post(
            "/v1/store",
            json={
                "content": "Python is a programming language",
                "user_id": "test-user",
                "node_type": "fact",
                "scope": "personal",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "event_id" in data

    def test_store_missing_content_returns_422(self, client):
        resp = client.post(
            "/v1/store",
            json={"user_id": "test-user"},
        )
        assert resp.status_code == 422

    def test_store_missing_user_id_returns_422(self, client):
        resp = client.post(
            "/v1/store",
            json={"content": "hello"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    def test_retrieve_empty(self, client):
        """Retrieve with no stored data returns empty results."""
        resp = client.post(
            "/v1/retrieve",
            json={
                "query": "What is the capital of France?",
                "user_id": "test-user",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_retrieve_after_store(self, client):
        """Store something, then retrieve it."""
        # Store
        client.post(
            "/v1/store",
            json={
                "content": "The Earth orbits the Sun",
                "user_id": "retriever",
            },
        )

        # Retrieve
        resp = client.post(
            "/v1/retrieve",
            json={
                "query": "What does the Earth orbit?",
                "user_id": "retriever",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        # Should find the stored content
        if data["results"]:
            assert any("Earth" in r["content"] for r in data["results"])

    def test_retrieve_missing_query_returns_422(self, client):
        resp = client.post(
            "/v1/retrieve",
            json={"user_id": "test-user"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Organize
# ---------------------------------------------------------------------------


class TestOrganize:
    def test_organize_basic(self, client):
        resp = client.post(
            "/v1/organize",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs_run" in data
        assert "duration_ms" in data
        assert isinstance(data["jobs_run"], list)

    def test_organize_with_specific_jobs(self, client):
        resp = client.post(
            "/v1/organize",
            json={
                "jobs": ["promote"],
                "budget_ms": 1000,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs_run" in data


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


class TestNodes:
    def test_query_nodes_empty(self, client):
        resp = client.get("/v1/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "count" in data
        assert isinstance(data["nodes"], list)

    def test_get_node_not_found(self, client):
        resp = client.get("/v1/nodes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_store_then_get_node(self, client):
        # Store a node
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Test node content",
                "user_id": "node-test-user",
                "node_type": "fact",
            },
        )
        assert store_resp.status_code == 200
        node_id = store_resp.json().get("node_id")

        if node_id:
            # Get the node
            resp = client.get(f"/v1/nodes/{node_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Test node content"
            assert data["node_type"] == "fact"

    def test_query_nodes_with_user_filter(self, client):
        # Store a node
        client.post(
            "/v1/store",
            json={
                "content": "User-specific fact",
                "user_id": "filter-user",
            },
        )

        # Query with user filter
        resp = client.get("/v1/nodes", params={"user_id": "filter-user"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1

    def test_query_nodes_invalid_type_returns_422(self, client):
        resp = client.get("/v1/nodes", params={"type": "nonexistent_type"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


class TestPromote:
    def test_promote_node(self, client):
        # Store a tentative node
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Promotable fact",
                "user_id": "promote-user",
                "node_type": "fact",
            },
        )
        node_id = store_resp.json().get("node_id")

        if node_id:
            # Promote it
            resp = client.put(f"/v1/nodes/{node_id}/promote")
            assert resp.status_code == 200
            data = resp.json()
            assert data["lifecycle_state"] == "stable"

    def test_promote_nonexistent_returns_404(self, client):
        resp = client.put("/v1/nodes/00000000-0000-0000-0000-000000000000/promote")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestArchive:
    def test_archive_node(self, client):
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Archivable fact",
                "user_id": "archive-user",
                "node_type": "fact",
            },
        )
        node_id = store_resp.json().get("node_id")

        if node_id:
            resp = client.put(f"/v1/nodes/{node_id}/archive")
            assert resp.status_code == 200
            data = resp.json()
            assert data["lifecycle_state"] == "archived"


# ---------------------------------------------------------------------------
# Reinforce
# ---------------------------------------------------------------------------


class TestReinforce:
    def test_reinforce_node(self, client):
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Reinforceable fact",
                "user_id": "reinforce-user",
                "node_type": "fact",
            },
        )
        node_id = store_resp.json().get("node_id")

        if node_id:
            # Get initial confidence
            before = client.get(f"/v1/nodes/{node_id}").json()
            initial_confidence = before["confidence"]

            # Reinforce
            resp = client.put(f"/v1/nodes/{node_id}/reinforce")
            assert resp.status_code == 200

    def test_reinforce_nonexistent_returns_404(self, client):
        resp = client.put("/v1/nodes/00000000-0000-0000-0000-000000000000/reinforce")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Neighborhood / Chain
# ---------------------------------------------------------------------------


class TestGraphEndpoints:
    def test_neighborhood_not_found(self, client):
        resp = client.get("/v1/nodes/00000000-0000-0000-0000-000000000000/neighborhood")
        assert resp.status_code == 404

    def test_chain_not_found(self, client):
        resp = client.get("/v1/nodes/00000000-0000-0000-0000-000000000000/chain")
        assert resp.status_code == 404

    def test_neighborhood_of_stored_node(self, client):
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Neighborhood test fact",
                "user_id": "graph-user",
                "node_type": "fact",
            },
        )
        node_id = store_resp.json().get("node_id")

        if node_id:
            resp = client.get(f"/v1/nodes/{node_id}/neighborhood")
            assert resp.status_code == 200
            data = resp.json()
            assert "nodes" in data
            assert "count" in data

    def test_chain_of_stored_node(self, client):
        store_resp = client.post(
            "/v1/store",
            json={
                "content": "Chain test fact",
                "user_id": "graph-user",
                "node_type": "fact",
            },
        )
        node_id = store_resp.json().get("node_id")

        if node_id:
            resp = client.get(f"/v1/nodes/{node_id}/chain")
            assert resp.status_code == 200
            data = resp.json()
            assert "nodes" in data


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_invalid_json_returns_422(self, client):
        resp = client.post(
            "/v1/store",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_nonexistent_endpoint_returns_404(self, client):
        resp = client.get("/v1/nonexistent")
        assert resp.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Authentication (issue #34)
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-secret-key"


@pytest.fixture
def auth_config(tmp_dir):
    """Config with bearer-token auth enabled."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        api=APIConfig(api_key=TEST_API_KEY),
    )


@pytest.fixture
def auth_client(auth_config):
    """TestClient against an app that requires an API key."""
    with TestClient(create_app(auth_config), raise_server_exceptions=True) as c:
        yield c


class TestAuth:
    def test_missing_token_returns_401(self, auth_client):
        resp = auth_client.get("/v1/nodes")
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"

    def test_wrong_token_returns_401(self, auth_client):
        resp = auth_client.get(
            "/v1/nodes",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_valid_token_succeeds(self, auth_client):
        resp = auth_client.get(
            "/v1/nodes",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert resp.status_code == 200

    def test_post_route_requires_token(self, auth_client):
        resp = auth_client.post(
            "/v1/store",
            json={"content": "secret fact", "user_id": "auth-user"},
        )
        assert resp.status_code == 401

    def test_post_route_with_token_succeeds(self, auth_client):
        resp = auth_client.post(
            "/v1/store",
            json={"content": "secret fact", "user_id": "auth-user"},
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert resp.status_code == 200

    def test_health_open_without_token(self, auth_client):
        """Liveness checks must work without credentials."""
        resp = auth_client.get("/v1/health")
        assert resp.status_code == 200

    def test_stats_requires_token(self, auth_client):
        resp = auth_client.get("/v1/stats")
        assert resp.status_code == 401

    def test_no_api_key_configured_allows_access(self, client):
        """Auth is a no-op when no API key is set (default client fixture)."""
        resp = client.get("/v1/nodes")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS (issue #34)
# ---------------------------------------------------------------------------


class TestCORS:
    def test_cors_disabled_by_default(self, client):
        """No Access-Control-Allow-Origin header without pinned origins."""
        resp = client.get(
            "/v1/health",
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers

    def test_cors_pinned_origin_allowed(self, tmp_dir):
        lexical_path = Path(tmp_dir) / "lexical_index"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(lexical_path),
            api=APIConfig(
                cors_origins=["http://localhost:3000"],
                cors_allow_credentials=True,
            ),
        )
        with TestClient(create_app(config)) as c:
            resp = c.get(
                "/v1/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert (
                resp.headers.get("access-control-allow-origin")
                == "http://localhost:3000"
            )
            assert resp.headers.get("access-control-allow-credentials") == "true"

            # Unpinned origins get no CORS headers.
            resp = c.get(
                "/v1/health",
                headers={"Origin": "http://evil.example"},
            )
            assert "access-control-allow-origin" not in resp.headers

    def test_cors_wildcard_never_allows_credentials(self, tmp_dir):
        lexical_path = Path(tmp_dir) / "lexical_index"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(lexical_path),
            api=APIConfig(
                cors_origins=["*"],
                cors_allow_credentials=True,
            ),
        )
        with TestClient(create_app(config)) as c:
            resp = c.get(
                "/v1/health",
                headers={"Origin": "http://anywhere.example"},
            )
            assert "access-control-allow-credentials" not in resp.headers


# ---------------------------------------------------------------------------
# Stats counting (issue #34)
# ---------------------------------------------------------------------------


class TestStatsCounting:
    def test_stats_counts_stored_nodes(self, client):
        client.post(
            "/v1/store",
            json={"content": "Fact one", "user_id": "stats-user-a"},
        )
        client.post(
            "/v1/store",
            json={"content": "Fact two", "user_id": "stats-user-b"},
        )

        resp = client.get("/v1/stats")
        assert resp.status_code == 200
        assert resp.json()["node_count"] >= 2

    def test_stats_scoped_by_user(self, client):
        client.post(
            "/v1/store",
            json={"content": "Scoped fact", "user_id": "stats-scoped-user"},
        )
        client.post(
            "/v1/store",
            json={"content": "Other user's fact", "user_id": "stats-other-user"},
        )

        scoped = client.get(
            "/v1/stats", params={"user_id": "stats-scoped-user"}
        ).json()
        unscoped = client.get("/v1/stats").json()

        assert scoped["node_count"] >= 1
        assert unscoped["node_count"] > scoped["node_count"]

    def test_stats_unknown_user_counts_zero(self, client):
        resp = client.get("/v1/stats", params={"user_id": "nobody-here"})
        assert resp.status_code == 200
        assert resp.json()["node_count"] == 0


# ---------------------------------------------------------------------------
# Server defaults (issue #34)
# ---------------------------------------------------------------------------


class TestServerDefaults:
    def test_run_server_defaults_to_loopback(self):
        import inspect

        from prme.api.server import run_server

        host_default = inspect.signature(run_server).parameters["host"].default
        assert host_default == "127.0.0.1"
