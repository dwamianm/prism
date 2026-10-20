"""CLI tooling for PRME memory inspection and management.

Provides command-line tools for inspecting, querying, and managing
memory packs. Uses argparse (no external dependencies) and handles
async engine lifecycle internally.

Every command that takes <db_path> accepts a directory pack's DuckDB file
(memory.duckdb) or a single-file pack (a file ending in .prme).

Commands:
    prme init [path]         -- Initialize a memory directory or a .prme pack
    prme doctor [path]       -- Check memory pack health
    prme compact <pack>      -- Rewrite a closed pack file to give back space
    prme migrate <dir> <pack.prme> -- Copy a directory pack into a .prme pack
    prme config-audit        -- Inspect provisional configuration policies
    prme info <db_path>      -- Show memory pack info
    prme nodes <db_path>     -- List nodes with filters
    prme edges <db_path>     -- List edges with filters
    prme node <db_path> <id> -- Show single node detail
    prme chain <db_path> <id> -- Show supersedence chain
    prme search <db_path> <q> -- Run retrieval query
    prme observe <db_path> <file>... -- Remember what photos and videos showed (RFC-0018)
    prme organize <db_path>  -- Run organizer jobs
    prme profile-jobs <db_path> -- Inspect owned profile preparations
    prme process-profiles <db_path> -- Resume owned profile preparations
    prme resume-profile <db_path> <id> -- Resume one owned preparation
    prme discard-profile <db_path> <id> -- Abandon one owned preparation
    prme collect-profile-staging <db_path> -- Reclaim abandoned profile indexes
    prme rebuild <db_path>   -- Rebuild indexes from the durable graph
    prme stats <db_path>     -- Show memory statistics
    prme export <db_path>    -- Export memory pack as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import UUID

from prme.config import PRMEConfig
from prme.config_audit import audit_hypotheses
from prme.models import MemoryEdge, MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope


# ---------------------------------------------------------------------------
# Engine lifecycle helper
# ---------------------------------------------------------------------------


def _single_file_config(path: str) -> PRMEConfig:
    """Configure an engine for the single-file pack at absolute *path*."""
    # As for a directory pack below, the positional path is the command's
    # target: an ambient database_url must not redirect it to PostgreSQL.
    return PRMEConfig(database_url=None, pack_path=path)


async def _create_engine(db_path: str, *, vision_overrides: dict[str, Any] | None = None) -> Any:
    """Create a MemoryEngine from a database path.

    Resolves the db_path to an absolute path. A path ending in ``.prme``
    that is not a directory opens a single-file pack; any other path is a
    directory pack's DuckDB file, with its vector and lexical indexes beside
    it.

    Args:
        db_path: Path to the DuckDB database file or the single-file pack.
        vision_overrides: Optional VisionConfig fields that replace the
            configured ones, on top of ``PRME_VISION_*`` and
            ``PRME_VISION__*`` from the environment (``prme observe`` turns
            vision on for its own run).

    Returns:
        An initialized MemoryEngine.

    Raises:
        SystemExit: If the database file does not exist.
    """
    from prme.client import is_pack_file
    from prme.storage.engine import MemoryEngine

    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        print(f"Error: database file not found: {abs_path}", file=sys.stderr)
        sys.exit(1)

    if is_pack_file(abs_path):
        config = _single_file_config(abs_path)
    else:
        db_dir = os.path.dirname(abs_path)
        config = PRMEConfig(
            # A positional local artifact is the command's target. Ambient settings
            # must not silently redirect inspection or maintenance to PostgreSQL
            # or to a single-file pack.
            database_url=None,
            pack_path=None,
            db_path=abs_path,
            vector_path=os.path.join(db_dir, "vectors.usearch"),
            lexical_path=os.path.join(db_dir, "lexical_index"),
        )
    if vision_overrides:
        config = config.model_copy(update={"vision": config.vision.model_copy(update=vision_overrides)})
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
            print("  Content:")
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


async def cmd_observe(args: argparse.Namespace) -> None:
    """Remember what photos and videos showed (RFC-0018, phase 1).

    Each file is observed in turn: a photo becomes one observation event, a
    video one per keyframe that changed and whose description does not
    repeat the previous kept frame's. The command turns ``vision.enabled``
    on for its own run, because the command itself is the explicit request;
    every other vision setting comes from the environment
    (``PRME_VISION_*``) unless a flag overrides it. Extraction, when on, uses
    the configured extraction provider (``PRME_EXTRACTION_*``).
    """
    from prme.vision.errors import VisionError
    from prme.vision.observe import observe_source

    overrides: dict[str, Any] = {"enabled": True}
    if args.model:
        overrides["model"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.extract is not None:
        overrides["extract"] = args.extract
    if args.no_thumbnails:
        overrides["keep_thumbnails"] = False
    captured_at = None
    if args.captured_at:
        captured_at = datetime.fromisoformat(args.captured_at)
        if captured_at.tzinfo is None:
            # The clock becomes every observation's event_time, so it must be
            # unambiguous; the local timezone is the honest reading of a bare
            # wall-clock time typed at the terminal.
            captured_at = captured_at.astimezone()
    scope = Scope(args.scope)

    engine = await _create_engine(args.db_path, vision_overrides=overrides)
    extraction = engine._config.extraction
    admission = (f"ingest() with extraction by {extraction.provider}/{extraction.model}"
                 if engine._config.vision.extract else "store(), no extraction")
    reports: list[dict[str, Any]] = []
    failures = 0
    try:
        for path in args.files:
            try:
                report = await observe_source(
                    engine, path, user_id=args.user_id, captured_at=captured_at,
                    session_id=args.session_id, scope=scope,
                )
            except (VisionError, FileNotFoundError, ValueError) as exc:
                failures += 1
                partial = getattr(exc, "report", None)
                entry: dict[str, Any] = {"source": path, "error": f"{type(exc).__name__}: {exc}"}
                if partial is not None:
                    entry["event_ids"] = partial.event_ids
                reports.append(entry)
                print(f"Error: {path}: {exc}", file=sys.stderr)
                continue
            failures += int(report.failed > 0)
            reports.append(report.to_dict() if args.format == "json" else report.summary())
            if args.format != "json":
                print(f"{report.source} ({report.kind}, {report.vision_model}, prompt v{report.describe_prompt_version})")
                print(f"  Capture clock:    {report.captured_at.isoformat()} ({report.capture_clock})")
                print(f"  Frames sampled:   {report.frames}")
                print(f"  Keyframes:        {report.keyframes}")
                print(f"  Described:        {report.described}")
                print(f"  Repeats dropped:  {report.repeats_dropped}")
                print(f"  Already observed: {report.already_observed}")
                print(f"  Admitted:         {report.admitted} ({admission})")
                print(f"  Failed:           {report.failed}")
                for record in report.observations:
                    if record.status == "admitted":
                        when = f"t={record.time_s:.1f}s " if record.time_s is not None else ""
                        print(f"    {when}event {record.event_id}: {_truncate(record.description or '', 90)}")
                    elif record.error:
                        print(f"    frame {record.index}: {record.status}: {record.error}")
    finally:
        await engine.close()
    if args.format == "json":
        print(json.dumps(reports, indent=2, default=str))
    if failures:
        sys.exit(1)


async def cmd_organize(args: argparse.Namespace) -> None:
    """Run the organizer with optional job selection and budget."""
    engine = await _create_engine(args.db_path)
    try:
        jobs = args.jobs.split(",") if args.jobs else None
        result = await engine.organize(
            user_id=getattr(args, "user_id", None),
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


async def cmd_reingest(args: argparse.Namespace) -> None:
    """Re-derive one owner's stored memories after an engine change (needs PRME_ENABLE_REINGEST=true)."""
    engine = await _create_engine(args.db_path)
    try:
        result = await engine.reingest(args.user_id, scope=args.scope, limit=args.limit, cursor=args.cursor,
                                       dry_run=args.dry_run)
        values = result.model_dump(mode="json")
        if args.format == "json":
            print(json.dumps(values, indent=2))
        else:
            for name, value in values.items():
                if value is not None:
                    print(f"{name}: {value}")
        if result.failed:
            raise SystemExit(1)
    finally:
        await engine.close()


async def cmd_extractions(args: argparse.Namespace) -> None:
    """Inspect, queue or execute explicitly scoped durable extraction work."""
    engine = await _create_engine(args.db_path)
    try:
        if args.action == "process":
            result = await engine.process_extractions(user_id=args.user_id, limit=args.limit, budget_ms=args.budget_ms)
        elif args.action == "retry":
            result = await engine.retry_extraction(args.event_id, user_id=args.user_id, replan=args.replan)
        else:
            result = await engine.extraction_status(args.event_id, user_id=args.user_id)
        if result is None:
            raise ValueError("Extraction work not found")
        values = result.model_dump(mode="json")
        if args.format == "json":
            print(json.dumps(values, indent=2))
        else:
            for name, value in values.items():
                if value is not None:
                    print(f"{name}: {value}")
        if args.action == "process" and result.failed:
            raise SystemExit(1)
    finally:
        await engine.close()


async def cmd_profiles(args: argparse.Namespace) -> None:
    """Inspect or recover explicitly owned profile work without model calls."""
    engine = await _create_engine(args.db_path)
    failed = False
    try:
        scope = Scope(args.scope) if getattr(args, "scope", None) else None
        if args.action == "jobs":
            result = await engine.profile_jobs(
                user_id=args.user_id, scope=scope, status=args.status, limit=args.limit,
            )
        elif args.action in {"process", "collect"}:
            method = engine.process_profiles if args.action == "process" else engine.collect_profile_staging
            result = await method(user_id=args.user_id, scope=scope, limit=args.limit, budget_ms=args.budget_ms)
            failed = bool(result["failed"] or result.get("blocked_reason"))
        elif args.action == "resume":
            profile_id = await engine.resume_profile(args.profile_id, user_id=args.user_id)
            result = {"profile_id": profile_id, "resumed": profile_id is not None}
            failed = profile_id is None
        else:
            discarded = await engine.discard_profile(args.profile_id, user_id=args.user_id)
            result = {"profile_id": args.profile_id, "discarded": discarded}
            failed = not discarded
        if args.format == "json":
            print(json.dumps(result, indent=2))
        elif isinstance(result, list):
            columns = ["plan_id", "scope", "status", "attempts", "last_error"]
            print(_format_table(columns, [[str(row[c]) if row.get(c) is not None else "" for c in columns] for row in result]))
        else:
            for key, value in result.items():
                print(f"{key}: {value}")
        if failed:
            raise SystemExit(1)
    finally:
        await engine.close()


async def cmd_rebuild(args: argparse.Namespace) -> None:
    """Rebuild the vector and lexical indexes from the durable graph.

    Drops the derived search indexes and re-materializes them from the
    active nodes already persisted in the pack. The event log and graph
    nodes are never modified, so the rebuild is safe to re-run.
    """
    engine = await _create_engine(args.db_path)
    try:
        summary = await engine.rebuild_indexes(batch_size=args.batch_size)

        if args.format == "json":
            print(json.dumps(summary, indent=2))
        else:
            print("Rebuild complete:")
            print(f"  Nodes indexed:  {summary['nodes_indexed']}")
            print(f"  Nodes skipped:  {summary['nodes_skipped']}")
            print(f"  Active nodes:   {summary['total_active']}")
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
            print("\nTotals:")
            print(f"  Nodes:   {node_count}")
            print(f"  Edges:   {edge_count}")
            print(f"  Events:  {event_count}")
            print("\nAverages:")
            print(f"  Confidence:  {avg_confidence:.4f}")
            print(f"  Salience:    {avg_salience:.4f}")

            if by_type:
                print("\nNodes by type:")
                for t, c in by_type:
                    print(f"  {t:<15} {c:>6}")

            if by_state:
                print("\nNodes by state:")
                for s, c in by_state:
                    print(f"  {s:<15} {c:>6}")

            if by_scope:
                print("\nNodes by scope:")
                for s, c in by_scope:
                    print(f"  {s:<15} {c:>6}")

            if edges_by_type:
                print("\nEdges by type:")
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
# Pack file maintenance commands
# ---------------------------------------------------------------------------


async def cmd_compact(args: argparse.Namespace) -> None:
    """Rewrite a closed pack file into a fresh copy that holds only live data."""
    from prme.storage.pack_tools import compact_pack

    # Runs on this thread, so an interrupt reaches the cleanup of its copy.
    result = compact_pack(args.pack, encryption_key=_encryption_key(args, args.pack))
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Compacted memory pack: {result['path']}")
        print(f"  Size before:  {result['bytes_before']:,} bytes")
        print(f"  Size after:   {result['bytes_after']:,} bytes")


async def cmd_migrate(args: argparse.Namespace) -> None:
    """Copy a directory pack into a new single-file pack."""
    from prme.client import PACK_SUFFIX
    from prme.storage.pack_tools import migrate_directory_pack

    if not args.pack.endswith(PACK_SUFFIX):
        # Only a .prme name opens as a single-file pack in MemoryClient and
        # in these commands; any other name would open as a directory pack.
        print(f"Error: the new pack's name must end in {PACK_SUFFIX}: {args.pack}", file=sys.stderr)
        sys.exit(1)
    result = await migrate_directory_pack(
        args.directory, args.pack, encryption_key=_encryption_key(args, args.pack),
    )
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Migrated memory pack: {result['source']}")
        print(f"  New pack:      {result['destination']}")
        print(f"  Nodes:         {result['nodes']}")
        print(f"  Edges:         {result['edges']}")
        print(f"  Events:        {result['events']}")
        print(f"  Vectors:       {result['vectors']}")
        print(f"  Size before:   {result['bytes_before']:,} bytes (directory pack files)")
        print(f"  Size after:    {result['bytes_after']:,} bytes")
        print(f"  Encrypted:     {'yes' if result['encrypted'] else 'no'}")
        print("  The directory pack was left in place.")


# ---------------------------------------------------------------------------
# Init and Doctor commands
# ---------------------------------------------------------------------------


async def _init_pack_file(path: str) -> None:
    """Create the single-file pack at absolute *path*, or check an existing one."""
    from prme.storage.engine import MemoryEngine

    # One open and close writes the schema, the vector snapshot and the
    # manifest into a new file. An existing pack is opened and checked; a
    # file that is not a pack fails to open and is left as it is.
    engine = await MemoryEngine.create(_single_file_config(path))
    await engine.close()

    # No .env.example is written for a single-file pack. The pack is meant to
    # be one file that can be copied on its own; a sidecar file would break
    # that, would collide when several packs share a directory, and would not
    # be read from there anyway, because settings come from the environment
    # and from a .env file in the working directory. A hint is printed instead.
    print(f"Initialized PRME memory pack: {path}")
    print()
    print("  File:")
    print(f"    Pack:        {path}")
    print()
    print("  Next steps:")
    print("    1. Configure PRME with PRME_ environment variables or a .env file in the")
    print("       directory you run from (prme init <directory> writes an annotated")
    print("       .env.example to start from)")
    print("    2. Use PRME in your code:")
    print()
    print("       from prme import MemoryClient")
    print()
    print(f'       with MemoryClient("{path}") as client:')
    print('           client.store("hello world", user_id="me")')
    print()


async def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new memory directory or single-file pack."""
    from prme.client import is_pack_file, config_from_directory

    if is_pack_file(args.directory):
        await _init_pack_file(os.path.abspath(args.directory))
        return

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
                "# Encryption at rest (optional). Both are required to enable it.\n"
                "# PRME_ENCRYPTION_ENABLED=true\n"
                "# PRME_ENCRYPTION_KEY=your-secret-key\n"
                "\n"
                "# Visual memory (optional; RFC-0018). observe() and prme observe describe\n"
                "# photos and videos with a local vision model served by Ollama.\n"
                "# PRME_VISION__ENABLED=true\n"
                "# PRME_VISION__MODEL=qwen3.5:9b\n"
                "# PRME_VISION__EXTRACT=true\n"
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


async def _verify_extraction_provider(
    extraction: Any,
    local: dict[str, str | None],
    *,
    timeout: float,
    transport: Any = None,
) -> tuple[bool, str]:
    """Verify provider reachability, credential, and model without generation."""
    import httpx

    provider = extraction.provider.strip().casefold()
    prefix = {"openai": "OPENAI", "anthropic": "ANTHROPIC"}.get(provider)
    key = extraction.api_key.get_secret_value() if extraction.api_key else None
    if prefix and not key:
        key_name = f"{prefix}_API_KEY"
        key = os.environ.get(key_name, local.get(key_name))
    if prefix and not key:
        return False, f"{provider} extraction credential is missing"

    provider_url_name = f"{prefix}_BASE_URL" if prefix else None
    configured_url = extraction.base_url
    if not configured_url and provider_url_name:
        configured_url = os.environ.get(
            provider_url_name, local.get(provider_url_name)
        )
    if provider == "openai":
        base_url = (configured_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/models/{quote(extraction.model, safe='')}"
        method = "GET"
        headers = {"Authorization": f"Bearer {key}"}
        body = None
    elif provider == "anthropic":
        base_url = (configured_url or "https://api.anthropic.com/v1").rstrip("/")
        url = f"{base_url}/models/{quote(extraction.model, safe='')}"
        method = "GET"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = None
    elif provider == "ollama":
        base_url = (configured_url or "http://127.0.0.1:11434").rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        url = f"{base_url}/api/show"
        method = "POST"
        headers = {}
        body = {"model": extraction.model}
    else:
        return False, f"provider verification is unavailable for {provider}"

    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.request(method, url, headers=headers, json=body)
    except httpx.TimeoutException:
        return False, f"{provider} verification timed out after {timeout:g}s"
    except httpx.HTTPError as error:
        return False, f"{provider} connection failed ({type(error).__name__})"

    if response.status_code == 200:
        if provider == "ollama":
            return True, "ollama endpoint and model metadata verified"
        return (
            True,
            f"{provider} credential and model metadata verified "
            "(generation quota not checked)",
        )
    if response.status_code in (401, 403):
        return False, f"{provider} credential rejected (HTTP {response.status_code})"
    if response.status_code == 404:
        return False, f"{provider} model is unavailable at the configured endpoint (HTTP 404)"
    return False, f"{provider} verification failed (HTTP {response.status_code})"


def _encryption_key(args: argparse.Namespace, target: str) -> str | None:
    """Return the key for the encrypted pack *target* named on the command line.

    ``--encryption-key`` wins, and an empty one means no key. Otherwise, when
    *target* is a single-file pack, PRME_ENCRYPTION_KEY applies when
    PRME_ENCRYPTION_ENABLED is true, as when an engine opens the pack, so the
    key can stay out of the command line. A directory pack's memory.duckdb
    never takes it, because there it configures file-level encryption, not
    DuckDB's. Only those two settings are read, from the environment and
    then from a .env file in the working directory, so an invalid setting
    elsewhere does not stop the command.
    """
    explicit = getattr(args, "encryption_key", None)
    if explicit is not None:
        return explicit or None
    from prme.client import is_pack_file

    if not is_pack_file(target):
        return None
    from dotenv import dotenv_values

    # PRME settings are case-insensitive, and the environment outranks .env.
    settings = {name.upper(): value for name, value in dotenv_values(".env").items()}
    settings.update((name.upper(), value) for name, value in os.environ.items())
    # The values a PRMEConfig boolean reads as true.
    enabled = (settings.get("PRME_ENCRYPTION_ENABLED") or "").lower() in {"1", "on", "t", "true", "y", "yes"}
    key = settings.get("PRME_ENCRYPTION_KEY") or ""
    return key if enabled and key else None


def _check_pack_file(path: str, encryption_key: str | None, ok, warn, fail) -> None:
    """Run the storage checks of ``prme doctor`` on a single-file pack."""
    from contextlib import ExitStack

    from prme.storage.pack_tools import open_pack_read_only

    if not os.path.isfile(path):
        fail(f"Memory pack file not found: {path}")
        print(f"\n  Run 'prme init {path}' to create it.")
        sys.exit(1)
    ok(f"Memory pack file exists ({os.path.getsize(path):,} bytes)")
    # DuckDB keeps the log beside the real file, which an engine opens.
    if os.path.lexists(os.path.realpath(path) + ".wal"):
        warn("Write-ahead log present: the pack is open elsewhere or was not closed cleanly")

    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_pack_read_only(path, encryption_key=encryption_key))
        except Exception as e:
            fail(f"DuckDB cannot open: {e}")
            return
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        missing = [name for name in ("events", "nodes", "prme_manifest", "vector_snapshot")
                   if name not in tables]
        if missing:
            fail(f"DuckDB schema incomplete (missing: {', '.join(missing)})")
            return
        (encrypted,) = conn.execute(
            "SELECT encrypted FROM duckdb_databases() WHERE database_name = current_database()"
        ).fetchone()
        ok(f"DuckDB database valid ({len(tables)} tables{', encrypted' if encrypted else ''})")

        manifest = conn.execute(
            "SELECT format_version, prme_version, embedding_model, embedding_version, "
            "embedding_dim, created_at, updated_at FROM prme_manifest WHERE singleton"
        ).fetchone()
        if manifest is None:
            warn("Pack manifest missing (it will be written on the next open)")
        else:
            format_version, prme_version, model, model_version, dimension, created, updated = manifest
            ok("Pack manifest present")
            print(f"           Format version:   {format_version}")
            print(f"           PRME version:     {prme_version}")
            print(f"           Embedding model:  {model} (version {model_version}, {dimension} dimensions)")
            print(f"           Created:          {created.isoformat(timespec='seconds')}")
            print(f"           Updated:          {updated.isoformat(timespec='seconds')}")

        snapshot = conn.execute(
            "SELECT vector_count, octet_length(data) FROM vector_snapshot WHERE singleton"
        ).fetchone()
        if snapshot is None:
            warn("Vector snapshot missing (it will be rebuilt from vector payloads on open)")
            return
        stored, size = snapshot
        if {"vector_metadata", "vector_payloads"} <= tables:
            (expected,) = conn.execute(
                "SELECT count(*) FROM vector_metadata JOIN vector_payloads USING (vector_key)"
            ).fetchone()
            if stored != expected:
                warn(f"Vector snapshot holds {stored} vectors but the pack has {expected} "
                     "(it will be reconciled on open)")
                return
        ok(f"Vector snapshot stored ({stored} vectors, {size:,} bytes)")


async def cmd_doctor(args: argparse.Namespace) -> None:
    """Check memory pack health."""
    import duckdb as _duckdb

    from prme.client import is_pack_file

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

    if is_pack_file(directory):
        # 1-4. A single-file pack holds its indexes and manifest in one file.
        _check_pack_file(directory, _encryption_key(args, directory), ok, warn, fail)
    else:
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
    from dotenv import dotenv_values
    from prme.config import ExtractionConfig

    extraction = ExtractionConfig()
    local = dotenv_values(".env")
    key_name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(extraction.provider)
    configured = bool(extraction.api_key)
    if key_name:
        configured = configured or bool(os.environ.get(key_name, local.get(key_name)))
    if extraction.provider == "ollama":
        suffix = (
            ""
            if getattr(args, "verify_extraction", False)
            else " (server availability not checked)"
        )
        ok(f"Local Ollama extraction selected{suffix}")
    elif configured:
        ok(f"{extraction.provider} extraction credential configured (not verified)")
    else:
        warn(f"No credential found for {extraction.provider} extraction; store() works without an LLM")

    # Diagnose the settings used by this CLI, without exposing values or making
    # a provider request. PRME settings are case-insensitive; SDK variables are
    # case-sensitive. Ignore lower-priority SDK settings when PRME overrides them.
    process_settings = {name.upper(): value for name, value in os.environ.items()}
    file_settings = {name.upper(): value for name, value in local.items()}
    active_settings = ["PRME_EXTRACTION_PROVIDER", "PRME_EXTRACTION_MODEL"]
    if extraction.api_key:
        active_settings.append("PRME_EXTRACTION_API_KEY")
    if extraction.base_url:
        active_settings.append("PRME_EXTRACTION_BASE_URL")
    shadowed = [name for name in active_settings if (
        name in process_settings and file_settings.get(name) is not None
        and process_settings[name] != file_settings[name]
    )]
    if key_name:
        for name, overridden in (
            (key_name, bool(extraction.api_key)),
            (key_name.replace("API_KEY", "BASE_URL"), bool(extraction.base_url)),
        ):
            if (not overridden and name in os.environ and local.get(name) is not None
                    and os.environ[name] != local[name]):
                shadowed.append(name)
    for name in shadowed:
        warn(
            f"{name} in the process environment overrides a different value in .env; "
            f"update or unset {name} before recreating the client"
        )

    if getattr(args, "verify_extraction", False):
        verified, message = await _verify_extraction_provider(
            extraction,
            local,
            timeout=args.provider_timeout,
        )
        if verified:
            ok(message)
        else:
            fail(message)

    # Summary
    print()
    total = checks_passed + checks_failed + checks_warned
    print(f"  {checks_passed}/{total} passed, {checks_warned} warnings, {checks_failed} failures")
    if checks_failed > 0:
        sys.exit(1)


async def cmd_config_audit(args: argparse.Namespace) -> None:
    """Report every hypothesis-tagged setting in the effective configuration."""
    report = audit_hypotheses(PRMEConfig())
    if args.format == "json":
        print(report.model_dump_json(indent=2))
        return

    print(
        "Provisional configuration: "
        f"{report.hypothesis_count} hypothesis-tagged settings, "
        f"{report.effective_count} effective, "
        f"{report.customized_count} customized"
    )
    print()
    rows = [
        [
            item.path,
            "effective" if item.effective else "dormant",
            "yes" if item.customized else "no",
            _truncate(json.dumps(item.value, sort_keys=True), 44),
            item.environment_variable,
        ]
        for item in report.settings
    ]
    print(_format_table(["SETTING", "STATE", "CUSTOM", "VALUE", "ENVIRONMENT"], rows))
    print()
    print(
        "Effective means the governing feature is enabled; a setting may still "
        "apply only to its documented query, ingestion, or organizer path."
    )


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

    db_path_help = "Path to a directory pack's DuckDB file (memory.duckdb) or a single-file pack (.prme)"
    encryption_key_help = (
        "Key of an encrypted pack, in any form PRME_ENCRYPTION_KEY takes; an empty "
        "key means none (default for a .prme pack: PRME_ENCRYPTION_KEY when "
        "PRME_ENCRYPTION_ENABLED is true)"
    )

    # Common arguments factory
    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("db_path", help=db_path_help)
        sub.add_argument(
            "--format",
            choices=["table", "json"],
            default="table",
            help="Output format (default: table)",
        )

    def owner(value: str) -> str:
        if not value.strip():
            raise argparse.ArgumentTypeError("A nonempty owner is required")
        return value

    def work_limit(value: str) -> int:
        number = int(value)
        if not 1 <= number <= 1000:
            raise argparse.ArgumentTypeError("Limit must be between 1 and 1000")
        return number

    def work_budget(value: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise argparse.ArgumentTypeError("Budget must be finite and nonnegative")
        return number

    def positive_timeout(value: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise argparse.ArgumentTypeError("Timeout must be finite and positive")
        return number

    def profile_uuid(value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError:
            raise argparse.ArgumentTypeError("Profile identity must be a UUID") from None

    for command, action, help_text in (
        ("profile-jobs", "jobs", "Inspect owned prepared-profile jobs"),
        ("process-profiles", "process", "Resume owned profiles without new model calls"),
        ("resume-profile", "resume", "Resume one owned prepared profile"),
        ("discard-profile", "discard", "Abandon one unpublished profile; preserve sources and journal"),
        ("collect-profile-staging", "collect", "Reclaim uniquely owned abandoned profile indexes"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        add_common(sub)
        sub.add_argument("--user-id", required=True, type=owner, help="Profile owner")
        if action in {"resume", "discard"}:
            sub.add_argument("profile_id", type=profile_uuid, help="Prepared profile UUID")
        else:
            sub.add_argument("--scope", choices=[s.value for s in Scope], help="Omit to visit every scope for this owner")
            sub.add_argument("--limit", type=work_limit, default=100, help="Maximum preparations (1–1000)")
        if action == "jobs":
            sub.add_argument("--status", choices=["pending", "complete", "abandoned"], default="pending")
        if action in {"process", "collect"}:
            sub.add_argument("--budget-ms", type=work_budget, default=5000, help="Cooperative budget checked between preparations")
        sub.set_defaults(func=cmd_profiles, action=action)

    # info
    for command, action, help_text in (
        ("extraction-status", "status", "Inspect owned extraction progress"),
        ("retry-extraction", "retry", "Queue an owned extraction retry without calling a model"),
        ("process-extractions", "process", "Run due owned extraction jobs (may call a model)"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        add_common(sub)
        sub.add_argument("--user-id", required=True, help="Source owner; required for every extraction operation")
        if action == "process":
            sub.add_argument("--limit", type=int, default=100)
            sub.add_argument("--budget-ms", type=float, default=5000, help="Cooperative budget checked between jobs")
        else:
            sub.add_argument("event_id", help="Source event UUID")
        if action == "retry":
            sub.add_argument("--replan", action="store_true", help="Prepare a new revision from saved extraction")
        sub.set_defaults(func=cmd_extractions, action=action)

    p_reingest = subparsers.add_parser(
        "reingest", help="Re-derive one owner's stored memories under the present engine (may call a model)",
    )
    add_common(p_reingest)
    p_reingest.add_argument("--user-id", required=True, help="The owner whose memories are re-derived")
    p_reingest.add_argument("--scope", choices=[s.value for s in Scope], help="Omit to visit every scope for this owner")
    p_reingest.add_argument("--limit", type=int, help="The most memories to re-derive in this run")
    p_reingest.add_argument("--cursor", help="The cursor of an earlier run, to continue after it")
    p_reingest.add_argument("--dry-run", action="store_true", help="Count the memories and model requests; do no work")
    p_reingest.set_defaults(func=cmd_reingest)

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

    # observe
    p_observe = subparsers.add_parser(
        "observe",
        help="Remember what photos and videos showed: describe keyframes with a vision model and store them as events",
    )
    p_observe.add_argument("db_path", help=db_path_help)
    p_observe.add_argument("files", nargs="+", help="Image or video files to observe, in order")
    p_observe.add_argument("--user-id", required=True, type=owner, help="Owner of the observations")
    p_observe.add_argument(
        "--captured-at",
        help=(
            "Capture clock as an ISO 8601 datetime (a video's start), used for every file given; "
            "omit to read it from the photo's EXIF data, the video container or the file time"
        ),
    )
    p_observe.add_argument("--session-id", help="Session id for every observation (default: video:<file> or photo:<file>)")
    p_observe.add_argument("--scope", choices=[s.value for s in Scope], default=Scope.PERSONAL.value,
                           help="Scope of the observations (default: personal)")
    p_observe.add_argument("--model", help="Vision model (default: PRME_VISION_MODEL or qwen3.5:9b)")
    p_observe.add_argument("--base-url", help="Vision endpoint (default: PRME_VISION_BASE_URL, OLLAMA_HOST or the local server)")
    extract_group = p_observe.add_mutually_exclusive_group()
    extract_group.add_argument("--extract", dest="extract", action="store_true", default=None,
                               help="Admit descriptions with ingest() and extraction (default: PRME_VISION_EXTRACT, on)")
    extract_group.add_argument("--no-extract", dest="extract", action="store_false",
                               help="Admit descriptions with store(): one note per keyframe, no model extraction")
    p_observe.add_argument("--no-thumbnails", action="store_true", help="Keep no thumbnails in the pack")
    p_observe.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table); json includes every keyframe decision and its score",
    )
    p_observe.set_defaults(func=cmd_observe)

    # organize
    p_organize = subparsers.add_parser("organize", help="Run the organizer")
    add_common(p_organize)
    p_organize.add_argument(
        "--user-id",
        help=(
            "Scope every job to one user. Multi-tenant stores should run "
            "once per tenant; an unscoped run maintains the whole store"
        ),
    )
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

    # rebuild
    p_rebuild = subparsers.add_parser(
        "rebuild",
        help="Rebuild vector and lexical indexes from the durable graph",
    )
    add_common(p_rebuild)
    p_rebuild.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Nodes fetched from the graph per page (default: 256)",
    )
    p_rebuild.set_defaults(func=cmd_rebuild)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show memory statistics")
    add_common(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    # export
    p_export = subparsers.add_parser("export", help="Export memory pack as JSON")
    p_export.add_argument("db_path", help=db_path_help)
    p_export.set_defaults(func=cmd_export)

    # compact
    p_compact = subparsers.add_parser(
        "compact",
        help="Rewrite a closed pack file into a fresh copy that holds only live data",
    )
    p_compact.add_argument(
        "pack",
        help="Single-file pack (.prme) or directory pack DuckDB file (memory.duckdb) to compact",
    )
    p_compact.add_argument("--encryption-key", help=encryption_key_help)
    p_compact.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_compact.set_defaults(func=cmd_compact)

    # migrate
    p_migrate = subparsers.add_parser(
        "migrate",
        help="Copy a directory pack into a new single-file pack, leaving the directory in place",
    )
    p_migrate.add_argument("directory", help="Memory directory to copy")
    p_migrate.add_argument("pack", help="New single-file pack to create; must end in .prme and not exist")
    p_migrate.add_argument(
        "--encryption-key",
        help=(
            "Key of an encrypted directory pack, in any form PRME_ENCRYPTION_KEY takes; "
            "the new pack is encrypted with it whenever a key is given "
            "(default: PRME_ENCRYPTION_KEY when PRME_ENCRYPTION_ENABLED is true)"
        ),
    )
    p_migrate.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_migrate.set_defaults(func=cmd_migrate)

    # init
    p_init = subparsers.add_parser(
        "init", help="Initialize a new memory directory or single-file pack"
    )
    p_init.add_argument(
        "directory",
        nargs="?",
        default=".",
        metavar="path",
        help=(
            "Directory to initialize, or a single-file pack to create when the "
            "path ends in .prme (default: current directory)"
        ),
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
        metavar="path",
        help=(
            "Memory directory, or single-file pack ending in .prme, to check "
            "(default: current directory)"
        ),
    )
    p_doctor.add_argument("--encryption-key", help=encryption_key_help)
    p_doctor.add_argument(
        "--verify-extraction",
        action="store_true",
        help="Contact the configured model endpoint without generating or storing content",
    )
    p_doctor.add_argument(
        "--provider-timeout",
        type=positive_timeout,
        default=10.0,
        help="Seconds to wait for --verify-extraction (default: 10)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    # config-audit
    p_config_audit = subparsers.add_parser(
        "config-audit",
        help="Inspect hypothesis-tagged configuration policies",
    )
    p_config_audit.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_config_audit.set_defaults(func=cmd_config_audit)

    from prme.privacy.cli import register as register_privacy_keys
    register_privacy_keys(subparsers)
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

    # Keep command output, including --format json, usable by pipes. Preserve
    # configured processors/levels while directing library diagnostics to stderr.
    import structlog
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

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
