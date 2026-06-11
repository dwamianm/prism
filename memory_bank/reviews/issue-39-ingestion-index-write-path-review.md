# Review — issue-39-ingestion-index-write-path

## Files Changed
- `src/prme/storage/vector_index.py` — debounced HNSW save; DuckDB+USearch writes moved off the event loop into `to_thread` under `conn_lock`; USearch search read serialized under `_write_lock` to avoid interleaving with a threaded save.
- `src/prme/storage/lexical_index.py` — long-lived tantivy writer with count/time-batched commits; `flush()`; flush-on-search; writer released on `close()`.
- `src/prme/ingestion/pipeline.py` — `asyncio.Semaphore` bounding concurrent background LLM extraction.
- `src/prme/config.py` — 4 new config fields: `vector_save_interval`, `lexical_commit_interval`, `lexical_commit_max_delay_s`, `max_concurrent_extractions`.
- `src/prme/storage/engine.py` — wires the new config into the DuckDB-path index/pipeline constructors and the Postgres-path pipeline.
- `tests/test_index_write_path.py` — new tests for all of the above.

## Approach Summary
Addresses the highest impact-per-effort items from the June 2026 efficiency audit (issue #39: H2, H3, M4):
- **H2** — full USearch index was saved to disk on every insert (O(N^2) write volume) and its writes ran synchronously on the event loop bypassing `conn_lock`. Now saves are debounced to `vector_save_interval` inserts (flushed on `close()`), and the DuckDB+USearch work runs in `to_thread` under `conn_lock`.
- **H3** — tantivy created a fresh 50MB writer + commit + reload per document. Now a per-batch writer (created on the first add, released on commit) batches commits by count or elapsed time; the redundant post-index reload is gone; `search()` flushes pending docs first so it never misses its own writes. The writer holds tantivy's exclusive directory lock only while a batch is uncommitted, so other instances can still open the index path when idle.
- **M4** — `ingest()` spawned one unbounded background extraction task per message. A semaphore now bounds concurrent extraction to `max_concurrent_extractions`.

M3 (store write amplification), M5 (organizer cursors), and M6 (per-node organizer vector search) from the issue are larger algorithmic reworks that overlap a separate retrieval issue and the organizer correctness track; they are deliberately left for follow-up and noted on the PR.

## Must Fix (resolved)
| # | Finding | Resolution |
|---|---------|------------|
| 1 | New interleaving: index writes moved to a worker thread, but `search_by_vector` read the USearch index lock-free, so a concurrent full-index `save()` could overlap a search traversal. | Search read now runs under `_write_lock` in `to_thread` (`_search_index`/`_do_search_index`); it cannot interleave with `add`/`save`. |
| 2 | Counter not reset on a failed save/commit → permanent per-insert retry storm. | `vector_index._do_index` resets `_unsaved_inserts` in a `finally`; `lexical_index._commit_locked` resets counters in a `finally`. Error still propagates. Covered by tests. |
| 3 | Unused `do_save`/`did_save` return value (dead code). | `_do_index` now returns just `key`. |
| 4 | No tests for new logic. | Added `tests/test_index_write_path.py` (10 tests): debounce save + flush-on-close, in-memory searchability, failed-save reset, lexical flush-on-search/interval/max-delay/close + reopen durability, failed-commit reset, semaphore bound. |
| 5 | Long-lived tantivy writer holds an exclusive directory lock for the index lifetime. This broke any second `LexicalIndex` opened on the same path (caught by the full suite: 27 failures in `test_cli.py`/langchain where a CLI command opens its own engine alongside a live one). | Switched to a **per-batch writer**: created lazily on the first add of a batch and released (`wait_merging_threads()`) on every commit, so the directory lock is held only while a batch is uncommitted. Still one commit per batch; another instance can open the index when this one is idle. `close()` flushes (which commits + releases). |

## Should Fix (resolved)
| # | Finding | Resolution |
|---|---------|------------|
| 6 | Docstrings oversold `commit_max_delay_s` as a wall-clock timer. | Class/`index()` docstrings and the config description now state the bound is evaluated on the next index/search call (no background timer), and that `search()` always flushes first. |

## Out of Scope (noted, not fixed here)
| Finding | Why deferred |
|---------|--------------|
| Extraction timeout never enforced (`extraction.py` ignores `self._timeout`) — a hung LLM call holds a semaphore permit indefinitely and, with the new bound, can starve all ingestion. | Pre-existing bug in a file outside this change's scope; the semaphore makes it more visible but does not create it. Flagged on the PR as a follow-up so it gets its own change + tests. |
| CI workflow triggers only on `master`; PRs into `main` skip CI. | Pre-existing CI config issue unrelated to this change; touching CI branch filters is out of scope. Noted on PR. |
| No `.env.example` / docs enumerate the new config fields. | Fields are self-documented via `Field(description=...)`; mentioned in the PR body. No tracked env template exists to update. |

## Security Audit Results
| Area | Result | Details |
|------|--------|---------|
| Secrets/PII in logs | PASS | No new log lines; existing logs carry ids/counts only. |
| Injection (SQL/cmd/path) | PASS | `_do_index` is a pure refactor of existing parameterized inserts; no new input reaches SQL/path. |
| Config bounds | PASS | All new fields have `ge=` bounds; consumers also clamp with `max(...)`. |
| Durability / data-loss | PASS | Vector/lexical indexes are rebuildable from the event log (source of truth). Graceful `close()` flushes both. Hard-crash window is bounded by interval and reconstructable. |
| Availability (semaphore) | FAIL (pre-existing) | Extraction timeout unenforced — see Out of Scope. |

## Pattern Consistency Assessment
PASS. `_do_index` follows the established `async with conn_lock: await to_thread(_sync_fn)` pattern used by `event_store`/`duckpgq_graph`. New config fields match the top-level infra-knob style of `write_queue_size`/`materialization_queue_size` (correctly not `[HYPOTHESIS]`). `max_concurrent_extractions` is wired symmetrically into both the DuckDB and Postgres pipeline constructions; the index-batching args are correctly DuckDB-path only (Pg backends have native commit semantics).

## Redundancy Check
PASS. No existing debounce/batch/semaphore helper to reuse (`WriteQueue` handles ordering, not commit cadence). All 4 config fields are consumed. The dead `did_save` value was removed. The `_uncommitted` counter and `_oldest_uncommitted_at` timestamp are both needed (independent interval/time triggers).

## Wiring Findings
PASS. All 4 config fields defined with defaults and consumed in `engine.py`. `MemoryEngine.close()` drains the write queue before closing both indexes, so buffered/debounced writes flush in the right order. Existing index→search tests stay correct because `search()` auto-flushes. No test reads the indexes in a way that bypasses the flush.

## Resolution Status
| Severity | Count | Status |
|----------|-------|--------|
| Must Fix | 5 | All resolved |
| Should Fix | 1 | Resolved |
| Out of scope (noted) | 3 | Deferred / flagged on PR |
