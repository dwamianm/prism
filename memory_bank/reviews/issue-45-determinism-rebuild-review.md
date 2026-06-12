# Code Review — issue-45-determinism-rebuild

## Files Changed
- `src/prme/config.py` — new `vector_exact_search` bool field (default True).
- `src/prme/storage/vector_index.py` — `exact_search` ctor param; `exact=` on usearch search; new `clear()`/`_do_clear()`.
- `src/prme/storage/lexical_index.py` — new `clear()`/`_do_clear()`.
- `src/prme/storage/engine.py` — wire `exact_search` from config; new `rebuild_indexes()`; import `ACTIVE_LIFECYCLE_STATES`.
- `src/prme/cli.py` — new `cmd_rebuild` handler + `rebuild` subparser + docstring entry.
- `tests/test_determinism_rebuild.py` — new (exact determinism, clear, rebuild, harness).
- `tests/test_cli.py` — new rebuild CLI tests + parser test.

## Approach Summary
Delivers the determinism and rebuildability claims for issue #45 with a bounded,
non-destructive change. (1) Exact vector search: pass `exact=True` to usearch
(brute-force cosine, order-independent) gated by a config flag, default on.
(2) `prme rebuild`: clears the *derived* vector + lexical indexes and reconstructs
them from the durable graph nodes (active lifecycle states only), in deterministic
id order. The event log and graph nodes/edges (the source of truth) are never
modified, so rebuild is safe to re-run and idempotent. Builds on PR #38 (lifecycle
visibility filter) and PR #41 (eviction/compaction) — rebuild indexes the same
active set those enforce.

## Must Fix
- None. (6-agent review found no correctness, security, or wiring defect that
  blocks the bounded scope.)

## Should Fix (resolved)
- **Postgres ignores the exact-search flag silently** (Agents 5, 6). Resolved by
  documenting in the config field description that the flag applies to the
  DuckDB/USearch backend only (pgvector uses its own index). Changing Postgres
  ANN semantics is out of scope for this PR and would be a behavior change.
- **`placeholders` recomputed each loop iteration** (Agents 3, 4). Resolved —
  hoisted out of the pagination loop (loop-invariant).
- **Missing tests: empty graph + whitespace-only content skip** (Agents 1, 3).
  Resolved — added `test_rebuild_empty_graph` and `test_rebuild_skips_empty_content`.

## Consider (acknowledged, not changed)
- **rebuild reads the nodes table via raw SQL rather than `graph_store.query_nodes()`**
  (Agent 4). Deliberate: rebuild is DuckDB-only (guarded), and it needs
  `ORDER BY id` for determinism, which `query_nodes` (orders by `created_at`)
  does not provide. The direct read matches how `cmd_stats`/`cmd_info` read the
  same connection. Documented inline.

## Security Audit Results
All six areas PASS (Agent 2):
- SQL injection: PASS — only `?` placeholders interpolated into the IN clause;
  all values parameterized (matches `_fetch_allowed_keys`).
- Tenant isolation: PASS — per-node `user_id` carried into both index writes;
  search still filters by user at query time.
- Path traversal: PASS — CLI reuses `_create_engine` (abspath + existence check).
- Data destruction: PASS — only derived indexes cleared; graph nodes remain, so
  rebuild rehydrates from authoritative data (recoverable, non-destructive).
- PII in logs: PASS — completion log emits aggregate counts only, no content/ids.

## Pattern Consistency Assessment
PASS (Agent 4): config field matches existing bool-field shape; `clear()`/`_do_clear()`
mirror `delete_by_node_id()`/`_do_delete()` lock + thread patterns in both index
modules; `write_queue.submit(lambda: ...)` idiom matches `store()`; CLI subparser
matches `cmd_organize`.

## Redundancy Check
PASS (Agent 5): no existing rebuild/reindex/clear path; `index_compaction` (#41) is
orthogonal (evicts stale vs reconstructs all). `clear()` (bulk reset) justified over
looping `delete_by_node_id`. No new dependencies (usearch `exact=` and tantivy
`delete_all_documents` are existing APIs).

## Wiring Findings
PASS (Agent 6) for DuckDB path: config → VectorIndex → search; CLI registered and
dispatched; args resolve; env loads via `PRME_` prefix; docstring/help updated; tests
discovered. The Postgres-flag gap is addressed via documentation (see Should Fix).

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Postgres ignores exact flag | Should Fix | Resolved (documented as DuckDB-only) |
| `placeholders` in loop | Should Fix | Resolved (hoisted) |
| Missing empty-graph test | Should Fix | Resolved (added) |
| Missing empty-content test | Should Fix | Resolved (added) |
| raw SQL vs query_nodes | Consider | Acknowledged (determinism requires ORDER BY id) |
