# Phase 1: Storage Foundation - Research

**Researched:** 2026-02-19
**Domain:** Embedded storage backends (DuckDB event store, DuckPGQ graph, USearch HNSW vectors, tantivy-py lexical search) with unified memory interface
**Confidence:** MEDIUM-HIGH

## Summary

Phase 1 builds the four storage backends (event store, graph store, vector index, lexical index) behind a unified MemoryEngine interface with typed data model, temporal validity, lifecycle states, and user/session scoping. The critical technical bet is DuckPGQ as the graph backend -- research confirms it can handle the core graph operations PRME needs (pattern matching, variable-length path traversal, shortest path, property filtering) but has significant gaps (no ALTER graph, no OPTIONAL MATCH, open crash bugs, no betweenness centrality). The GraphStore abstraction is essential not just as an escape hatch, but because DuckPGQ's graph functions are limited to PageRank, LCC, and WCC -- confidence-weighted path queries and some neighborhood operations will need to be implemented as SQL queries on the underlying tables rather than through SQL/PGQ syntax. USearch and tantivy-py are mature and well-suited. The async question resolves to async-first with thread-pool wrapping of sync libraries, since Phase 4 requires async and retrofitting is harder than starting async.

**Primary recommendation:** Build on DuckPGQ for graph queries (eliminating Kuzu dependency) with a full-spec GraphStore abstraction. Use DuckDB relational tables as the underlying storage for graph data, with DuckPGQ's SQL/PGQ syntax for pattern matching and path queries. Fall back to standard SQL JOINs for operations DuckPGQ cannot express. Use USearch (not DuckDB VSS) for vectors and tantivy-py for lexical search.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Graph backend strategy
- **DuckDB primary via DuckPGQ, with GraphStore abstraction as escape hatch.** User wants to explore DuckPGQ to eliminate the Kuzu dependency entirely. Build on DuckPGQ but behind a GraphStore interface so an alternative can be swapped in if DuckPGQ has gaps.
- If research shows DuckPGQ can't handle critical graph operations (multi-hop path queries, temporal filtering): **Claude's discretion** to evaluate the tradeoff and pick the best fallback path.
- GraphStore abstraction should be **full-spec** -- all graph operations the spec describes (supersedence chains, provenance traversal, confidence-weighted paths, neighborhood queries, temporal filtering). Not minimal CRUD.
- Portable artifact format: **doesn't matter** to the user -- implementation detail. If graph lives in DuckDB, one fewer file is fine.

#### Day-one data model scope
- **Full schema from day one.** All fields from the spec (confidence, salience, scope, evidence refs, supersedence pointers, validity windows) are present in the Phase 1 schema, even if not all are used until later phases.
- Node types: **7 fixed spec types + generic 'Note' type.** Entity, Event, Fact, Decision, Preference, Task, Summary are the core types. A catch-all Note type handles anything that doesn't fit. No open-ended extensibility.
- Scope: **All three scopes (personal/project/org) supported from day one.** Schema and isolation logic built for all three scopes immediately.
- Embedding model support: **Claude's discretion** on whether to support multiple embedding models simultaneously or one at a time. Evaluate based on spec requirements and practicality.

#### Lifecycle state handling
- **Full transition API in Phase 1.** Developers can call promote(), supersede(), archive() with validation rules (e.g., can't go backwards from Stable to Tentative). Transition logic lives in Phase 1, not deferred to Phase 5.
- Superseded objects: **Stay in place, marked as superseded.** Not moved to a separate partition. Queries can filter them out or include them.
- Supersedence evidence: **Required for automated transitions, optional for manual.** When Phase 5's Organizer supersedes something, evidence is required. When a developer calls supersede() directly, evidence pointer is optional.
- Query defaults: **Filter to active (Tentative + Stable) by default.** Callers must explicitly opt in to see Superseded/Archived objects.

#### Store API surface
- **Unified memory interface.** Single MemoryEngine entry point, not separate store objects. Developers interact with one interface that routes to the right backend.
- **Auto-propagate to all backends.** When a developer calls memory.store(), it writes to the event log AND updates graph AND indexes into vector AND lexical in one call. Developer doesn't think about which backends are involved.
- Async vs sync: **Claude's discretion.** Evaluate based on what downstream phases need (Phase 4 requires async) and what's practical for DuckDB/Tantivy.
- Configuration approach: **Claude's discretion.** Pick whatever serves portability and developer ergonomics best.

### Claude's Discretion
- Embedding model multiplicity (single vs. concurrent models in vector index)
- Async-first vs sync-first API design
- Configuration/initialization approach for the MemoryEngine
- DuckPGQ fallback strategy if gaps are found during research
- Portable artifact file layout

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STOR-01 | System stores all conversational inputs as immutable events in an append-only DuckDB event log | DuckDB Python API supports CREATE TABLE, INSERT, and parameterized queries. UUID type supported. Append-only enforced by application (no UPDATE/DELETE on events table). DuckDB single-writer model serializes concurrent appends safely within one process. |
| STOR-02 | System represents entities as typed graph nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) in Kuzu behind an abstract GraphStore interface | **Kuzu replaced by DuckPGQ per user decision.** DuckPGQ CREATE PROPERTY GRAPH maps DuckDB tables as vertex/edge tables. 8 node types (7 spec + Note) stored in DuckDB tables, exposed as graph vertices via DuckPGQ. GraphStore abstraction is a Python Protocol/ABC. |
| STOR-03 | System represents relationships as typed edges with valid_from, valid_to, confidence, and provenance reference | DuckDB edge tables carry all metadata columns. DuckPGQ MATCH queries can filter on edge properties including temporal fields. WHERE clauses work inside MATCH patterns. |
| STOR-04 | System indexes event content and facts in an HNSW vector index with versioned embedding metadata | USearch 2.23.0 provides HNSW index with integer keys, save/load, filtered search, f16/i8 quantization. Metadata (model, version, dimension) stored in a DuckDB side table keyed by the same integer ID. |
| STOR-05 | System indexes event content and facts in a full-text lexical index (Tantivy) | tantivy-py 0.25.1 provides SchemaBuilder, IndexWriter, Searcher with BM25 scoring. Supports text/integer/float fields, stored fields, custom tokenizers. Persistent index on disk. |
| STOR-06 | System scopes all memory operations by user_id and session_id | All DuckDB tables include user_id and session_id columns. All queries include WHERE user_id = ? AND session_id = ? filtering. USearch filtered_search with predicate. Tantivy query includes user_id term filter. |
| STOR-07 | System tracks memory object lifecycle states (Tentative -> Stable -> Superseded -> Archived) | Lifecycle state is a VARCHAR/ENUM column on node tables. Transition API (promote/supersede/archive) validates state machine rules. Default query filter: WHERE lifecycle_state IN ('tentative', 'stable'). |
| STOR-08 | System maintains supersedence chains with provenance -- superseded facts link to their replacement and the evidence that triggered the change | SUPERSEDES edge type in graph with evidence_id column. DuckPGQ variable-length path queries (`-[s:SUPERSEDES]->+`) can traverse supersedence chains. Superseded nodes retain their data with lifecycle_state = 'superseded'. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| DuckDB | 1.4.4 | Event store (append-only), graph data tables, metadata storage | Embedded, ACID, columnar, single-file. LTS release. Mature Python API. Supports UUID, TIMESTAMP, JSON, ARRAY types natively. |
| DuckPGQ | v0.1.0+ (community ext) | SQL/PGQ graph queries over DuckDB tables | Eliminates separate graph DB dependency. SQL:2023 standard syntax. Pattern matching, variable-length paths, shortest path. Persistent property graphs since v0.1.0. |
| USearch | 2.23.0 | HNSW approximate nearest neighbor vector index | 10x faster than FAISS, f16/i8 quantization, disk-viewable indexes, filtered search, thread-safe. Used internally by DuckDB VSS. Apache-2.0. Actively maintained. |
| tantivy-py | 0.25.1 | BM25 full-text search (Rust via PyO3) | 30x faster than Whoosh. Custom tokenizers, snippet generation, persistent indexes. Actively maintained (Sep 2025 release). |
| Pydantic | 2.12.5 | Domain models, validation, serialization | Industry standard. Rust-backed v2 is 5-50x faster than v1. Used for MemoryObject, Event, Node, Edge models. |
| pydantic-settings | 2.9.1 | Configuration management | Type-safe settings from env vars, .env files, JSON. Nested config, SecretStr support. 12-factor compatible. |
| FastEmbed | 0.7.4 | Default local embedding provider (ONNX, no PyTorch) | Lightweight, CPU-optimized. Default model: BAAI/bge-small-en-v1.5 (384 dims). Generator-based API for memory efficiency. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | latest | Structured logging | Always -- JSON logging for debugging storage operations, query traces. |
| uuid7 or stdlib | - | UUIDv7 generation | DuckDB supports gen_random_uuid() (v4). For sortable IDs use Python uuid7 library or DuckDB's UUIDv7 if available. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| DuckPGQ (graph) | Kuzu 0.11.3 (archived) | Better Cypher support, more graph algorithms, but archived/unmaintained. User explicitly chose DuckPGQ to reduce dependencies. |
| DuckPGQ (graph) | RyuGraph (Kuzu fork) | Active development, Cypher support, but adds a dependency. Falls back here if DuckPGQ proves insufficient. |
| USearch (vectors) | DuckDB VSS extension | Zero extra dependencies, but experimental, float32-only, RAM-only, WAL recovery broken, not production-safe. USearch is strictly superior. |
| tantivy-py (FTS) | DuckDB FTS extension | Zero extra dependencies, but indexes don't auto-update on INSERT, must rebuild manually. tantivy-py is production-grade. |
| FastEmbed (embeddings) | sentence-transformers | More models, cross-encoders, but requires PyTorch (~2GB). FastEmbed is ONNX-only (~100MB). |

**Installation:**
```bash
# Core storage
uv add duckdb==1.4.4
uv add usearch==2.23.0
uv add tantivy==0.25.1

# Models & config
uv add pydantic==2.12.5
uv add pydantic-settings

# Embedding
uv add fastembed==0.7.4

# Supporting
uv add structlog

# Dev
uv add --dev pytest pytest-asyncio ruff mypy
```

Note: DuckPGQ is installed from within DuckDB at runtime:
```python
conn.execute("INSTALL duckpgq FROM community")
conn.execute("LOAD duckpgq")
```

## Architecture Patterns

### Recommended Project Structure
```
src/
└── prme/
    ├── __init__.py
    ├── config.py               # PRMEConfig (pydantic-settings)
    ├── models/                  # Pydantic domain models
    │   ├── __init__.py
    │   ├── base.py              # MemoryObject base, LifecycleState enum
    │   ├── events.py            # Event model (immutable)
    │   ├── nodes.py             # Entity, Fact, Decision, Preference, Task, Summary, Note
    │   └── edges.py             # Edge types with temporal validity
    ├── storage/                 # Storage backends
    │   ├── __init__.py
    │   ├── engine.py            # MemoryEngine (unified interface)
    │   ├── event_store.py       # DuckDB append-only event log
    │   ├── graph_store.py       # GraphStore Protocol + DuckPGQ implementation
    │   ├── vector_index.py      # USearch HNSW wrapper
    │   ├── lexical_index.py     # tantivy-py wrapper
    │   └── embedding.py         # EmbeddingProvider Protocol + FastEmbed impl
    └── types.py                 # Shared type definitions (NodeType, EdgeType, Scope)
```

### Pattern 1: GraphStore as Protocol with DuckPGQ Implementation

**What:** Define a Python Protocol (structural typing) for all graph operations. Implement it with DuckPGQ on top of DuckDB tables. The protocol covers the full spec: create/read/update nodes and edges, neighborhood queries, path traversal, supersedence chains, temporal filtering, and confidence-weighted queries.

**When to use:** Always. This is the escape hatch for DuckPGQ limitations.

**Why Protocol over ABC:** Protocols enable structural subtyping (duck typing). An alternative implementation (e.g., RyuGraph) just needs to implement the same methods without inheriting from a base class. Better for testing (easy mocking) and future swapability.

**Example:**
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class GraphStore(Protocol):
    """Full-spec graph store interface."""

    # Node operations
    async def create_node(self, node: MemoryNode) -> str: ...
    async def get_node(self, node_id: str, *, include_superseded: bool = False) -> MemoryNode | None: ...
    async def query_nodes(
        self,
        node_type: NodeType | None = None,
        user_id: str | None = None,
        scope: Scope | None = None,
        lifecycle_states: list[LifecycleState] | None = None,  # defaults to [TENTATIVE, STABLE]
        valid_at: datetime | None = None,  # temporal filter
        limit: int = 100,
    ) -> list[MemoryNode]: ...

    # Edge operations
    async def create_edge(self, edge: MemoryEdge) -> str: ...
    async def get_edges(
        self,
        source_id: str | None = None,
        target_id: str | None = None,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
    ) -> list[MemoryEdge]: ...

    # Graph traversal
    async def get_neighborhood(
        self,
        node_id: str,
        max_hops: int = 2,
        edge_types: list[EdgeType] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
    ) -> list[MemoryNode]: ...

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        edge_types: list[EdgeType] | None = None,
    ) -> list[str] | None: ...

    # Supersedence
    async def get_supersedence_chain(
        self,
        node_id: str,
        direction: str = "forward",  # "forward" = what replaced this, "backward" = what this replaced
    ) -> list[MemoryNode]: ...

    # Lifecycle transitions
    async def promote(self, node_id: str) -> None: ...
    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        evidence_id: str | None = None,  # optional for manual, required for automated
    ) -> None: ...
    async def archive(self, node_id: str) -> None: ...
```

### Pattern 2: DuckDB Tables as Graph Storage (DuckPGQ Overlay)

**What:** Store all graph data in standard DuckDB tables (nodes table, edges table). Define a DuckPGQ PROPERTY GRAPH as a view layer on top. Use SQL/PGQ for pattern matching and path queries. Fall back to standard SQL JOINs for operations DuckPGQ cannot express (confidence-weighted aggregations, complex temporal joins).

**When to use:** This is the core architecture for the DuckPGQ-based GraphStore implementation.

**Key insight:** The data lives in DuckDB tables. DuckPGQ is a query syntax, not a storage engine. This means:
- All DuckDB SQL features (aggregation, window functions, CTEs) work on graph data directly
- Graph data participates in ACID transactions with event store data
- No separate backup/restore for graph data
- Schema evolution uses standard ALTER TABLE

**Example:**
```python
# Schema creation
def create_graph_schema(conn: duckdb.DuckDBPyConnection):
    # Node tables - one per type for DuckPGQ type discrimination
    # OR single table with type column (simpler, less DuckPGQ-native)

    # Option A: Single nodes table (recommended for simplicity)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            node_type VARCHAR NOT NULL,  -- 'entity','event','fact','decision','preference','task','summary','note'
            user_id VARCHAR NOT NULL,
            session_id VARCHAR,
            scope VARCHAR NOT NULL DEFAULT 'personal',  -- 'personal','project','org'
            content TEXT NOT NULL,
            metadata JSON,
            confidence FLOAT DEFAULT 0.5,
            salience FLOAT DEFAULT 0.5,
            lifecycle_state VARCHAR NOT NULL DEFAULT 'tentative',
            valid_from TIMESTAMP DEFAULT current_timestamp,
            valid_to TIMESTAMP,
            superseded_by UUID,
            evidence_refs JSON,  -- array of event IDs
            created_at TIMESTAMP DEFAULT current_timestamp,
            updated_at TIMESTAMP DEFAULT current_timestamp,
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL REFERENCES nodes(id),
            target_id UUID NOT NULL REFERENCES nodes(id),
            edge_type VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            confidence FLOAT DEFAULT 0.5,
            valid_from TIMESTAMP DEFAULT current_timestamp,
            valid_to TIMESTAMP,
            provenance_event_id UUID,  -- FK to events table
            metadata JSON,
            created_at TIMESTAMP DEFAULT current_timestamp,
        )
    """)

    # DuckPGQ property graph overlay
    conn.execute("""
        CREATE OR REPLACE PROPERTY GRAPH memory_graph
        VERTEX TABLES (
            nodes
        )
        EDGE TABLES (
            edges SOURCE KEY (source_id) REFERENCES nodes (id)
                  DESTINATION KEY (target_id) REFERENCES nodes (id)
        )
    """)


# SQL/PGQ query example: neighborhood
def get_neighborhood_query(node_id: str, max_hops: int = 2):
    return f"""
        FROM GRAPH_TABLE(memory_graph
            MATCH (a:nodes WHERE a.id = '{node_id}')-[e:edges]->{{1,{max_hops}}}(b:nodes)
            COLUMNS (b.id, b.node_type, b.content, b.confidence, b.lifecycle_state,
                     e.edge_type, e.confidence AS edge_confidence)
        )
        WHERE b.lifecycle_state IN ('tentative', 'stable')
    """


# Fallback to standard SQL for confidence-weighted queries
def get_high_confidence_facts(conn, entity_id: str, min_confidence: float = 0.7):
    return conn.execute("""
        SELECT n.* FROM edges e
        JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ?
          AND n.node_type = 'fact'
          AND n.confidence >= ?
          AND n.lifecycle_state IN ('tentative', 'stable')
          AND (n.valid_to IS NULL OR n.valid_to > current_timestamp)
        ORDER BY n.confidence DESC
    """, [entity_id, min_confidence]).fetchall()
```

### Pattern 3: Async-First with Thread-Pool Wrapping

**What:** Define all public APIs as async. Wrap synchronous libraries (DuckDB, USearch, tantivy-py) using `asyncio.to_thread()` to run blocking operations in a thread pool without blocking the event loop.

**When to use:** Always. Phase 4 requires async (INTG-03). Starting sync and retrofitting async is harder than starting async.

**Example:**
```python
import asyncio
import duckdb

class EventStore:
    def __init__(self, db_path: str):
        self._conn = duckdb.connect(db_path)
        self._write_lock = asyncio.Lock()

    async def append_event(self, event: Event) -> str:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._append_event_sync, event
            )

    def _append_event_sync(self, event: Event) -> str:
        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO events (id, timestamp, role, content, content_hash, user_id, session_id, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [event.id, event.timestamp, event.role, event.content,
             event.content_hash, event.user_id, event.session_id,
             event.metadata_json]
        )
        return str(event.id)

    async def get_event(self, event_id: str) -> Event | None:
        return await asyncio.to_thread(self._get_event_sync, event_id)
```

### Pattern 4: MemoryEngine Coordinated Write (Auto-Propagation)

**What:** The MemoryEngine.store() method orchestrates writes across all four backends in a single call. Event store write is the critical first step (source of truth). Graph, vector, and lexical updates follow.

**When to use:** Every store() call. This is the "auto-propagate" behavior the user requested.

**Key constraint:** Event store write MUST succeed before any derived writes. If vector/lexical indexing fails, the event is still persisted (eventual consistency for derived stores).

**Example:**
```python
class MemoryEngine:
    def __init__(self, config: PRMEConfig):
        self._event_store = EventStore(config.db_path)
        self._graph_store = DuckPGQGraphStore(config.db_path)
        self._vector_index = VectorIndex(config.vector_path, config.embedding)
        self._lexical_index = LexicalIndex(config.lexical_path)

    async def store(self, content: str, *, user_id: str, session_id: str | None = None,
                    node_type: NodeType = NodeType.NOTE, scope: Scope = Scope.PERSONAL,
                    metadata: dict | None = None) -> str:
        """Store content across all backends. Returns event ID."""

        # 1. Event store (must succeed first -- source of truth)
        event = Event(
            content=content, user_id=user_id, session_id=session_id,
            role="user", metadata=metadata,
        )
        event_id = await self._event_store.append_event(event)

        # 2. Graph node (with provenance ref to event)
        node = MemoryNode(
            node_type=node_type, content=content, user_id=user_id,
            session_id=session_id, scope=scope,
            evidence_refs=[event_id],
        )
        node_id = await self._graph_store.create_node(node)

        # 3. Vector embedding + index (parallel with lexical)
        embedding_task = self._vector_index.index(node_id, content)
        lexical_task = self._lexical_index.index(node_id, content, user_id=user_id)
        await asyncio.gather(embedding_task, lexical_task)

        return event_id
```

### Anti-Patterns to Avoid

- **Storing graph data separately from DuckDB:** With DuckPGQ, graph data IS DuckDB data. Do not create a separate storage mechanism. The entire point of DuckPGQ is that graph queries run on the same DuckDB tables.
- **Using DuckDB VSS instead of USearch:** DuckDB's VSS extension is experimental, float32-only, RAM-only, and has WAL recovery bugs. USearch is production-grade and what VSS uses internally anyway.
- **Building sync API first, planning to add async later:** Retrofitting async onto a sync codebase requires touching every function signature and call site. Start async, provide sync wrappers via `asyncio.run()`.
- **Separate DuckDB connections for event store and graph store:** Since both use the same DuckDB instance, use a shared connection pool. Thread-local cursors handle concurrency.
- **Putting embedding generation in the hot write path without timeout:** Embedding generation (especially remote API calls) is the slowest part of store(). The event store write should complete fast; embedding can be fire-and-forget or have a timeout with retry.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Vector similarity search | Custom distance computation over arrays | USearch HNSW index | HNSW is algorithmically complex (multi-layer graph, construction heuristics). USearch handles quantization, threading, disk I/O. |
| Full-text search | Custom inverted index or LIKE queries | tantivy-py with BM25 | BM25 scoring, tokenization, stemming, segment merging are deeply non-trivial. tantivy is battle-tested. |
| Embedding generation | HTTP client to OpenAI API directly | FastEmbed (local) or LiteLLM (multi-provider) | Model loading, ONNX optimization, batching, error handling all handled. |
| Configuration validation | Custom YAML/JSON parsing | pydantic-settings | Type coercion, env var binding, nested config, SecretStr, validation errors all handled. |
| UUID generation | Custom timestamp-based IDs | DuckDB gen_random_uuid() or Python uuid7 | UUIDv4 for random IDs. UUIDv7 for sortable timestamp-based IDs. Both well-specified standards. |
| Graph path algorithms | Recursive Python traversal | DuckPGQ ANY SHORTEST + SQL/PGQ path quantifiers | SQL/PGQ path finding is optimized at the query engine level. Python recursion hits stack limits and is orders of magnitude slower. |
| Lifecycle state machine | Ad-hoc if/elif chains | Explicit state enum + transition validation function | State machines need explicit transition rules, error messages, and audit logging. A function with clear allowed transitions is simpler to maintain and test. |

**Key insight:** Every storage backend (DuckDB, USearch, tantivy) is a solved problem with production-grade implementations. The value of Phase 1 is the integration layer (MemoryEngine, GraphStore protocol, typed models) -- not re-implementing storage primitives.

## Common Pitfalls

### Pitfall 1: DuckPGQ Property Graph Requires DROP + RECREATE for Schema Changes

**What goes wrong:** DuckPGQ does not support ALTER PROPERTY GRAPH. Adding a new node type or edge type to the graph requires dropping and recreating the entire property graph definition.

**Why it happens:** DuckPGQ is still a research-stage community extension. ALTER syntax is tracked as a low-priority issue (#43).

**How to avoid:** Design the property graph definition as a function that can be called idempotently. Use `CREATE OR REPLACE PROPERTY GRAPH`. Since the graph definition is just a view over tables (the data lives in DuckDB tables regardless), dropping/recreating the property graph is cheap -- no data is lost or moved.

**Warning signs:** Code that manually manages property graph state or caches the graph definition.

### Pitfall 2: DuckPGQ Segfaults in CTEs and UNION ALL

**What goes wrong:** Open bugs (#276, #249) show segmentation faults when using MATCH inside CTEs or with UNION ALL. These are query engine crashes, not data corruption, but they mean certain query patterns are unusable.

**Why it happens:** DuckPGQ is a community extension with active development. Edge cases in query planning are not fully handled.

**How to avoid:** Test all SQL/PGQ query patterns during development. For queries that trigger crashes, fall back to standard SQL JOINs on the underlying tables. The GraphStore abstraction makes this transparent -- the implementation chooses SQL/PGQ or standard SQL per query pattern.

**Warning signs:** Unexpected process crashes during graph queries. Always catch exceptions around DuckPGQ queries and have a standard SQL fallback.

### Pitfall 3: USearch Keys Are Integers, Not UUIDs

**What goes wrong:** USearch index keys are 64-bit integers. PRME uses UUIDs for node/event IDs. A mapping layer is needed.

**Why it happens:** USearch is optimized for integer keys for performance. UUID-to-integer mapping adds a lookup step.

**How to avoid:** Maintain a DuckDB mapping table (uuid_to_vector_key) that maps UUIDs to auto-incrementing integer keys. When indexing, insert the mapping first, then add the vector with the integer key. When searching, translate integer results back to UUIDs.

**Alternative:** Use DuckDB ROWID as the integer key if the vector corresponds to a specific row. This avoids an extra mapping table.

**Warning signs:** UUID-to-integer conversion that loses information (truncation) or creates collisions.

### Pitfall 4: tantivy-py Index Not Visible Until commit() + reload()

**What goes wrong:** Documents added via `writer.add_document()` are not searchable until `writer.commit()` is called AND the searcher is reloaded via `index.reload()`. If you forget either step, searches return stale results.

**Why it happens:** tantivy uses a segment-based architecture (like Lucene). Uncommitted documents exist only in memory. The searcher reads from committed segments.

**How to avoid:** Always call `writer.commit()` after adding documents. Always call `index.reload()` before creating a new searcher. Wrap this in the LexicalIndex class so callers never forget.

**Warning signs:** Newly stored content not appearing in search results. "I just stored X but search returns nothing."

### Pitfall 5: DuckDB Single-Writer Blocks Concurrent store() Calls

**What goes wrong:** Multiple concurrent `memory.store()` calls all try to write to DuckDB. DuckDB serializes writes internally, but without an explicit async lock, thread contention causes unpredictable behavior.

**Why it happens:** DuckDB's single-writer model means INSERT operations are serialized. With `asyncio.to_thread()`, multiple threads can attempt concurrent writes.

**How to avoid:** Use an `asyncio.Lock()` around all DuckDB write operations. Reads can be concurrent (separate cursor per thread). The write lock ensures only one write transaction at a time.

**Warning signs:** "Transaction conflict" errors. Slow writes under concurrent load. Inconsistent state between event store and graph store.

### Pitfall 6: Forgetting to Filter by lifecycle_state in Graph Queries

**What goes wrong:** Queries return superseded or archived nodes alongside active ones, confusing downstream consumers.

**Why it happens:** SQL/PGQ MATCH patterns don't automatically filter by lifecycle_state. Every query must explicitly include the filter.

**How to avoid:** Build the lifecycle filter into the GraphStore implementation, not into every caller. The GraphStore.query_nodes() and GraphStore.get_neighborhood() methods should default to lifecycle_states=['tentative', 'stable'] unless the caller explicitly opts in to see superseded/archived nodes.

**Warning signs:** Retrieval returning contradictory facts (one active, one superseded). Users seeing information they previously corrected.

## Code Examples

### Event Store Schema and Append

```python
# Source: DuckDB Python API + project spec requirements
import duckdb
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

def create_event_store(conn: duckdb.DuckDBPyConnection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id UUID PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            role VARCHAR NOT NULL,
            content TEXT NOT NULL,
            content_hash VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            session_id VARCHAR,
            metadata JSON,
            created_at TIMESTAMPTZ DEFAULT current_timestamp
        )
    """)
    # Index for common access patterns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(user_id, session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")

def append_event(conn: duckdb.DuckDBPyConnection, role: str, content: str,
                 user_id: str, session_id: str | None = None,
                 metadata: dict | None = None) -> str:
    event_id = str(uuid4())
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO events (id, timestamp, role, content, content_hash, user_id, session_id, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON)",
        [event_id, now, role, content, content_hash, user_id, session_id,
         json.dumps(metadata) if metadata else None]
    )
    return event_id
```

### DuckPGQ Graph Query: Supersedence Chain Traversal

```python
# Source: DuckPGQ docs (duckpgq.org/documentation/sql_pgq/)
def get_supersedence_chain(conn: duckdb.DuckDBPyConnection, node_id: str) -> list[dict]:
    """Follow the supersedence chain forward: what replaced this node?"""
    result = conn.execute(f"""
        FROM GRAPH_TABLE(memory_graph
            MATCH p = ANY SHORTEST
                (a:nodes WHERE a.id = '{node_id}')
                -[e:edges WHERE e.edge_type = 'SUPERSEDES']->+
                (b:nodes)
            COLUMNS (
                b.id AS successor_id,
                b.content AS successor_content,
                b.lifecycle_state,
                path_length(p) AS chain_depth
            )
        )
        ORDER BY chain_depth
    """).fetchall()
    return [dict(zip(['successor_id', 'successor_content', 'lifecycle_state', 'chain_depth'], row))
            for row in result]
```

### USearch Vector Index with Metadata

```python
# Source: USearch GitHub (github.com/unum-cloud/USearch)
import numpy as np
from usearch.index import Index

class VectorIndex:
    def __init__(self, index_path: str, ndim: int = 384, metric: str = 'cos'):
        self._index = Index(ndim=ndim, metric=metric, dtype='f32')
        self._index_path = index_path
        self._next_key = 0

    def add(self, vector: np.ndarray) -> int:
        key = self._next_key
        self._next_key += 1
        self._index.add(key, vector)
        return key

    def search(self, query_vector: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
        matches = self._index.search(query_vector, k)
        return [(m.key, m.distance) for m in matches]

    def filtered_search(self, query_vector: np.ndarray, k: int, predicate) -> list[tuple[int, float]]:
        matches = self._index.filtered_search(query_vector, k, predicate)
        return [(m.key, m.distance) for m in matches]

    def save(self):
        self._index.save(self._index_path)

    def load(self):
        self._index.load(self._index_path)
```

### tantivy-py Lexical Index

```python
# Source: tantivy-py docs (tantivy-py.readthedocs.io)
import tantivy

class LexicalIndex:
    def __init__(self, index_path: str):
        schema_builder = tantivy.SchemaBuilder()
        schema_builder.add_text_field("node_id", stored=True, tokenizer_name="raw")
        schema_builder.add_text_field("content", stored=True, tokenizer_name="en_stem")
        schema_builder.add_text_field("user_id", stored=True, tokenizer_name="raw")
        schema_builder.add_text_field("node_type", stored=True, tokenizer_name="raw")
        self._schema = schema_builder.build()
        self._index = tantivy.Index(self._schema, path=index_path)

    def add_document(self, node_id: str, content: str, user_id: str, node_type: str):
        writer = self._index.writer()
        writer.add_document(tantivy.Document(
            node_id=[node_id],
            content=[content],
            user_id=[user_id],
            node_type=[node_type],
        ))
        writer.commit()

    def search(self, query_text: str, user_id: str, limit: int = 10) -> list[dict]:
        self._index.reload()
        searcher = self._index.searcher()
        # Combine content search with user_id filter
        query = self._index.parse_query(
            f'{query_text} AND user_id:"{user_id}"',
            ["content"]
        )
        results = []
        for score, doc_address in searcher.search(query, limit).hits:
            doc = searcher.doc(doc_address)
            results.append({
                "node_id": doc["node_id"][0],
                "content": doc["content"][0],
                "score": score,
            })
        return results
```

### Lifecycle State Machine

```python
# Source: Project spec (RFC-0001 section 4.3) + CONTEXT.md decisions
from enum import Enum

class LifecycleState(str, Enum):
    TENTATIVE = "tentative"
    STABLE = "stable"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

# Valid transitions
ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.TENTATIVE: {LifecycleState.STABLE, LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED},
    LifecycleState.STABLE: {LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED},
    LifecycleState.SUPERSEDED: {LifecycleState.ARCHIVED},
    LifecycleState.ARCHIVED: set(),  # terminal state
}

def validate_transition(current: LifecycleState, target: LifecycleState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())

def transition_node(conn, node_id: str, target_state: LifecycleState,
                    evidence_id: str | None = None,
                    superseded_by: str | None = None):
    """Transition a node's lifecycle state with validation."""
    current = conn.execute(
        "SELECT lifecycle_state FROM nodes WHERE id = ?", [node_id]
    ).fetchone()
    if not current:
        raise ValueError(f"Node {node_id} not found")

    current_state = LifecycleState(current[0])
    if not validate_transition(current_state, target_state):
        raise ValueError(
            f"Invalid transition: {current_state.value} -> {target_state.value}"
        )

    conn.execute("""
        UPDATE nodes SET
            lifecycle_state = ?,
            superseded_by = ?,
            updated_at = current_timestamp
        WHERE id = ?
    """, [target_state.value, superseded_by, node_id])

    # Create supersedence edge if applicable
    if target_state == LifecycleState.SUPERSEDED and superseded_by:
        conn.execute("""
            INSERT INTO edges (id, source_id, target_id, edge_type, user_id,
                              provenance_event_id, confidence)
            SELECT gen_random_uuid(), ?, ?, 'SUPERSEDES', user_id, ?, 1.0
            FROM nodes WHERE id = ?
        """, [superseded_by, node_id, evidence_id, node_id])
```

## DuckPGQ Capability Assessment

### What DuckPGQ CAN Do (Confirmed)

| Operation | SQL/PGQ Support | Confidence |
|-----------|----------------|------------|
| Pattern matching (1-hop) | `MATCH (a)-[e]->(b)` with WHERE filters | HIGH -- documented, tested |
| Variable-length paths | `->+`, `->*`, `->{1,5}` quantifiers | HIGH -- documented |
| Shortest path | `ANY SHORTEST` modifier | HIGH -- documented |
| Property filtering in MATCH | `WHERE a.prop = value` inside MATCH | HIGH -- documented |
| Path retrieval functions | `vertices(p)`, `edges(p)`, `path_length(p)` | HIGH -- documented |
| Edge direction control | Directed, undirected, bidirectional | HIGH -- documented |
| Integration with SQL aggregation | GROUP BY, HAVING, ORDER BY on GRAPH_TABLE results | HIGH -- documented in DuckDB blog |
| Persistent property graphs | CREATE PROPERTY GRAPH persists across connections | HIGH -- since v0.1.0 with DuckDB 1.1.3 |
| PageRank | `pagerank(graph, vertex_label, edge_label)` | HIGH -- documented |
| Weakly connected components | `weakly_connected_component(graph, ...)` | HIGH -- documented |
| Local clustering coefficient | `local_clustering_coefficient(graph, ...)` | HIGH -- documented |

### What DuckPGQ CANNOT Do (Confirmed Gaps)

| Operation | Status | Workaround | Impact |
|-----------|--------|------------|--------|
| ALTER PROPERTY GRAPH | Not supported (issue #43, low priority) | DROP + CREATE OR REPLACE (cheap, no data loss) | LOW -- property graph is just a view definition |
| OPTIONAL MATCH | Not supported (issue #112, high priority) | Standard SQL LEFT JOIN on underlying tables | MEDIUM -- neighborhood queries that need optional paths require SQL fallback |
| Betweenness centrality | Not implemented (issue #132) | Compute in Python using graph data or use PageRank as proxy | LOW -- not needed in Phase 1, possibly Phase 5 |
| Strongly connected components | Not implemented | WCC available; SCC computable via SQL CTEs if needed | LOW |
| MATCH inside CTEs | Segfault (issue #276) | Avoid; use subqueries or materialize GRAPH_TABLE results first | MEDIUM -- workaround exists but limits query composition |
| UNION ALL with MATCH | Segfault (issue #249) | Run queries separately, combine results in Python | MEDIUM -- workaround exists |
| Named parameters for PageRank | Not supported (issue #187) | Use defaults (damping=0.85, tolerance=1e-6) | LOW |

### Assessment

DuckPGQ handles the critical operations PRME needs: pattern matching for neighborhood queries, variable-length paths for supersedence chain traversal, shortest path for graph distance, and property filtering for temporal/confidence queries. The gaps are real but manageable -- OPTIONAL MATCH and CTE segfaults are the most impactful, but both have standard SQL workarounds since the data lives in DuckDB tables anyway.

**Recommendation: Proceed with DuckPGQ as primary graph backend.** The GraphStore abstraction must include SQL fallback methods for operations where DuckPGQ fails or is unavailable. The DuckPGQ implementation class should try SQL/PGQ first and fall back to standard SQL transparently.

**Fallback strategy if DuckPGQ proves insufficient:** If DuckPGQ crashes are too frequent or path query performance is unacceptable, the fallback is pure SQL on DuckDB tables (recursive CTEs for path traversal, standard JOINs for neighborhood queries). This loses the syntactic elegance of SQL/PGQ but retains the single-DuckDB-file architecture. A Kuzu/RyuGraph fallback should only be considered if the SQL approach is also insufficient.

## Discretion Decisions

### Embedding Model Multiplicity: Single Model at a Time

**Decision:** Support one active embedding model at a time, with full re-embedding capability when switching.

**Rationale:**
- The REQUIREMENTS.md explicitly lists "Multi-model simultaneous embedding" as Out of Scope
- Mixing embeddings from different models in the same vector index produces meaningless similarity scores
- Re-embedding from the event log is a supported operation (deterministic rebuild)
- Schema stores model metadata per vector so the system knows when re-embedding is needed
- Dual-index migration (old + new) during re-embedding is a Phase 5/6 concern

**Implementation:** The vector metadata table stores `embedding_model`, `embedding_version`, `embedding_dim` per entry. The VectorIndex checks these on initialization and warns/refuses if the configured model doesn't match existing vectors.

### Async-First API Design

**Decision:** All public APIs are async. Wrap sync libraries (DuckDB, USearch, tantivy-py) with `asyncio.to_thread()`.

**Rationale:**
- Phase 4 (INTG-03) explicitly requires async support throughout
- DuckDB has no native async Python API; aioduckdb is unmaintained (not on PyPI, no recent releases)
- `asyncio.to_thread()` is stdlib (Python 3.9+), zero dependencies, well-understood pattern
- Starting sync and retrofitting async requires touching every function in the call chain
- Sync wrappers (`asyncio.run()`) are trivial to add for non-async consumers

**Implementation:** Internal methods are sync (e.g., `_append_event_sync`). Public methods are async and delegate via `asyncio.to_thread()`. An `asyncio.Lock()` serializes DuckDB writes. Reads use separate cursors and can be concurrent.

### Configuration Approach: pydantic-settings with Builder Pattern

**Decision:** Use pydantic-settings for configuration with a builder-style MemoryEngine.create() factory.

**Rationale:**
- pydantic-settings provides type-safe config from env vars, .env files, and JSON
- SecretStr for API keys (embedding provider)
- Nested config for sub-components (EventStoreConfig, VectorConfig, etc.)
- Developer ergonomics: `engine = MemoryEngine.create(db_path="./memory.duckdb")` with sensible defaults
- Portability: config serializes to JSON for inclusion in manifest.json

**Implementation:**
```python
from pydantic_settings import BaseSettings
from pydantic import Field

class EmbeddingConfig(BaseSettings):
    provider: str = "fastembed"
    model_name: str = "BAAI/bge-small-en-v1.5"
    dimension: int = 384

class PRMEConfig(BaseSettings):
    db_path: str = "./memory.duckdb"
    vector_path: str = "./vectors.usearch"
    lexical_path: str = "./lexical_index"
    embedding: EmbeddingConfig = EmbeddingConfig()

    model_config = {"env_prefix": "PRME_", "env_nested_delimiter": "__"}
```

### Portable Artifact File Layout

**Decision:** Single directory with DuckDB file + USearch index + tantivy directory + manifest.

**Layout:**
```
memory_pack/
├── memory.duckdb          # Events table + nodes/edges tables (graph data)
├── vectors.usearch        # USearch HNSW index file
├── lexical_index/         # tantivy index directory
└── manifest.json          # Version metadata, config snapshot
```

**Rationale:** Graph data lives inside DuckDB (via DuckPGQ), so no separate graph file. Three files + one directory. The manifest records embedding model, extraction version, scoring weights, and schema version for deterministic rebuild.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Kuzu as embedded graph DB | Kuzu archived (Apple acquisition Oct 2025); DuckPGQ or forks | Oct 2025 | Must use GraphStore abstraction; DuckPGQ eliminates dependency |
| DuckDB VSS for vectors | USearch directly (DuckDB VSS still experimental, WAL bugs) | Ongoing 2025-2026 | Use USearch standalone, not through DuckDB extension |
| DuckPGQ transient graphs | DuckPGQ persistent graphs (since v0.1.0 / DuckDB 1.1.3) | Late 2024 | Property graphs survive across connections; no need to recreate on startup |
| hnswlib for HNSW | USearch (spiritual successor, actively maintained) | 2024 | USearch supports f16/i8, disk views, filtered search |
| aiosqlite pattern for DuckDB | asyncio.to_thread() wrapping | Python 3.9+ | Simpler than dedicated async wrapper library |

**Deprecated/outdated:**
- **Kuzu**: Archived Oct 2025 (Apple acquisition). v0.11.3 is final. Do not use for new projects unless pinned with GraphStore abstraction.
- **hnswlib**: Last release Dec 2023. No Python 3.13 wheels. Use USearch instead.
- **DuckDB VSS for production**: Experimental, WAL recovery broken, float32-only, RAM-only. Fine for prototyping, not for production HNSW.
- **Whoosh**: Pure Python FTS, abandoned 2015. Never use.

## Open Questions

1. **DuckPGQ stability under sustained load**
   - What we know: DuckPGQ has open crash bugs (#276, #249, #280). Pattern matching and path queries work in basic tests.
   - What's unclear: How frequently crashes occur in practice with PRME's query patterns. Whether the segfaults are edge cases or common patterns.
   - Recommendation: Build comprehensive integration tests for all GraphStore operations. If crash rate is >1%, implement SQL-only fallback mode (no DuckPGQ) as a config toggle.

2. **tantivy-py document deletion API**
   - What we know: tantivy-py has `delete_all_documents()` on IndexWriter (added in PR #133). The underlying Rust library supports term-based deletion.
   - What's unclear: Whether tantivy-py exposes `delete_documents(field, term)` for selective deletion. The Python docs don't explicitly document this method.
   - Recommendation: For Phase 1, deletion is not critical (append-only model). If needed for re-indexing, use `delete_all_documents()` + full re-index. Verify selective deletion API during implementation.

3. **DuckDB UUIDv7 compatibility**
   - What we know: DuckDB 1.3.0+ supports UUIDv7 generation, but timestamps in generated values may be incorrect when used outside DuckDB.
   - What's unclear: Whether DuckDB 1.4.4 fixed the UUIDv7 timestamp issue from 1.3.0.
   - Recommendation: Use UUIDv4 (`gen_random_uuid()`) for IDs by default. If sortable IDs are needed, generate UUIDv7 in Python and pass as parameter.

4. **USearch key-to-UUID mapping performance at scale**
   - What we know: USearch uses integer keys. PRME uses UUID node IDs. A mapping table is needed.
   - What's unclear: Performance of the mapping lookup at 100K+ vectors.
   - Recommendation: Use an auto-incrementing integer as the USearch key. Store the mapping in a DuckDB table with an index on both columns. DuckDB columnar storage handles point lookups efficiently. Benchmark at 100K during Phase 1 testing.

## Sources

### Primary (HIGH confidence)
- [DuckPGQ SQL/PGQ documentation](https://duckpgq.org/documentation/sql_pgq/) -- pattern matching, path finding, quantifiers
- [DuckPGQ property graph documentation](https://duckpgq.org/documentation/property_graph/) -- CREATE/DROP, persistence, type discrimination
- [DuckPGQ graph functions](https://duckpgq.org/documentation/graph_functions/) -- PageRank, WCC, LCC (only 3 algorithms available)
- [DuckDB graph queries blog (Oct 2025)](https://duckdb.org/2025/10/22/duckdb-graph-queries-duckpgq) -- practical usage, SQL integration
- [DuckDB concurrency documentation](https://duckdb.org/docs/stable/connect/concurrency) -- single-writer model, MVCC, multi-process limitations
- [DuckDB Python threading](https://duckdb.org/docs/stable/guides/python/multiple_threads) -- thread-local cursors, concurrent reads
- [DuckDB VSS extension](https://duckdb.org/docs/stable/core_extensions/vss) -- experimental status, float32-only, WAL recovery broken
- [USearch GitHub](https://github.com/unum-cloud/USearch) -- Python API, create/add/search, save/load, filtered search, quantization
- [tantivy-py tutorials](https://tantivy-py.readthedocs.io/en/latest/tutorials/) -- schema, indexing, searching, snippets
- [tantivy-py readthedocs](https://tantivy-py.readthedocs.io/en/stable/) -- API reference, field types, tokenizers
- [FastEmbed getting started](https://qdrant.github.io/fastembed/Getting%20Started/) -- TextEmbedding API, model listing, dimensions

### Secondary (MEDIUM confidence)
- [DuckPGQ GitHub issues](https://github.com/cwida/duckpgq-extension/issues) -- 28 open issues, crash bugs (#276, #249), missing ALTER (#43), missing OPTIONAL MATCH (#112)
- [aioduckdb GitHub](https://github.com/kouta-kun/aioduckdb) -- async wrapper pattern, not on PyPI
- [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) -- env vars, nested config, SecretStr
- [DuckDB async discussion](https://github.com/duckdb/duckdb/discussions/3560) -- no native async, thread-wrapping recommended

### Tertiary (LOW confidence)
- [tantivy-py delete_all_documents](https://github.com/quickwit-oss/tantivy-py/releases) -- PR #133 mentioned in release notes, but delete API not fully documented in Python
- [DuckDB UUIDv7 issue](https://github.com/duckdb/duckdb/discussions/11047) -- timestamp accuracy concerns in v1.3.0, unclear if fixed in 1.4.4

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- DuckDB, USearch, tantivy-py are well-documented with verified versions
- DuckPGQ capabilities: MEDIUM -- documented features work, but open crash bugs and missing features create uncertainty
- Architecture patterns: HIGH -- async-first with thread wrapping, Protocol-based abstractions, DuckDB tables as graph storage are all well-established patterns
- Pitfalls: HIGH -- DuckPGQ issues verified via GitHub, DuckDB concurrency model verified via official docs, USearch/tantivy patterns verified via official repos

**Research date:** 2026-02-19
**Valid until:** 2026-03-19 (30 days -- DuckPGQ is actively developed, check for new releases)
