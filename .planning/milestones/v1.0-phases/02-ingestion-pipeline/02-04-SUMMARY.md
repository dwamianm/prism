---
phase: 02-ingestion-pipeline
plan: 04
subsystem: ingestion
tags: [asyncio, write-queue, extraction, entity-merge, supersedence, dateparser, pipeline]

# Dependency graph
requires:
  - phase: 02-ingestion-pipeline/02-01
    provides: "WriteQueue, embedding providers"
  - phase: 02-ingestion-pipeline/02-02
    provides: "ExtractionProvider, ExtractionResult schema, grounding validation"
  - phase: 02-ingestion-pipeline/02-03
    provides: "EntityMerger, SupersedenceDetector"
provides:
  - "IngestionPipeline class orchestrating two-phase ingestion"
  - "MemoryEngine.ingest() and ingest_batch() methods"
  - "WriteQueue integration for all MemoryEngine write operations"
  - "IngestionPipeline convenience export from prme package"
affects: [03-retrieval-pipeline, 05-organizer, 06-portability]

# Tech tracking
tech-stack:
  added: [dateparser]
  patterns: [two-phase-ingestion, write-queue-serialization, lazy-import-circular-avoidance, exponential-backoff-retry]

key-files:
  created:
    - src/prme/ingestion/pipeline.py
  modified:
    - src/prme/storage/engine.py
    - src/prme/__init__.py
    - src/prme/ingestion/__init__.py
    - src/prme/storage/__init__.py

key-decisions:
  - "Lazy imports in engine.py to break circular import chain (engine -> pipeline -> entity_merge -> graph_store -> engine)"
  - "Module-level __getattr__ in prme/__init__.py for lazy IngestionPipeline import"
  - "Lambda default args for write_queue.submit closures to capture loop variables correctly"
  - "Relationship type to EdgeType mapping with RELATES_TO fallback for unrecognized types"

patterns-established:
  - "Two-phase ingestion: Phase 1 persists event immediately, Phase 2 extracts in background"
  - "All engine writes serialized through WriteQueue"
  - "Lazy imports for cross-package circular dependency resolution"

requirements-completed: [INGE-01, INGE-04, INGE-05]

# Metrics
duration: 5min
completed: 2026-02-19
---

# Phase 2 Plan 4: Ingestion Pipeline Orchestrator Summary

**IngestionPipeline orchestrating two-phase ingestion with write queue serialization, entity merge, supersedence detection, grounding validation, temporal resolution via dateparser, and exponential backoff retry**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-19T21:19:52Z
- **Completed:** 2026-02-19T21:24:56Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- IngestionPipeline class with Phase 1 (immediate event persist + lexical index) and Phase 2 (LLM extraction + materialization)
- Entity merge, grounding validation, supersedence detection, temporal resolution all wired into materialization flow
- MemoryEngine.ingest() and ingest_batch() as developer-facing ingestion methods
- All MemoryEngine writes (store, ingest) serialized through WriteQueue for DuckDB safety
- Clean shutdown flow: pipeline.shutdown() -> write_queue.stop() -> backend close

## Task Commits

Each task was committed atomically:

1. **Task 1: IngestionPipeline orchestrator with two-phase ingestion** - `da90b8f` (feat)
2. **Task 2: Integrate pipeline into MemoryEngine with write queue** - `a59e6d4` (feat)

## Files Created/Modified
- `src/prme/ingestion/pipeline.py` - IngestionPipeline class with ingest(), ingest_batch(), _extract_and_materialize(), _materialize(), _resolve_temporal(), _schedule_retry(), shutdown()
- `src/prme/storage/engine.py` - Added write queue and pipeline integration, ingest()/ingest_batch() methods, updated create() and close()
- `src/prme/__init__.py` - Added IngestionPipeline to convenience exports via lazy import
- `src/prme/ingestion/__init__.py` - Added IngestionPipeline to ingestion package re-exports
- `src/prme/storage/__init__.py` - Added WriteQueue to storage package re-exports

## Decisions Made
- **Lazy imports for circular dependency**: engine.py imports IngestionPipeline and create_extraction_provider inside create() method body rather than at module level. prme/__init__.py uses module __getattr__ for lazy IngestionPipeline import. This breaks the circular chain: engine -> pipeline -> entity_merge -> graph_store -> storage.__init__ -> engine.
- **Lambda capture pattern**: Used default argument binding (lambda ev=event: ...) in write_queue.submit() calls to avoid late-binding closure issues in loops.
- **Relationship type mapping**: Extracted relationship types map to EdgeType enum values with RELATES_TO as fallback for any unrecognized type strings from LLM extraction.
- **create_embedding_provider factory**: Replaced hardcoded FastEmbedProvider in engine.create() with the config-driven factory function from the embedding module.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Resolved circular import chain**
- **Found during:** Task 2 (MemoryEngine integration)
- **Issue:** Adding `from prme.ingestion.pipeline import IngestionPipeline` at module level in engine.py created a circular import: engine.py -> pipeline.py -> entity_merge.py -> graph_store.py -> storage/__init__.py -> engine.py
- **Fix:** Used lazy imports inside create() method for ingestion modules, and module __getattr__ in prme/__init__.py for IngestionPipeline
- **Files modified:** src/prme/storage/engine.py, src/prme/__init__.py
- **Verification:** Both `from prme import IngestionPipeline` and full engine integration test pass
- **Committed in:** a59e6d4 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Circular import resolution was necessary for the module to load. Lazy import is a standard Python pattern. No scope creep.

## Issues Encountered
None beyond the circular import handled above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 2 (Ingestion Pipeline) is now complete: all four plans delivered
- Full ingestion flow operational: `engine.ingest("message", user_id="u1")` persists event, extracts entities/facts/relationships, merges entities, detects supersedence, and indexes across all four backends
- Ready for Phase 3 (Retrieval Pipeline): hybrid search, re-ranking, context packing can build on top of the indexed data
- ExtractionProvider requires LLM API keys at runtime (OPENAI_API_KEY etc.) but construction is lazy -- no keys needed until first extraction call

## Self-Check: PASSED

- [x] src/prme/ingestion/pipeline.py exists
- [x] .planning/phases/02-ingestion-pipeline/02-04-SUMMARY.md exists
- [x] Commit da90b8f found
- [x] Commit a59e6d4 found

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-02-19*
