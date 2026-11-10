# Issue #517 Security Re-Review

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: 68700cdab0cf41f480e2d7bc753fd9c09be29aa52914d73f2c2bde0a61a74847 (uncommitted work tree; nothing edited)
- Prior report: memory_bank/reviews/issue-517-security.md
- Scope: verify the round-one fixes (F1-F6), the "u:" scope encoding, the erasure key match, the owner view on the new PostgreSQL path, the verification query, and the protected projection change in `src/prme/privacy/diagnostics.py`.

## Verdict

All six round-one fixes are confirmed in source. No new exposure was found. The remaining items are test-coverage and maintainability residuals (N1-N5), none of which is a blocker for #517.

## Area | Result | Details

| Area | Result | Details |
|---|---|---|
| F1 scope key cannot collide with an unscoped run | PASS (fixed) | `scope_key()` returns `""` only for `None` and `_SCOPED_PREFIX + user_id` ("u:") for any other id, including `""` (watermarks.py:38-49). Unit assertion at tests/test_organizer_incremental.py:160-164. `organize(user_id="")` on a protected store is denied before any work (`project_public_result` rejects a falsy owner, reads.py:593-594), so the collision path is closed at both ends. |
| F1 erasure DELETE matches the same encoding | PASS | Purge deletes `"u:" + str(intent.owner_id)` (erasure.py:695-698); verify checks the same literal (erasure.py:846-849). Encoding agrees with `scope_key`. Residual coupling noted as N2. |
| F2 PostgreSQL `scan_changed_nodes` hides a foreign `superseded_by` | PASS (fixed) | pg/graph_store.py:566-571 now applies `_owned_view` when `user_id` is set, the same helper `scan_nodes` (pg/graph_store.py:684) and `query_nodes` (pg/graph_store.py:453) use; parity with DuckDB duckpgq_graph.py:433. Helper `_owned_view` is pg/graph_store.py:1727-1732 and binds its ids (1716-1725). |
| F3 absent lower bound = whole window to the ceiling | PASS (fixed) | read_graph.py:170-183: `in_window` starts `True` only when both bounds are absent; the `updated_after` keyset and the `created_after` alternate bound are ORed exactly as in the SQL backs (duckpgq_graph.py:476-494, pg/graph_store.py:539-557). Tests at tests/test_organizer_incremental.py:204-242. |
| F4 watermark API bypasses the owner facade | ACCEPTED | Documented as operational metadata at engine.py:3157-3166; its only caller is `_run_incremental` (jobs.py:81-126); no HTTP route or CLI command exposes it (`rg` over src/prme/api and cli.py returns nothing). The resume node id is popped from job details (jobs.py:104) and `project_organize` rebuilds `JobResult` without `details` (diagnostics.py:178-190), so the UUID never leaves the engine. No API route means only in-process embedders, who already hold the store. |
| F5 tenant-isolation test for `scan_changed_nodes` | PARTIAL | Tests exist on both backends (tests/test_organizer_incremental.py:355-368; tests/test_organizer_incremental_pg.py:149-164) but assert the deduplicate outcome, not the new scan predicate; downstream `get_node(user_id=...)` filters (duckpgq_graph.py:171-177; pg/graph_store.py:213-217) and per-node vector scoping (deduplication.py:196-199, 223) would mask a leak from the scan. See N1. |
| F6 `_verify_purge` rejects a surviving owner watermark row | PASS (fixed) | erasure.py:843-853 rejects a surviving `u:<owner>` row only in memory mode, matching the purge (erasure.py:691-698). Test seeds the owner row and an unscoped cursor naming the erased node and asserts both are removed/cleared (tests/test_privacy_erasure_retirement.py:55-77), parametrized across directory/pack/postgres layouts. Residuals N5. |
| "u:" encoding vs erasure key | PASS | Covered by F1 rows above. On a protected store `ReadContext.create` rejects a non-canonical owner (reads.py:68-73), and `organize` itself rejects a falsy owner (reads.py:593-594), so every watermark a protected store can write is `u:` plus the canonical lowercase UUID that `str(intent.owner_id)` produces. |
| Owner view on the new PostgreSQL path | PASS | See F2. |
| Verification query | PASS | Bound parameter, correct owner, memory-mode gated identically to the purge; a surviving row raises `privacy_invalid_result`, which `process_erasures` records as a `coverage_unavailable` failure rather than claiming a purge (erasure.py:818-822). |
| Protected projection change in diagnostics.py | PASS | `status` is whitelisted to `completed`/`budget_exhausted`/`error` and `stopped_on_budget` is a typed bool, so no free text or memory content can enter the projected result; `details` is dropped by the reconstruction (diagnostics.py:178-190). Test at tests/test_organizer_incremental.py:392-406. |
| SQL injection safety of new statements | PASS | All new predicates bind values (`?`/`$n`); only placeholder counts and fixed identifiers are interpolated (pg/graph_store.py:497-571, duckpgq_graph.py:438-504, erasure.py:686-698, 843-853). |
| New HTTP/CLI/result surface | PASS | No route or command for the watermark API or `scan_changed_nodes`; the CLI prints the fixed status literal (cli.py:619-625). |
| Logging on the protected path (F7, unchanged) | RESIDUAL (Informational) | The new incremental paths log `exc_info=True` through the plain logger (jobs.py:614, 645, 689), not `ScopedLogger`; parameters are ids/timestamps and this matches existing organizer logging. Unchanged from round one. |
| `engine.scan_changed_nodes` fencng | PASS (observed) | The method has no `@privacy_disclosure` decorator (engine.py:3148-3155), unlike `query_nodes`. The fence is provided by `_ordinary_graph` (engine.py:3186-3196): `ReadContext.create` applies the legacy-range and boundary checks, `AdmittedGraphView` projects each node, and `context.disclose` takes the erasure guard. No new exposure, but a decorator-parity question for a later change (N6). |

## Findings

### N1 (Informational) The new tenant-isolation tests do not isolate the new scan
tests/test_organizer_incremental.py:356-368 and tests/test_organizer_incremental_pg.py:149-164 run `organize(user_id=owner_a)` and assert `owner_b`'s nodes are unchanged. They would pass even if `scan_changed_nodes` returned `owner_b`'s nodes, because `merge_duplicates` re-fetches every candidate with `engine.get_node(id, user_id=user_id)` (deduplication.py:298-303), which returns `None` for a foreign owner (duckpgq_graph.py:171-177; pg/graph_store.py:213-217), and because the neighbor search is scoped per node (deduplication.py:194-199, 223). The direct `TestScanChangedNodes` cases use one owner only (tests/test_organizer_incremental.py:167-242). Recommendation: add a case to tests/test_owner_scoped_graph_reads.py:252-279 (`reads` dict at 265-276 is already the place) that calls `scan_changed_nodes(user_id=user)` with another owner's nodes present and asserts they are absent, plus the foreign-`superseded_by` clearing for the new PG path (F2 currently has no dedicated test; the helper is exercised via `scan_nodes`).

### N2 (Informational) The "u:" prefix is duplicated, not shared
`scope_key()` owns the encoding through the private `_SCOPED_PREFIX` (watermarks.py:40-49), but the purge and the verification check hardcode the literal in two places (erasure.py:697, 848). A change to the prefix would silently stop the purge from matching, and only the owner-row check (not the cursor clear, erasure.py:686-690) is verified at purge time. Recommendation: call the public `scope_key(str(intent.owner_id))` in both statements, or export a single helper, so the erasure key has one definition.

### N3 (Informational) Stale comments describe `scope_key` as the raw user id
The module docstring (watermarks.py:5-6) and both schema comments (src/prme/storage/schema.py:232-233; src/prme/storage/pg/schema.py:233-234) still say `scope_key` is "a user id, or the empty string for an unscoped operator run". The implemented encoding is `""` or `u:<user id>`. Since the erasure contract now depends on that exact form, the comments should be updated to avoid a future mismatch.

### N4 (Informational, carried from F7) Plain-logger `exc_info` on the new protected paths
jobs.py:614, 645 and 689 emit raw tracebacks through `logging.getLogger` inside the incremental deduplicate path. The arguments are a user id and timestamps and this matches the pre-existing organizer logging, so no memory content is shown; flagged because these are the new failure points.

### N5 (Informational) `_verify_purge` skips the watermark check for an empty target set
If `targets` is empty and the request is owner-wide, `_verify_purge` returns before the `organizer_watermarks` check (erasure.py:836-841 vs 843-853). The purge still deletes the owner row unconditionally for memory mode (erasure.py:691-698), so this only weakens defense-in-depth for an owner with a watermark row and no memories. The per-node resume-cursor clear is verified by test, not by `_verify_purge`.

### N6 (Informational, observed) `engine.scan_changed_nodes` relies on `_ordinary_graph` for its fence
engine.py:3148-3155 is the only new engine read method without `@privacy_disclosure`. The protected outcome is equivalent today because `_ordinary_graph` builds `AdmittedGraphView` (projection plus `disclose`/erasure guard) and `ReadContext.create` enforces the canonical owner and legacy range. Worth noting so a later refactor of `_ordinary_graph` keeps the fence.

## Residual risks accepted
- The table stores `scope_key` (a prefixed user id) and a node UUID in plaintext outside the privacy journal. It holds no content, the owner row is purged in memory mode, and a cross-scope resume cursor naming an erased node is cleared. Export/restore copies it, consistent with other operational metadata.
- On a privacy-admitted store `AdmittedGraphView.scan_changed_nodes` materializes the whole admitted scope in memory and sorts it (read_graph.py:163-185); the RFC records this and the nothing-new bound as follow-up (docs/RFC-0015-Self-Organizing-Memory.md, section 5.5). A tenant can only affect its own scoped run.
- `organizer_watermarks` was added to PG `_RUNTIME_TABLES` (pg/schema.py:29), which forces a privileged re-bootstrap of pre-existing PostgreSQL deployments. Availability only; owned by the infrastructure checklist.
