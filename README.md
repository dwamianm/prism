<![CDATA[# PRME — Portable Relational Memory Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Local-first, embeddable memory substrate for LLM-powered systems.**

PRME gives AI agents and chatbots stable long-term memory by combining an append-only event log, a graph-based relational model, hybrid retrieval (graph + vector + lexical), and epistemic state tracking — all in a portable, single-directory bundle.

## Why PRME?

LLMs are stateless. Every conversation starts from zero. Existing solutions bolt on vector search and call it "memory," but that misses the relational structure of how humans actually remember things — preferences override old ones, decisions have context, facts get corrected.

PRME models memory the way it actually works:

- **Event sourcing** — immutable append-only log, deterministic rebuild
- **Graph-based relational model** — typed nodes (facts, preferences, decisions) with edges capturing relationships, supersedence, and temporal validity
- **Epistemic state tracking** — memories have lifecycle states (tentative → stable → superseded → archived) and confidence scores
- **Hybrid retrieval** — semantic similarity + lexical search + graph proximity, scored and packed into a token-efficient context bundle
- **Local-first** — everything lives in a single directory (DuckDB + usearch + Tantivy). No cloud dependency.

## Installation

```bash
pip install prme
```

With PostgreSQL backend support:

```bash
pip install prme[postgres]
```

### From source

```bash
git clone https://github.com/dwamianm/prism.git
cd prism
pip install -e ".[dev]"
```

## Quickstart

```python
import asyncio
from prme import MemoryEngine, PRMEConfig, NodeType, Scope

async def main():
    config = PRMEConfig(
        db_path="./memory.duckdb",
        vector_path="./vectors.usearch",
        lexical_path="./lexical_index",
    )
    engine = await MemoryEngine.create(config)

    # Store memories directly (no LLM needed)
    await engine.store(
        "Alice prefers dark mode in all her editors.",
        user_id="alice",
        node_type=NodeType.PREFERENCE,
        scope=Scope.PERSONAL,
    )
    await engine.store(
        "The team decided to use PostgreSQL for the backend.",
        user_id="alice",
        node_type=NodeType.DECISION,
        scope=Scope.PROJECT,
    )

    # Retrieve with hybrid scoring
    response = await engine.retrieve(
        "What are Alice's preferences?",
        user_id="alice",
    )
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")

    await engine.close()

asyncio.run(main())
```

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

See [`examples/quickstart.py`](examples/quickstart.py) for a full walkthrough and [`examples/chat.py`](examples/chat.py) for a complete terminal chat app with persistent memory.

## Architecture

```
┌──────────────────────────────────────────────┐
│                 PRME Engine                   │
├──────────────┬──────────────┬────────────────┤
│  Ingestion   │  Retrieval   │   Epistemic    │
│  Pipeline    │  Pipeline    │   State Model  │
├──────────────┴──────────────┴────────────────┤
│              Storage Layer                    │
│  ┌──────────┬───────────┬──────────────────┐ │
│  │ DuckDB   │ usearch   │ Tantivy          │ │
│  │ Events + │ HNSW      │ Full-text        │ │
│  │ Graph    │ Vectors   │ Search           │ │
│  └──────────┴───────────┴──────────────────┘ │
│         Optional: PostgreSQL backend          │
└──────────────────────────────────────────────┘
```

- **Ingestion Pipeline** — stores raw events, optionally extracts entities/facts/relationships via LLM
- **Retrieval Pipeline** — query analysis → multi-source candidate generation → deterministic scoring → context packing
- **Epistemic State Model** — tracks confidence, lifecycle transitions, contradiction detection, and supersedence
- **Storage** — DuckDB (events + graph), usearch (HNSW vectors), Tantivy (full-text). Optional PostgreSQL backend for production deployments.

## Configuration

PRME uses pydantic-settings. Configure via constructor arguments, environment variables (`PRME_` prefix), or `.env` files:

```bash
# Extraction provider
PRME_EXTRACTION_PROVIDER=openai        # openai | anthropic | ollama
PRME_EXTRACTION_MODEL=gpt-4o-mini

# Embedding
PRME_EMBEDDING_PROVIDER=fastembed      # fastembed (local, default) or openai
PRME_EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5
```

## Documentation

Detailed technical documentation lives in [`docs/`](docs/):

- [RFC Suite Overview](docs/RFC-0000-Suite-Overview.md)
- [Core Data Model](docs/RFC-0001-Core-Data-Model.md)
- [Event Store](docs/RFC-0002-Event-Store.md)
- [Epistemic State Model](docs/RFC-0003-Epistemic-State-Model.md)
- [Namespace & Scope Isolation](docs/RFC-0004-Namespace-and-Scope-Isolation.md)
- [Hybrid Retrieval Pipeline](docs/RFC-0005-Hybrid-Retrieval-Pipeline.md)
- [Integration Guide](docs/INTEGRATION.md)
- [Full RFC Index](docs/INDEX.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
]]>