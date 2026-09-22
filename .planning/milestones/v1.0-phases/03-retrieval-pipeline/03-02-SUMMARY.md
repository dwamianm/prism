---
phase: 03-retrieval-pipeline
plan: 02
subsystem: retrieval
tags: [dateparser, asyncio, bm25, normalization, hybrid-retrieval, query-analysis, candidate-generation]

# Dependency graph
requires:
  - phase: 01-storage-foundation
    provides: "GraphStore, VectorIndex, LexicalIndex backends; MemoryNode model"
  - phase: 03-retrieval-pipeline-01
    provides: "QueryAnalysis, RetrievalCandidate, PackingConfig data models; QueryIntent, RetrievalMode enums"
provides:
  - "Query analysis stage: async analyze_query() with intent classification, entity extraction, temporal signal extraction"
  - "Parallel candidate generation from 4 backends via asyncio.gather with graceful failure handling"
  - "BM25 min-max score normalization to [0,1]"
  - "Candidate merging by node_id with path_count multi-path tracking"
  - "Embedding version mismatch detection with lexical-only fallback"
affects: [03-retrieval-pipeline, 05-hardening]

# Tech tracking
tech-stack:
  added: [dateparser]
  patterns:
    - "dateparser false positive filtering via known-word allowlist for single-word matches"
    - "Incremental hop queries (1-hop, 2-hop, 3-hop) for graph proximity scoring without hop info in API"
    - "asyncio.gather with return_exceptions=True for graceful backend failure isolation"
    - "Batch node resolution after generation (anti-pattern avoidance per research)"

key-files:
  created:
    - src/prme/retrieval/query_analysis.py
    - src/prme/retrieval/candidates.py
  modified: []

key-decisions:
  - "dateparser false positive filtering: single-word matches only trusted if they are known temporal words (e.g. yesterday, monday) or contain digits; prevents 'me'->Monday, 'hour'->datetime misclassification"
  - "Graph proximity via incremental hop queries: runs get_neighborhood at 1, 2, 3 hops and subtracts prior levels to approximate per-node hop distance"

patterns-established:
  - "Query analysis uses pattern matching only (no LLM) per RFC-0005 S3"
  - "Candidate generation returns (candidates, counts_dict) tuple for metadata tracking"
  - "Backend failures in parallel generation produce empty candidates, not crashes"

requirements-completed: [RETR-01, RETR-02, RETR-03]

# Metrics
duration: 4min
completed: 2026-02-21
---

# Phase 03 Plan 02: Query Analysis and Candidate Generation Summary

**Query analysis with dateparser temporal extraction and parallel 4-backend candidate generation with BM25 normalization and path_count merging**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-21T02:51:04Z
- **Completed:** 2026-02-21T02:54:48Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Built query analysis module with intent classification (5 intents via keyword/pattern matching), entity extraction (capitalization + quoted strings), and temporal signal extraction (dateparser with false positive filtering)
- Built parallel candidate generation from graph, vector, lexical, and pinned backends using asyncio.gather with return_exceptions=True for graceful failure handling
- Implemented BM25 min-max normalization producing [0,1] scores with correct edge cases (equal scores -> 1.0, empty -> empty)
- Implemented candidate merging by node_id with path_count tracking, max-score aggregation, and batch node resolution

## Task Commits

Each task was committed atomically:

1. **Task 1: Build query analysis module** - `f45327a` (feat)
2. **Task 2: Build parallel candidate generation and merging** - `bc69f71` (feat)

## Files Created/Modified
- `src/prme/retrieval/query_analysis.py` - Stage 1: analyze_query() with intent classification, entity extraction, temporal signals via dateparser
- `src/prme/retrieval/candidates.py` - Stages 2-3: generate_candidates() with parallel 4-backend generation, BM25 normalization, candidate merging

## Decisions Made
- dateparser false positive filtering: Single-word dateparser matches are only trusted if they appear in a known temporal word set (yesterday, monday, january, etc.) or contain digits. This prevents common words like "me", "hour", "may" from being misinterpreted as temporal expressions and incorrectly triggering TEMPORAL intent.
- Graph proximity via incremental hop queries: Since get_neighborhood() returns a flat list without hop info, we run it at max_hops=1, then max_hops=2 (subtract 1-hop results), then max_hops=3 (subtract 1&2-hop) to determine per-node hop distances for graph_proximity scoring (1.0, 0.7, 0.4).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed dateparser false positive temporal classification**
- **Found during:** Task 1 (Build query analysis module)
- **Issue:** dateparser interprets common English words as dates (e.g. "me" -> Monday, "hour" -> current hour). This caused queries like "tell me about machine learning" to be classified as TEMPORAL intent.
- **Fix:** Added known-word allowlist for single-word dateparser matches. Only single-word matches that are recognized temporal words (yesterday, monday, january, etc.) or contain digits are trusted. Multi-word matches and digit-containing matches pass through unchanged.
- **Files modified:** src/prme/retrieval/query_analysis.py
- **Verification:** "tell me about machine learning" -> SEMANTIC; "why does the system restart every hour" -> FACTUAL; "what happened last week" -> TEMPORAL
- **Committed in:** f45327a (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for correctness -- without it, most queries would be misclassified as TEMPORAL. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Query analysis and candidate generation modules are ready for Plans 03-04
- analyze_query() returns QueryAnalysis suitable for downstream filtering and scoring
- generate_candidates() returns merged candidate list with path_count for composite scoring
- BM25 normalization produces [0,1] scores ready for the composite score formula
- Embedding mismatch detection sets a flag for RetrievalMetadata.embedding_mismatch

## Self-Check: PASSED

All 2 files verified present. Both commit hashes (f45327a, bc69f71) verified in git log.

---
*Phase: 03-retrieval-pipeline*
*Completed: 2026-02-21*
