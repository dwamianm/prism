---
phase: 01-storage-foundation
plan: 04
subsystem: database
tags: [duckdb, graph-store, lifecycle, supersedence, memory-engine, unified-api, recursive-cte, asyncio]

# Dependency graph
requires:
  - phase: 01-02
    provides: "EventStore, GraphStore Protocol, DuckPGQGraphStore CRUD, DuckDB schema"
  - phase: 01-03
    provides: "VectorIndex with USearch HNSW, LexicalIndex with tantivy BM25, FastEmbedProvider"
provides:
  - "Complete DuckPGQGraphStore with lifecycle transitions (promote, supersede, archive)"
  - "Graph traversal via recursive CTEs (neighborhood, shortest path, supersedence chains)"
  - "Lifecycle state machine enforcement with descriptive error messages"
  - "SUPERSEDES edge creation with optional provenance tracking"
  - "MemoryEngine unifying EventStore, GraphStore, VectorIndex, LexicalIndex"
  - "Auto-propagation: single store() call writes to all four backends"
  - "Parallel search across vector and lexical backends"
  - "Developer convenience exports: from prme import MemoryEngine, PRMEConfig, NodeType"
affects: [02-ingestion-pipeline, 03-retrieval-pipeline, 05-organizer]

# Tech tracking
tech-stack:
  added: []
  patterns: [recursive-cte-traversal, iterative-bfs, lifecycle-state-machine, auto-propagation-pipeline, asyncio-gather-parallel, graceful-index-failure]

key-files:
  created:
    - src/prme/storage/engine.py
  modified:
    - src/prme/storage/duckpgq_graph.py
    - src/prme/storage/schema.py
    - src/prme/storage/__init__.py
    - src/prme/__init__.py

key-decisions:
  - "Removed FK constraints from edges table -- DuckDB treats UPDATE as DELETE+INSERT internally, causing FK violations when updating nodes referenced by edges"
  - "All graph traversal uses recursive CTEs as primary path -- DuckPGQ unavailable for DuckDB 1.4.4"
  - "Shortest path uses Python-side iterative BFS rather than recursive CTE (DuckDB CTE limitations with complex path tracking)"
  - "Non-UUID evidence_id in supersede() is handled gracefully (logged warning, edge stored without provenance)"
  - "Vector/lexical indexing failures in store() are logged but do not fail the call -- event is persisted, indexes can be rebuilt"

patterns-established:
  - "Lifecycle state machine: validate_transition() before any state change, descriptive ValueError on invalid"
  - "Auto-propagation pipeline: EventStore first (source of truth), GraphStore second, vector+lexical in parallel last"
  - "Graceful degradation on derived indexes: if vector/lexical fail, store() still succeeds"
  - "Bidirectional edge traversal in neighborhood queries (CASE WHEN on source/target)"
  - "Iterative BFS with parent-pointer path reconstruction for shortest path"
  - "Supersedence chain: iterative SQL following SUPERSEDES edges with cycle detection"

requirements-completed: [STOR-06, STOR-07, STOR-08]

# Metrics
duration: 6min
completed: 2026-02-19
---

# Phase 1 Plan 4: GraphStore Advanced Operations and MemoryEngine Summary

**Complete lifecycle state machine (promote/supersede/archive) with recursive CTE graph traversal and unified MemoryEngine auto-propagating to all four backends**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-19T18:45:21Z
- **Completed:** 2026-02-19T18:51:56Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Implemented full lifecycle transition API: promote (Tentative->Stable), supersede (creates SUPERSEDES edge + marks old node), archive (terminal state) with validation via state machine
- Built graph traversal using recursive CTEs: bidirectional N-hop neighborhood, iterative BFS shortest path, supersedence chain following in both directions
- Created MemoryEngine as the single developer entry point: store() auto-propagates to EventStore, GraphStore, VectorIndex, and LexicalIndex in one call
- Enabled `from prme import MemoryEngine, PRMEConfig, NodeType` for developer convenience

## Task Commits

Each task was committed atomically:

1. **Task 1: GraphStore advanced operations (lifecycle, traversal, supersedence)** - `0c24d3f` (feat)
2. **Task 2: MemoryEngine unified interface with auto-propagation** - `7d28c4e` (feat)

## Files Created/Modified
- `src/prme/storage/engine.py` - MemoryEngine: factory create(), store() auto-propagation, search(), lifecycle pass-throughs, close()
- `src/prme/storage/duckpgq_graph.py` - Complete DuckPGQGraphStore: promote/supersede/archive, get_neighborhood, find_shortest_path, get_supersedence_chain
- `src/prme/storage/schema.py` - Removed FK constraints from edges table (DuckDB UPDATE limitation)
- `src/prme/storage/__init__.py` - Re-exports: MemoryEngine, EventStore, GraphStore, DuckPGQGraphStore, VectorIndex, LexicalIndex
- `src/prme/__init__.py` - Developer convenience exports: MemoryEngine, PRMEConfig, NodeType, EdgeType, Scope, LifecycleState

## Decisions Made
- **Removed foreign key constraints from edges table:** DuckDB treats UPDATE as an internal DELETE+INSERT, which triggers FK constraint violations when updating a node that is referenced by edges (e.g., archiving a superseded node). Since edges reference nodes by UUID and referential integrity is enforced at the application level, FK constraints are unnecessary and harmful.
- **Recursive CTE as primary traversal path:** DuckPGQ is confirmed unavailable for DuckDB 1.4.4 (per Plan 02 findings). All traversal uses standard SQL: recursive CTEs for neighborhood, iterative BFS for shortest path, iterative SQL for supersedence chains.
- **Python-side BFS for shortest path:** DuckDB recursive CTEs do not support complex path tracking with string concatenation and cycle detection in the way needed for shortest path. Switched to Python-side BFS with per-level SQL queries.
- **Graceful evidence_id handling:** The supersede() method accepts any string as evidence_id. If it's not a valid UUID, a warning is logged and the SUPERSEDES edge is created without a provenance reference. This supports both manual (casual) and automated (proper UUID) supersedence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Non-UUID evidence_id crashes supersede()**
- **Found during:** Task 1 (lifecycle implementation)
- **Issue:** The plan's verification script passes evidence_id='ev-123' which is not a valid UUID. The MemoryEdge model requires UUID for provenance_event_id. Attempting UUID('ev-123') raises ValueError.
- **Fix:** Added try/except around UUID parsing of evidence_id. If invalid, logs a warning and stores the edge without provenance_event_id.
- **Files modified:** src/prme/storage/duckpgq_graph.py
- **Verification:** Supersede with 'ev-123' completes without error, edge is created.
- **Committed in:** 0c24d3f (Task 1 commit)

**2. [Rule 1 - Bug] DuckDB FK constraints prevent UPDATE on referenced nodes**
- **Found during:** Task 1 (archive implementation)
- **Issue:** DuckDB internally implements UPDATE as DELETE+INSERT. When a node is referenced by edges (e.g., as target_id of a SUPERSEDES edge), the DELETE step triggers a foreign key violation even though the primary key isn't changing.
- **Fix:** Removed REFERENCES nodes(id) from source_id and target_id in the edges table CREATE statement. Referential integrity is enforced at the application level.
- **Files modified:** src/prme/storage/schema.py
- **Verification:** Archive of superseded nodes succeeds. All lifecycle transitions verified.
- **Committed in:** 0c24d3f (Task 1 commit)

**3. [Rule 1 - Bug] DuckDB recursive CTE doesn't support multiple UNION branches**
- **Found during:** Task 1 (neighborhood traversal)
- **Issue:** Initial implementation used 4 UNION branches in a recursive CTE. DuckDB requires exactly one non-recursive base case and one recursive case connected by UNION ALL.
- **Fix:** Restructured to use CASE WHEN for bidirectional traversal within a single base case (UNION ALL) single recursive case. Shortest path switched to Python-side iterative BFS.
- **Files modified:** src/prme/storage/duckpgq_graph.py
- **Verification:** Neighborhood returns correct 2-hop neighbors, shortest path finds correct route.
- **Committed in:** 0c24d3f (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (3 bugs)
**Impact on plan:** All fixes are DuckDB-specific workarounds. The core logic matches the plan exactly. FK removal has no functional impact since the application validates node existence before creating edges. The recursive CTE restructuring produces identical results.

## Issues Encountered
- DuckDB's internal UPDATE-as-DELETE+INSERT behavior is documented but not obvious. This is the second DuckDB-specific workaround after the DuckPGQ unavailability (Plan 02). Future phases should be aware that DuckDB has limitations with FK constraints on frequently-updated tables.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 1 (Storage Foundation) is now COMPLETE. All four storage backends are operational.
- The MemoryEngine provides a single async API for Phase 2 (Ingestion Pipeline) to build upon
- Lifecycle transitions are ready for Phase 5 (Organizer) to use for automated promotion/supersedence
- Hybrid retrieval foundations (vector + lexical search) are ready for Phase 3 (Retrieval Pipeline)
- The `from prme import MemoryEngine` pattern is the entry point for all downstream phases

## Self-Check: PASSED

All 5 created/modified files verified on disk. Both task commits (0c24d3f, 7d28c4e) verified in git log. SUMMARY.md verified.

---
*Phase: 01-storage-foundation*
*Completed: 2026-02-19*
