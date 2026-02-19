# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-19)

**Core value:** An LLM-powered agent can reliably recall long-term context -- preferences, decisions, relationships -- without resurfacing superseded information or wasting context window tokens.
**Current focus:** Phase 2: Ingestion Pipeline

## Current Position

**Phase:** 2 of 7 (Ingestion Pipeline)
**Current Plan:** 4
**Total Plans in Phase:** 4
**Status:** Phase complete — ready for verification
**Last Activity:** 2026-02-19

Progress: [██████░░░░] 21%

## Performance Metrics

**Velocity:**
- Total plans completed: 7
- Average duration: 4.3min
- Total execution time: 0.50 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-storage-foundation | 4 | 19min | 4.8min |
| 02-ingestion-pipeline | 4 | 15min | 3.8min |

**Recent Trend:**
- Last 5 plans: 3min, 6min, 4min, 6min, 5min
- Trend: Stable

*Updated after each plan completion*
| Phase 02 P01 | 3min | 2 tasks | 5 files |
| Phase 01 P03 | 4min | 2 tasks | 3 files |
| Phase 01 P02 | 6min | 2 tasks | 6 files |
| Phase 01 P04 | 6min | 2 tasks | 5 files |
| Phase 02 P03 | 3min | 2 tasks | 4 files |
| Phase 02 P02 | 4min | 2 tasks | 4 files |
| Phase 02 P04 | 5min | 2 tasks | 5 files |

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

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: Kuzu pinned at 0.11.3 (archived repo) -- GraphStore abstraction must be built before any graph code. Monitor RyuGraph fork and DuckPGQ as migration paths.

## Session Continuity

Last session: 2026-02-19
Stopped at: Completed 02-04-PLAN.md (IngestionPipeline orchestrator + MemoryEngine integration). Phase 2 complete.
Resume file: Next phase (03-retrieval-pipeline)
