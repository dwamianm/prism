# MCP Server

PRME includes a Model Context Protocol (MCP) server that lets any MCP-compatible client use PRME as a memory backend. This includes Claude Desktop, Cursor, Claude Code, and other MCP clients.

## Installation

```bash
pip install prme[mcp]
```

## Running the Server

```bash
prme-mcp --db-path ./my_memories
```

Or as a Python module:

```bash
python -m prme.mcp --db-path ./my_memories
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--db-path` | Current directory | Path to the memory directory |
| `--transport` | `stdio` | Transport protocol: `stdio` or `sse` |

## Client Configuration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "prme": {
      "command": "prme-mcp",
      "args": ["--db-path", "/path/to/my/memories"]
    }
  }
}
```

### Claude Code

Add to your project's `.mcp.json` or global MCP settings:

```json
{
  "mcpServers": {
    "prme": {
      "command": "prme-mcp",
      "args": ["--db-path", "./memories"]
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "prme": {
      "command": "prme-mcp",
      "args": ["--db-path", "./memories"]
    }
  }
}
```

### Using uv (development)

If running from source:

```json
{
  "mcpServers": {
    "prme": {
      "command": "uv",
      "args": ["--directory", "/path/to/prism", "run", "prme-mcp", "--db-path", "./memories"]
    }
  }
}
```

### Environment Variables

Pass configuration via environment variables:

```json
{
  "mcpServers": {
    "prme": {
      "command": "prme-mcp",
      "args": ["--db-path", "./memories"],
      "env": {
        "PRME_EXTRACTION__PROVIDER": "openai",
        "PRME_EXTRACTION__MODEL": "gpt-4o-mini",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

## Available Tools

The MCP server exposes 7 tools that LLMs can call:

### memory_store

Store a memory node with vector embedding and full-text indexing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | required | Text content to store |
| `user_id` | string | required | Owner of this memory |
| `node_type` | string | `"note"` | One of: entity, fact, decision, preference, task, instruction, summary, note |
| `scope` | string | `"personal"` | One of: personal, project, organisation |

Returns JSON: `{"event_id": "...", "node_id": "..."}`

### memory_retrieve

Search memories using 6-signal hybrid retrieval.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Natural language search query |
| `user_id` | string | required | User whose memories to search |
| `scope` | string | optional | Filter by scope |
| `knowledge_at` | string | optional | ISO datetime for point-in-time queries |

Returns JSON: `{"results": [...], "count": N}`

### memory_ingest

Ingest content with LLM-powered entity/fact extraction. Requires an LLM API key.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | required | Text to ingest |
| `user_id` | string | required | Owner |
| `role` | string | `"user"` | Speaker role |
| `scope` | string | `"personal"` | Scope |

Returns JSON: `{"event_id": "..."}`

### memory_organize

Run maintenance jobs (promotion, decay, deduplication, etc.).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_id` | string | optional | Scope to user |
| `jobs` | string | optional | Comma-separated job names |
| `budget_ms` | integer | `5000` | Time budget in ms |

Returns JSON: `{"jobs_run": [...], "duration_ms": N}`

### memory_get_node

Get full details of a memory node by UUID.

| Parameter | Type | Description |
|-----------|------|-------------|
| `node_id` | string | UUID of the node |

Returns JSON with all node fields, or `{"error": "..."}`.

### memory_promote_node

Promote a node's lifecycle state (tentative to stable).

| Parameter | Type | Description |
|-----------|------|-------------|
| `node_id` | string | UUID of the node |

Returns the updated node as JSON.

### memory_archive_node

Archive a node (terminal state, excluded from retrieval).

| Parameter | Type | Description |
|-----------|------|-------------|
| `node_id` | string | UUID of the node |

Returns the updated node as JSON.

## Available Resources

Resources provide read-only data access:

| URI | Description |
|-----|-------------|
| `memory://health` | Engine health status and version |
| `memory://stats` | Node count, backend type, version |
| `memory://nodes/{node_id}` | Get a specific node by ID |

## How It Works

The MCP server wraps the PRME `MemoryEngine` directly. When the server starts:

1. It reads configuration from environment variables (`PRME_*` prefix)
2. The `--db-path` flag sets `PRME_DB_PATH`, `PRME_VECTOR_PATH`, and `PRME_LEXICAL_PATH`
3. A `MemoryEngine` is initialized with the configuration
4. Tools and resources delegate to engine methods
5. On shutdown, the engine is closed cleanly

The server uses the `stdio` transport by default — the client launches it as a subprocess and communicates via stdin/stdout.
