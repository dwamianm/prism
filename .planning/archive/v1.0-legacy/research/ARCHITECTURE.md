# Architecture Research

**Domain:** Local-first LLM memory engine (event-sourced, graph-relational, hybrid retrieval)
**Researched:** 2026-02-19
**Confidence:** MEDIUM-HIGH

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Client Layer                               │
│  ┌───────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  HTTP API     │  │ Python Lib   │  │  CLI                 │  │
│  │  (FastAPI)    │  │ (wrapper)    │  │  (inspect/manage)    │  │
│  └──────┬────────┘  └──────┬───────┘  └──────────┬───────────┘  │
├─────────┴──────────────────┴────────────────────┴───────────────┤
│                     Service Layer                               │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  Ingestion Pipeline                      │    │
│  │  (event append → extraction → graph write → indexing)    │    │
│  └──────────────────────┬──────────────────────────────────┘    │
│  ┌──────────────────────┴──────────────────────────────────┐    │
│  │                  Retrieval Pipeline                      │    │
│  │  (query analysis → candidate gen → re-rank → pack)      │    │
│  └──────────────────────┬──────────────────────────────────┘    │
│  ┌──────────────────────┴──────────────────────────────────┐    │
│  │               Scheduled Organizer                        │    │
│  │  (salience → promote/demote → summarize → dedup → archive)│   │
│  └──────────────────────┬──────────────────────────────────┘    │
├─────────────────────────┴───────────────────────────────────────┤
│                     Storage Layer                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Event   │  │  Graph   │  │  Vector  │  │  Lexical     │    │
│  │  Store   │  │  Store   │  │  Index   │  │  Index       │    │
│  │ (DuckDB) │  │  (Kùzu)  │  │  (HNSW)  │  │ (Tantivy)   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │
├─────────────────────────────────────────────────────────────────┤
│                   External Dependencies                         │
│  ┌────────────────────────────┐  ┌─────────────────────────┐    │
│  │  Embedding Provider        │  │  LLM Provider           │    │
│  │  (OpenAI / local model)    │  │  (extraction, summarize)│    │
│  └────────────────────────────┘  └─────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Communicates With |
|-----------|----------------|-------------------|
| **HTTP API (FastAPI)** | Request/response boundary; authentication; rate limiting; request validation | Ingestion Pipeline, Retrieval Pipeline |
| **Python Library** | In-process wrapper calling the same service layer directly; no HTTP overhead | Ingestion Pipeline, Retrieval Pipeline |
| **CLI** | Human-facing inspection and management commands; memory browsing, stats, rebuild triggers | Service Layer (all pipelines) |
| **Ingestion Pipeline** | Accept raw events, append to event store, extract entities/facts/decisions, write to graph, generate embeddings, index for FTS | Event Store, Graph Store, Vector Index, Lexical Index, Embedding Provider, LLM Provider |
| **Retrieval Pipeline** | Query analysis (intent/entity/time), multi-source candidate generation, deterministic re-ranking, context packing into memory bundles | Graph Store, Vector Index, Lexical Index, Event Store |
| **Scheduled Organizer** | Background maintenance: salience recalculation, promotion/demotion, summarization cascades, deduplication, entity alias resolution, archival | All storage backends, LLM Provider |
| **Event Store (DuckDB)** | Append-only immutable event log; single source of truth; all derived state rebuildable from here | Ingestion Pipeline (write), Retrieval Pipeline (read), Organizer (read) |
| **Graph Store (Kuzu)** | Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and edges with temporal validity, confidence, provenance | Ingestion Pipeline (write), Retrieval Pipeline (read), Organizer (read/write) |
| **Vector Index (HNSW)** | Approximate nearest neighbor search over embeddings with versioned metadata (model, version, dimension) | Ingestion Pipeline (write), Retrieval Pipeline (read), Organizer (write on re-embed) |
| **Lexical Index (Tantivy)** | BM25-based full-text search over event content, facts, summaries | Ingestion Pipeline (write), Retrieval Pipeline (read), Organizer (write on summary creation) |
| **Embedding Provider** | Generate vector embeddings; pluggable (OpenAI API, Voyage, local sentence-transformers) | Ingestion Pipeline, Organizer |
| **LLM Provider** | Entity extraction, intent classification, summarization; pluggable (OpenAI, Anthropic, local) | Ingestion Pipeline, Retrieval Pipeline (query analysis), Organizer (summarization) |

## Recommended Project Structure

```
prism/
├── src/
│   └── prme/
│       ├── __init__.py
│       ├── config.py              # Settings, scoring weights, provider config
│       ├── models.py              # Shared domain models (MemoryObject, Event, etc.)
│       │
│       ├── api/                   # HTTP API layer
│       │   ├── __init__.py
│       │   ├── app.py             # FastAPI app factory
│       │   ├── routes/
│       │   │   ├── ingest.py      # POST /events, POST /conversations
│       │   │   ├── retrieve.py    # POST /retrieve, GET /entities/{id}
│       │   │   ├── manage.py      # Memory management endpoints
│       │   │   └── health.py      # Health, stats, version
│       │   └── middleware.py      # Auth, logging, error handling
│       │
│       ├── core/                  # Service layer (framework-agnostic)
│       │   ├── __init__.py
│       │   ├── ingestion.py       # Ingestion pipeline orchestrator
│       │   ├── retrieval.py       # Retrieval pipeline orchestrator
│       │   ├── extraction.py      # Entity/fact/decision extraction
│       │   ├── ranking.py         # Deterministic re-ranking logic
│       │   └── packing.py         # Context bundle construction
│       │
│       ├── storage/               # Storage backends
│       │   ├── __init__.py
│       │   ├── event_store.py     # DuckDB event store
│       │   ├── graph_store.py     # Kuzu graph operations
│       │   ├── vector_index.py    # HNSW index management
│       │   ├── lexical_index.py   # Tantivy FTS index
│       │   └── pack.py           # Portable artifact (manifest, bundle)
│       │
│       ├── organizer/             # Scheduled background jobs
│       │   ├── __init__.py
│       │   ├── scheduler.py       # APScheduler setup, job registration
│       │   ├── salience.py        # Salience recalculation
│       │   ├── lifecycle.py       # Promotion, demotion, supersedence
│       │   ├── summarizer.py      # Daily → weekly → monthly summarization
│       │   ├── dedup.py           # Deduplication, entity alias resolution
│       │   └── archival.py        # TTL enforcement, compression
│       │
│       ├── providers/             # Pluggable external services
│       │   ├── __init__.py
│       │   ├── embeddings.py      # Embedding provider interface + impls
│       │   └── llm.py             # LLM provider interface + impls
│       │
│       └── cli/                   # CLI commands
│           ├── __init__.py
│           └── commands.py        # Inspect, rebuild, stats, export
│
├── tests/
│   ├── unit/                      # Fast, isolated tests per module
│   ├── integration/               # Tests with real storage backends
│   └── eval/                      # Evaluation harness (recall, determinism)
│
├── memory_pack/                   # Default artifact directory
│   ├── events.duckdb
│   ├── graph.kuzu/
│   ├── vectors.bin
│   ├── hnsw.idx
│   └── manifest.json
│
└── pyproject.toml
```

### Structure Rationale

- **`api/`:** Thin HTTP layer. Routes call `core/` service functions. No business logic here -- just request validation, serialization, and response formatting. This keeps the Python library wrapper trivial (it calls the same `core/` functions directly).
- **`core/`:** Framework-agnostic orchestration. Ingestion and retrieval pipelines live here. Both the HTTP API and the Python library import from `core/`. This is the center of the system.
- **`storage/`:** Each backend gets its own module with a consistent interface (init, write, read, close). The `pack.py` module handles the portable artifact format (manifest generation, bundle creation, rebuild from event log).
- **`organizer/`:** Each maintenance job is a separate module. The scheduler registers them independently so they can be enabled/disabled via config. Jobs operate on `storage/` backends through the same interfaces as `core/`.
- **`providers/`:** Abstract interface + concrete implementations for embedding and LLM providers. Swapping OpenAI for local sentence-transformers means implementing one interface, not touching `core/` or `storage/`.

## Architectural Patterns

### Pattern 1: Event Sourcing with Derived Read Models (CQRS-lite)

**What:** All data enters the system as immutable events in DuckDB. The graph store, vector index, and lexical index are derived projections -- materialized views built from the event log. The event store is the write model; the other stores are read models optimized for different query patterns.

**When to use:** Always. This is the foundational pattern for the entire system. Every piece of data traces back to an event.

**Trade-offs:** Rebuilding derived state from the full event log is slow at scale (must replay all events). Mitigated by snapshotting: periodically checkpoint the derived state so rebuilds start from the latest snapshot rather than event zero.

**How it flows:**
```
Event arrives → DuckDB append (immutable)
             → Extract entities/facts (LLM or rules)
             → Write to Kuzu graph (with provenance ref to event_id)
             → Generate embedding → Write to HNSW
             → Index text → Write to Tantivy
```

**Build order implication:** Event Store must be built first. Everything depends on it.

### Pattern 2: Hybrid Retrieval with Deterministic Re-ranking

**What:** Query analysis decomposes a retrieval request into intent, entities, and time bounds. Four candidate generators run in parallel (graph neighborhood, vector similarity, lexical match, recent high-salience items). Results merge into a single candidate set. A deterministic scoring formula (weighted sum of semantic similarity, lexical relevance, graph proximity, recency decay, salience, confidence) produces a final ranked list. Weights are versioned in config.

**When to use:** Every retrieval request. The hybrid approach catches what any single modality misses -- graph captures relationships, vectors capture semantics, lexical captures exact terms.

**Trade-offs:** More complex than vector-only retrieval. But vector-only fails on structured queries ("what did I decide about X last week?") where graph traversal and temporal filtering are essential. The deterministic scoring formula means identical inputs always produce identical outputs -- critical for the reproducibility requirement.

**Evidence:** Mem0's architecture validates this hybrid approach: when a memory is added, it updates vector store for similarity, graph store for relationships, and a history log for audit trail. Hybrid retrieval combining graph traversal with vector similarity achieves 26% improvement over vector-only approaches (Mem0 research, 2025). Neo4j's advanced RAG techniques document confirms that graph + vector retrieval enables multi-hop reasoning across connected memories.

### Pattern 3: Tiered Memory Lifecycle (Tentative → Stable → Superseded → Archived)

**What:** Memory objects progress through lifecycle states. New assertions start as Tentative (low confidence). When reinforced by additional evidence, they promote to Stable. When contradicted, they become Superseded (with a pointer to the replacing assertion). When past TTL or below salience threshold, they Archive. The organizer drives these transitions.

**When to use:** All graph-stored memory objects (Facts, Decisions, Preferences, Tasks). Events themselves are immutable and don't have lifecycle states.

**Trade-offs:** Adds complexity to graph queries (must filter by lifecycle state). But without it, the system resurfaces outdated information -- the core problem PRME exists to solve.

**Evidence:** MemGPT/Letta's architecture demonstrates the necessity of tiered memory management, where information moves between fast volatile memory (primary context) and persistent storage. The Serokell design patterns survey identifies progressive summarization and memory compaction as standard patterns in production LLM memory systems.

### Pattern 4: Background Organizer as Separate Concern

**What:** The organizer runs on a schedule (not inline with requests). It performs expensive operations: salience recalculation (graph centrality, frequency, recency), promotion/demotion (confidence threshold checks), summarization cascades (daily summaries roll up to weekly, weekly to monthly), deduplication (entity alias resolution via embedding similarity), and archival (TTL enforcement).

**When to use:** All maintenance operations. Never block an ingestion or retrieval request with organizer work.

**Trade-offs:** Eventual consistency -- a newly ingested fact won't be promoted to Stable until the next organizer cycle. This is acceptable because the retrieval pipeline can still find Tentative items; they just rank lower.

**Implementation:** APScheduler with AsyncIOScheduler (integrates with FastAPI's event loop). Jobs are independent and idempotent. Each job operates through the same `storage/` interfaces as the rest of the system.

## Data Flow

### Ingestion Flow

```
Client (HTTP/Library)
    │
    ▼
Ingestion Pipeline
    │
    ├──→ Event Store (DuckDB)           [1. Append immutable event]
    │        │
    │        ▼
    ├──→ Extraction (LLM/rules)         [2. Extract entities, facts, decisions]
    │        │
    │        ▼
    ├──→ Graph Store (Kùzu)             [3. Create/update nodes + edges]
    │        │                               with temporal validity,
    │        │                               confidence, provenance
    │        ▼
    ├──→ Embedding Provider              [4. Generate vectors]
    │        │
    │        ▼
    ├──→ Vector Index (HNSW)            [5. Index embeddings with metadata]
    │        │
    │        ▼
    └──→ Lexical Index (Tantivy)        [6. Index text content]
```

**Key constraint:** Step 1 (event append) MUST complete before steps 2-6. Steps 2-6 can potentially be parallelized or pipelined, but all must reference the event_id from step 1 as provenance.

### Retrieval Flow

```
Client query
    │
    ▼
Query Analysis (LLM or rules)
    │
    ├── Intent classification (lookup / temporal / relational / exploratory)
    ├── Entity extraction
    └── Time bound detection
    │
    ▼
Candidate Generation (parallel)
    │
    ├──→ Graph Store    →  neighborhood expansion (1-3 hops from entities)
    ├──→ Graph Store    →  stable facts for matched entities
    ├──→ Vector Index   →  top-k by semantic similarity
    ├──→ Lexical Index  →  BM25 matches
    └──→ Event Store    →  recent high-salience items
    │
    ▼
Merge + Deduplicate candidates
    │
    ▼
Deterministic Re-ranking
    │   score = w1*semantic + w2*lexical + w3*graph_proximity
    │         + w4*recency_decay + w5*salience + w6*confidence
    │
    ▼
Context Packing
    │
    ├── Entity snapshots (structured summaries over raw events)
    ├── Stable facts
    ├── Recent decisions
    ├── Active tasks
    └── Provenance references (for explainability)
    │
    ▼
Memory Bundle → Client
```

### Organizer Flow (Background)

```
Scheduler trigger (periodic)
    │
    ▼
┌─────────────────────────────────┐
│  Job 1: Salience Recalculation  │  Signals: frequency, recency,
│                                 │  graph centrality, user pins,
│                                 │  task linkage
└────────────┬────────────────────┘
             ▼
┌─────────────────────────────────┐
│  Job 2: Promotion / Demotion    │  Promote reinforced assertions
│                                 │  (Tentative → Stable)
│                                 │  Supersede contradictions
│                                 │  Demote stale items
└────────────┬────────────────────┘
             ▼
┌─────────────────────────────────┐
│  Job 3: Summarization           │  Daily → Weekly → Monthly
│                                 │  Per-entity snapshots
│                                 │  Delta-based summaries
│                                 │  (requires LLM provider)
└────────────┬────────────────────┘
             ▼
┌─────────────────────────────────┐
│  Job 4: Deduplication           │  Entity alias resolution
│                                 │  Assertion consolidation
│                                 │  (embedding similarity check)
└────────────┬────────────────────┘
             ▼
┌─────────────────────────────────┐
│  Job 5: Archival                │  TTL enforcement
│                                 │  Compression
│                                 │  Policy-based retention
└─────────────────────────────────┘
```

**Key constraint:** Jobs should be independent and idempotent. Running them in sequence is safest (salience informs promotion, promotion informs summarization), but they must not block the ingestion or retrieval paths.

### Key Data Flows

1. **Event → Derived State:** Every mutation enters as an event. Extraction produces graph mutations, embeddings, and text indexes. All derived state carries `source_event_id` for provenance and rebuild capability.

2. **Query → Memory Bundle:** A retrieval request triggers parallel candidate generation across four backends, merges results, applies deterministic scoring, and packs the highest-ranked items into a structured memory bundle optimized for LLM context windows.

3. **Organizer → Quality Improvement:** The background organizer improves retrieval quality over time by promoting validated facts, superseding contradictions, generating summaries that reduce token footprint, and archiving stale data.

4. **Rebuild → Deterministic State:** On rebuild, replay all events from the event store through the ingestion pipeline to regenerate graph, vector, and lexical indexes. Snapshotting at intervals avoids full replay.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0-10k events | Everything in-process. Single DuckDB file, single Kuzu directory, single HNSW index. No optimization needed. |
| 10k-100k events | HNSW index rebuild time becomes noticeable. Use incremental adds, not full rebuilds. Organizer summarization starts paying dividends by compacting context. Snapshot event store position to speed rebuilds. |
| 100k-1M events | Graph traversal performance matters. Kuzu handles this well (benchmarked to billions of edges). Vector index may need partitioning by time window or scope. Consider Tantivy index segments. Summarization cascades critical for keeping memory bundles lean. |
| 1M+ events | Likely beyond single-user local-first scope. If reached: shard event store by stream, partition vector indexes, implement lazy graph loading. But this is a future concern -- PRME targets personal/project-level memory, not enterprise-scale. |

### Scaling Priorities

1. **First bottleneck: Embedding generation latency.** External API calls (OpenAI) are the slowest part of ingestion. Mitigate by batching embedding requests and making ingestion async (return event ID immediately, process embeddings in background).
2. **Second bottleneck: Organizer summarization cost.** LLM calls for summarization are expensive in time and tokens. Mitigate by running summarization less frequently and using incremental (delta-based) summaries rather than full re-summarization.
3. **Third bottleneck: HNSW index size in memory.** hnswlib keeps the full index in memory. For large indexes (100k+ embeddings at 1536 dimensions), this consumes ~600MB+ RAM. Mitigate by pruning archived embeddings from the active index.

## Anti-Patterns

### Anti-Pattern 1: Mutable Event Store

**What people do:** Update or delete events in the event store to "fix" data.
**Why it's wrong:** Destroys the single source of truth. Derived state becomes non-reproducible. The deterministic rebuild guarantee breaks -- you can no longer regenerate the exact same graph/vector/lexical state from events.
**Do this instead:** Append a correction event that supersedes the original. The graph store handles supersedence natively (SUPERSEDES edge type). The original event remains for audit.

### Anti-Pattern 2: Inline Organizer Work During Requests

**What people do:** Run salience recalculation, summarization, or deduplication inline during ingestion or retrieval requests.
**Why it's wrong:** Ingestion latency spikes unpredictably. A simple "remember this" call might trigger an expensive LLM summarization. Retrieval times become non-deterministic.
**Do this instead:** All organizer work runs on a schedule or is triggered asynchronously. Ingestion writes to the four stores and returns. Retrieval reads from the four stores and returns. The organizer improves quality in the background.

### Anti-Pattern 3: Vector-Only Retrieval

**What people do:** Skip the graph and lexical indexes and rely entirely on vector similarity.
**Why it's wrong:** Vector similarity fails on structured queries. "What did I decide about the database migration?" requires graph traversal (find Decision nodes related to "database migration" entity). "Tell me about X from last Tuesday" requires temporal filtering that vector similarity cannot provide. Exact keyword matching (lexical) catches things that embedding similarity misses.
**Do this instead:** Always run the full hybrid pipeline. The re-ranking formula weights handle cases where some sources return no candidates.

### Anti-Pattern 4: Tight Coupling Between Storage Backends

**What people do:** Have the graph store directly write to the vector index, or the vector index query the event store.
**Why it's wrong:** Storage backends become interdependent. Replacing Kuzu becomes impossible without touching vector code. Testing requires all four backends running.
**Do this instead:** Storage backends are leaf nodes. Only the service layer (`core/`) orchestrates across backends. Each backend exposes a clean interface (init, write, read, query, close). The ingestion pipeline calls each backend's write interface in sequence. The retrieval pipeline calls each backend's query interface in parallel.

### Anti-Pattern 5: Ignoring Embedding Model Versioning

**What people do:** Change embedding model without re-indexing, or store embeddings without recording which model generated them.
**Why it's wrong:** Embeddings from different models occupy incompatible vector spaces. Similarity scores between old and new embeddings are meaningless. Deterministic rebuild fails because the model version isn't recorded.
**Do this instead:** Every embedding record includes model_name, model_version, and dimension. When the embedding model changes, either re-index all content or maintain separate indexes per model version.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| OpenAI / Anthropic API (embeddings) | Async HTTP client with retry, rate limiting, batching | Latency-sensitive. Batch embeddings during ingestion. Cache locally to avoid re-embedding unchanged content. |
| OpenAI / Anthropic API (LLM extraction) | Async HTTP client with structured output parsing | Used for entity extraction, intent classification, summarization. Consider rule-based fallbacks for extraction to reduce cost. |
| Local sentence-transformers | In-process Python call | Zero network latency but requires GPU for performance. Good for offline/air-gapped use. |
| Local LLM (llama.cpp, etc.) | In-process or local HTTP | For extraction/summarization without cloud dependency. Quality tradeoff vs cloud models. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| API ↔ Core | Direct Python function calls | FastAPI routes call core service functions. No serialization overhead. |
| Core ↔ Storage | Python interface (protocol/ABC) | Each storage backend implements a consistent interface. Core never knows the concrete backend. |
| Core ↔ Providers | Python interface (protocol/ABC) | Embedding and LLM providers are pluggable. Core calls the interface; config determines the implementation. |
| Organizer ↔ Storage | Same storage interfaces as Core | Organizer jobs use the same `storage/` module interfaces. No special access. |
| Organizer ↔ Scheduler | APScheduler job registration | AsyncIOScheduler for FastAPI integration. Jobs registered at startup with configurable intervals. |
| CLI ↔ Core | Direct Python function calls | CLI commands import and call core functions. Same code path as the library wrapper. |

## Build Order (Dependency Chain)

The architecture has clear dependency chains that dictate build order:

```
Phase 1: Foundation (nothing works without this)
─────────────────────────────────────────────────
  1. Event Store (DuckDB)         ← everything depends on this
  2. Graph Store (Kùzu)           ← depends on event store for provenance
  3. Vector Index (HNSW)          ← depends on embedding provider
  4. Lexical Index (Tantivy)      ← independent, but needs content from events
  5. Embedding Provider interface  ← vector index needs this
  6. Ingestion Pipeline           ← orchestrates 1-5
  7. Retrieval Pipeline           ← reads from 2-4
  8. HTTP API (FastAPI)           ← thin wrapper over 6-7

Phase 2: Intelligence (requires Phase 1 complete)
─────────────────────────────────────────────────
  9. LLM Provider interface       ← extraction/summarization needs this
 10. Entity extraction            ← upgrades ingestion from raw to structured
 11. Salience calculation         ← first organizer job
 12. Promotion/Demotion           ← depends on salience
 13. Summarization                ← depends on LLM provider + graph data
 14. Deduplication                ← depends on embeddings + graph
 15. Archival                     ← depends on salience scores + TTL config

Phase 3: Hardening (requires Phase 2 complete)
─────────────────────────────────────────────────
 16. Encryption at rest           ← wraps portable artifact
 17. CLI tooling                  ← calls core functions
 18. Deterministic rebuild        ← validates event sourcing guarantee
 19. Evaluation harness           ← proves recall accuracy
 20. Python library wrapper       ← packages core for embedding
```

**Key dependencies that cannot be reordered:**
- Event Store before anything else (it's the source of truth)
- Embedding Provider before Vector Index (vectors need embeddings)
- Graph Store before Retrieval Pipeline (graph neighborhood queries)
- All storage backends before Retrieval Pipeline (hybrid retrieval needs all four sources)
- Ingestion Pipeline before Retrieval Pipeline (need data to retrieve)
- LLM Provider before Extraction and Summarization
- Salience before Promotion/Demotion (lifecycle transitions depend on salience scores)

## Critical Risk: Kuzu Acquisition

**[HIGH CONFIDENCE]** Kuzu was acquired by Apple in October 2025. The [GitHub repository was archived on October 10, 2025](https://github.com/kuzudb/kuzu) and is now read-only under MIT license. Version 0.11.3 (the final release) bundles FTS, vector, JSON, and algorithm extensions.

**Implications for PRME:**
- Kuzu v0.11.3 is usable as-is. MIT license permits indefinite use. No code modifications needed.
- No future bug fixes, security patches, or feature development from upstream.
- The community fork [Bighorn](https://github.com/kineviz/bighorn) (by Kineviz) is attempting to continue development but its viability is uncertain.
- FalkorDB offers a [migration path](https://www.falkordb.com/blog/kuzudb-to-falkordb-migration/) but is a client-server database, not embedded.
- **Recommendation:** Use Kuzu v0.11.3 for Phase 1. It works, it's embedded, it has the features needed (Cypher, typed nodes/edges, property storage, multi-hop traversal). Isolate the graph store behind a clean interface (`storage/graph_store.py`) so migration to an alternative is possible if Bighorn doesn't materialize. This is a PITFALLS.md item.

## Sources

- [Design Patterns for Long-Term Memory in LLM-Powered Architectures](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures) — Comprehensive survey of memory patterns (cumulative, reflective, structured)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413) — Hybrid vector+graph architecture validation, 26% improvement over vector-only
- [Mem0 Graph Memory Documentation](https://docs.mem0.ai/open-source/features/graph-memory) — Graph memory implementation patterns
- [MemGPT/Letta Architecture](https://docs.letta.com/concepts/memgpt/) — Tiered memory management (primary context / archival storage)
- [Kuzu GitHub Repository (archived)](https://github.com/kuzudb/kuzu) — Archived Oct 2025, MIT license, v0.11.3 final
- [KuzuDB abandoned, community mulls options](https://www.theregister.com/2025/10/14/kuzudb_abandoned/) — Acquisition context, fork status
- [hnswlib GitHub](https://github.com/nmslib/hnswlib) — HNSW implementation details, Python bindings
- [tantivy-py GitHub](https://github.com/quickwit-oss/tantivy-py) — Python bindings for Tantivy FTS, v0.25.1 (Dec 2025)
- [Event Sourcing Pattern](https://microservices.io/patterns/data/event-sourcing.html) — Event sourcing architecture reference
- [Martin Fowler: Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html) — Canonical event sourcing description
- [CQRS and Event Sourcing Database Architecture](https://www.upsolver.com/blog/cqrs-event-sourcing-build-database-architecture) — CQRS pattern with derived read models
- [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices) — Service layer architecture patterns
- [APScheduler Documentation](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — Background scheduler architecture
- [Hybrid Retrieval Pipeline Patterns](https://www.emergentmind.com/topics/hybrid-retrieval-pipeline) — Multi-modal fusion, dynamic weighting
- [Advanced RAG Techniques (Neo4j)](https://neo4j.com/blog/genai/advanced-rag-techniques/) — Graph + vector hybrid retrieval validation

---
*Architecture research for: Local-first LLM memory engine (PRME)*
*Researched: 2026-02-19*
