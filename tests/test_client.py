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
from prme.types import ConditionState, EpistemicType, LifecycleState, NodeType, Scope


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
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="closed"):
                client.store("hello", user_id="u1")
            with pytest.raises(RuntimeError, match="closed"):
                client.retrieve("hello", user_id="u1")
        assert not [w for w in observed if issubclass(w.category, RuntimeWarning)]

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
    def test_lifecycle_retries_with_request_ids(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            event_id = client.store(
                "Lifecycle claim", user_id="alice", node_type=NodeType.FACT,
            )
            node = client.get_event_nodes(event_id, user_id="alice")[0]
            promote_id = "8a41eede-58ad-4f09-9983-76cb75f57c16"
            client.promote(str(node.id), user_id="alice", request_id=promote_id)
            client.promote(str(node.id), user_id="alice", request_id=promote_id)
            archive_id = "ba064fb7-a2d7-4832-802b-b810b1d9e098"
            client.archive(str(node.id), user_id="alice", request_id=archive_id)
            client.archive(str(node.id), user_id="alice", request_id=archive_id)
            saved = client.get_node(
                str(node.id), user_id="alice", include_superseded=True,
            )
            assert saved.lifecycle_state == LifecycleState.ARCHIVED

    def test_contradiction_roundtrip_is_retry_safe(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            first_event = client.store(
                "Atlas uses east.", user_id="alice", node_type=NodeType.FACT,
            )
            second_event = client.store(
                "Atlas uses west.", user_id="alice", node_type=NodeType.FACT,
            )
            first = client.get_event_nodes(first_event, user_id="alice")[0]
            second = client.get_event_nodes(second_event, user_id="alice")[0]

            contested = client.contradict(
                str(first.id), str(second.id), user_id="alice", actor_id="reviewer",
            )
            assert [node.lifecycle_state for node in contested] == [
                LifecycleState.CONTESTED,
                LifecycleState.CONTESTED,
            ]
            assert client.contradict(
                str(first.id), str(second.id), user_id="alice", actor_id="reviewer",
            ) == contested

            winner, loser = client.resolve_contradiction(
                str(second.id), str(first.id), user_id="alice",
                resolver_actor_id="reviewer",
            )
            assert winner.lifecycle_state == LifecycleState.STABLE
            assert loser.lifecycle_state == LifecycleState.DEPRECATED
            assert client.resolve_contradiction(
                str(second.id), str(first.id), user_id="alice",
                resolver_actor_id="reviewer",
            ) == (winner, loser)

    def test_condition_evaluation_roundtrip(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            event_id = client.store(
                "If approved, deploy Atlas.", user_id="alice",
                epistemic_type=EpistemicType.CONDITIONAL,
                metadata={"condition": "approved"},
            )
            node = client.get_event_nodes(event_id, user_id="alice")[0]
            updated = client.evaluate_condition(
                str(node.id), ConditionState.TRUE, user_id="alice",
                request_id="4fae6bcc-3904-4ad3-859e-ea14c3ec31c5",
            )
            assert updated.metadata["condition_state"] == "true"
            assert any(
                result.node.id == node.id
                for result in client.retrieve("deploy Atlas", user_id="alice").results
            )
            provenance = client.get_provenance(str(node.id), user_id="alice")
            assert provenance.node.id == node.id
            assert provenance.operations[0].op_type == "EPISTEMIC_TRANSITION"

    def test_deferred_ingestion_has_public_processing_status(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            event_id = client.ingest_fast("Alice uses a telescope", user_id="alice")
            assert client.processing_status(event_id, user_id="alice").status == "pending"
            result = client.process_pending(user_id="alice")
            assert result.pending == 0 and result.processed == 1
            assert client.processing_status(event_id, user_id="alice").status == "complete"
            assert client.retrieve("telescope", user_id="alice").results

    def test_store_returns_uuid(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            event_id = client.store("Alice likes dark mode", user_id="alice")
            assert isinstance(event_id, str)
            assert len(event_id) == 36  # UUID format

    def test_store_and_retrieve_roundtrip(self, tmp_dir):
        from datetime import datetime, timezone

        with MemoryClient(tmp_dir) as client:
            client.store("Alice likes dark mode", user_id="alice")
            client.store("Bob prefers vim", user_id="alice")

            clock = datetime.now(timezone.utc)
            response = client.retrieve("preferences?", user_id="alice", reference_time=clock)
            assert response.metadata.reference_time == clock
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


class TestFailedConstruction:
    def test_invalid_directory_preserves_original_error(self):
        partial = MemoryClient.__new__(MemoryClient)
        with pytest.raises(TypeError):
            partial.__init__(object())
        assert partial._closed
        partial.__del__()  # Must not emit an unraisable AttributeError.

    def test_engine_creation_failure_stops_thread_and_loop(self, tmp_dir, monkeypatch):
        from unittest.mock import AsyncMock
        from prme import MemoryEngine
        monkeypatch.setattr(MemoryEngine, "create", AsyncMock(side_effect=RuntimeError("startup failed")))
        partial = MemoryClient.__new__(MemoryClient)
        with pytest.raises(RuntimeError, match="startup failed"):
            partial.__init__(tmp_dir)
        assert partial._closed and not partial._thread.is_alive()
        assert partial._loop.is_closed()
        partial.close()

    def test_normal_close_releases_event_loop(self, tmp_dir):
        with MemoryClient(tmp_dir) as client:
            assert not client._loop.is_closed()
        assert client._loop.is_closed() and not client._thread.is_alive()
