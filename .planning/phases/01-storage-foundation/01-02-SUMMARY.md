---
phase: 01-storage-foundation
plan: 02
subsystem: database
tags: [duckdb, event-store, graph-store, duckpgq, sql-pgq, async, protocol]

# Dependency graph
requires:
  - phase: 01-01
    provides: "Domain models (Event, MemoryNode, MemoryEdge), type enums, lifecycle state machine"
provides:
  - "DuckDB schema with events, nodes, edges tables and 12 indexes"
  - "Async EventStore with append-only event log, retrieval by id/user/session/hash"
  - "GraphStore Protocol (runtime_checkable) defining full-spec graph operations"
  - "DuckPGQGraphStore implementing node/edge CRUD with user scoping and temporal filtering"
  - "DuckPGQ graceful degradation -- SQL-only fallback when extension unavailable"
  - "initialize_database() convenience function for schema bootstrapping"
affects: [01-03, 01-04, 02-ingestion-pipeline]

# Tech tracking
tech-stack:
  added: [pytz]
  patterns: [asyncio-to-thread-wrapping, write-lock-serialization, protocol-structural-typing, graceful-extension-degradation, parameterized-sql-queries, dynamic-where-clause-builder]

key-files:
  created:
    - src/prme/storage/schema.py
    - src/prme/storage/event_store.py
    - src/prme/storage/graph_store.py
    - src/prme/storage/duckpgq_graph.py
  modified:
    - pyproject.toml
    - uv.lock

key-decisions:
  - "DuckPGQ unavailable for DuckDB 1.4.4 on osx_arm64 -- implemented graceful fallback with is_duckpgq_available() flag and SQL-only mode"
  - "All CRUD operations use standard SQL (not SQL/PGQ) so they work with or without DuckPGQ"
  - "Added pytz as dependency required by DuckDB for TIMESTAMPTZ Python bridging"
  - "DuckDB FLOAT precision accepted for confidence/salience (0.9 stores as 0.8999...) -- no precision workaround needed"

patterns-established:
  - "asyncio.to_thread() wrapping for all sync DuckDB calls in public async methods"
  - "asyncio.Lock() for serializing writes to DuckDB (separate lock per store class)"
  - "Parameterized queries (?) for all user data -- no string interpolation in SQL"
  - "Dynamic WHERE clause builder pattern for flexible query filters"
  - "Default lifecycle filter: tentative + stable (callers opt in to see archived/superseded)"
  - "model_validate() for constructing Pydantic models from DB rows with timezone handling"

requirements-completed: [STOR-01, STOR-02, STOR-03, STOR-06]

# Metrics
duration: 6min
completed: 2026-02-19
---

# Phase 1 Plan 2: EventStore and GraphStore Summary

**DuckDB-backed EventStore with append-only event log and GraphStore Protocol with DuckPGQGraphStore CRUD, gracefully degrading without DuckPGQ extension**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-19T18:36:53Z
- **Completed:** 2026-02-19T18:42:34Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Built DuckDB schema with events, nodes, edges tables and 12 indexes covering all spec columns
- Implemented async EventStore with append, get by ID, get by user/session, and get by content hash
- Defined full-spec GraphStore Protocol with runtime_checkable for structural typing
- Implemented DuckPGQGraphStore with node/edge CRUD, user scoping, temporal filtering, and lifecycle defaults
- Discovered DuckPGQ extension unavailable for DuckDB 1.4.4 -- implemented graceful fallback

## Task Commits

Each task was committed atomically:

1. **Task 1: DuckDB schema and EventStore implementation** - `9b30873` (feat)
2. **Task 2: GraphStore Protocol and DuckPGQ implementation** - `c9c4f03` (feat)

## Files Created/Modified
- `src/prme/storage/schema.py` - DuckDB table creation (events/nodes/edges), DuckPGQ install with fallback, property graph definition, initialize_database()
- `src/prme/storage/event_store.py` - Async EventStore wrapping DuckDB with append/get/get_by_user/get_by_hash
- `src/prme/storage/graph_store.py` - Full-spec GraphStore Protocol with node/edge CRUD, traversal, supersedence, lifecycle methods
- `src/prme/storage/duckpgq_graph.py` - DuckPGQGraphStore implementing node/edge CRUD with dynamic query builders
- `pyproject.toml` - Added pytz dependency
- `uv.lock` - Updated lockfile

## Decisions Made
- DuckPGQ extension is not available for DuckDB 1.4.4 on osx_arm64 (HTTP 404 from community extensions). Implemented graceful degradation: install_duckpgq() returns False, create_property_graph() becomes a no-op, is_duckpgq_available() exposes the flag. All graph CRUD uses standard SQL anyway (DuckPGQ is only needed for SQL/PGQ pattern matching in Plan 04).
- Added pytz as an explicit dependency -- DuckDB requires it for TIMESTAMPTZ to Python datetime bridging, but it wasn't in the original dependencies.
- DuckDB FLOAT type stores 0.9 as 0.8999999761581421 (IEEE 754 single precision). This is expected behavior for FLOAT columns and doesn't affect correctness -- query filters use >= comparisons which handle this correctly.
- All CRUD operations use standard SQL (INSERT/SELECT with parameterized queries) rather than SQL/PGQ, ensuring they work identically with or without DuckPGQ.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] DuckPGQ extension unavailable for DuckDB 1.4.4**
- **Found during:** Task 1 (schema creation)
- **Issue:** DuckPGQ community extension returns HTTP 404 for DuckDB v1.4.4 on osx_arm64. Extension only available for DuckDB <= 1.2.0.
- **Fix:** Made install_duckpgq() return a boolean, added is_duckpgq_available() flag, made create_property_graph() a conditional no-op. All graph data still lives in DuckDB tables -- DuckPGQ is only a query layer, not storage. Per research doc: "the fallback is pure SQL on DuckDB tables."
- **Files modified:** src/prme/storage/schema.py
- **Verification:** initialize_database() completes without error, all tables and indexes created, is_duckpgq_available() returns False
- **Committed in:** 9b30873 (Task 1 commit)

**2. [Rule 3 - Blocking] Missing pytz dependency for DuckDB TIMESTAMPTZ**
- **Found during:** Task 1 (EventStore verification)
- **Issue:** DuckDB requires the pytz module for bridging TIMESTAMPTZ values to Python datetime objects. Module was not in project dependencies.
- **Fix:** Added pytz via `uv add pytz` (resolved to pytz==2025.2)
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** EventStore get/query operations return proper datetime objects
- **Committed in:** 9b30873 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** DuckPGQ unavailability is the most significant deviation. However, the GraphStore abstraction was designed precisely for this scenario, and the research doc explicitly planned for SQL fallback. All CRUD operations in this plan use standard SQL anyway. Plan 04 (traversal operations) will implement recursive CTEs instead of SQL/PGQ MATCH patterns.

## Issues Encountered
- DuckDB FLOAT precision causes stored 0.9 to return as 0.8999... -- not a bug, expected IEEE 754 behavior. Verification tests use approximate comparisons.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- EventStore and GraphStore are ready for Plan 03 (vector and lexical indexes)
- Plan 04 (MemoryEngine) will coordinate writes across all backends
- Plan 04 traversal implementations will use recursive CTEs for neighborhood/path queries (SQL fallback for DuckPGQ)
- The is_duckpgq_available() flag allows Plan 04 to conditionally use SQL/PGQ if a future DuckDB version ships the extension

## Self-Check: PASSED

All 4 created files verified on disk. Both task commits (9b30873, c9c4f03) verified in git log.

---
*Phase: 01-storage-foundation*
*Completed: 2026-02-19*
