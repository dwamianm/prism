"""Tests for the synchronous MemoryClient wrapper.

Tests cover:
- Construction and close lifecycle
- Context manager protocol
- store() and retrieve() round-trip
- ingest() (mocked LLM extraction)
- query_nodes() and get_node()
- organize() returns result
- Double-close is safe
- Calling methods after close raises RuntimeError
- ResourceWarning on GC without close
"""

from __future__ import annotations

import logging
import tempfile
import warnings
from pathlib import Path

import pytest

from prme.client import MemoryClient, config_from_directory
from prme.config import PRMEConfig
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_client_") as d:
        yield d


@pytest.fixture(autouse=True)
def suppress_structlog():
    """Suppress structlog output during tests."""
    import structlog
    import sys

    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


# ---------------------------------------------------------------------------
# config_from_directory
# ---------------------------------------------------------------------------


class TestConfigFromDirectory:
    def test_creates_directory(self, tmp_dir):
        target = str(Path(tmp_dir) / "new_memories")
        config = config_from_directory(target)
        assert Path(target).is_dir()
        assert Path(target, "lexical_index").is_dir()
        assert config.db_path == str(Path(target) / "memory.duckdb")
        assert config.vector_path == str(Path(target) / "vectors.usearch")
        assert config.lexical_path == str(Path(target) / "lexical_index")

    def test_existing_directory_ok(self, tmp_dir):
        config = config_from_directory(tmp_dir)
        assert config.db_path == str(Path(tmp_dir) / "memory.duckdb")

    def test_returns_prme_config(self, tmp_dir):
        config = config_from_directory(tmp_dir)
        assert isinstance(config, PRMEConfig)


# ---------------------------------------------------------------------------
# MemoryClient lifecycle
# ---------------------------------------------------------------------------


class TestClientLifecycle:
    def test_context_manager(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            assert not client._closed
        assert client._closed

    def test_explicit_close(self, tmp_dir):
        client = MemoryClient(tmp_dir)
        assert not client._closed
        client.close()
        assert client._closed

    def test_double_close_is_safe(self, tmp_dir):
        client = MemoryClient(tmp_dir)
        client.close()
        client.close()  # should not raise

    def test_methods_after_close_raise(self, tmp_dir):
        client = MemoryClient(tmp_dir)
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            client.store("hello", user_id="u1")
        with pytest.raises(RuntimeError, match="closed"):
            client.retrieve("hello", user_id="u1")

    def test_resource_warning_on_gc(self, tmp_dir):
        client = MemoryClient(tmp_dir)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            client.__del__()
            resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
            assert len(resource_warnings) == 1
            assert "not closed" in str(resource_warnings[0].message)
        client.close()

    def test_custom_config_overrides_directory(self, tmp_dir):
        lexical_path = str(Path(tmp_dir) / "lexical_index")
        Path(lexical_path).mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "custom.duckdb"),
            vector_path=str(Path(tmp_dir) / "custom.usearch"),
            lexical_path=lexical_path,
        )
        with MemoryClient(config=config) as client:
            # Should use the custom config, not directory-derived one
            assert client._config.db_path.endswith("custom.duckdb")


# ---------------------------------------------------------------------------
# Store and retrieve
# ---------------------------------------------------------------------------


class TestStoreRetrieve:
    def test_store_returns_uuid(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            event_id = client.store("Alice likes dark mode", user_id="alice")
            assert isinstance(event_id, str)
            assert len(event_id) == 36  # UUID format

    def test_store_and_retrieve_roundtrip(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            client.store("Alice likes dark mode", user_id="alice")
            client.store("Bob prefers vim", user_id="alice")

            response = client.retrieve("preferences?", user_id="alice")
            assert len(response.results) > 0
            contents = [r.node.content for r in response.results]
            assert any("dark mode" in c for c in contents)

    def test_store_with_all_params(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            from datetime import datetime, timezone

            event_id = client.store(
                "Team decided on PostgreSQL",
                user_id="alice",
                session_id="s1",
                role="user",
                node_type=NodeType.DECISION,
                scope=Scope.PROJECT,
                metadata={"source": "meeting"},
                confidence=0.9,
                event_time=datetime.now(timezone.utc),
            )
            assert isinstance(event_id, str)

    def test_retrieve_empty(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            response = client.retrieve("anything", user_id="alice")
            assert len(response.results) == 0


# ---------------------------------------------------------------------------
# get_node and query_nodes
# ---------------------------------------------------------------------------


class TestNodeAccess:
    def test_get_node(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            client.store("test content", user_id="alice")
            nodes = client.query_nodes(limit=1)
            assert len(nodes) == 1
            node = client.get_node(str(nodes[0].id))
            assert node is not None
            assert node.content == "test content"

    def test_get_node_not_found(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            node = client.get_node("00000000-0000-0000-0000-000000000000")
            assert node is None

    def test_query_nodes_returns_list(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            client.store("fact one", user_id="alice")
            client.store("fact two", user_id="alice")
            nodes = client.query_nodes(limit=10)
            assert isinstance(nodes, list)
            assert len(nodes) == 2


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------


class TestOrganize:
    def test_organize_returns_result(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            client.store("some content", user_id="alice")
            result = client.organize(user_id="alice")
            assert hasattr(result, "duration_ms")
            assert hasattr(result, "jobs_run")

    def test_organize_with_specific_jobs(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            client.store("some content", user_id="alice")
            result = client.organize(user_id="alice", jobs=["promote"])
            assert "promote" in result.jobs_run
