---
phase: 02-ingestion-pipeline
plan: 03
subsystem: ingestion
tags: [entity-merge, supersedence, deduplication, contradiction-detection, graph-quality]

# Dependency graph
requires:
  - phase: 01-storage-foundation
    provides: "GraphStore Protocol, DuckPGQGraphStore, MemoryNode/MemoryEdge models, schema initialization"
  - phase: 02-01
    provides: "Ingestion package structure, dependencies"
provides:
  - "EntityMerger class for conservative entity deduplication at ingestion"
  - "SupersedenceDetector class for contradiction detection and supersedence chain creation"
  - "PREDICATE_EQUIVALENCES dict for predicate matching"
  - "HAS_FACT EdgeType for entity-to-fact relationships"
affects: [02-04, 03-retrieval-pipeline, 05-organizer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Conservative entity matching: name+type case-insensitive with user isolation"
    - "Predicate equivalence classes for fuzzy contradiction detection"
    - "Ingestion-time quality gates (merge + supersedence) before graph write"

key-files:
  created:
    - src/prme/ingestion/entity_merge.py
    - src/prme/ingestion/supersedence.py
  modified:
    - src/prme/ingestion/__init__.py
    - src/prme/types.py

key-decisions:
  - "Conservative entity merge: match on both name AND entity_type to prevent cross-type merging (e.g., Jordan person vs Jordan country)"
  - "Added HAS_FACT EdgeType for entity-to-fact graph relationships (was missing from original EdgeType enum)"
  - "Predicate equivalence classes are small and explicit (3 groups) rather than using fuzzy/semantic matching"

patterns-established:
  - "Entity deduplication: query all active entities for user, match name.strip().lower() + entity_type"
  - "Supersedence detection: traverse entity edges, check target FACT nodes for predicate match + object mismatch"
  - "Predicate matching: exact match first, then equivalence class lookup via canonical form"

requirements-completed: [INGE-01]

# Metrics
duration: 3min
completed: 2026-02-19
---

# Phase 02 Plan 03: Entity Merge and Supersedence Detection Summary

**Conservative entity deduplication and ingestion-time contradiction detection with predicate equivalence classes for graph quality gates**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-19T21:12:37Z
- **Completed:** 2026-02-19T21:15:03Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- EntityMerger performs conservative entity deduplication: name+type match (case-insensitive) with user isolation
- SupersedenceDetector identifies contradicting facts by predicate match (exact + equivalence classes) with differing objects
- Both modules integrate cleanly with existing GraphStore Protocol and DuckPGQGraphStore implementation

## Task Commits

Each task was committed atomically:

1. **Task 1: Entity merge module** - `1ed343d` (feat)
2. **Task 2: Supersedence detection module** - `3d5504b` (feat)

## Files Created/Modified
- `src/prme/ingestion/entity_merge.py` - EntityMerger class: conservative entity deduplication at ingestion time
- `src/prme/ingestion/supersedence.py` - SupersedenceDetector class: contradiction detection and supersedence chain creation
- `src/prme/ingestion/__init__.py` - Added EntityMerger and SupersedenceDetector re-exports
- `src/prme/types.py` - Added HAS_FACT EdgeType for entity-to-fact relationships

## Decisions Made
- Conservative entity merge matches on BOTH name AND entity_type (case-insensitive) to prevent cross-type merging per research Pitfall 2
- Added HAS_FACT EdgeType which was missing from the original enum but needed for entity-to-fact graph edges
- Predicate equivalence classes kept intentionally small (3 groups: works_at, lives_in, role) per research recommendation to start with exact match and expand only if hit rate is too low

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added HAS_FACT EdgeType**
- **Found during:** Task 1 (Entity merge module)
- **Issue:** The EdgeType enum lacked HAS_FACT, which Task 2 verification script requires for linking entities to facts
- **Fix:** Added `HAS_FACT = "has_fact"` to EdgeType enum in types.py
- **Files modified:** src/prme/types.py
- **Verification:** Task 2 verification script successfully creates HAS_FACT edges and detects supersedence
- **Committed in:** 1ed343d (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** HAS_FACT EdgeType is a necessary addition for entity-to-fact graph modeling. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Entity merge and supersedence detection modules ready for integration into the full ingestion pipeline (02-04)
- Both modules depend only on GraphStore Protocol, making them backend-agnostic
- Predicate equivalence classes can be extended as usage patterns emerge

## Self-Check: PASSED

All files exist, all commits verified.

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-02-19*
