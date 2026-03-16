# PRME — Portable Relational Memory Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/dwamianm/prism/actions/workflows/ci.yml/badge.svg)](https://github.com/dwamianm/prism/actions/workflows/ci.yml)

**Local-first, embeddable memory substrate for LLM-powered systems.**

PRME gives AI agents and chatbots stable long-term memory by combining an append-only event log, a graph-based relational model, hybrid retrieval (graph + vector + lexical), and epistemic state tracking — all in a portable, single-directory bundle.

## Benchmark Results

PRME is evaluated against two established long-term memory benchmarks. Results reflect end-to-end accuracy: ingestion, retrieval, and LLM-generated answers judged against ground truth.

| Benchmark | Score | Queries | Details |
|-----------|-------|---------|---------|
| **[LongMemEval](https://github.com/xiaowu0162/LongMemEval)** | **92.5%** | 470 | 5 ability categories across multi-session conversations |
| **[LoCoMo](https://github.com/snap-stanford/locomo)** | **79.0%** | 152 | Long-conversation QA with temporal, multi-hop, and inference |

### LongMemEval Breakdown

| Category | Accuracy | Description |
|----------|----------|-------------|
| Information Extraction | 97% (117/120) | Retrieving specific facts from past conversations |
| Temporal Reasoning | 95% (123/127) | Time-based queries ("when did...", "how long ago...") |
| Multi-Session | 92% (112/121) | Connecting information across separate conversations |
| Knowledge Update | 91% (66/72) | Tracking how facts change over time |
| Abstention | 65% (30/30) | Correctly saying "I don't know" when info is absent |

### LoCoMo Breakdown

| Category | Accuracy | Description |
|----------|----------|-------------|
| Temporal | 93.5% | Date and time reasoning over conversation history |
| Inference | 81.5% | Drawing conclusions from stored memories |
| Multi-Hop | 80.4% | Combining multiple facts to answer a question |
| Single-Hop | 58.1% | Direct fact retrieval from long conversations |

<details>
<summary>Methodology and competitive context</summary>

- **Generation model**: gpt-5-mini (OpenAI). The same retrieval pipeline with gpt-4o-mini scores 80.1% on LME and 72.6% on LoCoMo — the gap is generation quality, not retrieval.
- **Retrieval pipeline**: 6-signal hybrid scoring (semantic, lexical, graph, recency, salience, confidence) with supersedence-aware filtering, query reformulation, and temporal context formatting.
- **LongMemEval competitive context**: Mastra (95%), **PRME (92.5%)**, Hindsight (91.4%), Emergence (86%), Supermemory (85%). PRME places top-3 among published systems.
- **Evaluation**: LLM-as-judge scoring with structured output. All benchmarks are deterministic given the same retrieval results and generation model.
- **Test suite**: 944 tests passing, 19 simulation scenarios, 6 stress tests.

</details>

## Why PRME?

LLMs are stateless. Every conversation starts from zero. Existing solutions bolt on vector search and call it "memory," but that misses the relational structure of how humans actually remember things — preferences override old ones, decisions have context, facts get corrected.

PRME models memory the way it actually works:

- **Event sourcing** — immutable append-only log, deterministic rebuild
- **Graph-based relational model** — 9 typed node kinds (entities, facts, preferences, decisions, tasks, instructions, summaries, events, notes) with edges capturing relationships, supersedence, and temporal validity
- **Epistemic state tracking** — memories have lifecycle states (tentative -> stable -> superseded -> archived), confidence scores, contradiction detection, and oscillation dampening
- **Hybrid retrieval** — semantic similarity + lexical search + graph proximity, scored and packed into a token-efficient context bundle
- **Self-organizing memory** — 11 organizer jobs handle promotion, decay, deduplication, summarization, consolidation, and archival automatically
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
- **Organizer** — 11 background jobs: `promote`, `decay_sweep`, `archive`, `deduplicate`, `alias_resolve`, `summarize`, `feedback_apply`, `centrality_boost`, `tombstone_sweep`, `snapshot_generation`, `consolidate`. Runs on schedule or opportunistically during retrieve/ingest.
- **Storage** — DuckDB (events + graph), usearch (HNSW vectors), Tantivy (full-text). Optional PostgreSQL backend with asyncpg + pgvector.

## CLI

PRME includes a command-line tool for memory inspection:

```bash
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
PRME_ENCRYPTION_KEY=your-secret-key    # Enables AES-128-CBC + HMAC encryption
```

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

**Current (v0.4.x)** — Retrieval pipeline hardening, benchmark-validated scoring
**Next (v0.5)** — MCP server, SDK ergonomics, plugin architecture
**Future (v0.6+)** — Multi-agent memory, federation, hosted offering

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
