# Review — issue-41 index deletion on supersede/archive

## Files Changed
- `src/prme/storage/lexical_index.py` — real `delete_by_node_id` (tantivy `delete_documents_by_term` + own short-lived writer + commit/reload), replacing the warn-only stub.
- `src/prme/storage/vector_index.py` — new `delete_by_node_id`/`_do_delete` (usearch `Index.remove` + `vector_metadata` delete + immediate save); added module logger.
- `src/prme/storage/pg/vector_index.py` — new `delete_by_node_id` (nulls `nodes.embedding`) for backend parity; removed pre-existing unused `numpy` import.
- `src/prme/storage/engine.py` — `supersede()`/`archive()` now evict via new `_evict_from_indexes()`; `consolidate_knowledge()` upserts the entity-profile SUMMARY (archive+evict prior) instead of appending duplicates.
- `src/prme/storage/write_queue.py` — `WriteTracker.rollback()` gains optional `vector_index`/`lexical_index` and evicts orphaned entries; stale "cleanup deferred" comment replaced.
- `src/prme/ingestion/pipeline.py` — passes the indexes into `tracker.rollback()`.
- `src/prme/organizer/jobs.py` — new `index_compaction` job reconciling `vector_metadata` against active nodes; registered in `ALL_JOBS` + dispatch; uses `ACTIVE_LIFECYCLE_STATES`.
- `docs/RFC-0015-Self-Organizing-Memory.md` — Available Jobs table updated (snapshot_generation, consolidate, index_compaction).

## Approach Summary
Superseded/archived/rolled-back content is now evicted from the vector (usearch + DuckDB metadata) and lexical (tantivy) indexes at the lifecycle transition, with a best-effort contract (graph transition never undone by an index failure). The `index_compaction` organizer job is the backstop that reconciles index drift left by any failed inline eviction. `consolidate_knowledge` upserts profiles to stop duplicate SUMMARY growth. Eviction is reconstructable from the append-only event log, so it is non-destructive.

## Must Fix
1. `PgVectorIndex` was missing `delete_by_node_id` → `AttributeError` on the PG backend path. **Fixed** (added method, returns int count, mirrors DuckDB contract).
2. Duplicated active-states literal in the compaction job. **Fixed** (now uses `ACTIVE_LIFECYCLE_STATES` from `prme.types`, dynamic placeholder count).

## Should Fix
3. Silent `except Exception: pass` on usearch `remove`. **Fixed** (narrowed to `(KeyError, ValueError, RuntimeError)` + debug log).
4. RFC-0015 job table outdated. **Fixed** (three missing jobs added).

## Investigated and Rejected (verified non-issues)
- "tantivy writer lock conflict in `_do_delete`": `_commit_locked` releases `self._writer` synchronously before the delete writer is created; verified by a delete-after-buffered-add + re-add test. No conflict.
- "SQL `list(active)` with `(?, ?, ?)` is unsafe/broken": verified DuckDB binds a Python list positionally and correctly; query returns expected rows. (Still switched to dynamic placeholders for robustness.)
- Lambda closure binding in eviction/rollback: correct default-arg capture; submit accepts the coroutine-factory lambda as used everywhere else.
- OrganizerConfig enable-flag for the job: intentionally always-on (correctness backstop, same as `tombstone_sweep` which also has no flag).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| SQL injection | PASS | All deletes/reads parameterized; no interpolation of user data. |
| Tenant isolation | PASS | `node_id` is a globally-unique UUID, 1:1 with a user; callers operate on in-scope nodes; consolidate queries are `user_id`-scoped. |
| Destructive ops | PASS | Index entries only; event log and graph untouched (archive is a state transition); rebuildable. |
| PII in logs | PASS | Only `node_id`/`vector_key` logged, never content. |

## Pattern Consistency Assessment
New delete methods mirror existing `index()`/`_do_*` lock + `asyncio.to_thread` split. The job matches the `_job_*(job_name, engine, config, budget_ms)->JobResult` shape, budget checks, `time.monotonic` timing, and `getattr(engine, "_conn")` pattern used by `tombstone_sweep`. Backend parity restored (both PG indexes now have `delete_by_node_id`).

## Redundancy Check
`_evict_from_indexes` (lifecycle transitions) and `WriteTracker.rollback` eviction (materialization failure) are different contexts sharing the same `delete_by_node_id` primitives — not duplication. No new dependencies (usearch.remove, tantivy.delete_documents_by_term already present).

## Wiring Findings
Job registered in both `ALL_JOBS` and dispatch; runs by default via `organize()`. `tests/test_organizer.py` and `tests/test_self_organizing_integration.py` assert `set(jobs_run)==set(ALL_JOBS)` and `jobs_skipped==[]` — the new job runs cleanly on the real-DuckDB fixtures (vector_metadata + nodes tables present), so those assertions still hold. Only caller of `rollback` updated.

## Resolution Status
| # | Severity | Status |
|---|---|---|
| 1 | Must Fix | Resolved |
| 2 | Must Fix | Resolved |
| 3 | Should Fix | Resolved |
| 4 | Should Fix | Resolved |
