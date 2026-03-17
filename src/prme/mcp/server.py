"""PRME MCP Server — tools and resources for memory operations.

All tools are thin wrappers around MemoryEngine methods.
No business logic belongs here — delegate everything to the engine.
"""

import argparse
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP

from prme.config import PRMEConfig
from prme.types import LifecycleState, NodeType, Scope

logger = logging.getLogger(__name__)

# Module-level engine reference for resources (which don't get Context).
_engine_ref: Any = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a MemoryNode to a JSON-serializable dict."""
    return {
        "id": str(node.id),
        "user_id": node.user_id,
        "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        "content": node.content,
        "lifecycle_state": node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
        "confidence": node.confidence,
        "salience": node.salience,
        "epistemic_type": node.epistemic_type.value if node.epistemic_type and hasattr(node.epistemic_type, "value") else None,
        "source_type": node.source_type.value if node.source_type and hasattr(node.source_type, "value") else None,
        "scope": node.scope.value if hasattr(node.scope, "value") else str(node.scope),
        "metadata": node.metadata,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "superseded_by": str(node.superseded_by) if node.superseded_by else None,
        "evidence_refs": [str(r) for r in node.evidence_refs],
        "pinned": node.pinned,
    }


def _get_engine(ctx: Context) -> Any:
    """Extract the MemoryEngine from MCP lifespan context."""
    return ctx.request_context.lifespan_context["engine"]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def engine_lifespan(server: FastMCP):
    """Manage MemoryEngine lifecycle for the MCP server."""
    global _engine_ref
    from prme.storage.engine import MemoryEngine

    config = PRMEConfig()
    logger.info("Starting PRME MemoryEngine (backend=%s)...", config.backend)
    engine = await MemoryEngine.create(config)
    _engine_ref = engine
    logger.info("PRME MemoryEngine ready")

    try:
        yield {"engine": engine}
    finally:
        logger.info("Shutting down PRME MemoryEngine...")
        _engine_ref = None
        await engine.close()
        logger.info("PRME MemoryEngine shut down")


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "prme",
    description="PRME — Portable Relational Memory Engine. "
    "Store, retrieve, and organize long-term memory for AI agents.",
    lifespan=engine_lifespan,
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def memory_store(
    content: str,
    user_id: str,
    node_type: str = "note",
    scope: str = "personal",
    ctx: Context = None,
) -> str:
    """Store a memory.

    Stores content as a typed memory node with vector embedding and
    full-text indexing. Returns the event ID and node ID.

    Args:
        content: The text content to store as a memory.
        user_id: User who owns this memory.
        node_type: Type of memory node. One of: entity, fact, decision,
            preference, task, instruction, summary, note. Default: note.
        scope: Memory scope. One of: personal, project, organisation. Default: personal.
    """
    engine = _get_engine(ctx)

    try:
        nt = NodeType(node_type)
    except ValueError:
        return json.dumps({"error": f"Invalid node_type: {node_type!r}. Valid: {[e.value for e in NodeType]}"})

    try:
        sc = Scope(scope)
    except ValueError:
        return json.dumps({"error": f"Invalid scope: {scope!r}. Valid: {[e.value for e in Scope]}"})

    meta = None

    try:
        event_id = await engine.store(
            content,
            user_id=user_id,
            node_type=nt,
            scope=sc,
            metadata=meta,
        )

        # Try to find the created node
        node_id = None
        try:
            nodes = await engine.query_nodes(user_id=user_id, limit=1)
            if nodes:
                latest = max(nodes, key=lambda n: n.created_at)
                node_id = str(latest.id)
        except Exception:
            pass

        return json.dumps({"event_id": event_id, "node_id": node_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_retrieve(
    query: str,
    user_id: str,
    scope: Optional[str] = None,
    knowledge_at: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Search memories using hybrid retrieval.

    Runs the full hybrid retrieval pipeline: semantic similarity,
    lexical search, graph proximity, recency, salience, and confidence
    scoring. Returns the top matching memories ranked by composite score.

    Args:
        query: Natural language search query.
        user_id: User whose memories to search.
        scope: Optional scope filter (personal, project, organisation).
        knowledge_at: Optional ISO datetime for point-in-time retrieval
            (e.g. "2024-06-15T00:00:00" to see what was known at that time).
    """
    engine = _get_engine(ctx)

    kwargs: dict[str, Any] = {
        "query": query,
        "user_id": user_id,
    }

    if scope:
        try:
            kwargs["scope"] = Scope(scope)
        except ValueError:
            return json.dumps({"error": f"Invalid scope: {scope!r}"})

    if knowledge_at:
        try:
            kwargs["knowledge_at"] = datetime.fromisoformat(knowledge_at)
        except ValueError:
            return json.dumps({"error": f"Invalid knowledge_at datetime: {knowledge_at!r}"})

    try:
        response = await engine.retrieve(**kwargs)

        results = []
        for candidate in response.results:
            node = candidate.node
            results.append({
                "node_id": str(node.id),
                "content": node.content,
                "score": round(candidate.composite_score, 4),
                "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                "lifecycle_state": node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
                "confidence": node.confidence,
            })

        return json.dumps({
            "results": results,
            "count": len(results),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_ingest(
    content: str,
    user_id: str,
    role: str = "user",
    scope: str = "personal",
    ctx: Context = None,
) -> str:
    """Ingest content with LLM-powered extraction.

    Processes content through the full LLM extraction pipeline to
    automatically identify entities, facts, relationships, preferences,
    and decisions. Requires an LLM API key (OpenAI, Anthropic, or Ollama).

    Args:
        content: The text content to ingest (e.g. a conversation message).
        user_id: User who owns this memory.
        role: Role of the speaker (user or assistant). Default: user.
        scope: Memory scope. One of: personal, project, organisation. Default: personal.
    """
    engine = _get_engine(ctx)

    try:
        sc = Scope(scope)
    except ValueError:
        return json.dumps({"error": f"Invalid scope: {scope!r}"})

    try:
        event_id = await engine.ingest(
            content,
            user_id=user_id,
            role=role,
            scope=sc,
        )
        return json.dumps({"event_id": event_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_organize(
    user_id: Optional[str] = None,
    jobs: Optional[str] = None,
    budget_ms: int = 5000,
    ctx: Context = None,
) -> str:
    """Run memory organization jobs.

    Executes background maintenance: promotion, decay, deduplication,
    summarization, consolidation, and archival. Can target specific
    jobs or run all.

    Args:
        user_id: Optional user to scope jobs to.
        jobs: Optional comma-separated list of jobs to run (e.g.
            "promote,decay_sweep,deduplicate"). Omit to run all.
        budget_ms: Time budget in milliseconds. Default: 5000.
    """
    engine = _get_engine(ctx)

    kwargs: dict[str, Any] = {"budget_ms": budget_ms}
    if user_id:
        kwargs["user_id"] = user_id
    if jobs:
        kwargs["jobs"] = [j.strip() for j in jobs.split(",")]

    try:
        result = await engine.organize(**kwargs)
        return json.dumps({
            "jobs_run": result.jobs_run,
            "duration_ms": result.duration_ms,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_get_node(
    node_id: str,
    ctx: Context = None,
) -> str:
    """Get a memory node by ID.

    Retrieves the full details of a specific memory node including
    content, type, lifecycle state, confidence, and metadata.

    Args:
        node_id: The UUID of the memory node.
    """
    engine = _get_engine(ctx)

    try:
        node = await engine.get_node(node_id, include_superseded=True)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})
        return json.dumps(_node_to_dict(node))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_promote_node(
    node_id: str,
    ctx: Context = None,
) -> str:
    """Promote a memory node's lifecycle state.

    Advances a tentative node to stable, indicating the memory has
    been confirmed or reinforced.

    Args:
        node_id: The UUID of the node to promote.
    """
    engine = _get_engine(ctx)

    try:
        node = await engine.get_node(node_id)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})

        await engine.promote(node_id)

        updated = await engine.get_node(node_id, include_superseded=True)
        if updated is None:
            return json.dumps({"error": f"Node {node_id!r} not found after promote"})
        return json.dumps(_node_to_dict(updated))
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def memory_archive_node(
    node_id: str,
    ctx: Context = None,
) -> str:
    """Archive a memory node.

    Moves a node to the archived terminal state. Archived nodes are
    excluded from default retrieval but remain in the event log.

    Args:
        node_id: The UUID of the node to archive.
    """
    engine = _get_engine(ctx)

    try:
        node = await engine.get_node(node_id, include_superseded=True)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})

        await engine.archive(node_id)

        updated = await engine.get_node(node_id, include_superseded=True)
        if updated is None:
            return json.dumps({"error": f"Node {node_id!r} not found after archive"})
        return json.dumps(_node_to_dict(updated))
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("memory://health")
async def resource_health() -> str:
    """PRME engine health status."""
    return json.dumps({"status": "ok", "version": "0.4.0"})


@mcp.resource("memory://stats")
async def resource_stats() -> str:
    """Memory database statistics — node count, backend type."""
    engine = _engine_ref
    if engine is None:
        return json.dumps({"error": "Engine not initialized"})

    node_count = 0
    backend = "duckdb"
    try:
        nodes = await engine.query_nodes(limit=10000)
        node_count = len(nodes)
    except Exception:
        pass

    try:
        backend = engine._config.backend
    except Exception:
        pass

    return json.dumps({
        "node_count": node_count,
        "backend": backend,
        "version": "0.4.0",
    })


@mcp.resource("memory://nodes/{node_id}")
async def resource_node(node_id: str) -> str:
    """Get a specific memory node by ID."""
    engine = _engine_ref
    if engine is None:
        return json.dumps({"error": "Engine not initialized"})

    try:
        node = await engine.get_node(node_id, include_superseded=True)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})
        return json.dumps(_node_to_dict(node))
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    """Run the PRME MCP server."""
    parser = argparse.ArgumentParser(
        prog="prme-mcp",
        description="PRME MCP Server — memory backend for MCP-compatible clients",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the memory directory (default: current directory)",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args()

    # Configure PRME paths from --db-path
    if args.db_path:
        db_dir = os.path.abspath(args.db_path)
        os.makedirs(db_dir, exist_ok=True)
        lexical_dir = os.path.join(db_dir, "lexical_index")
        os.makedirs(lexical_dir, exist_ok=True)
        os.environ["PRME_DB_PATH"] = os.path.join(db_dir, "memory.duckdb")
        os.environ["PRME_VECTOR_PATH"] = os.path.join(db_dir, "vectors.usearch")
        os.environ["PRME_LEXICAL_PATH"] = lexical_dir

    mcp.run(transport=args.transport)
