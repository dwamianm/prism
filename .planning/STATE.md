# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-19)

**Core value:** An LLM-powered agent can reliably recall long-term context -- preferences, decisions, relationships -- without resurfacing superseded information or wasting context window tokens.
**Current focus:** Phase 2.2: WriteQueue Contract & Async Safety

## Current Position

**Phase:** 2.2 of 7 (WriteQueue Contract & Async Safety)
**Current Plan:** Not started
**Total Plans in Phase:** 3
**Status:** Ready to plan
**Last Activity:** 2026-02-20

Progress: [███████░░░] 28%

## Performance Metrics

**Velocity:**
- Total plans completed: 11
- Average duration: 3.5min
- Total execution time: 0.63 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-storage-foundation | 4 | 19min | 4.8min |
| 02-ingestion-pipeline | 4 | 15min | 3.8min |
| 02.1-scope-isolation-fix | 3 | 7min | 2.3min |
| 02.2-writequeue-contract-async-safety | 2 | 5min | 2.5min |

**Recent Trend:**
- Last 5 plans: 6min, 5min, 3min, 1min, 2min
- Trend: Stable

*Updated after each plan completion*
| Phase 02.1 P01 | 3min | 2 tasks | 2 files |
| Phase 02 P01 | 3min | 2 tasks | 5 files |
| Phase 01 P03 | 4min | 2 tasks | 3 files |
| Phase 01 P02 | 6min | 2 tasks | 6 files |
| Phase 01 P04 | 6min | 2 tasks | 5 files |
| Phase 02 P03 | 3min | 2 tasks | 4 files |
| Phase 02 P02 | 4min | 2 tasks | 4 files |
| Phase 02 P04 | 5min | 2 tasks | 5 files |
| Phase 02.1 P03 | 1min | 1 tasks | 1 files |
| Phase 02.1 P02 | 3min | 2 tasks | 5 files |
| Phase 02.2 P01 | 3min | 2 tasks | 5 files |
| Phase 02.2 P02 | 2min | 2 tasks | 2 files |
| Phase 02.2 P03 | 5min | 2 tasks | 7 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 7-phase structure derived from 34 requirements across 6 categories, following research-recommended build order (storage -> ingestion -> retrieval -> API -> organizer -> portability -> hardening)
- [Roadmap]: GraphStore abstraction interface is Phase 1 day-one priority due to Kuzu repo being archived (Apple acquisition Oct 2025)
- [Phase 01]: Used (str, Enum) pattern for all domain enums for DuckDB VARCHAR compatibility
- [Phase 01]: Event model uses frozen=True for immutability; MemoryEdge inherits BaseModel (not MemoryObject)
- [Phase 01]: Flexible version pins (>=) in pyproject.toml rather than exact pins, locked via uv.lock
- [Phase 01]: Used post-filter strategy for USearch user_id scoping (filtered_search not available in v2.23.0)
- [Phase 01]: tantivy query parser AND user_id:value syntax for native user_id filtering (no post-filter needed)
- [Phase 01]: Lazy FastEmbed model initialization to avoid blocking constructor with model downloads
- [Phase 01]: DuckPGQ unavailable for DuckDB 1.4.4 -- implemented graceful fallback with SQL-only mode for all graph CRUD
- [Phase 01]: Added pytz dependency required by DuckDB for TIMESTAMPTZ Python bridging
- [Phase 01]: Removed FK constraints from edges table -- DuckDB treats UPDATE as DELETE+INSERT causing FK violations on referenced nodes
- [Phase 01]: All graph traversal uses recursive CTEs as primary path (neighborhood, shortest path, supersedence chains)
- [Phase 01]: MemoryEngine auto-propagation: store() writes to all four backends with graceful degradation on derived index failures
- [Phase 02]: WriteQueue uses asyncio.Queue with None sentinel for clean shutdown (research Pattern 3)
- [Phase 02]: OpenAIEmbeddingProvider.embed() uses asyncio.run() for sync wrapper (runs from asyncio.to_thread in VectorIndex)
- [Phase 02]: create_embedding_provider factory uses simple if/elif dispatch (2 providers, no registry needed)
- [Phase 02]: Conservative entity merge: match on both name AND entity_type (case-insensitive) to prevent cross-type merging
- [Phase 02]: Added HAS_FACT EdgeType for entity-to-fact graph relationships
- [Phase 02]: Predicate equivalence classes kept small (3 groups) -- start exact, expand if hit rate too low
- [Phase 02]: Single ExtractionResult schema with fact_type field for facts/decisions/preferences rather than separate models per type
- [Phase 02]: InstructorExtractionProvider fails open (returns empty ExtractionResult) on extraction errors -- pipeline handles retry
- [Phase 02]: Grounding validation uses conservative substring matching; facts filtered by subject only (object may be paraphrased)
- [Phase 02]: Lazy imports in engine.py and prme/__init__.py to break circular import chain (engine -> pipeline -> entity_merge -> graph_store -> engine)
- [Phase 02]: All MemoryEngine writes serialized through WriteQueue -- store() and ingest() both route through write_queue.submit()
- [Phase 02.1]: DuckDB 1.4.x ALTER TABLE ADD COLUMN does not support NOT NULL constraint -- migration uses DEFAULT only; CREATE TABLE has full NOT NULL DEFAULT
- [Phase 02.1]: Explicit column SELECT list (_EVENT_COLUMNS) replaces SELECT * in EventStore to avoid positional dependency on schema evolution
- [Phase 02.1]: LLM-extracted scope (per entity/fact) overrides ingestion-level scope when present; null falls back to ingestion-level default
- [Phase 02.1]: Entity merge lookup does not filter by scope (conservative merge: same entity in different scopes still merges within same user_id)
- [Phase 02.1]: Scope param defaults to Scope.PERSONAL at all entry points for backward compatibility
- [Phase 02.2]: Async threading pushed into provider (FastEmbed uses to_thread internally) rather than caller (VectorIndex)
- [Phase 02.2]: CachedEmbeddingProvider uses SHA-256 text hash for cache keys -- deduplicates identical content across calls
- [Phase 02.2]: LRU eviction via OrderedDict.popitem(last=False) -- simple, no external deps
- [Phase 02.2]: GraphWriter Protocol uses runtime_checkable for structural typing enforcement of write-only graph interface
- [Phase 02.2]: DuckPGQGraphStore._write_lock retained on delete methods as defense-in-depth (primary serialization via WriteQueue)
- [Phase 02.2]: WriteTracker.rollback() graph-only; orphaned vector/lexical entries logged as warning (cleanup deferred)
- [Phase 02.2]: Per-event WriteTracker in _materialize() isolates rollback scope per event rather than using shared tracker
- [Phase 02.2]: Dual-interface injection: EntityMerger/SupersedenceDetector take GraphStore (reads) + GraphWriter (writes) for structural write prevention
- [Phase 02.2]: Concurrency tests use MockEmbeddingProvider/MockExtractionProvider for CI speed (no LLM or model download needed)

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: Kuzu pinned at 0.11.3 (archived repo) -- GraphStore abstraction must be built before any graph code. Monitor RyuGraph fork and DuckPGQ as migration paths.

## Session Continuity

Last session: 2026-02-20
Stopped at: Completed 02.2-02-PLAN.md (Async EmbeddingProvider with LRU cache and VectorIndex direct await).
Resume file: .planning/phases/02.2-writequeue-contract-async-safety/02.2-03-PLAN.md
