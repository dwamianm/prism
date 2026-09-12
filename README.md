# PRME — Portable Relational Memory Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/dwamianm/prism/actions/workflows/ci.yml/badge.svg)](https://github.com/dwamianm/prism/actions/workflows/ci.yml)

**Local-first, embeddable memory substrate for LLM-powered systems.**

PRME gives AI agents and chatbots stable long-term memory by combining an append-only event log, a graph-based relational model, hybrid retrieval (graph + vector + lexical), and epistemic state tracking — all in a portable, single-directory bundle.

## Retrieval quality and evaluation

PRME includes synthetic regression scenarios and LoCoMo/LongMemEval adapters.
There is **no validated current-release accuracy headline yet**. Historical runs
in `benchmarks/results/` used different configurations, including dataset-provided
observations and benchmark-only retrieval expansion. They are research artifacts,
not a reproducible measurement of the current product.

See [BENCHMARKS.md](BENCHMARKS.md) for the measurement contract, commands, and
remaining baseline work. The [roadmap](ROADMAP.md) prioritizes retrieval quality:
reliable evidence retrieval, complete aggregation, and correct temporal state.

## Why PRME?

LLMs are stateless. Every conversation starts from zero. Existing solutions bolt on vector search and call it "memory," but that misses the relational structure of how humans actually remember things — preferences override old ones, decisions have context, facts get corrected.

PRME models memory the way it actually works:

- **Event sourcing** — immutable append-only log, deterministic rebuild
- **Graph-based relational model** — 9 typed node kinds (entities, facts, preferences, decisions, tasks, instructions, summaries, events, notes) with edges capturing relationships, supersedence, and temporal validity
- **Epistemic state tracking** — memories have lifecycle states (tentative -> stable -> superseded -> archived), confidence scores, contradiction detection, and oscillation dampening
- **Hybrid retrieval** — semantic similarity + lexical search + graph proximity, scored and packed into a token-efficient context bundle
- **Self-organizing memory** — organizer jobs handle promotion, decay, deduplication, summarization, consolidation, and archival
- **Dual-stream ingestion** — sub-50ms fast path for real-time use, with deferred graph materialization
- **Local-first** — everything lives in a single directory (DuckDB + usearch + Tantivy). No cloud dependency. Optional PostgreSQL backend for production.

## Installation

```bash
pip install prme
```

With optional extras:

```bash
pip install prme[postgres]   # PostgreSQL backend
pip install prme[api]        # HTTP API (FastAPI)
```

### From source

```bash
git clone https://github.com/dwamianm/prism.git
cd prism
uv sync --dev
```

## Quickstart

```python
from prme import MemoryClient

with MemoryClient("./my_memories") as client:
    # Store memories (no LLM needed)
    client.store("Alice prefers dark mode in all her editors.", user_id="alice")
    client.store("The team decided to use PostgreSQL.", user_id="alice")

    # Retrieve with hybrid scoring
    response = client.retrieve("What are Alice's preferences?", user_id="alice")
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")
```

`MemoryClient` is a synchronous wrapper — no `async`/`await` needed. It works everywhere: scripts, notebooks, FastAPI apps.

<details>
<summary>Async API (advanced)</summary>

```python
import asyncio
from prme import MemoryEngine, PRMEConfig

async def main():
    config = PRMEConfig(
        db_path="./memory.duckdb",
        vector_path="./vectors.usearch",
        lexical_path="./lexical_index",
    )
    engine = await MemoryEngine.create(config)

    await engine.store("Alice prefers dark mode.", user_id="alice")
    response = await engine.retrieve("preferences?", user_id="alice")
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")

    await engine.close()

asyncio.run(main())
```

</details>

### LLM-Powered Ingestion

With an API key set, PRME can automatically extract entities, facts, and relationships from conversation text:

```python
# Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or configure Ollama
events = await engine.ingest_batch(
    [
        {"role": "user", "content": "I just switched to Neovim and love it."},
        {"role": "assistant", "content": "Great choice! The plugin ecosystem is excellent."},
    ],
    user_id="alice",
    session_id="session-1",
    scope=Scope.PERSONAL,
)
```

For real-time use, the fast path skips graph extraction:

```python
# Guaranteed sub-50ms — event store + vector only
await engine.ingest_fast(content, user_id="alice", scope=Scope.PERSONAL)
```

See [`examples/quickstart.py`](examples/quickstart.py) for a full walkthrough and [`examples/chat.py`](examples/chat.py) for a terminal chat app with persistent memory.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    PRME Engine                        │
├──────────────┬──────────────┬────────────┬───────────┤
│  Ingestion   │  Retrieval   │ Epistemic  │ Organizer │
│  Pipeline    │  Pipeline    │ State      │ Jobs      │
├──────────────┴──────────────┴────────────┴───────────┤
│                   Storage Layer                       │
│  ┌──────────┬───────────┬──────────────────────────┐ │
│  │ DuckDB   │ usearch   │ Tantivy                  │ │
│  │ Events + │ HNSW      │ Full-text                │ │
│  │ Graph    │ Vectors   │ Search                   │ │
│  └──────────┴───────────┴──────────────────────────┘ │
│           Optional: PostgreSQL backend                │
└──────────────────────────────────────────────────────┘
```

- **Ingestion Pipeline** — stores raw events, optionally extracts entities/facts/relationships via LLM (OpenAI, Anthropic, Ollama). Dual-stream mode provides a sub-50ms fast path with deferred graph materialization.
- **Retrieval Pipeline** — query analysis -> multi-source candidate generation -> deterministic scoring -> context packing. Supports bi-temporal queries with `knowledge_at` for point-in-time snapshots.
- **Epistemic State Model** — tracks confidence, lifecycle transitions (tentative -> stable -> superseded -> archived), contradiction detection, supersedence chains, oscillation dampening, and surprise-gated storage.
- **Organizer** — twelve registered jobs, including index compaction; `centrality_boost` is currently a stub. Explicit passes run through `prme organize`. Retrieve/ingest can schedule opportunistic in-process maintenance; there is no built-in cron or daemon scheduler.
- **Storage** — DuckDB (events + graph), usearch (HNSW vectors), Tantivy (full-text). Optional PostgreSQL backend with asyncpg + pgvector.

## CLI

PRME includes a command-line tool for setup and memory inspection:

```bash
prme init ./my_memories            # Initialize a new memory directory
prme doctor ./my_memories          # Check memory pack health
prme info ./memory.duckdb          # Memory pack statistics
prme nodes ./memory.duckdb         # List nodes (--type, --state, --limit)
prme search ./memory.duckdb "query" # Run hybrid retrieval
prme chain ./memory.duckdb <id>    # Show supersedence chain
prme organize ./memory.duckdb     # Run organizer jobs
prme stats ./memory.duckdb        # Detailed statistics
prme export ./memory.duckdb       # Export as JSON
```

## HTTP API

Install with `pip install prme[api]` and run:

```bash
uvicorn prme.api:app
```

Endpoints under `/v1`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/store` | Store a memory node |
| `POST` | `/v1/ingest` | LLM-powered ingestion |
| `POST` | `/v1/retrieve` | Hybrid retrieval |
| `POST` | `/v1/organize` | Run organizer jobs |
| `GET` | `/v1/nodes` | Query nodes with filters |
| `GET` | `/v1/nodes/{id}` | Get single node |
| `PUT` | `/v1/nodes/{id}/promote` | Promote lifecycle state |
| `PUT` | `/v1/nodes/{id}/archive` | Archive node |
| `PUT` | `/v1/nodes/{id}/reinforce` | Increase confidence |
| `GET` | `/v1/nodes/{id}/neighborhood` | Graph neighborhood |
| `GET` | `/v1/nodes/{id}/chain` | Supersedence chain |
| `GET` | `/v1/health` | Health check |
| `GET` | `/v1/stats` | Memory statistics |

## Configuration

PRME uses pydantic-settings. Configure via constructor arguments, environment variables (`PRME_` prefix), or `.env` files:

```bash
# Extraction provider
PRME_EXTRACTION_PROVIDER=openai        # openai | anthropic | ollama
PRME_EXTRACTION_MODEL=gpt-4o-mini

# Embedding
PRME_EMBEDDING_PROVIDER=fastembed      # fastembed (local, default) or openai
PRME_EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5

# Encryption at rest
PRME_ENCRYPTION_ENABLED=true           # Master toggle (default false)
PRME_ENCRYPTION_KEY=your-secret-key    # Passphrase (PBKDF2 -> Fernet AES-128-CBC + HMAC)
# Optional explicit key type (avoids passphrase/raw-key ambiguity):
#   PRME_ENCRYPTION_KEY=passphrase:your-secret-key   # always derive via PBKDF2
#   PRME_ENCRYPTION_KEY=raw_key:<44-char-fernet-key> # use a raw Fernet key
```

> **Plaintext window.** When the engine is open, pack files are decrypted to
> plaintext on disk and are re-encrypted on a clean `close()` (an `atexit`
> handler also re-encrypts on normal process exit as a best-effort safety
> net). A hard crash (`SIGKILL`, power loss) can leave the pack plaintext on
> disk until the next clean `close()`. Encryption secrets are held as
> `SecretStr` so they are never rendered by config dumps or tracebacks.

## Testing

```bash
# Run all tests
pytest tests/ -q

# Run simulations (19 scenarios)
python -m simulations --list           # List available scenarios
python -m simulations                  # Run all
python -m simulations changing_facts   # Run specific scenario

# Run benchmarks
python -m benchmarks                   # All benchmarks
python -m benchmarks epistemic         # Epistemic benchmark only

# Stress tests (opt-in)
PRME_STRESS_TESTS=1 pytest tests/test_stress.py
```

## Documentation

Detailed technical documentation lives in [`docs/`](docs/):

- [RFC Suite Overview](docs/RFC-0000-Suite-Overview.md)
- [Core Data Model](docs/RFC-0001-Core-Data-Model.md)
- [Event Store](docs/RFC-0002-Event-Store.md)
- [Epistemic State Model](docs/RFC-0003-Epistemic-State-Model.md)
- [Namespace & Scope Isolation](docs/RFC-0004-Namespace-and-Scope-Isolation.md)
- [Hybrid Retrieval Pipeline](docs/RFC-0005-Hybrid-Retrieval-Pipeline.md)
- [Decay and Forgetting](docs/RFC-0007-Decay-and-Forgetting.md)
- [Confidence Evolution](docs/RFC-0008-Confidence-Evolution.md)
- [Integration Guide](docs/INTEGRATION.md)
- [Full RFC Index](docs/INDEX.md) (15 RFCs)

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full development plan.

**Current (v0.11.0)** — hybrid retrieval, synchronous and async clients, MCP/REST, framework adapters, deterministic vector search, and index rebuilds.

**Next** — a trustworthy retrieval baseline, complete aggregation results, temporal state, and measured improvements to context packing. See the roadmap for acceptance criteria and GitHub issue links.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
