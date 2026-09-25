# HTTP API Reference

PRME includes a FastAPI-based REST API for language-agnostic integration.

## Installation

```bash
pip install prme[api]
```

## Running the Server

```bash
python -m prme.api
```

Or with custom host/port:

```bash
python -m prme.api --host 127.0.0.1 --port 8000
```

The server binds to `127.0.0.1` (loopback only) by default, and with no credentials configured it serves single-user local use without authentication. A non-loopback bind such as `0.0.0.0` exposes the API to the network, so `python -m prme.api` refuses to start there unless API credentials are configured (see [Authentication](#authentication)). `prme.api.server.run_server` does the same by raising `prme.api.server.UnauthenticatedBindError`, a `ValueError`. The check runs before the engine starts or a port is opened. Only literal loopback addresses (anything in `127.0.0.0/8`, `::1`, and IPv4-mapped forms such as `::ffff:127.0.0.1`) and the name `localhost` (when it resolves only to loopback addresses) count as loopback. Any other host name counts as a network address, because the server resolves it again when it binds.

If an authenticating reverse proxy is the only way to reach the server and it must run without its own credentials, pass `--allow-unauthenticated-external-bind` (`allow_unauthenticated_external_bind=True` for `run_server`). The server then starts with a warning. The API accepts every request it receives and binds no identity to it, so every caller that gets through the proxy has operator access to every user's memories and can name any `user_id`. Make sure nothing but the proxy can reach that port, and remove the flag if the proxy goes away. When the proxy fronts several users, keep `PRME_API_USER_KEYS` configured and have the proxy send each user's key instead.

Running the app under another ASGI server (`uvicorn prme.api.app:create_app --factory`, Gunicorn, Hypercorn or your own process) skips this check, because only the server that opens the socket knows the bind address. Protect those deployments yourself: configure credentials, or bind the server to loopback behind an authenticating proxy.

The API reads configuration from environment variables (`PRME_*` prefix). Set `PRME_DB_PATH`, `PRME_VECTOR_PATH`, and `PRME_LEXICAL_PATH` to point at your memory directory.

## Authentication

Authentication is disabled when no credentials are configured, which is only suitable for single-user use on loopback. Configure one of these to require a bearer token on every `/v1` endpoint except `/v1/health`:

- Per-user keys, which bind each request to its owner (recommended for shared deployments). A caller can then only reach its own owner's memories, and naming another `user_id` returns 403:

  ```bash
  export PRME_API_USER_KEYS='{"alice": "<alice key>", "bob": "<bob key>"}'
  ```

- A single global key with operator access, for example for one backend service that acts for many users:

  ```bash
  export PRME_API_API_KEY="<operator key>"
  ```

The two cannot be combined. Generate each key rather than reusing an example value, for instance with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, and keep keys out of version control (use an untracked `.env` file or a secret store). Clients send their own key:

```
Authorization: Bearer <key>
```

Requests with a missing or wrong key receive `401 {"detail": "Invalid or missing API key"}`. Bearer keys travel in plain text over HTTP, so terminate TLS in front of the API whenever requests cross a network.

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

An optional `min_score` in the request drops results below an inclusive floor.
With weighted scoring (`PRME_SCORING__FUSION=weighted`) it compares against
`score`. With rank fusion (`PRME_SCORING__FUSION=rrf`, the default), `score`
says where a result ranks among the candidates, not how relevant it is, so the
floor compares against `semantic_relevance` instead. Each result then carries `semantic_relevance`: the
semantic cosine similarity of the memory behind it. A floor tuned on weighted
scores does not carry over, and when a floor is set, a result with a low cosine
is filtered out even if it is an exact keyword match. If vector search is
failing, or the embedding model changed and the index has not been rebuilt yet,
no result has a cosine to compare. The floor is then skipped rather than
returning nothing: results keep their ranked order, `metrics.backend_failures`
names the vector failure, and `metrics.min_score_skipped` is `true`. Treat that
flag as a sign that the floor did not filter anything. `min_score: 0` keeps
everything in both modes.

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

System statistics. Node counting uses `COUNT(*)` (no rows are materialized).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_id` | string | | Scope the node count to one user |

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

CORS is disabled by default — browser pages cannot read API responses cross-origin. To allow specific origins:

```bash
export PRME_API_CORS_ORIGINS='["http://localhost:3000"]'
export PRME_API_CORS_ALLOW_CREDENTIALS=true   # optional
```

Credentialed CORS is only honored when origins are explicitly pinned; a wildcard (`*`) origin never allows credentials.

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error description"
}
```

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key (when auth is enabled) |
| 404 | Node/resource not found |
| 422 | Invalid input (bad enum value, invalid state transition) |
| 500 | Unexpected internal error (details logged server-side only) |
| 503 | Engine not initialized |
