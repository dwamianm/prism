---
phase: 01-storage-foundation
verified: 2026-02-19T20:00:00Z
status: passed
score: 5/5 must-haves verified
re_verification: null
gaps: []
human_verification:
  - test: "Run end-to-end MemoryEngine integration against actual embedding model"
    expected: "store() propagates to all four backends, vector search returns non-empty results, lexical search returns BM25-ranked results"
    why_human: "FastEmbed downloads model on first call (~100MB ONNX file). Automated grep cannot verify runtime embedding or BM25 scoring works correctly with the actual model."
---

# Phase 1: Storage Foundation Verification Report

**Phase Goal:** A developer can programmatically create, read, and query all four storage backends with typed nodes, edges, temporal validity, lifecycle states, and user/session isolation
**Verified:** 2026-02-19
**Status:** PASSED (with one human verification item)
**Re-verification:** No — initial verification

## Architecture Note: DuckDB Replaces Kuzu

REQUIREMENTS.md STOR-02 and ROADMAP.md Success Criterion 2 reference Kuzu as the graph backend. Per `01-CONTEXT.md` and `01-RESEARCH.md`, this was superseded by an explicit user decision: "User wants to explore DuckPGQ to eliminate the Kuzu dependency entirely." Kuzu was archived (Apple acquisition, Oct 2025) and the user chose DuckPGQ as the graph layer, with GraphStore abstraction as the escape hatch. DuckPGQ itself proved unavailable for DuckDB 1.4.4 (HTTP 404 from community extensions), so all traversal uses recursive CTEs and iterative BFS. The GraphStore Protocol abstraction is in place so Kuzu/RyuGraph can be swapped in later. All observable behaviors (typed nodes, temporal validity, confidence, user isolation) are delivered.

---

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | An event written to DuckDB event store is immutable and retrievable by ID, user_id, and session_id | VERIFIED | `EventStore` in `event_store.py` provides `append()`, `get()`, `get_by_user()` (with `session_id` filter); `Event` model has `ConfigDict(frozen=True)`; schema has `events` table with indexes on `user_id`, `(user_id, session_id)` |
| 2 | A typed node (Entity, Fact, Decision, Preference, Task, Summary) created through GraphStore abstraction is queryable with temporal validity and confidence scores | VERIFIED | `DuckPGQGraphStore.query_nodes()` accepts `valid_at`, `min_confidence`, `node_type` filters; `MemoryNode` has `valid_from`, `valid_to`, `confidence` fields; 8 node types defined in `NodeType` enum; `GraphStore` Protocol is `@runtime_checkable` |
| 3 | A typed edge carries valid_from, valid_to, confidence, and provenance reference; supersedence chains link replaced facts to successors with evidence | VERIFIED | `MemoryEdge` has `valid_from`, `valid_to`, `confidence`, `provenance_event_id` fields; `_supersede_sync()` creates SUPERSEDES edge with `provenance_event_id`, updates `superseded_by` on old node; `get_supersedence_chain()` traverses via iterative SQL |
| 4 | Content embedded into HNSW vector index returns approximate nearest neighbors; each vector record includes embedding model name, version, and dimension metadata | VERIFIED | `VectorIndex` wraps USearch HNSW; `vector_metadata` table stores `embedding_model`, `embedding_version`, `embedding_dim` per entry; `search()` post-filters by `user_id`; `FastEmbedProvider` provides model metadata properties |
| 5 | Content indexed in Tantivy returns BM25-ranked full-text search results | VERIFIED | `LexicalIndex` uses `tantivy.SchemaBuilder` with `en_stem` tokenizer on content field, `raw` tokenizer on `user_id` for exact-match filtering; `_do_search()` builds combined query `{query_text} AND user_id:{user_id}` and returns BM25-ranked hits |

**Score:** 5/5 truths verified

---

## Required Artifacts

### Plan 01-01 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | Project metadata and dependency declarations; contains duckdb | VERIFIED | Contains duckdb>=1.2.0, usearch>=2.16.0, tantivy>=0.22.0, pydantic>=2.12, pydantic-settings>=2.9, fastembed>=0.7.4, structlog |
| `src/prme/types.py` | NodeType, EdgeType, Scope, LifecycleState enums and ALLOWED_TRANSITIONS | VERIFIED | 8 NodeTypes (ENTITY, EVENT, FACT, DECISION, PREFERENCE, TASK, SUMMARY, NOTE), 8 EdgeTypes, 3 Scopes, 4 LifecycleStates; `ALLOWED_TRANSITIONS` dict; `validate_transition()` function |
| `src/prme/models/base.py` | MemoryObject base model with shared fields | VERIFIED | `MemoryObject(BaseModel)` with `id`, `user_id`, `session_id`, `scope`, `created_at`, `updated_at` |
| `src/prme/models/events.py` | Event model (immutable, append-only) | VERIFIED | `Event(MemoryObject)` with `ConfigDict(frozen=True)`; `model_validator` computes SHA-256 `content_hash` from content |
| `src/prme/models/nodes.py` | MemoryNode model for all 8 node types | VERIFIED | `MemoryNode(MemoryObject)` with `node_type`, `confidence`, `salience`, `lifecycle_state`, `valid_from`, `valid_to`, `superseded_by`, `evidence_refs` |
| `src/prme/models/edges.py` | MemoryEdge model with temporal validity | VERIFIED | `MemoryEdge(BaseModel)` with `valid_from`, `valid_to`, `confidence`, `provenance_event_id` |
| `src/prme/config.py` | PRMEConfig with nested sub-configs | VERIFIED | `PRMEConfig(BaseSettings)` with `env_prefix="PRME_"`, `env_nested_delimiter="__"`; `EmbeddingConfig` nested with `env_prefix="PRME_EMBEDDING_"` |

### Plan 01-02 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/prme/storage/event_store.py` | Async EventStore wrapping DuckDB | VERIFIED | `EventStore` class with `append()`, `get()`, `get_by_user()`, `get_by_hash()` all async; `asyncio.Lock()` for write serialization; `asyncio.to_thread()` for sync DuckDB calls |
| `src/prme/storage/graph_store.py` | GraphStore Protocol defining full-spec graph operations | VERIFIED | `@runtime_checkable` `GraphStore(Protocol)` with all methods: `create_node`, `get_node`, `query_nodes`, `create_edge`, `get_edges`, `get_neighborhood`, `find_shortest_path`, `get_supersedence_chain`, `promote`, `supersede`, `archive` |
| `src/prme/storage/duckpgq_graph.py` | DuckPGQ-backed GraphStore implementation | VERIFIED | `DuckPGQGraphStore` class (structural Protocol satisfaction); all Protocol methods implemented (no stubs); recursive CTEs for traversal |
| `src/prme/storage/schema.py` | DuckDB schema creation for events, nodes, edges | VERIFIED | `create_schema()` creates events/nodes/edges tables with all spec columns; `install_duckpgq()` with graceful fallback; `create_property_graph()` conditional; `initialize_database()` convenience function |

### Plan 01-03 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/prme/storage/embedding.py` | EmbeddingProvider Protocol and FastEmbed implementation | VERIFIED | `@runtime_checkable EmbeddingProvider(Protocol)` with `model_name`, `model_version`, `dimension` properties and `embed()` method; `FastEmbedProvider` with lazy init on first `embed()` call |
| `src/prme/storage/vector_index.py` | Async VectorIndex wrapping USearch with metadata tracking | VERIFIED | `VectorIndex` with `index()`, `search()`, `search_by_vector()`, `save()`, `close()`; `vector_metadata` DuckDB table with `embedding_model`, `embedding_version`, `embedding_dim`; post-filter strategy for user_id scoping |
| `src/prme/storage/lexical_index.py` | Async LexicalIndex wrapping tantivy-py | VERIFIED | `LexicalIndex` with `index()`, `search()`, `delete_by_node_id()` (stub, logged), `close()`; `tantivy.SchemaBuilder` with en_stem content field; commit+reload pattern implemented |

### Plan 01-04 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/prme/storage/duckpgq_graph.py` (completed) | Complete DuckPGQGraphStore with traversal, lifecycle, supersedence | VERIFIED | `promote()` validates Tentative->Stable via `validate_transition()`; `supersede()` creates SUPERSEDES edge + marks old node; `archive()` terminal state; `get_neighborhood()` recursive CTE; `find_shortest_path()` iterative BFS; `get_supersedence_chain()` iterative SQL |
| `src/prme/storage/engine.py` | Unified MemoryEngine coordinating all four backends | VERIFIED | `MemoryEngine` with `create()` factory; `store()` auto-propagates to EventStore, GraphStore, VectorIndex (parallel), LexicalIndex (parallel); `search()` runs vector+lexical in parallel; lifecycle pass-throughs |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `models/nodes.py` | `types.py` | `NodeType, LifecycleState, Scope` imports | WIRED | Line 13: `from prme.types import LifecycleState, NodeType` |
| `models/edges.py` | `types.py` | `EdgeType` import | WIRED | Line 11: `from prme.types import EdgeType` |
| `event_store.py` | `models/events.py` | `Event` model import | WIRED | Line 15: `from prme.models import Event` |
| `duckpgq_graph.py` | `graph_store.py` | Structural Protocol satisfaction | WIRED | `DuckPGQGraphStore` implements all 11 Protocol methods; `@runtime_checkable` enables `isinstance(gs, GraphStore)` check |
| `schema.py` | DuckDB | CREATE TABLE DDL | WIRED | `create_schema()` creates events, nodes, edges tables; `install_duckpgq()` attempts DuckPGQ (graceful fallback); `create_property_graph()` conditional on `_duckpgq_available` |
| `vector_index.py` | `embedding.py` | `EmbeddingProvider` used for indexing | WIRED | Line 17: `from prme.storage.embedding import EmbeddingProvider`; `embedding_provider.embed()` called in `index()` and `search()` |
| `vector_index.py` | DuckDB | `vector_metadata` table | WIRED | Constructor calls `_init_metadata_table()`; `index()` inserts model metadata; `search_by_vector()` queries `vector_metadata` for user_id filtering |
| `lexical_index.py` | tantivy | SchemaBuilder, IndexWriter, Searcher | WIRED | Line 11: `import tantivy`; `tantivy.SchemaBuilder()`, `tantivy.Index()`, `index.writer()`, `index.searcher()` all used |
| `engine.py` | `event_store.py` | `self._event_store` | WIRED | Line 20: `from prme.storage.event_store import EventStore`; `self._event_store.append(event)` in `store()` |
| `engine.py` | `duckpgq_graph.py` | `self._graph_store` | WIRED | Line 19: `from prme.storage.duckpgq_graph import DuckPGQGraphStore`; `self._graph_store.create_node()`, `promote()`, `supersede()`, `archive()` all used |
| `engine.py` | `vector_index.py` | `self._vector_index` | WIRED | Line 25: `from prme.storage.vector_index import VectorIndex`; `self._vector_index.index()` in `store()`; `self._vector_index.search()` in `search()` |
| `engine.py` | `lexical_index.py` | `self._lexical_index` | WIRED | Line 22: `from prme.storage.lexical_index import LexicalIndex`; `self._lexical_index.index()` in `store()`; `self._lexical_index.search()` in `search()` |
| `__init__.py` (pkg) | `engine.py` | `from prme import MemoryEngine` | WIRED | Line 11: `from prme.storage.engine import MemoryEngine`; exported in `__all__` |

---

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|----------------|-------------|--------|----------|
| STOR-01 | 01-02 | Immutable append-only DuckDB event log | SATISFIED | `EventStore` with `Event(frozen=True)`; events table with PK; no UPDATE path |
| STOR-02 | 01-02 | Typed graph nodes (7 spec types) behind abstract GraphStore | SATISFIED (with deviation) | 8 NodeTypes (7 spec + NOTE catch-all) in DuckPGQGraphStore behind `GraphStore(Protocol)`. Kuzu replaced by DuckDB+recursive CTEs per documented user decision in `01-CONTEXT.md`. Observable behavior (typed nodes, queryable, temporal validity) is delivered. |
| STOR-03 | 01-02 | Typed edges with valid_from, valid_to, confidence, provenance; supersedence chains | SATISFIED | `MemoryEdge` carries all 4 fields; `_supersede_sync()` creates SUPERSEDES edge with `provenance_event_id`; `get_supersedence_chain()` traverses chain iteratively with cycle detection |
| STOR-04 | 01-03 | HNSW vector index with versioned embedding metadata | SATISFIED | `VectorIndex` wraps USearch HNSW; `vector_metadata` table stores `embedding_model`, `embedding_version`, `embedding_dim` per vector |
| STOR-05 | 01-03 | Full-text lexical index (Tantivy) | SATISFIED | `LexicalIndex` wraps tantivy-py with BM25, `en_stem` tokenizer, commit+reload pattern |
| STOR-06 | 01-01, 01-02, 01-03 | All operations scoped by user_id and session_id | SATISFIED | All query methods require or accept `user_id`; `MemoryObject` base carries `user_id`/`session_id`; EventStore `get_by_user()` WHERE clause; GraphStore `query_nodes()` user filter; VectorIndex post-filters by user_id; LexicalIndex `user_id:{user_id}` query term |
| STOR-07 | 01-01, 01-04 | Lifecycle states Tentative -> Stable -> Superseded -> Archived | SATISFIED | `LifecycleState` enum with 4 states; `ALLOWED_TRANSITIONS` dict; `validate_transition()` function; `promote()`, `supersede()`, `archive()` all validate before transitioning; ValueError on invalid transition |
| STOR-08 | 01-04 | Supersedence chains with provenance | SATISFIED | `_supersede_sync()` updates old node with `superseded_by = new_node_id` and creates SUPERSEDES edge with `provenance_event_id`; `get_supersedence_chain()` traverses in "forward" (what replaced this) or "backward" (what this replaced) directions with cycle detection |

**All 8 requirements satisfied.**

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `storage/lexical_index.py` | 154-171 | `delete_by_node_id()` is a stub (logs warning, does nothing) | Info | Documented stub; deletion not required for Phase 1 append-only model. No blocking impact. |

No blocker or warning-level anti-patterns found. The one stub (`delete_by_node_id`) is explicitly documented as intentional for Phase 1 and does not affect any phase goal.

---

## Human Verification Required

### 1. End-to-End Embedding and Retrieval

**Test:** In a temp directory, run:
```python
import asyncio, tempfile, os
from prme import MemoryEngine, PRMEConfig, NodeType

async def test():
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, 'lexical'), exist_ok=True)
        config = PRMEConfig(
            db_path=os.path.join(td, 'memory.duckdb'),
            vector_path=os.path.join(td, 'vectors.usearch'),
            lexical_path=os.path.join(td, 'lexical'),
        )
        engine = await MemoryEngine.create(config)
        eid = await engine.store('Python is great for data science', user_id='u1', node_type=NodeType.FACT)
        results = await engine.search('data science', 'u1')
        print('event_id:', eid)
        print('vector_results:', results['vector_results'])
        print('lexical_results:', results['lexical_results'])
        await engine.close()

asyncio.run(test())
```
**Expected:** `vector_results` has at least 1 entry with `score > 0`, `lexical_results` has at least 1 entry with `score > 0`. Both contain `node_id` matching the stored node.
**Why human:** FastEmbed downloads ~100MB ONNX model on first call. Cannot verify actual embedding quality or BM25 scoring effectiveness programmatically without running the model.

---

## Gaps Summary

No gaps. All phase goal must-haves verified against the codebase.

The only notable deviation from the written requirements is STOR-02's mention of Kuzu, which was superseded by an explicit user decision documented in the phase context (`01-CONTEXT.md`, `01-RESEARCH.md`) to use DuckPGQ/DuckDB instead. The abstract `GraphStore(Protocol)` interface provides the escape hatch to swap in Kuzu/RyuGraph in future phases if needed. The observable behavior (typed nodes queryable by type, temporal validity, confidence, and user_id) is fully delivered.

---

_Verified: 2026-02-19_
_Verifier: Claude (gsd-verifier)_
