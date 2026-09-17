"""Tests for the PRME MCP server.

Uses the MCP SDK's in-memory transport for end-to-end testing
of tools and resources without starting a subprocess.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import __version__

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
            "memory_ingest_fast_many",
            "memory_process_materializations",
            "memory_retrieve",
            "memory_ingest",
            "memory_organize",
            "memory_get_node",
            "memory_scan_nodes",
            "memory_aggregate_assertions",
            "memory_aggregate_quantities",
            "memory_get_extraction",
            "memory_promote_node",
            "memory_archive_node",
            "memory_evaluate_condition",
            "memory_get_provenance",
            "memory_evaluate_learning",
            "memory_supersede",
            "memory_mark_contradiction",
            "memory_resolve_contradiction",
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
    async def test_store_accepts_typed_value_bindings(self, session):
        source = '{"plan":"Current City: Salt Lake City(Utah)"}'
        result = await session.call_tool("memory_store", {
            "content": source,
            "retrieval_content": "Current City: Salt Lake City(Utah)",
            "value_bindings": [{
                "reference": "current-city-1",
                "kind": "city",
                "presentation": "Salt Lake City(Utah)",
                "lookup": "Salt Lake City",
            }],
            "user_id": "binding-user",
        })
        assert not result.isError
        stored = json.loads(result.content[0].text)
        fetched = await session.call_tool(
            "memory_get_node", {"node_id": stored["node_id"]}
        )
        node = json.loads(fetched.content[0].text)
        assert node["metadata"]["prme_value_bindings_v1"][0]["lookup"] == (
            "Salt Lake City"
        )
        retrieved = await session.call_tool("memory_retrieve", {
            "query": "Salt Lake City",
            "user_id": "binding-user",
            "include_context": True,
        })
        assert not retrieved.isError
        payload = json.loads(retrieved.content[0].text)
        assert payload["value_bindings"][0]["presentation"] == (
            "Salt Lake City(Utah)"
        )

    async def test_store_source_clock_is_returned_separately_from_validity(self, session):
        clock = "2025-04-03T09:15:00+05:30"
        valid_from = "2025-04-04T00:00:00+05:30"
        valid_to = "2025-05-04T00:00:00+05:30"
        result = await session.call_tool("memory_store", {
            "content": "Imported telescope observation",
            "user_id": "source-clock-user",
            "event_time": clock,
            "valid_from": valid_from,
            "valid_to": valid_to,
        })
        assert not result.isError
        stored = json.loads(result.content[0].text)
        assert stored["node_id"]
        result = await session.call_tool("memory_get_node", {
            "node_id": stored["node_id"],
        })
        node = json.loads(result.content[0].text)
        assert datetime.fromisoformat(node["event_time"]) == datetime.fromisoformat(clock)
        assert datetime.fromisoformat(node["valid_from"]) == datetime.fromisoformat(valid_from)
        assert datetime.fromisoformat(node["valid_to"]) == datetime.fromisoformat(valid_to)

    async def test_store_rejects_invalid_validity_window_before_admission(self, session):
        result = await session.call_tool("memory_store", {
            "content": "Incomplete validity interval",
            "user_id": "validity-user",
            "valid_to": "2025-05-04T00:00:00Z",
        })
        assert not result.isError
        assert json.loads(result.content[0].text) == {
            "error": "valid_to requires an explicit valid_from"
        }

    async def test_store_rejects_timezone_free_clock_before_engine_write(self, session, monkeypatch):
        write = AsyncMock(side_effect=AssertionError("invalid clock reached storage"))
        monkeypatch.setattr("prme.storage.engine.MemoryEngine.store", write)
        result = await session.call_tool("memory_store", {
            "content": "Ambiguous imported observation",
            "user_id": "source-clock-user",
            "event_time": "2025-04-03T09:15:00",
        })
        assert result.isError
        assert "timezone" in result.content[0].text.lower()
        write.assert_not_awaited()

    async def test_store_basic(self, session):
        result = await session.call_tool("memory_store", {
            "content": "Paris is the capital of France",
            "user_id": "test-user",
        })
        data = json.loads(result.content[0].text)
        assert "event_id" in data
        assert data["event_id"]
        assert data["node_id"]
        assert data["processing_status"]["status"] == "complete"
        assert data["processing_status"]["event_id"] == data["event_id"]
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

    async def test_fast_batch_admission_and_processing(self, session):
        request_id = str(uuid4())
        arguments = {
            "user_id": "batch-user",
            "request_id": request_id,
            "items": [
                {"content": "First MCP batch source", "scope": "project"},
                {"content": "Second MCP batch source", "role": "tool"},
            ],
        }
        admitted = await session.call_tool(
            "memory_ingest_fast_many",
            arguments,
        )
        payload = json.loads(admitted.content[0].text)
        assert payload["accepted"] == 2
        assert len(payload["event_ids"]) == 2
        replay = await session.call_tool("memory_ingest_fast_many", arguments)
        assert json.loads(replay.content[0].text) == payload
        conflict = await session.call_tool(
            "memory_ingest_fast_many",
            {
                "user_id": "batch-user",
                "request_id": request_id,
                "items": [{"content": "changed"}],
            },
        )
        assert json.loads(conflict.content[0].text)["conflict"] is True

        processed = await session.call_tool(
            "memory_process_materializations",
            {"user_id": "batch-user", "budget_ms": 5000},
        )
        assert json.loads(processed.content[0].text) == {
            "processed": 2,
            "pending": 0,
            "failed": 0,
        }

    async def test_fast_batch_rejects_empty_input(self, session):
        result = await session.call_tool(
            "memory_ingest_fast_many",
            {"user_id": "batch-user", "items": []},
        )
        assert "error" in json.loads(result.content[0].text)


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

    async def test_retrieve_exposes_aggregation_coverage(self, session):
        result = await session.call_tool("memory_retrieve", {
            "query": "How many museums did I visit?",
            "user_id": "counter",
            "include_context": True,
        })
        data = json.loads(result.content[0].text)
        coverage = data["metrics"]["aggregation_coverage"]
        assert coverage["exhaustive"] is False
        assert coverage["status"] == "semantic_candidates"
        assert coverage["candidate_count"] == 0
        assert data["context"].startswith("Aggregation coverage:")

    async def test_retrieve_exposes_knowledge_at_boundary(self, session):
        result = await session.call_tool("memory_retrieve", {
            "query": "What was known?",
            "user_id": "historian",
            "knowledge_at": "2026-09-13T00:00:00+00:00",
            "include_context": True,
        })
        data = json.loads(result.content[0].text)
        coverage = data["metrics"]["historical_coverage"]
        assert coverage["semantics"] == "ingestion_cutoff"
        assert coverage["exact_snapshot"] is False
        assert "current_derived_indexes" in coverage["limitations"]
        assert data["context"].startswith("Historical coverage:")

        invalid = await session.call_tool("memory_retrieve", {
            "query": "What was known?",
            "user_id": "historian",
            "knowledge_at": "2026-09-13T00:00:00",
        })
        assert "error" in json.loads(invalid.content[0].text)


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
    async def test_supersedence_roundtrip_and_retry(self, session):
        stored = []
        for content in ("Atlas uses east.", "Atlas uses west."):
            result = await session.call_tool("memory_store", {
                "content": content, "user_id": "correction-user", "node_type": "fact",
            })
            stored.append(json.loads(result.content[0].text))
        arguments = {
            "old_node_id": stored[0]["node_id"],
            "new_node_id": stored[1]["node_id"],
            "evidence_id": stored[1]["event_id"],
        }
        result = await session.call_tool("memory_supersede", arguments)
        assert [node["lifecycle_state"] for node in json.loads(
            result.content[0].text
        )["nodes"]] == ["superseded", "tentative"]
        replay = await session.call_tool("memory_supersede", arguments)
        assert "error" not in json.loads(replay.content[0].text)

    async def test_contradiction_roundtrip(self, session):
        node_ids = []
        for content in ("Atlas uses east.", "Atlas uses west."):
            stored = await session.call_tool("memory_store", {
                "content": content, "user_id": "conflict-user", "node_type": "fact",
            })
            node_ids.append(json.loads(stored.content[0].text)["node_id"])
        marked = await session.call_tool("memory_mark_contradiction", {
            "node_a_id": node_ids[0], "node_b_id": node_ids[1],
        })
        assert {node["lifecycle_state"] for node in json.loads(marked.content[0].text)["nodes"]} == {"contested"}
        resolved = await session.call_tool("memory_resolve_contradiction", {
            "winner_id": node_ids[1], "loser_id": node_ids[0],
        })
        assert [node["lifecycle_state"] for node in json.loads(resolved.content[0].text)["nodes"]] == ["stable", "deprecated"]

    async def test_new_condition_must_start_unresolved(self, session):
        result = await session.call_tool("memory_store", {
            "content": "If approved, deploy Atlas.",
            "user_id": "condition-user",
            "epistemic_type": "conditional",
        })
        assert "metadata.condition" in json.loads(result.content[0].text)["error"]

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
                "request_id": "1bd30f33-dae6-4378-809d-8739e4a0f194",
            })
            data = json.loads(result.content[0].text)
            assert data.get("lifecycle_state") == "stable"
            replay = await session.call_tool("memory_promote_node", {
                "node_id": node_id,
                "request_id": "1bd30f33-dae6-4378-809d-8739e4a0f194",
            })
            assert json.loads(replay.content[0].text)["lifecycle_state"] == "stable"

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
                "request_id": "fca23648-e2e0-4501-bcf8-ec5b2daf5aaa",
            })
            data = json.loads(result.content[0].text)
            assert data.get("lifecycle_state") == "archived"
            replay = await session.call_tool("memory_archive_node", {
                "node_id": node_id,
                "request_id": "fca23648-e2e0-4501-bcf8-ec5b2daf5aaa",
            })
            assert json.loads(replay.content[0].text)["lifecycle_state"] == "archived"

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

    async def test_evaluate_condition_and_retry(self, session):
        stored = await session.call_tool("memory_store", {
            "content": "If approved, deploy Atlas.",
            "user_id": "condition-user",
            "epistemic_type": "conditional",
            "metadata": {"condition": "approved", "condition_state": "unknown"},
        })
        node_id = json.loads(stored.content[0].text)["node_id"]
        request_id = "b58d4597-4d95-4597-af61-3fe8d0f1d989"
        arguments = {
            "node_id": node_id,
            "state": "true",
            "request_id": request_id,
            "evaluation_method": "tool",
            "reason": "Approval service confirmed",
            "evaluated_at": "2026-09-13T12:30:00Z",
        }
        result = await session.call_tool("memory_evaluate_condition", arguments)
        data = json.loads(result.content[0].text)
        assert "error" not in data, data
        assert data["metadata"]["condition_state"] == "true"
        replay = await session.call_tool("memory_evaluate_condition", arguments)
        assert json.loads(replay.content[0].text)["metadata"]["condition_state"] == "true"
        conflict = await session.call_tool(
            "memory_evaluate_condition", {**arguments, "state": "false"}
        )
        assert "request_id" in json.loads(conflict.content[0].text)["error"]
        provenance = await session.call_tool("memory_get_provenance", {
            "node_id": node_id,
        })
        history = json.loads(provenance.content[0].text)
        assert history["node"]["id"] == node_id
        assert history["operations"][0]["op_type"] == "EPISTEMIC_TRANSITION"


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
        assert data["version"] == __version__

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
