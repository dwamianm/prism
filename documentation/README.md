# PRME Developer Documentation

Complete guide to integrating PRME (Portable Relational Memory Engine) into your application.

## Guides

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Installation, quickstart, and your first memory-powered app |
| [Core Concepts](concepts.md) | Node types, lifecycle states, scopes, epistemic model |
| [MemoryClient API](memory-client.md) | Synchronous Python SDK (recommended for most use cases) |
| [Async Engine API](async-engine.md) | Low-level async API for advanced use |
| [CLI Reference](cli.md) | Command-line tool for setup, inspection, and maintenance |
| [MCP Server](mcp-server.md) | Model Context Protocol server for Claude, Cursor, and other MCP clients |
| [HTTP API](http-api.md) | REST API for language-agnostic integration |
| [Configuration](configuration.md) | Environment variables, scoring weights, and tuning |
| [Deployment](deployment.md) | PostgreSQL backend, encryption at rest, production setup |

## Architecture Overview

```
Your Application
      |
      v
 MemoryClient (sync) or MemoryEngine (async)
      |
      v
 +-----------+-----------+-----------+
 | Ingestion | Retrieval | Organizer |
 | Pipeline  | Pipeline  | Jobs      |
 +-----------+-----------+-----------+
      |
      v
 +---------+---------+---------+
 | DuckDB  | usearch | Tantivy |
 | Events  | HNSW    | FTS     |
 | + Graph | Vectors | Search  |
 +---------+---------+---------+
   Optional: PostgreSQL backend
```

## Quick Links

- **Store a memory**: [`client.store()`](memory-client.md#store)
- **Search memories**: [`client.retrieve()`](memory-client.md#retrieve)
- **LLM extraction**: [`client.ingest()`](memory-client.md#ingest)
- **Run as MCP server**: [`prme-mcp --db-path ./memories`](mcp-server.md#running-the-server)
- **Run as HTTP API**: [`uvicorn prme.api:app`](http-api.md#running-the-server)
