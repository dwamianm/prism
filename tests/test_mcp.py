"""Tests for the PRME MCP server.

Uses the MCP SDK's in-memory transport for end-to-end testing
of tools and resources without starting a subprocess.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mcp.shared.memory import create_connected_server_and_client_session


@pytest.fixture()
def tmp_memory_dir(tmp_path):
    """Create a temporary memory directory with required subdirs."""
    lexical = tmp_path / "lexical_index"
    lexical.mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _set_prme_env(tmp_memory_dir, monkeypatch):
    """Set PRME env vars so the MCP server uses a temp directory."""
    monkeypatch.setenv("PRME_DB_PATH", str(tmp_memory_dir / "memory.duckdb"))
    monkeypatch.setenv("PRME_VECTOR_PATH", str(tmp_memory_dir / "vectors.usearch"))
    monkeypatch.setenv("PRME_LEXICAL_PATH", str(tmp_memory_dir / "lexical_index"))


@pytest.fixture()
async def session():
    """Create an MCP client session connected to the PRME server."""
    # Import after env vars are set so PRMEConfig picks them up
    from prme.mcp.server import mcp as mcp_server

    try:
        async with create_connected_server_and_client_session(
            mcp_server._mcp_server,
            raise_exceptions=True,
        ) as client_session:
            await client_session.initialize()
            yield client_session
    except RuntimeError as e:
        # anyio cancel scope teardown race with pytest-asyncio
        if "cancel scope" in str(e):
            pass
        else:
            raise


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    async def test_lists_all_tools(self, session):
        result = await session.list_tools()
        names = {t.name for t in result.tools}
        expected = {
            "memory_store",
            "memory_retrieve",
            "memory_ingest",
            "memory_organize",
            "memory_get_node",
            "memory_promote_node",
            "memory_archive_node",
        }
        assert expected.issubset(names), f"Missing tools: {expected - names}"

    async def test_tools_have_descriptions(self, session):
        result = await session.list_tools()
        for tool in result.tools:
            assert tool.description, f"Tool {tool.name} has no description"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestStore:
    async def test_store_basic(self, session):
        result = await session.call_tool("memory_store", {
            "content": "Paris is the capital of France",
            "user_id": "test-user",
        })
        data = json.loads(result.content[0].text)
        assert "event_id" in data
        assert data["event_id"]
        assert "error" not in data

    async def test_store_with_type_and_scope(self, session):
        result = await session.call_tool("memory_store", {
            "content": "Use dark mode everywhere",
            "user_id": "test-user",
            "node_type": "preference",
            "scope": "personal",
        })
        data = json.loads(result.content[0].text)
        assert "event_id" in data
        assert "error" not in data

    async def test_store_invalid_node_type(self, session):
        result = await session.call_tool("memory_store", {
            "content": "test",
            "user_id": "test-user",
            "node_type": "invalid_type",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data

    async def test_store_invalid_scope(self, session):
        result = await session.call_tool("memory_store", {
            "content": "test",
            "user_id": "test-user",
            "scope": "invalid_scope",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    async def test_retrieve_empty(self, session):
        result = await session.call_tool("memory_retrieve", {
            "query": "What is the capital of France?",
            "user_id": "test-user",
        })
        data = json.loads(result.content[0].text)
        assert "results" in data
        assert "count" in data
        assert data["count"] == 0

    async def test_retrieve_after_store(self, session):
        # Store first
        await session.call_tool("memory_store", {
            "content": "The Earth orbits the Sun",
            "user_id": "retriever",
        })
        # Retrieve
        result = await session.call_tool("memory_retrieve", {
            "query": "What does the Earth orbit?",
            "user_id": "retriever",
        })
        data = json.loads(result.content[0].text)
        assert "results" in data
        assert data["count"] > 0
        assert data["results"][0]["content"] == "The Earth orbits the Sun"

    async def test_retrieve_invalid_scope(self, session):
        result = await session.call_tool("memory_retrieve", {
            "query": "test",
            "user_id": "test-user",
            "scope": "invalid_scope",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data


# ---------------------------------------------------------------------------
# Get Node
# ---------------------------------------------------------------------------


class TestGetNode:
    async def test_get_nonexistent_node(self, session):
        result = await session.call_tool("memory_get_node", {
            "node_id": "00000000-0000-0000-0000-000000000000",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data

    async def test_get_stored_node(self, session):
        # Store and get the node_id
        store_result = await session.call_tool("memory_store", {
            "content": "A retrievable fact",
            "user_id": "node-tester",
        })
        store_data = json.loads(store_result.content[0].text)
        node_id = store_data.get("node_id")
        if node_id:
            result = await session.call_tool("memory_get_node", {
                "node_id": node_id,
            })
            data = json.loads(result.content[0].text)
            assert data["content"] == "A retrievable fact"
            assert data["id"] == node_id


# ---------------------------------------------------------------------------
# Organize
# ---------------------------------------------------------------------------


class TestOrganize:
    async def test_organize_all(self, session):
        result = await session.call_tool("memory_organize", {})
        data = json.loads(result.content[0].text)
        assert "jobs_run" in data
        assert "duration_ms" in data

    async def test_organize_specific_jobs(self, session):
        result = await session.call_tool("memory_organize", {
            "jobs": "promote,decay_sweep",
        })
        data = json.loads(result.content[0].text)
        assert "jobs_run" in data


# ---------------------------------------------------------------------------
# Lifecycle: Promote / Archive
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_promote_node(self, session):
        # Store a node
        store_result = await session.call_tool("memory_store", {
            "content": "A promotable fact",
            "user_id": "lifecycle-user",
            "node_type": "fact",
        })
        store_data = json.loads(store_result.content[0].text)
        node_id = store_data.get("node_id")
        if node_id:
            result = await session.call_tool("memory_promote_node", {
                "node_id": node_id,
            })
            data = json.loads(result.content[0].text)
            assert data.get("lifecycle_state") == "stable"

    async def test_archive_node(self, session):
        store_result = await session.call_tool("memory_store", {
            "content": "An archivable fact",
            "user_id": "lifecycle-user",
        })
        store_data = json.loads(store_result.content[0].text)
        node_id = store_data.get("node_id")
        if node_id:
            result = await session.call_tool("memory_archive_node", {
                "node_id": node_id,
            })
            data = json.loads(result.content[0].text)
            assert data.get("lifecycle_state") == "archived"

    async def test_promote_nonexistent(self, session):
        result = await session.call_tool("memory_promote_node", {
            "node_id": "00000000-0000-0000-0000-000000000000",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data

    async def test_archive_nonexistent(self, session):
        result = await session.call_tool("memory_archive_node", {
            "node_id": "00000000-0000-0000-0000-000000000000",
        })
        data = json.loads(result.content[0].text)
        assert "error" in data


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    async def test_list_resources(self, session):
        result = await session.list_resources()
        uris = {str(r.uri) for r in result.resources}
        assert "memory://health" in uris

    async def test_health_resource(self, session):
        from pydantic import AnyUrl

        result = await session.read_resource(AnyUrl("memory://health"))
        data = json.loads(result.contents[0].text)
        assert data["status"] == "ok"
        assert data["version"] == "0.4.0"

    async def test_stats_resource(self, session):
        from pydantic import AnyUrl

        result = await session.read_resource(AnyUrl("memory://stats"))
        data = json.loads(result.contents[0].text)
        assert "node_count" in data
        assert "backend" in data

    async def test_resource_templates(self, session):
        result = await session.list_resource_templates()
        uris = {t.uriTemplate for t in result.resourceTemplates}
        assert "memory://nodes/{node_id}" in uris
