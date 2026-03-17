# Deployment Guide

## DuckDB (Default)

The default backend stores everything in a single directory:

```
my_memories/
  memory.duckdb       # Events + graph (DuckDB)
  vectors.usearch     # HNSW vector index (usearch)
  lexical_index/      # Full-text search (Tantivy)
```

This directory is portable — you can copy, backup, or encrypt it as a single unit.

### Initialization

```bash
prme init ./my_memories
```

Or programmatically:

```python
from prme import MemoryClient

with MemoryClient("./my_memories") as client:
    # Directory created automatically
    pass
```

### Backup

Copy the entire directory while the engine is closed:

```bash
cp -r ./my_memories ./my_memories_backup
```

Or export as JSON:

```bash
prme export ./my_memories/memory.duckdb > backup.json
```

## PostgreSQL Backend

For production deployments with concurrent access, use PostgreSQL with pgvector.

### Prerequisites

```bash
pip install prme[postgres]
```

PostgreSQL must have the `pgvector` extension installed:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Configuration

```bash
export PRME_DATABASE_URL=postgresql://user:pass@localhost:5432/prme
```

Or programmatically:

```python
from prme.config import PRMEConfig

config = PRMEConfig(database_url="postgresql://user:pass@localhost:5432/prme")
```

When `database_url` is set, PRME uses PostgreSQL for events, graph, and vectors (via pgvector). Full-text search uses PostgreSQL's built-in capabilities.

### Schema

Tables are created automatically on first connection. The PostgreSQL backend provides:

- Connection pooling via asyncpg
- Native concurrent write support (no write queue needed)
- pgvector for vector similarity search
- PostgreSQL full-text search

## Encryption at Rest

PRME can encrypt the memory pack at rest using Fernet (AES-128-CBC + HMAC-SHA256) with PBKDF2 key derivation.

### Enabling Encryption

```bash
export PRME_ENCRYPTION_ENABLED=true
export PRME_ENCRYPTION_KEY=my-secret-passphrase
```

Or with a pre-generated Fernet key:

```bash
export PRME_ENCRYPTION_KEY=ZmVybmV0LWtleS1oZXJlLTQ0LWNoYXJzLWJhc2U2NA==
```

### How It Works

1. **On startup** (`MemoryEngine.create()`): Decrypts `.enc` files in the memory directory
2. **During operation**: Data is unencrypted in memory and on disk
3. **On shutdown** (`engine.close()`): Encrypts database, vector index, and lexical index files

Encrypted files have the `.enc` extension. A `manifest.json` records encryption metadata.

### Key Derivation

If the encryption key is a passphrase (not a 44-char Fernet key), PRME derives the key using:

- Algorithm: PBKDF2-HMAC-SHA256
- Iterations: 600,000
- Salt: randomly generated, stored in the encrypted file header

### Lock/Unlock

You can encrypt/decrypt without closing the engine:

```python
engine.lock()    # Encrypt memory pack
engine.unlock()  # Decrypt memory pack
```

## Production Checklist

### Performance

- **DuckDB**: Single-writer. Use the write queue (enabled by default). For multi-process, use PostgreSQL.
- **Embeddings**: The default `fastembed` provider runs locally. For production, consider `openai` embeddings for better quality, or keep `fastembed` for zero-dependency local operation.
- **Organizer**: Opportunistic maintenance runs during retrieve/ingest with a 200ms budget. Increase `PRME_ORGANIZER__OPPORTUNISTIC_BUDGET_MS` for larger memory stores, or disable with `PRME_ORGANIZER__OPPORTUNISTIC_ENABLED=false` and run `organize()` on a schedule.

### Monitoring

- Use `prme doctor ./memories` to check health
- Use `prme stats ./memories/memory.duckdb` for metrics
- The HTTP API exposes `/v1/health` and `/v1/stats` endpoints
- Watch `materialization_debt` — a growing debt means ingestion outpaces materialization

### Scaling

| Scale | Recommendation |
|-------|---------------|
| < 10K nodes | DuckDB is fine |
| 10K - 100K nodes | DuckDB with tuned organizer budgets |
| 100K+ nodes | PostgreSQL backend |
| Multi-process | PostgreSQL backend |

### Security

- Set `PRME_ENCRYPTION_ENABLED=true` for data at rest
- The HTTP API enables CORS `*` by default — restrict `allow_origins` in production
- The MCP server runs over stdio (no network exposure) by default
- Never commit `.env` files with API keys to version control

## Docker

Example `Dockerfile`:

```dockerfile
FROM python:3.11-slim

RUN pip install prme[api]

ENV PRME_DB_PATH=/data/memory.duckdb
ENV PRME_VECTOR_PATH=/data/vectors.usearch
ENV PRME_LEXICAL_PATH=/data/lexical_index

VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "prme.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t prme .
docker run -v ./memories:/data -p 8000:8000 prme
```
