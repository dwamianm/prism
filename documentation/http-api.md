# HTTP API Reference

PRME includes a FastAPI-based REST API for language-agnostic integration.

## Installation

```bash
pip install prme[api]
```

## Running the Server

```bash
uvicorn prme.api:app
```

Or with custom host/port:

```bash
uvicorn prme.api:app --host 0.0.0.0 --port 8000
```

The API reads configuration from environment variables (`PRME_*` prefix). Set `PRME_DB_PATH`, `PRME_VECTOR_PATH`, and `PRME_LEXICAL_PATH` to point at your memory directory.

## Endpoints

All endpoints are under the `/v1` prefix.

### POST /v1/store

Store a memory node.

**Request:**

```json
{
  "content": "Alice prefers dark mode.",
  "user_id": "alice",
  "role": "user",
  "node_type": "preference",
  "scope": "personal",
  "metadata": {"source": "chat"}
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `content` | string | yes | | Text content |
| `user_id` | string | yes | | Owner |
| `role` | string | no | `"user"` | Speaker role |
| `node_type` | string | no | `null` | Node type enum value |
| `scope` | string | no | `null` | Scope enum value |
| `epistemic_type` | string | no | `null` | Epistemic type |
| `metadata` | object | no | `null` | Arbitrary metadata |

**Response (200):**

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "node_id": "660e8400-e29b-41d4-a716-446655440000"
}
```

### POST /v1/ingest

Run the LLM extraction pipeline on content.

**Request:**

```json
{
  "content": "I switched from VS Code to Neovim last week.",
  "user_id": "alice",
  "role": "user",
  "scope": "personal"
}
```

**Response (200):**

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### POST /v1/retrieve

Run hybrid retrieval.

**Request:**

```json
{
  "query": "What editor does Alice use?",
  "user_id": "alice"
}
```

**Response (200):**

```json
{
  "results": [
    {
      "node_id": "660e8400-...",
      "content": "Alice switched to Neovim",
      "score": 0.847,
      "node_type": "preference",
      "lifecycle_state": "stable",
      "confidence": 0.85,
      "salience": 0.72,
      "epistemic_type": "asserted",
      "metadata": null
    }
  ],
  "bundle": { ... },
  "metrics": { ... }
}
```

### POST /v1/organize

Run organizer jobs.

**Request:**

```json
{
  "user_id": "alice",
  "jobs": ["promote", "deduplicate"],
  "budget_ms": 5000
}
```

All fields are optional. Omit `jobs` to run all.

**Response (200):**

```json
{
  "jobs_run": ["promote", "deduplicate"],
  "per_job": {
    "promote": {"nodes_processed": 5, "nodes_modified": 2},
    "deduplicate": {"nodes_processed": 10, "nodes_modified": 1}
  },
  "duration_ms": 342.5
}
```

### GET /v1/nodes/{node_id}

Get a single node by ID.

**Response (200):**

```json
{
  "id": "660e8400-...",
  "user_id": "alice",
  "node_type": "fact",
  "content": "Python 3.12 was released in October 2023.",
  "lifecycle_state": "stable",
  "confidence": 0.9,
  "salience": 0.65,
  "epistemic_type": "asserted",
  "source_type": "user_stated",
  "scope": "personal",
  "metadata": null,
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:30:00",
  "superseded_by": null,
  "evidence_refs": ["550e8400-..."],
  "pinned": false
}
```

**Response (404):** `{"detail": "Node '...' not found"}`

### GET /v1/nodes

Query nodes with filters.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | string | | Filter by node type |
| `state` | string | | Filter by lifecycle state |
| `user_id` | string | | Filter by user |
| `limit` | integer | 50 | Max results |

**Response (200):**

```json
{
  "nodes": [ ... ],
  "count": 15
}
```

### PUT /v1/nodes/{node_id}/promote

Promote a node from tentative to stable.

**Response (200):** Updated node object.

**Response (404):** Node not found.

**Response (422):** Invalid state transition.

### PUT /v1/nodes/{node_id}/archive

Archive a node.

**Response (200):** Updated node object.

### PUT /v1/nodes/{node_id}/reinforce

Reinforce a node (boost confidence and salience).

**Response (200):** Updated node object.

### GET /v1/nodes/{node_id}/neighborhood

Get nodes within N hops of a starting node.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_hops` | integer | 2 | Maximum graph hops |

**Response (200):**

```json
{
  "nodes": [ ... ],
  "count": 8
}
```

### GET /v1/nodes/{node_id}/chain

Get the supersedence chain for a node.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `direction` | string | `"forward"` | `"forward"` or `"backward"` |

**Response (200):**

```json
{
  "nodes": [ ... ],
  "count": 3
}
```

### GET /v1/health

Health check.

**Response (200):**

```json
{
  "status": "ok",
  "version": "0.4.0"
}
```

### GET /v1/stats

System statistics.

**Response (200):**

```json
{
  "node_count": 42,
  "event_count": 0,
  "backend": "duckdb",
  "details": {}
}
```

## CORS

CORS is enabled by default with `allow_origins=["*"]`. Suitable for development; restrict origins in production.

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error description"
}
```

| Status | Meaning |
|--------|---------|
| 404 | Node/resource not found |
| 422 | Invalid input (bad enum value, invalid state transition) |
| 503 | Engine not initialized |
