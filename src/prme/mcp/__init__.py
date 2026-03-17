"""PRME MCP Server — Model Context Protocol interface for PRME.

Exposes the PRME MemoryEngine as an MCP server so any MCP-compatible
client (Claude Desktop, Cursor, Claude Code, etc.) can use PRME as a
memory backend.

Install::

    pip install prme[mcp]

Run::

    prme-mcp --db-path ./my_memories
    # or
    python -m prme.mcp --db-path ./my_memories
"""

from prme.mcp.server import main, mcp

__all__ = ["main", "mcp"]
