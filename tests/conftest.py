"""Shared pytest fixtures and configuration for PRME test suite.

Ensures DuckDB connection isolation across all test modules to prevent
segfaults from concurrent connection sharing (issue #19). Each test
that needs a DuckDB connection gets a fresh temporary database via
isolated fixtures.

Key design decisions:
- pytest-asyncio auto mode configured in pyproject.toml
- Each DuckDB connection is created per-test, not per-module
- WriteQueue instances are started/stopped per-test
- Temporary directories are cleaned up after each test
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import duckdb
import pytest

from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database


@pytest.fixture(autouse=True)
def _one_extraction_call_per_turn(monkeypatch):
    """Extract one turn per model call unless a test sets a batch target.

    Batched extraction is the default (``extraction_batch_target_tokens``,
    #254), but most tests fake the per-turn ``extract()`` call and count its
    calls, and a batched request goes to ``extract_batch()`` instead, which
    their fakes leave pointing at the real provider. One call per turn is
    still supported, so those tests keep testing it. The batched path, the
    default included, is covered in ``test_batched_extraction.py``, whose
    configurations set the target themselves, and a test that checks the
    defaults removes this variable first. Configurations isolated from the
    environment, such as the evidence gate's, keep the default.
    """
    monkeypatch.setenv("PRME_EXTRACTION_BATCH_TARGET_TOKENS", "0")


@pytest.fixture
def isolated_tmp_dir():
    """Create an isolated temporary directory for each test.

    Ensures no two tests share filesystem state (DuckDB files,
    vector indexes, lexical indexes).
    """
    with tempfile.TemporaryDirectory(prefix="prme_test_") as d:
        yield Path(d)


@pytest.fixture
def isolated_duckdb(isolated_tmp_dir):
    """Create an isolated DuckDB connection with initialized schema.

    Uses a file-backed database in a unique temp directory to avoid
    any possibility of connection sharing between tests. Closes the
    connection on teardown to release DuckDB file locks.
    """
    db_path = str(isolated_tmp_dir / "test.duckdb")
    conn = duckdb.connect(db_path)
    initialize_database(conn)
    yield conn
    conn.close()


@pytest.fixture
def isolated_graph_store(isolated_duckdb):
    """Create an isolated DuckPGQGraphStore backed by a fresh DuckDB.

    The asyncio.Lock ensures thread-safety for this specific connection.
    """
    conn_lock = asyncio.Lock()
    return DuckPGQGraphStore(isolated_duckdb, conn_lock)
