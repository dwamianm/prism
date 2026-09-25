# PRME — Portable Relational Memory Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/dwamianm/prism/actions/workflows/ci.yml/badge.svg)](https://github.com/dwamianm/prism/actions/workflows/ci.yml)

**Local-first, embeddable memory for LLM-powered applications.**

PRME gives agents and chatbots persistent memory through an append-only event
log, typed relationships, and hybrid retrieval. It preserves source evidence,
tracks changing claims, and packs relevant memories into a configurable token
budget. The default backend runs locally in a portable directory; PostgreSQL is
available for server deployments.

**Current release: v0.12.0.** Python 3.11+, synchronous and async clients, HTTP,
MCP, and LangChain/LlamaIndex integrations.

## Architecture

Four storage layers sit behind a unified memory API:

| Layer | Local implementation | Purpose |
|---|---|---|
| Event store | DuckDB | Immutable source events and durable operation records |
| Graph store | DuckDB tables and recursive CTEs | Typed memories, relationships, temporal validity, confidence, and provenance |
| Vector index | USearch | Semantic similarity search with versioned embeddings |
| Lexical index | Tantivy | BM25 full-text search |

The optional PostgreSQL backend uses PostgreSQL tables, pgvector, and full-text
search behind the same API. Both backends default to exact vector search;
approximate search is configurable.

```text
Source text → event log → direct storage or LLM extraction → graph + indexes

Query → intent, entity, and time analysis
      → graph, vector, lexical, and recent-memory candidates
      → deterministic scoring and filtering
      → token-bounded context with provenance
```

- **Ingestion:** `store()` writes a memory directly without an LLM.
  `ingest()` extracts entities, facts, and relationships through a configured
  provider. Deferred ingestion records durable work that can resume after restart.
- **Memory state:** typed nodes carry owner, scope, lifecycle, confidence,
  validity windows, and source references. Explicit corrections and contradiction
  operations preserve earlier claims; automatic store-time supersedence is opt-in.
- **Retrieval:** combines multiple search paths and packs selected evidence into
  context. Structured assertion-state and aggregation APIs support exact stored
  claim queries and decimal totals beyond semantic top-k retrieval.
- **Default retrieval behavior:** candidates are ranked by reciprocal rank
  fusion of their semantic and lexical ranks, with a recency boost on
  current-state questions and an event-time tie-break. Packing uses balanced
  ordering and renders each record as one plain line (the `reader` context
  format), while the full records stay in the bundle's sections and the
  retrieval receipt. These defaults changed after v0.12.0;
  [Default retrieval settings](docs/PACKING.md#default-retrieval-settings)
  lists the earlier values and the environment variables that restore them.
- **Organizer:** maintains promotion, decay, summaries, deduplication, archival,
  and index compaction. It runs opportunistically or through `prme organize`;
  recurring schedules use an external timer. Shared stores should pass `user_id`.
- **Workspaces:** `MemoryWorkspace` manages named local packs or PostgreSQL
  schemas with identity checks and scoped leases.

A local memory pack contains:

```text
my_memories/
├── memory.duckdb
├── vectors.usearch
├── lexical_index/
└── manifest.json
```

Search indexes can be regenerated from the durable graph with `prme rebuild`.
Event time, ingestion time, and validity time remain distinct. The `knowledge_at`
filter applies an ingestion cutoff to current graph/index state; exact historical
state replay is not currently available.

## Current benchmarks

| Benchmark | Correct answers | Answer accuracy |
|---|---:|---:|
| LongMemEval-S | 453 / 500 | **90.6%** |
| LoCoMo, non-adversarial questions | 1,250 / 1,540 | **81.2%** |

### LongMemEval-S by question type

| Question type | Correct answers | Answer accuracy |
|---|---:|---:|
| Single-session user | 69 / 70 | 98.6% |
| Single-session assistant | 54 / 56 | 96.4% |
| Knowledge update | 71 / 78 | 91.0% |
| Temporal reasoning | 120 / 133 | 90.2% |
| Single-session preference | 26 / 30 | 86.7% |
| Multi-session | 113 / 133 | 85.0% |

### LoCoMo by question type

| Question type | Correct answers | Answer accuracy |
|---|---:|---:|
| Single-hop | 756 / 841 | 89.9% |
| Temporal | 256 / 321 | 79.8% |
| Multi-hop | 182 / 282 | 64.5% |
| Open-domain | 56 / 96 | 58.3% |

Measured on September 25, 2026 with the current retrieval defaults and a
3,996-token memory-context budget per question. See
[BENCHMARKS.md](BENCHMARKS.md) for the method, earlier results and
reproducibility artifacts.

## Installation

```bash
pip install prme
```

Optional integrations:

```bash
pip install "prme[postgres]"   # PostgreSQL backend
pip install "prme[api]"        # HTTP API
pip install "prme[mcp]"        # MCP server
pip install "prme[langchain]"  # LangChain retriever and chat history
pip install "prme[llamaindex]" # LlamaIndex retriever and chat store
```

## Quickstart

```python
from prme import MemoryClient

with MemoryClient("./my_memories") as memory:
    memory.store("Alice prefers dark mode in her editors.", user_id="alice")
    memory.store("The team decided to use PostgreSQL.", user_id="alice")

    response = memory.retrieve("What are Alice's preferences?", user_id="alice")
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")
```

`MemoryClient` is synchronous; `MemoryEngine` provides the async API. Direct
storage and retrieval use local embeddings by default and require no LLM API key.
Use `ingest()` with an OpenAI, Anthropic, or Ollama extraction provider when you
want structured memories extracted from conversation text.

Configure PRME through `PRMEConfig`, `PRME_` environment variables, or a `.env`
file. Constructor values override the environment, which overrides `.env`.
The [configuration reference](documentation/configuration.md) lists the
settings and their defaults. To return to the v0.12.0 retrieval defaults, set
`PRME_SCORING__FUSION=weighted`, `PRME_PACKING__CONTEXT_FORMAT=auditable` and
`PRME_PACKING__MULTIPATH_ORDERING=balanced` (the last is the default again and
only makes it explicit; see
[Default retrieval settings](docs/PACKING.md#default-retrieval-settings)).
See the [integration reference](docs/INTEGRATION.md) and
[examples](examples/) for configuration and application patterns.

## Interfaces and tools

Use the CLI to initialize, inspect, search, and maintain a local pack:

```bash
prme init ./my_memories
prme doctor ./my_memories
prme search ./my_memories/memory.duckdb "Alice's preferences" --user-id alice
prme organize ./my_memories/memory.duckdb --user-id alice
```

For a local assistant, install the MCP extra and run with a fixed owner:

```bash
PRME_MCP_USER_ID=alice prme-mcp --db-path ./my_memories
```

The HTTP extra provides a FastAPI service via `python -m prme.api`, which binds to
`127.0.0.1` and refuses a network bind without API credentials (see
[Running the Server](documentation/http-api.md#running-the-server)).
Shared HTTP deployments bind owners to credentials through `PRME_API_USER_KEYS`;
MCP Streamable HTTP uses its separate `PRME_MCP_USER_KEYS` setting.
See the [HTTP guide](docs/HTTP-API.md) and
[framework integration guide](docs/FRAMEWORK-INTEGRATIONS.md) for application setup.

## Milestones

| Milestone | Delivered |
|---|---|
| Core memory foundation, through v0.9 | Event sourcing, typed graph, hybrid retrieval, organizer jobs, encryption, CLI, evaluation harness, and index rebuilds |
| v0.10 | Deterministic vector search, batched ingestion indexing, and context-budget enforcement |
| v0.11 | Tenant-scoped maintenance, ownership and retrieval-filter fixes, and broader backend CI coverage |
| v0.12 — current | Durable ingestion and recovery, atomic lifecycle and correction records, scoped workspaces, structured assertion and quantity operations, retrieval receipts, and evaluated ranking profiles |
| Unreleased, on `main` | Rank fusion with current-state recency, the one-line reader context format and balanced ordering as retrieval defaults; conversation participants with speaker names; refusal of unauthenticated network binds for the HTTP API; an offline evidence gate and paired answer runs for measuring retrieval changes |

Experimental reranking (including the cross-encoder rank order), temporal-relation
guidance, model-assisted verification, temporal-first query intent, event-time
recency for the weighted formula, and session context packing remain opt-in.
Detailed changes are in the [changelog](CHANGELOG.md).

Current work follows [epic #77](https://github.com/dwamianm/prism/issues/77),
which acts on the 2026-09-23 benchmark gap audit: a reader-facing context,
better ranking, the context budget, and dated facts extracted at write time for
multi-hop and list questions. See the [roadmap](ROADMAP.md) for what has been
delivered and what is still open.

## Documentation

- [Developer guides](documentation/README.md) and [configuration reference](documentation/configuration.md)
- [Architecture and RFC index](docs/INDEX.md)
- [Integration reference](docs/INTEGRATION.md) and [framework adapters](docs/FRAMEWORK-INTEGRATIONS.md)
- [Context packing and default retrieval settings](docs/PACKING.md) and [retrieval feedback and learning](docs/LEARNING.md)
- [Experimental retrieval policies](docs/EXPERIMENTAL-RETRIEVAL-POLICIES.md)
- [Memory corrections](docs/MEMORY-CORRECTIONS.md)
- [Entity profiles](docs/ENTITY-PROFILES.md) and [entity identity](docs/ENTITY-IDENTITY.md)
- [Workspaces](docs/WORKSPACES.md) and [local resource control](docs/LOCAL-RESOURCES.md)
- [Custom embeddings](docs/CUSTOM-EMBEDDINGS.md) and [typed value bindings](docs/VALUE-BINDINGS.md)
- [HTTP API](docs/HTTP-API.md)

## Development

```bash
git clone https://github.com/dwamianm/prism.git
cd prism
uv sync --dev
uv run pytest tests/ -q
```

PostgreSQL tests require a live database configured through
`PRME_TEST_DATABASE_URL`. See [CONTRIBUTING.md](CONTRIBUTING.md) for development
setup, checks, and contribution guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
