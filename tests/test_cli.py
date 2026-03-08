"""Tests for the PRME CLI tooling (issue #15).

Tests cover:
- info command output (table + JSON)
- nodes command with type/state filters
- node single node detail
- chain supersedence chain display
- search query
- organize command
- stats command
- export command (JSON output)
- error handling for missing files and invalid IDs
- JSON output format for all commands
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from prme import MemoryEngine, PRMEConfig
from prme.cli import (
    _format_table,
    _node_to_dict,
    _truncate,
    build_parser,
    cmd_chain,
    cmd_edges,
    cmd_export,
    cmd_info,
    cmd_node,
    cmd_nodes,
    cmd_organize,
    cmd_search,
    cmd_stats,
)
from prme.types import LifecycleState, NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_cli_") as d:
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


@pytest_asyncio.fixture
async def engine(config):
    eng = await MemoryEngine.create(config)
    yield eng
    await eng.close()


@pytest_asyncio.fixture
async def populated_engine(engine):
    """An engine with a few stored nodes for testing."""
    await engine.store(
        "Python is a programming language",
        user_id="test-user",
        node_type=NodeType.FACT,
    )
    await engine.store(
        "The sky is blue",
        user_id="test-user",
        node_type=NodeType.FACT,
    )
    await engine.store(
        "Buy groceries",
        user_id="test-user",
        node_type=NodeType.TASK,
    )
    return engine


@pytest.fixture(autouse=True)
def suppress_structlog(monkeypatch):
    """Redirect structlog output to stderr so capsys.readouterr().out is clean."""
    import structlog
    import sys

    # Configure structlog to write to stderr instead of stdout
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _make_args(**kwargs):
    """Create a simple namespace with given attributes."""
    import argparse
    return argparse.Namespace(**kwargs)


def _get_node_ids(engine):
    """Get node IDs from the database as strings."""
    rows = engine._conn.execute("SELECT id FROM nodes").fetchall()
    return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        assert _truncate("hello", 5) == "hello"


class TestFormatTable:
    def test_empty_rows(self):
        result = _format_table(["A", "B"], [])
        assert result == "(no results)"

    def test_basic_table(self):
        result = _format_table(["NAME", "AGE"], [["Alice", "30"], ["Bob", "25"]])
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header, separator, 2 rows
        assert "NAME" in lines[0]
        assert "Alice" in lines[2]

    def test_column_widths(self):
        result = _format_table(["X"], [["longertext"]])
        lines = result.strip().split("\n")
        # separator should be as wide as the widest value
        assert len(lines[1].strip()) >= len("longertext")


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_info_command(self):
        parser = build_parser()
        args = parser.parse_args(["info", "/tmp/test.duckdb"])
        assert args.command == "info"
        assert args.db_path == "/tmp/test.duckdb"

    def test_nodes_with_filters(self):
        parser = build_parser()
        args = parser.parse_args([
            "nodes", "/tmp/test.duckdb",
            "--type", "fact",
            "--state", "stable",
            "--limit", "50",
            "--format", "json",
        ])
        assert args.command == "nodes"
        assert args.type == "fact"
        assert args.state == "stable"
        assert args.limit == 50
        assert args.format == "json"

    def test_edges_with_filters(self):
        parser = build_parser()
        args = parser.parse_args([
            "edges", "/tmp/test.duckdb",
            "--type", "supersedes",
            "--source", "abc",
        ])
        assert args.command == "edges"
        assert args.type == "supersedes"
        assert args.source == "abc"

    def test_organize_with_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "organize", "/tmp/test.duckdb",
            "--jobs", "promote,archive",
            "--budget-ms", "3000",
        ])
        assert args.command == "organize"
        assert args.jobs == "promote,archive"
        assert args.budget_ms == 3000

    def test_no_command_exits(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ---------------------------------------------------------------------------
# Command integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_info_table(populated_engine, config, capsys):
    """info command prints summary in table format."""
    args = _make_args(db_path=config.db_path, format="table")
    await cmd_info(args)

    captured = capsys.readouterr().out
    assert "Memory Pack:" in captured
    assert "Nodes:" in captured
    assert "Edges:" in captured
    assert "Events:" in captured


@pytest.mark.asyncio
async def test_cmd_info_json(populated_engine, config, capsys):
    """info command prints valid JSON."""
    args = _make_args(db_path=config.db_path, format="json")
    await cmd_info(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "node_count" in data
    assert "edge_count" in data
    assert "event_count" in data
    assert data["node_count"] == 3
    assert data["event_count"] == 3


@pytest.mark.asyncio
async def test_cmd_nodes_all(populated_engine, config, capsys):
    """nodes command lists all nodes."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type=None, state=None, limit=20,
    )
    await cmd_nodes(args)

    captured = capsys.readouterr().out
    assert "3 node(s) returned" in captured


@pytest.mark.asyncio
async def test_cmd_nodes_filter_type(populated_engine, config, capsys):
    """nodes command filters by type."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type="fact", state=None, limit=20,
    )
    await cmd_nodes(args)

    captured = capsys.readouterr().out
    assert "2 node(s) returned" in captured


@pytest.mark.asyncio
async def test_cmd_nodes_filter_state(populated_engine, config, capsys):
    """nodes command filters by lifecycle state."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type=None, state="tentative", limit=20,
    )
    await cmd_nodes(args)

    captured = capsys.readouterr().out
    assert "3 node(s) returned" in captured


@pytest.mark.asyncio
async def test_cmd_nodes_json(populated_engine, config, capsys):
    """nodes command outputs valid JSON."""
    args = _make_args(
        db_path=config.db_path, format="json",
        type=None, state=None, limit=20,
    )
    await cmd_nodes(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert isinstance(data, list)
    assert len(data) == 3
    assert all("node_type" in n for n in data)


@pytest.mark.asyncio
async def test_cmd_node_detail(populated_engine, config, capsys):
    """node command shows single node detail."""
    node_ids = _get_node_ids(populated_engine)
    node_id = node_ids[0]

    args = _make_args(db_path=config.db_path, format="table", node_id=node_id)
    await cmd_node(args)

    captured = capsys.readouterr().out
    assert "Node:" in captured
    assert "Type:" in captured
    assert "State:" in captured
    assert "Content:" in captured


@pytest.mark.asyncio
async def test_cmd_node_json(populated_engine, config, capsys):
    """node command outputs valid JSON."""
    node_ids = _get_node_ids(populated_engine)
    node_id = node_ids[0]

    args = _make_args(db_path=config.db_path, format="json", node_id=node_id)
    await cmd_node(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "id" in data
    assert "content" in data
    assert "node_type" in data


@pytest.mark.asyncio
async def test_cmd_node_not_found(populated_engine, config):
    """node command exits with error for invalid ID."""
    fake_id = str(uuid4())
    args = _make_args(db_path=config.db_path, format="table", node_id=fake_id)
    with pytest.raises(SystemExit):
        await cmd_node(args)


@pytest.mark.asyncio
async def test_cmd_chain_no_supersedence(populated_engine, config, capsys):
    """chain command works when no supersedence exists."""
    node_ids = _get_node_ids(populated_engine)
    node_id = node_ids[0]

    args = _make_args(db_path=config.db_path, format="table", node_id=node_id)
    await cmd_chain(args)

    captured = capsys.readouterr().out
    assert "Supersedence chain for node:" in captured
    assert "(none)" in captured
    assert "1 node(s) in chain" in captured


@pytest.mark.asyncio
async def test_cmd_chain_with_supersedence(populated_engine, config, capsys):
    """chain command shows supersedence relationships."""
    node_ids = _get_node_ids(populated_engine)
    old_id = node_ids[0]
    new_id = node_ids[1]

    await populated_engine.supersede(old_id, new_id)

    args = _make_args(db_path=config.db_path, format="table", node_id=old_id)
    await cmd_chain(args)

    captured = capsys.readouterr().out
    assert "Supersedence chain for node:" in captured
    assert "Replaced by (forward chain):" in captured


@pytest.mark.asyncio
async def test_cmd_chain_json(populated_engine, config, capsys):
    """chain command outputs valid JSON."""
    node_ids = _get_node_ids(populated_engine)
    node_id = node_ids[0]

    args = _make_args(db_path=config.db_path, format="json", node_id=node_id)
    await cmd_chain(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "node_id" in data
    assert "forward_chain" in data
    assert "backward_chain" in data


@pytest.mark.asyncio
async def test_cmd_search(populated_engine, config, capsys):
    """search command returns results."""
    args = _make_args(
        db_path=config.db_path, format="table",
        query="programming language",
        user_id="test-user",
    )
    await cmd_search(args)

    captured = capsys.readouterr().out
    assert "Query:" in captured
    assert "Results:" in captured


@pytest.mark.asyncio
async def test_cmd_search_json(populated_engine, config, capsys):
    """search command outputs valid JSON."""
    args = _make_args(
        db_path=config.db_path, format="json",
        query="programming",
        user_id="test-user",
    )
    await cmd_search(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "query" in data
    assert "result_count" in data
    assert "results" in data


@pytest.mark.asyncio
async def test_cmd_organize(populated_engine, config, capsys):
    """organize command runs and reports results."""
    args = _make_args(
        db_path=config.db_path, format="table",
        jobs=None, budget_ms=5000,
    )
    await cmd_organize(args)

    captured = capsys.readouterr().out
    assert "Organize complete:" in captured
    assert "Duration:" in captured


@pytest.mark.asyncio
async def test_cmd_organize_specific_jobs(populated_engine, config, capsys):
    """organize command runs specific jobs."""
    args = _make_args(
        db_path=config.db_path, format="table",
        jobs="promote,archive", budget_ms=2000,
    )
    await cmd_organize(args)

    captured = capsys.readouterr().out
    assert "Organize complete:" in captured


@pytest.mark.asyncio
async def test_cmd_organize_json(populated_engine, config, capsys):
    """organize command outputs valid JSON."""
    args = _make_args(
        db_path=config.db_path, format="json",
        jobs="promote", budget_ms=2000,
    )
    await cmd_organize(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "jobs_run" in data
    assert "duration_ms" in data


@pytest.mark.asyncio
async def test_cmd_stats_table(populated_engine, config, capsys):
    """stats command shows statistics in table format."""
    args = _make_args(db_path=config.db_path, format="table")
    await cmd_stats(args)

    captured = capsys.readouterr().out
    assert "Memory Statistics" in captured
    assert "Totals:" in captured
    assert "Nodes by type:" in captured
    assert "Nodes by state:" in captured


@pytest.mark.asyncio
async def test_cmd_stats_json(populated_engine, config, capsys):
    """stats command outputs valid JSON."""
    args = _make_args(db_path=config.db_path, format="json")
    await cmd_stats(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["totals"]["nodes"] == 3
    assert data["totals"]["events"] == 3
    assert "nodes_by_type" in data
    assert "avg_confidence" in data


@pytest.mark.asyncio
async def test_cmd_export(populated_engine, config, capsys):
    """export command outputs valid JSON with all data."""
    args = _make_args(db_path=config.db_path)
    await cmd_export(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["node_count"] == 3
    assert data["event_count"] == 3
    assert len(data["nodes"]) == 3
    assert len(data["events"]) == 3
    assert "exported_at" in data
    # Verify node structure
    node = data["nodes"][0]
    assert "id" in node
    assert "node_type" in node
    assert "content" in node


@pytest.mark.asyncio
async def test_cmd_edges_empty(populated_engine, config, capsys):
    """edges command with no edges."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type=None, source=None, target=None,
    )
    await cmd_edges(args)

    captured = capsys.readouterr().out
    assert "0 edge(s) returned" in captured


@pytest.mark.asyncio
async def test_cmd_edges_after_supersede(populated_engine, config, capsys):
    """edges command shows edges after supersedence creates them."""
    node_ids = _get_node_ids(populated_engine)
    old_id = node_ids[0]
    new_id = node_ids[1]

    await populated_engine.supersede(old_id, new_id)

    args = _make_args(
        db_path=config.db_path, format="table",
        type=None, source=None, target=None,
    )
    await cmd_edges(args)

    captured = capsys.readouterr().out
    assert "supersedes" in captured
    assert "1 edge(s) returned" in captured


@pytest.mark.asyncio
async def test_cmd_edges_json(populated_engine, config, capsys):
    """edges command outputs valid JSON."""
    args = _make_args(
        db_path=config.db_path, format="json",
        type=None, source=None, target=None,
    )
    await cmd_edges(args)

    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_db_file():
    """Commands fail gracefully for missing database files."""
    args = _make_args(
        db_path="/tmp/nonexistent_prme_test_12345.duckdb",
        format="table",
    )
    with pytest.raises(SystemExit):
        await cmd_info(args)


@pytest.mark.asyncio
async def test_invalid_node_type_exits(populated_engine, config):
    """nodes command exits for invalid node type."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type="invalid_type", state=None, limit=20,
    )
    with pytest.raises(SystemExit):
        await cmd_nodes(args)


@pytest.mark.asyncio
async def test_invalid_state_exits(populated_engine, config):
    """nodes command exits for invalid lifecycle state."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type=None, state="invalid_state", limit=20,
    )
    with pytest.raises(SystemExit):
        await cmd_nodes(args)


@pytest.mark.asyncio
async def test_invalid_edge_type_exits(populated_engine, config):
    """edges command exits for invalid edge type."""
    args = _make_args(
        db_path=config.db_path, format="table",
        type="invalid_type", source=None, target=None,
    )
    with pytest.raises(SystemExit):
        await cmd_edges(args)


# ---------------------------------------------------------------------------
# Node-to-dict tests
# ---------------------------------------------------------------------------


class TestNodeToDict:
    @pytest.mark.asyncio
    async def test_node_to_dict_roundtrip(self, populated_engine, config):
        """_node_to_dict produces JSON-serializable output."""
        node_ids = _get_node_ids(populated_engine)
        node = await populated_engine.get_node(node_ids[0], include_superseded=True)
        d = _node_to_dict(node)
        # Should be JSON-serializable
        result = json.dumps(d)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["id"] == str(node.id)
