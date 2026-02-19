---
phase: 01-storage-foundation
plan: 03
subsystem: database
tags: [usearch, tantivy, fastembed, vector-search, bm25, hnsw, embeddings, full-text-search]

# Dependency graph
requires:
  - phase: 01-01
    provides: "prme Python package with core dependencies, domain types, storage package placeholder"
provides:
  - "EmbeddingProvider Protocol for swappable embedding backends"
  - "FastEmbedProvider with lazy ONNX model initialization (BAAI/bge-small-en-v1.5, 384 dims)"
  - "VectorIndex wrapping USearch HNSW with DuckDB vector_metadata table for UUID-key mapping"
  - "LexicalIndex wrapping tantivy-py with English stemming and BM25 ranking"
  - "User_id scoping on both vector and lexical search queries"
affects: [01-04, 02-ingestion-pipeline]

# Tech tracking
tech-stack:
  added: [usearch, tantivy, fastembed]
  patterns: [protocol-based-abstraction, lazy-model-initialization, async-to-thread-wrapping, post-filter-user-scoping, commit-reload-pattern]

key-files:
  created:
    - src/prme/storage/embedding.py
    - src/prme/storage/vector_index.py
    - src/prme/storage/lexical_index.py
  modified: []

key-decisions:
  - "Used post-filter strategy for USearch user_id scoping (USearch 2.23.0 lacks filtered_search method)"
  - "tantivy query parser with AND user_id:value syntax for user_id filtering (works natively, no post-filter needed)"
  - "Lazy FastEmbed model initialization to avoid blocking constructor with model downloads"
  - "Writer-per-operation pattern for tantivy (create writer, add, commit, reload per index call)"

patterns-established:
  - "EmbeddingProvider Protocol: runtime_checkable with model_name/version/dimension/embed()"
  - "asyncio.to_thread() wrapping for all sync library calls (USearch, tantivy, FastEmbed)"
  - "DuckDB sequence + metadata table for mapping external integer keys to UUIDs"
  - "asyncio.Lock for write serialization on shared mutable state"
  - "Immediate searchability: commit + reload after every write"

requirements-completed: [STOR-04, STOR-05, STOR-06]

# Metrics
duration: 4min
completed: 2026-02-19
---

# Phase 1 Plan 3: Vector Index and Lexical Index Summary

**USearch HNSW vector search and tantivy BM25 lexical search with FastEmbed embeddings, user_id scoping, and DuckDB metadata tracking**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-19T18:36:49Z
- **Completed:** 2026-02-19T18:40:59Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Built EmbeddingProvider Protocol and FastEmbedProvider with lazy ONNX model loading (384-dim bge-small-en-v1.5)
- Created VectorIndex wrapping USearch HNSW with DuckDB vector_metadata table mapping integer keys to node UUIDs with full embedding model tracking
- Created LexicalIndex wrapping tantivy-py with English stemming tokenization and BM25 ranking
- Implemented user_id scoping on both indexes: post-filter for USearch, native query syntax for tantivy
- Both indexes persist to disk and reload correctly

## Task Commits

Each task was committed atomically:

1. **Task 1: EmbeddingProvider Protocol and VectorIndex with USearch** - `829182e` (feat)
2. **Task 2: LexicalIndex with tantivy-py BM25 search** - `c2dd8cb` (feat)

## Files Created/Modified
- `src/prme/storage/embedding.py` - EmbeddingProvider Protocol and FastEmbedProvider with lazy model init
- `src/prme/storage/vector_index.py` - Async VectorIndex wrapping USearch with DuckDB metadata
- `src/prme/storage/lexical_index.py` - Async LexicalIndex wrapping tantivy-py with BM25 search

## Decisions Made
- **USearch user_id filtering: post-filter** - USearch 2.23.0 does not expose a `filtered_search` method. Used post-filter strategy: retrieve 3x candidates from USearch, then filter by user_id via DuckDB metadata lookup. Sufficient for Phase 1 scale.
- **tantivy user_id filtering: native query syntax** - tantivy-py's query parser supports `AND user_id:value` syntax for field-specific filtering, so no post-filter needed. This is more efficient than post-filtering.
- **Lazy model initialization** - FastEmbed model download can take seconds. Constructor stores config only; actual TextEmbedding is created on first embed() call.
- **Writer-per-operation for tantivy** - Create IndexWriter per index() call with 50MB heap, add document, commit, reload. Avoids holding long-lived writer references that could conflict.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] USearch filtered_search does not exist in v2.23.0**
- **Found during:** Task 1 (VectorIndex implementation)
- **Issue:** Plan specified using USearch `filtered_search` with a callable predicate. The installed USearch 2.23.0 does not have this method (confirmed via `hasattr` check).
- **Fix:** Used the plan's documented alternative: post-filter approach. Retrieve 3x k results from USearch without filtering, then filter by user_id via DuckDB metadata lookup.
- **Files modified:** src/prme/storage/vector_index.py
- **Verification:** Vector search correctly returns only user-scoped results in all tests.
- **Committed in:** 829182e (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minimal -- the plan explicitly documented this as an alternative approach. User_id isolation verified.

## Issues Encountered
None -- both libraries (USearch, tantivy-py) worked as documented in the research.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- VectorIndex and LexicalIndex are ready for integration into the unified MemoryEngine (Plan 04)
- EmbeddingProvider Protocol enables future swapping of embedding models
- vector_metadata table tracks model info per vector for re-embedding detection after model switches
- Both indexes support async operations via asyncio.to_thread wrapping, ready for Phase 4 FastAPI integration

## Self-Check: PASSED

All 3 created files verified on disk. Both task commits (829182e, c2dd8cb) verified in git log.

---
*Phase: 01-storage-foundation*
*Completed: 2026-02-19*
