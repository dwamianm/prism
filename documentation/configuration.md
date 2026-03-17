# Configuration Reference

PRME uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) for configuration. All settings can be set via:

1. Constructor arguments
2. Environment variables (with `PRME_` prefix)
3. `.env` files

Nested settings use double underscores: `PRME_EMBEDDING__PROVIDER=openai`.

## Quick Setup

Minimal `.env` for LLM-powered ingestion:

```bash
# Required for ingest() — pick one provider
OPENAI_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...

# Optional: extraction model
PRME_EXTRACTION__PROVIDER=openai
PRME_EXTRACTION__MODEL=gpt-4o-mini
```

For store-only usage (no LLM), no configuration is needed.

## Storage Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_DB_PATH` | `./memory.duckdb` | DuckDB database file |
| `PRME_VECTOR_PATH` | `./vectors.usearch` | usearch HNSW index file |
| `PRME_LEXICAL_PATH` | `./lexical_index` | Tantivy full-text index directory |
| `PRME_DATABASE_URL` | `None` | PostgreSQL URL (overrides DuckDB) |

## Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_EMBEDDING__PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `PRME_EMBEDDING__MODEL_NAME` | `BAAI/bge-small-en-v1.5` | Embedding model |
| `PRME_EMBEDDING__DIMENSION` | `384` | Embedding dimension |
| `PRME_EMBEDDING__API_KEY` | `None` | API key for OpenAI embeddings |

The default `fastembed` provider runs locally with no API key needed. It downloads the model on first use (~130MB).

## Extraction (LLM)

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_EXTRACTION__PROVIDER` | `openai` | `openai`, `anthropic`, or `ollama` |
| `PRME_EXTRACTION__MODEL` | `gpt-4o-mini` | Model name |
| `PRME_EXTRACTION__MAX_RETRIES` | `3` | Retry count for API failures |
| `PRME_EXTRACTION__TIMEOUT` | `30.0` | Timeout in seconds |

The extraction provider is only used by `ingest()` and `ingest_batch()`. The `store()` method does not call an LLM.

## Scoring Weights

These control the hybrid retrieval scoring formula. They should sum to approximately 1.0.

| Variable | Default | Signal |
|----------|---------|--------|
| `PRME_SCORING__W_SEMANTIC` | `0.25` | Vector cosine similarity |
| `PRME_SCORING__W_LEXICAL` | `0.20` | BM25 full-text score |
| `PRME_SCORING__W_GRAPH` | `0.20` | Graph proximity |
| `PRME_SCORING__W_RECENCY` | `0.10` | Time decay: exp(-lambda * days) |
| `PRME_SCORING__W_SALIENCE` | `0.10` | Virtual salience with decay |
| `PRME_SCORING__W_CONFIDENCE` | `0.15` | Virtual confidence with decay |
| `PRME_SCORING__W_EPISTEMIC` | `0.05` | Epistemic type multiplier |
| `PRME_SCORING__W_PATHS` | `0.00` | Path count tiebreaker |
| `PRME_SCORING__RECENCY_LAMBDA` | `0.02` | Decay rate for recency |
| `PRME_SCORING__TEMPORAL_BOOST` | `0.15` | Bonus for temporal queries |

## Context Packing

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_PACKING__TOKEN_BUDGET` | `4096` | Max tokens in packed context |
| `PRME_PACKING__OVERHEAD_TOKENS` | `100` | Reserved for formatting overhead |
| `PRME_PACKING__CHARS_PER_TOKEN` | `4.2` | Estimated chars per token |
| `PRME_PACKING__VECTOR_K` | `250` | Vector search candidates |
| `PRME_PACKING__LEXICAL_K` | `250` | Lexical search candidates |
| `PRME_PACKING__GRAPH_MAX_CANDIDATES` | `150` | Graph search candidates |
| `PRME_PACKING__GRAPH_MAX_HOPS` | `3` | Max graph traversal depth |
| `PRME_PACKING__SESSION_CONTEXT_WINDOW` | `3` | Adjacent turns to include |
| `PRME_PACKING__SESSION_CONTEXT_TOP_K` | `20` | Top results for session expansion |
| `PRME_PACKING__AGGREGATION_K_MULTIPLIER` | `2.5` | Multiplier for aggregation queries |
| `PRME_PACKING__AGGREGATION_K_MAX` | `500` | Max candidates for aggregation |

## Organizer

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ORGANIZER__OPPORTUNISTIC_ENABLED` | `true` | Run maintenance during retrieve/ingest |
| `PRME_ORGANIZER__OPPORTUNISTIC_COOLDOWN` | `3600` | Seconds between opportunistic runs |
| `PRME_ORGANIZER__OPPORTUNISTIC_BUDGET_MS` | `200` | Budget for opportunistic runs |
| `PRME_ORGANIZER__DEFAULT_ORGANIZE_BUDGET_MS` | `5000` | Default budget for explicit organize() |
| `PRME_ORGANIZER__PROMOTION_AGE_DAYS` | `7.0` | Days before auto-promotion |
| `PRME_ORGANIZER__PROMOTION_EVIDENCE_COUNT` | `1` | Min evidence refs for promotion |
| `PRME_ORGANIZER__ARCHIVE_SALIENCE_THRESHOLD` | `0.10` | Salience below this → archive candidate |
| `PRME_ORGANIZER__ARCHIVE_CONFIDENCE_THRESHOLD` | `0.40` | Confidence below this → archive candidate |
| `PRME_ORGANIZER__DEDUP_SIMILARITY_THRESHOLD` | `0.92` | Vector similarity for dedup |
| `PRME_ORGANIZER__ALIAS_SIMILARITY_THRESHOLD` | `0.85` | Similarity for entity alias detection |
| `PRME_ORGANIZER__CONSOLIDATION_MIN_CLUSTER_SIZE` | `3` | Min cluster size for consolidation |
| `PRME_ORGANIZER__CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.80` | Similarity for consolidation |

## Optional Features

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ENABLE_STORE_SUPERSEDENCE` | `false` | Auto-detect supersedence on store |
| `PRME_REINFORCE_SIMILARITY_THRESHOLD` | `None` | Auto-reinforce similar existing nodes |
| `PRME_ENABLE_SURPRISE_GATING` | `false` | Novelty-based salience adjustment |

## Encryption

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_ENCRYPTION_ENABLED` | `false` | Enable encryption at rest |
| `PRME_ENCRYPTION_KEY` | `None` | Passphrase or Fernet key |

See [Deployment](deployment.md#encryption-at-rest) for details.

## Write Queue

| Variable | Default | Description |
|----------|---------|-------------|
| `PRME_WRITE_QUEUE_SIZE` | `1000` | DuckDB write queue size |
| `PRME_MATERIALIZATION_QUEUE_SIZE` | `500` | Graph materialization queue |
| `PRME_MATERIALIZATION_BUDGET_MS` | `100` | Budget per materialization batch |

## Programmatic Configuration

```python
from prme.config import PRMEConfig

config = PRMEConfig(
    db_path="./custom.duckdb",
    vector_path="./custom.usearch",
    lexical_path="./custom_lexical",
    extraction={"provider": "anthropic", "model": "claude-sonnet-4-5-20250514"},
    embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5"},
    scoring={"w_semantic": 0.30, "w_lexical": 0.15},
)

from prme import MemoryClient
client = MemoryClient(config=config)
```
