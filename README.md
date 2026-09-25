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

### GPT-5.4 reference results

Completed September 23, 2026, using the PRME retrieval defaults of that date
(weighted scoring, the JSON `auditable` context format and balanced ordering)
over stored conversation turns and **GPT-5.4 (`gpt-5.4-2026-03-05`)** with
medium reasoning for both reader and judge. Each query had a **3,996-token
memory-context ceiling**; experimental retrieval features were disabled. These
remain the published reference. The retrieval defaults changed on September 25,
2026, and no GPT-5.4 run of the current defaults exists.

| Benchmark | Correct answers | Answer accuracy |
|---|---:|---:|
| LongMemEval-S | 430 / 500 | **86.0%** |
| LoCoMo, non-adversarial questions | 985 / 1,540 | **64.0%** |

All 2,040 questions completed with no terminal provider failures. LoCoMo uses
semantic yes/no judging rather than the dataset's token-F1 metric. These are
end-to-end answer scores on examined development cohorts; results depend on the
reader, judge, storage protocol, and context budget.

The LoCoMo judge is strict ([prompt](benchmarks/integrations/run_gpt54_comparison.py)):
when the reference lists several items, the answer must name all of them.
Several vendor LoCoMo scores are graded by Mem0-style lenient judges
([Mem0's prompt](benchmarks/integrations/lenient_judge.py)), which accept an
answer on the same topic. The lenient grade of these GPT-5.4 answers has not
been run yet;
[strict and lenient LoCoMo judges](BENCHMARKS.md#strict-and-lenient-locomo-judges)
shows both scores side by side where they exist.

### DeepSeek answer track

Retrieval changes made since v0.12.0 are answered on a separate track with no
API cost: **`deepseek-v4.1-flash:cloud`** as both reader and judge through a
local Ollama server, with the registered prompts and questions, the strict
LoCoMo judge and the same 3,996-token ceiling. **DeepSeek scores are not
comparable with the GPT-5.4 results above.**

| DeepSeek baseline | Retrieval defaults | LongMemEval-S | LoCoMo, non-adversarial questions |
|---|---|---:|---:|
| `prme` at `97c9402f`, September 24, 2026 | Those of the GPT-5.4 run (every context identical) | 423 / 500 (84.6%) | 1,014 / 1,540 (65.8%) |
| `prme@d811e3ed`, September 25, 2026 | Current defaults | 453 / 500 (90.6%) | 1,250 / 1,540 (81.2%) |

The current defaults were adopted in two steps on September 25, 2026. Each step
passed the project's default-change test twice on this track: a paired run
against a fresh run of the defaults it replaced, answered in the same session,
then a confirmation pair. The two baselines above are separate runs, not a
paired comparison; the paired results for each step are in
[BENCHMARKS.md](BENCHMARKS.md#deepseek-answer-track-through-ollama).

See [benchmark methodology and detailed results](BENCHMARKS.md) for category
scores, protocols, reproducibility artifacts, and evaluations with other readers.

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
| Unreleased, on `main` | Rank fusion with current-state recency, the one-line reader context format and balanced ordering as retrieval defaults; conversation participants with speaker names; refusal of unauthenticated network binds for the HTTP API; an offline evidence gate and a DeepSeek answer track for measuring retrieval changes |

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
