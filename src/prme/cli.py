"""CLI tooling for PRME memory inspection and management.

Provides command-line tools for inspecting, querying, and managing
memory packs. Uses argparse (no external dependencies) and handles
async engine lifecycle internally.

Commands:
    prme init [directory]    -- Initialize a new memory directory
    prme doctor [directory]  -- Check memory pack health
    prme info <db_path>      -- Show memory pack info
    prme nodes <db_path>     -- List nodes with filters
    prme edges <db_path>     -- List edges with filters
    prme node <db_path> <id> -- Show single node detail
    prme chain <db_path> <id> -- Show supersedence chain
    prme search <db_path> <q> -- Run retrieval query
    prme organize <db_path>  -- Run organizer jobs
    prme stats <db_path>     -- Show memory statistics
    prme export <db_path>    -- Export memory pack as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

from prme.config import PRMEConfig
from prme.models import MemoryEdge, MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType


# ---------------------------------------------------------------------------
# Engine lifecycle helper
# ---------------------------------------------------------------------------


async def _create_engine(db_path: str) -> Any:
    """Create a MemoryEngine from a database path.

    Resolves the db_path to an absolute path and configures the engine
    with collocated vector/lexical index paths.

    Args:
        db_path: Path to the DuckDB database file.

    Returns:
        An initialized MemoryEngine.

    Raises:
        SystemExit: If the database file does not exist.
    """
    from prme.storage.engine import MemoryEngine

    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        print(f"Error: database file not found: {abs_path}", file=sys.stderr)
        sys.exit(1)

    db_dir = os.path.dirname(abs_path)
    config = PRMEConfig(
        db_path=abs_path,
        vector_path=os.path.join(db_dir, "vectors.usearch"),
        lexical_path=os.path.join(db_dir, "lexical_index"),
    )
    return await MemoryEngine.create(config)


# ---------------------------------------------------------------------------
# Table formatting helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text with ellipsis if it exceeds max_len."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format data as an aligned text table.

    Args:
        headers: Column header strings.
        rows: List of row data (each row is a list of strings).

    Returns:
        Formatted table string with header separator.
    """
    if not rows:
        return "(no results)"

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    # Build format string
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    lines = [fmt.format(*headers)]
    lines.append("  ".join("-" * w for w in col_widths))
    for row in rows:
        # Pad row to match header count
        padded = row + [""] * (len(headers) - len(row))
        lines.append(fmt.format(*padded))

    return "\n".join(lines)


def _node_to_dict(node: MemoryNode) -> dict[str, Any]:
    """Convert a MemoryNode to a JSON-serializable dict."""
    return {
        "id": str(node.id),
        "node_type": node.node_type.value,
        "content": node.content,
        "confidence": round(node.confidence, 4),
        "salience": round(node.salience, 4),
        "lifecycle_state": node.lifecycle_state.value,
        "epistemic_type": node.epistemic_type.value,
        "source_type": node.source_type.value,
        "scope": node.scope.value,
        "user_id": node.user_id,
        "session_id": node.session_id,
        "valid_from": node.valid_from.isoformat(),
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        "superseded_by": str(node.superseded_by) if node.superseded_by else None,
        "evidence_refs": [str(ref) for ref in node.evidence_refs],
        "decay_profile": node.decay_profile.value,
        "pinned": node.pinned,
        "metadata": node.metadata,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
    }


def _edge_to_dict(edge: MemoryEdge) -> dict[str, Any]:
    """Convert a MemoryEdge to a JSON-serializable dict."""
    return {
        "id": str(edge.id),
        "source_id": str(edge.source_id),
        "target_id": str(edge.target_id),
        "edge_type": edge.edge_type.value,
        "user_id": edge.user_id,
        "confidence": round(edge.confidence, 4),
        "valid_from": edge.valid_from.isoformat(),
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "provenance_event_id": (
            str(edge.provenance_event_id)
            if edge.provenance_event_id
            else None
        ),
        "metadata": edge.metadata,
        "created_at": edge.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


async def cmd_info(args: argparse.Namespace) -> None:
    """Show memory pack info: node count, edge count, event count, timestamps."""
    engine = await _create_engine(args.db_path)
    try:
        conn = engine._conn

        # Count nodes
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        # Count edges
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        # Count events
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        # Last modified (most recent updated_at from nodes, or event timestamp)
        last_node = conn.execute(
            "SELECT MAX(updated_at) FROM nodes"
        ).fetchone()[0]
        last_event = conn.execute(
            "SELECT MAX(timestamp) FROM events"
        ).fetchone()[0]

        last_modified = max(
            t for t in [last_node, last_event] if t is not None
        ) if any(t is not None for t in [last_node, last_event]) else "N/A"

        db_path_abs = os.path.abspath(args.db_path)
        db_size = os.path.getsize(db_path_abs) if os.path.exists(db_path_abs) else 0

        if args.format == "json":
            print(json.dumps({
                "db_path": db_path_abs,
                "db_size_bytes": db_size,
                "node_count": node_count,
                "edge_count": edge_count,
                "event_count": event_count,
                "last_modified": str(last_modified),
            }, indent=2))
        else:
            print(f"Memory Pack: {db_path_abs}")
            print(f"  Database size:  {db_size:,} bytes")
            print(f"  Nodes:          {node_count}")
            print(f"  Edges:          {edge_count}")
            print(f"  Events:         {event_count}")
            print(f"  Last modified:  {last_modified}")
    finally:
        await engine.close()


async def cmd_nodes(args: argparse.Namespace) -> None:
    """List nodes with optional filters."""
    engine = await _create_engine(args.db_path)
    try:
        kwargs: dict[str, Any] = {"limit": args.limit}

        if args.type:
            try:
                kwargs["node_type"] = NodeType(args.type.lower())
            except ValueError:
                valid = ", ".join(t.value for t in NodeType)
                print(
                    f"Error: invalid node type '{args.type}'. "
                    f"Valid types: {valid}",
                    file=sys.stderr,
                )
                sys.exit(1)

        if args.state:
            try:
                state = LifecycleState(args.state.lower())
                kwargs["lifecycle_states"] = [state]
            except ValueError:
                valid = ", ".join(s.value for s in LifecycleState)
                print(
                    f"Error: invalid state '{args.state}'. "
                    f"Valid states: {valid}",
                    file=sys.stderr,
                )
                sys.exit(1)

        nodes = await engine.query_nodes(**kwargs)

        if args.format == "json":
            print(json.dumps([_node_to_dict(n) for n in nodes], indent=2))
        else:
            headers = ["ID", "TYPE", "STATE", "CONF", "SAL", "CONTENT"]
            rows = []
            for n in nodes:
                rows.append([
                    str(n.id)[:8] + "...",
                    n.node_type.value,
                    n.lifecycle_state.value,
                    f"{n.confidence:.2f}",
                    f"{n.salience:.2f}",
                    _truncate(n.content, 50),
                ])
            print(_format_table(headers, rows))
            print(f"\n{len(nodes)} node(s) returned")
    finally:
        await engine.close()


async def cmd_edges(args: argparse.Namespace) -> None:
    """List edges with optional filters."""
    engine = await _create_engine(args.db_path)
    try:
        kwargs: dict[str, Any] = {}

        if args.type:
            try:
                kwargs["edge_type"] = EdgeType(args.type.lower())
            except ValueError:
                valid = ", ".join(t.value for t in EdgeType)
                print(
                    f"Error: invalid edge type '{args.type}'. "
                    f"Valid types: {valid}",
                    file=sys.stderr,
                )
                sys.exit(1)

        if args.source:
            kwargs["source_id"] = args.source

        if args.target:
            kwargs["target_id"] = args.target

        edges = await engine._graph_store.get_edges(**kwargs)

        if args.format == "json":
            print(json.dumps([_edge_to_dict(e) for e in edges], indent=2))
        else:
            headers = ["ID", "TYPE", "SOURCE", "TARGET", "CONF"]
            rows = []
            for e in edges:
                rows.append([
                    str(e.id)[:8] + "...",
                    e.edge_type.value,
                    str(e.source_id)[:8] + "...",
                    str(e.target_id)[:8] + "...",
                    f"{e.confidence:.2f}",
                ])
            print(_format_table(headers, rows))
            print(f"\n{len(edges)} edge(s) returned")
    finally:
        await engine.close()


async def cmd_node(args: argparse.Namespace) -> None:
    """Show detailed info for a single node."""
    engine = await _create_engine(args.db_path)
    try:
        node = await engine.get_node(
            args.node_id, include_superseded=True
        )
        if node is None:
            print(f"Error: node '{args.node_id}' not found", file=sys.stderr)
            sys.exit(1)

        if args.format == "json":
            print(json.dumps(_node_to_dict(node), indent=2))
        else:
            d = _node_to_dict(node)
            print(f"Node: {d['id']}")
            print(f"  Type:            {d['node_type']}")
            print(f"  State:           {d['lifecycle_state']}")
            print(f"  Epistemic:       {d['epistemic_type']}")
            print(f"  Source:          {d['source_type']}")
            print(f"  Scope:           {d['scope']}")
            print(f"  Confidence:      {d['confidence']}")
            print(f"  Salience:        {d['salience']}")
            print(f"  Decay profile:   {d['decay_profile']}")
            print(f"  Pinned:          {d['pinned']}")
            print(f"  User ID:         {d['user_id']}")
            print(f"  Session ID:      {d['session_id']}")
            print(f"  Valid from:      {d['valid_from']}")
            print(f"  Valid to:        {d['valid_to']}")
            print(f"  Superseded by:   {d['superseded_by']}")
            print(f"  Evidence refs:   {', '.join(d['evidence_refs']) or 'none'}")
            print(f"  Created at:      {d['created_at']}")
            print(f"  Updated at:      {d['updated_at']}")
            print(f"  Metadata:        {json.dumps(d['metadata'])}")
            print(f"  Content:")
            for line in node.content.splitlines():
                print(f"    {line}")
    finally:
        await engine.close()


async def cmd_chain(args: argparse.Namespace) -> None:
    """Show supersedence chain for a node."""
    engine = await _create_engine(args.db_path)
    try:
        # Verify node exists
        node = await engine.get_node(args.node_id, include_superseded=True)
        if node is None:
            print(f"Error: node '{args.node_id}' not found", file=sys.stderr)
            sys.exit(1)

        # Get forward chain (what replaced this node)
        forward = await engine._graph_store.get_supersedence_chain(
            args.node_id, direction="forward"
        )
        # Get backward chain (what this node replaced)
        backward = await engine._graph_store.get_supersedence_chain(
            args.node_id, direction="backward"
        )

        if args.format == "json":
            print(json.dumps({
                "node_id": args.node_id,
                "forward_chain": [_node_to_dict(n) for n in forward],
                "backward_chain": [_node_to_dict(n) for n in backward],
            }, indent=2))
        else:
            print(f"Supersedence chain for node: {args.node_id}")
            print()

            if backward:
                print("  Replaced (backward chain):")
                for n in backward:
                    print(
                        f"    {str(n.id)[:8]}... "
                        f"[{n.lifecycle_state.value}] "
                        f"{_truncate(n.content, 40)}"
                    )
            else:
                print("  Replaced (backward chain): (none)")

            print()
            print(
                f"  >>> {str(node.id)[:8]}... "
                f"[{node.lifecycle_state.value}] "
                f"{_truncate(node.content, 40)}"
            )
            print()

            if forward:
                print("  Replaced by (forward chain):")
                for n in forward:
                    print(
                        f"    {str(n.id)[:8]}... "
                        f"[{n.lifecycle_state.value}] "
                        f"{_truncate(n.content, 40)}"
                    )
            else:
                print("  Replaced by (forward chain): (none)")

            total = len(backward) + 1 + len(forward)
            print(f"\n{total} node(s) in chain")
    finally:
        await engine.close()


async def cmd_search(args: argparse.Namespace) -> None:
    """Run a retrieval query and show results."""
    engine = await _create_engine(args.db_path)
    try:
        result = await engine.retrieve(
            args.query,
            user_id=args.user_id or "_cli",
        )

        if args.format == "json":
            output = {
                "query": args.query,
                "result_count": len(result.results),
                "results": [],
            }
            for r in result.results:
                output["results"].append({
                    "node_id": str(r.node.id),
                    "score": round(r.composite_score, 4),
                    "content": r.node.content,
                    "node_type": r.node.node_type.value,
                })
            print(json.dumps(output, indent=2))
        else:
            print(f"Query: {args.query}")
            print(f"Results: {len(result.results)}")
            print()
            if result.results:
                headers = ["#", "SCORE", "TYPE", "CONTENT"]
                rows = []
                for i, r in enumerate(result.results, 1):
                    rows.append([
                        str(i),
                        f"{r.composite_score:.4f}",
                        r.node.node_type.value,
                        _truncate(r.node.content, 50),
                    ])
                print(_format_table(headers, rows))
            else:
                print("(no results)")
    finally:
        await engine.close()


async def cmd_organize(args: argparse.Namespace) -> None:
    """Run the organizer with optional job selection and budget."""
    engine = await _create_engine(args.db_path)
    try:
        jobs = args.jobs.split(",") if args.jobs else None
        result = await engine.organize(
            jobs=jobs,
            budget_ms=args.budget_ms,
        )

        if args.format == "json":
            print(json.dumps(result.model_dump(), indent=2))
        else:
            print("Organize complete:")
            print(f"  Duration:          {result.duration_ms:.1f} ms")
            print(f"  Budget remaining:  {result.budget_remaining_ms:.1f} ms")
            print(f"  Jobs run:          {', '.join(result.jobs_run) or 'none'}")
            print(f"  Jobs skipped:      {', '.join(result.jobs_skipped) or 'none'}")
            if result.per_job:
                print()
                for name, jr in result.per_job.items():
                    print(f"  [{name}]")
                    print(f"    Processed: {jr.nodes_processed}")
                    print(f"    Modified:  {jr.nodes_modified}")
                    print(f"    Errors:    {jr.errors}")
                    print(f"    Duration:  {jr.duration_ms:.1f} ms")
    finally:
        await engine.close()


async def cmd_stats(args: argparse.Namespace) -> None:
    """Show statistics: nodes by type, by state, avg confidence, etc."""
    engine = await _create_engine(args.db_path)
    try:
        conn = engine._conn

        # Nodes by type
        by_type = conn.execute(
            "SELECT node_type, COUNT(*) FROM nodes GROUP BY node_type ORDER BY COUNT(*) DESC"
        ).fetchall()

        # Nodes by state
        by_state = conn.execute(
            "SELECT lifecycle_state, COUNT(*) FROM nodes GROUP BY lifecycle_state ORDER BY COUNT(*) DESC"
        ).fetchall()

        # Avg confidence and salience
        avgs = conn.execute(
            "SELECT AVG(confidence), AVG(salience) FROM nodes"
        ).fetchone()
        avg_confidence = avgs[0] if avgs[0] is not None else 0.0
        avg_salience = avgs[1] if avgs[1] is not None else 0.0

        # Total counts
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        # Edges by type
        edges_by_type = conn.execute(
            "SELECT edge_type, COUNT(*) FROM edges GROUP BY edge_type ORDER BY COUNT(*) DESC"
        ).fetchall()

        # Nodes by scope
        by_scope = conn.execute(
            "SELECT scope, COUNT(*) FROM nodes GROUP BY scope ORDER BY COUNT(*) DESC"
        ).fetchall()

        if args.format == "json":
            print(json.dumps({
                "totals": {
                    "nodes": node_count,
                    "edges": edge_count,
                    "events": event_count,
                },
                "avg_confidence": round(avg_confidence, 4),
                "avg_salience": round(avg_salience, 4),
                "nodes_by_type": {t: c for t, c in by_type},
                "nodes_by_state": {s: c for s, c in by_state},
                "nodes_by_scope": {s: c for s, c in by_scope},
                "edges_by_type": {t: c for t, c in edges_by_type},
            }, indent=2))
        else:
            print("Memory Statistics")
            print("=" * 50)
            print(f"\nTotals:")
            print(f"  Nodes:   {node_count}")
            print(f"  Edges:   {edge_count}")
            print(f"  Events:  {event_count}")
            print(f"\nAverages:")
            print(f"  Confidence:  {avg_confidence:.4f}")
            print(f"  Salience:    {avg_salience:.4f}")

            if by_type:
                print(f"\nNodes by type:")
                for t, c in by_type:
                    print(f"  {t:<15} {c:>6}")

            if by_state:
                print(f"\nNodes by state:")
                for s, c in by_state:
                    print(f"  {s:<15} {c:>6}")

            if by_scope:
                print(f"\nNodes by scope:")
                for s, c in by_scope:
                    print(f"  {s:<15} {c:>6}")

            if edges_by_type:
                print(f"\nEdges by type:")
                for t, c in edges_by_type:
                    print(f"  {t:<15} {c:>6}")
    finally:
        await engine.close()


async def cmd_export(args: argparse.Namespace) -> None:
    """Export the entire memory pack as JSON."""
    engine = await _create_engine(args.db_path)
    try:
        conn = engine._conn

        # Export all nodes
        all_nodes_raw = conn.execute("SELECT id FROM nodes").fetchall()
        nodes = []
        for (nid,) in all_nodes_raw:
            node = await engine.get_node(str(nid), include_superseded=True)
            if node is not None:
                nodes.append(_node_to_dict(node))

        # Export all edges
        edges_raw = await engine._graph_store.get_edges()
        edges = [_edge_to_dict(e) for e in edges_raw]

        # Export all events (basic info)
        events_raw = conn.execute(
            "SELECT id, user_id, role, content, timestamp, scope FROM events ORDER BY timestamp"
        ).fetchall()
        events = []
        for row in events_raw:
            events.append({
                "id": str(row[0]),
                "user_id": str(row[1]),
                "role": str(row[2]),
                "content": str(row[3]),
                "timestamp": str(row[4]),
                "scope": str(row[5]),
            })

        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "db_path": os.path.abspath(args.db_path),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "event_count": len(events),
            "nodes": nodes,
            "edges": edges,
            "events": events,
        }

        print(json.dumps(export_data, indent=2))
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Init and Doctor commands
# ---------------------------------------------------------------------------


async def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new memory directory."""
    from prme.client import config_from_directory

    directory = os.path.abspath(args.directory)
    config = config_from_directory(directory)

    # Write .env.example
    env_example = os.path.join(directory, ".env.example")
    if not os.path.exists(env_example):
        with open(env_example, "w") as f:
            f.write(
                "# PRME Configuration\n"
                "# Copy this file to .env and fill in your values.\n"
                "\n"
                "# LLM extraction provider (openai | anthropic | ollama)\n"
                "# PRME_EXTRACTION__PROVIDER=openai\n"
                "# PRME_EXTRACTION__MODEL=gpt-4o-mini\n"
                "\n"
                "# API keys (set the one matching your provider)\n"
                "# OPENAI_API_KEY=sk-...\n"
                "# ANTHROPIC_API_KEY=sk-ant-...\n"
                "\n"
                "# Embedding (fastembed is local, no API key needed)\n"
                "# PRME_EMBEDDING__PROVIDER=fastembed\n"
                "# PRME_EMBEDDING__MODEL_NAME=BAAI/bge-small-en-v1.5\n"
                "\n"
                "# Encryption at rest (optional)\n"
                "# PRME_ENCRYPTION_KEY=your-secret-key\n"
            )

    print(f"Initialized PRME memory directory: {directory}")
    print()
    print("  Files:")
    print(f"    Database:    {config.db_path}")
    print(f"    Vectors:     {config.vector_path}")
    print(f"    Lexical:     {config.lexical_path}")
    print(f"    Config:      {env_example}")
    print()
    print("  Next steps:")
    print("    1. Copy .env.example to .env and add your API key")
    print("    2. Use PRME in your code:")
    print()
    print("       from prme import MemoryClient")
    print()
    print(f'       with MemoryClient("{directory}") as client:')
    print('           client.store("hello world", user_id="me")')
    print()


async def cmd_doctor(args: argparse.Namespace) -> None:
    """Check memory pack health."""
    import duckdb as _duckdb

    directory = os.path.abspath(args.directory)
    db_path = os.path.join(directory, "memory.duckdb")
    vector_path = os.path.join(directory, "vectors.usearch")
    lexical_path = os.path.join(directory, "lexical_index")

    checks_passed = 0
    checks_failed = 0
    checks_warned = 0

    def ok(msg: str) -> None:
        nonlocal checks_passed
        checks_passed += 1
        print(f"  [OK]   {msg}")

    def fail(msg: str) -> None:
        nonlocal checks_failed
        checks_failed += 1
        print(f"  [FAIL] {msg}")

    def warn(msg: str) -> None:
        nonlocal checks_warned
        checks_warned += 1
        print(f"  [WARN] {msg}")

    print(f"Checking memory pack: {directory}")
    print()

    # 1. Directory exists
    if os.path.isdir(directory):
        ok("Memory directory exists")
    else:
        fail(f"Memory directory not found: {directory}")
        print(f"\n  Run 'prme init {directory}' to create it.")
        sys.exit(1)

    # 2. DuckDB file
    if os.path.exists(db_path):
        try:
            conn = _duckdb.connect(db_path, read_only=True)
            tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
            conn.close()
            if "events" in tables and "nodes" in tables:
                ok(f"DuckDB database valid ({len(tables)} tables)")
            else:
                fail(f"DuckDB schema incomplete (tables: {tables})")
        except Exception as e:
            fail(f"DuckDB cannot open: {e}")
    else:
        warn("DuckDB file not found (will be created on first use)")

    # 3. Vector index
    if os.path.exists(vector_path):
        size = os.path.getsize(vector_path)
        ok(f"Vector index exists ({size:,} bytes)")
    else:
        warn("Vector index not found (will be created on first use)")

    # 4. Lexical index
    if os.path.isdir(lexical_path) and os.listdir(lexical_path):
        ok("Lexical index exists")
    else:
        warn("Lexical index not found (will be created on first use)")

    # 5. LLM provider (advisory)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    if api_key:
        ok("LLM API key found in environment")
    else:
        warn("No LLM API key found (ingest() requires one; store() works without)")

    # Summary
    print()
    total = checks_passed + checks_failed + checks_warned
    print(f"  {checks_passed}/{total} passed, {checks_warned} warnings, {checks_failed} failures")
    if checks_failed > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="prme",
        description="PRME - Portable Relational Memory Engine CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Common arguments factory
    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("db_path", help="Path to DuckDB database file")
        sub.add_argument(
            "--format",
            choices=["table", "json"],
            default="table",
            help="Output format (default: table)",
        )

    # info
    p_info = subparsers.add_parser("info", help="Show memory pack info")
    add_common(p_info)
    p_info.set_defaults(func=cmd_info)

    # nodes
    p_nodes = subparsers.add_parser("nodes", help="List nodes")
    add_common(p_nodes)
    p_nodes.add_argument("--type", help="Filter by node type (e.g. fact, entity)")
    p_nodes.add_argument("--state", help="Filter by lifecycle state (e.g. tentative, stable)")
    p_nodes.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p_nodes.set_defaults(func=cmd_nodes)

    # edges
    p_edges = subparsers.add_parser("edges", help="List edges")
    add_common(p_edges)
    p_edges.add_argument("--type", help="Filter by edge type (e.g. relates_to, supersedes)")
    p_edges.add_argument("--source", help="Filter by source node ID")
    p_edges.add_argument("--target", help="Filter by target node ID")
    p_edges.set_defaults(func=cmd_edges)

    # node (single)
    p_node = subparsers.add_parser("node", help="Show single node detail")
    add_common(p_node)
    p_node.add_argument("node_id", help="Node UUID")
    p_node.set_defaults(func=cmd_node)

    # chain
    p_chain = subparsers.add_parser("chain", help="Show supersedence chain")
    add_common(p_chain)
    p_chain.add_argument("node_id", help="Node UUID")
    p_chain.set_defaults(func=cmd_chain)

    # search
    p_search = subparsers.add_parser("search", help="Run a retrieval query")
    add_common(p_search)
    p_search.add_argument("query", help="Query text")
    p_search.add_argument("--user-id", help="User ID for scoping (default: _cli)")
    p_search.set_defaults(func=cmd_search)

    # organize
    p_organize = subparsers.add_parser("organize", help="Run the organizer")
    add_common(p_organize)
    p_organize.add_argument(
        "--jobs",
        help="Comma-separated list of jobs to run (e.g. promote,archive)",
    )
    p_organize.add_argument(
        "--budget-ms",
        type=int,
        default=5000,
        help="Time budget in milliseconds (default: 5000)",
    )
    p_organize.set_defaults(func=cmd_organize)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show memory statistics")
    add_common(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    # export
    p_export = subparsers.add_parser("export", help="Export memory pack as JSON")
    p_export.add_argument("db_path", help="Path to DuckDB database file")
    p_export.set_defaults(func=cmd_export)

    # init
    p_init = subparsers.add_parser(
        "init", help="Initialize a new memory directory"
    )
    p_init.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to initialize (default: current directory)",
    )
    p_init.set_defaults(func=cmd_init)

    # doctor
    p_doctor = subparsers.add_parser(
        "doctor", help="Check memory pack health"
    )
    p_doctor.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Memory directory to check (default: current directory)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main() -> None:
    """Entry point for the PRME CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
