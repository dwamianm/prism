# Issue #517 Data/Storage Safety Re-Review (checklist 7)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: 68700cdab0cf41f480e2d7bc753fd9c09be29aa52914d73f2c2bde0a61a74847 (uncommitted work tree, not edited)
- Prior report: memory_bank/reviews/issue-517-data.md

## Verdict

Round-1 findings 1, 2, 3, 4, 5, 7, 8 and 9 are closed at source. Finding 6 is only partly
closed, and the way it was closed introduces a new erasure-completion defect (F-RR1). The
60-second overlap is sound for its stated purpose: it re-examines work but cannot duplicate a
durable write (merges revalidate `TENTATIVE`/`STABLE`), and it removes the ordinary
build-then-insert race; a bounded residual remains for a write that becomes visible more than
60 s after its `updated_at` stamp (F-RR2).

Result: the incremental/watermark criteria now hold, but the change is **not ready** until
F-RR1 is fixed, because it wedges privacy erasure completion for a tenant that is still active.

## Checklist results

- F1 read_graph ceiling-only window: FIXED. `read_graph.py:170-182` now starts `in_window` true
  when both lower bounds are absent, matching the SQL backends' absent window predicate
  (`duckpgq_graph.py:476-494`, `pg/graph_store.py:540-556`). Keyset, ceiling and
  `created_at_or_before` semantics also match the SQL one for one.
- F2 no cursor advance on stop/failure: FIXED. `jobs.py:695-697` breaks on `stopped or failed`
  before the `updated_after`/`after_id` advance at `:698-699`; a scan exception also breaks
  (`:644-648`).
- F3 completed prefix after a successful search: FIXED. `deduplication.py:193-203` increments
  `completed` only after `index.search` returns and breaks on exception; the exact-content
  budget return reports `0` (`:166-169`). A partially searched page cannot move the cursor
  (`jobs.py:677-687`).
- F4/F5 promote incremental: CLOSED BY REMOVAL. `INCREMENTAL_JOBS = {"deduplicate"}`
  (`watermarks.py:36`); `_job_promote` takes no window and documents why (`jobs.py:201-207`).
  The non-monotonic age-union scan and the first-run-stop gap no longer execute.
- F6 erasure of the watermark table: PARTIAL. `"u:" + owner` matching and the
  `_verify_purge` check are present (`erasure.py:682-698`, `:843-853`) and the new test seeds
  both an owner row and an unscoped cursor naming the erased node
  (`tests/test_privacy_erasure_retirement.py:55-77`, parametrized over directory, pack and
  postgres). The purge-time write fence the prior report also asked for is still absent
  (`engine.py:3168-3184`), and the new verification check is unsound for a re-creatable row
  (F-RR1).
- F7 docs: FIXED. The RFC states that a failed run records its processed prefix and keeps
  `completed_through` (`RFC-0015:481-489`) and the CHANGELOG states that the other jobs stay
  full-scope and that the watermark write happens on a completed run (CHANGELOG:12).
- F8 protected projection: FIXED. `diagnostics.py:185-186` carries `status` (allow-listed) and
  `stopped_on_budget`, and `tests/test_organizer_incremental.py:392-406` asserts it.
- F9 erasure assertion: FIXED (see F6).
- New overlap: no durable duplication, no skip inside 60 s; bounded residual below (F-RR2).
- Writers/readers and `"u:"` consistency: code is consistent (only `scope_key()` builds keys:
  `jobs.py:81`, `watermarks.py:43-49`; erasure uses `"u:" + str(owner_id)` at `erasure.py:697`
  and `:848`). Two schema comments are stale (F-RR3).
- Table writers/readers per column: unchanged from the prior report and still correct on both
  backends (`duckpgq_graph.py:506-560`, `pg/graph_store.py:573-620`).

## Findings

### F-RR1 (high, regression introduced by the F6 fix): the new watermark verification makes a
### legitimate, still-active tenant's erasure report a false purge failure forever

`_verify_purge` now fails when any row exists whose `scope_key` is `"u:" + str(owner_id)`
(`erasure.py:843-853`). That row is *re-creatable*: `deduplicate` is in `DEFAULT_JOBS`, and
every completed incremental run writes `completed_through = ceiling - 60` for the run's scope
(`jobs.py:121-126`; the write does not require any node write, so a zero-node run still writes
it). The purge deletes the owner's row once (`erasure.py:691-698`), but nothing stops a later
`organize(user_id=<owner>)` from inserting it again, because `engine.set_organizer_watermark`
goes straight to `self._graph_store` (`engine.py:3178`) and never consults the erasure
authority, its guard or `assert_records_current`.

Consequence: for an owner who is erased *in part* and remains an active tenant (erase one
memory, keep the account), a deduplicate run that completes between the purge transaction and
the verify stage makes `_verify_purge` raise `privacy_invalid_result`, which
`process_erasures` records as `coverage_unavailable` (`erasure.py:818-822`). The retry cannot
recover: the `sql` stage is already `done`, and `_purge_sql_step` short-circuits on that flag
(`erasure.py:616-617`), so the re-created row is never deleted again. The request stays at
`stage="verify"` with `logical_live_copies="pending"`, retried indefinitely and consuming a
slot of the bounded pending window (`erasure.py:783`).

Reachability is not a narrow race: the window between the `sql` stage and `verify` is
arbitrarily long whenever a call expires its budget or stalls at `indexes`/`keys` (the
provider-unavailable loop the ER02 test exercises), and organizer runs are explicitly meant to
be driven by external cron. Before this fix the same re-creation was silent; now it turns into
a permanent false failure.

The memory purge itself is unaffected (node/event rows are gone and access stays blocked), so
this is a completion/liveness defect in a privacy workflow rather than a disclosure, but it is
a regression introduced by this diff and blocks the change.

Fix direction (either is sufficient): (a) have `_verify_purge` re-run the owner delete/clear
inside its own transaction before asserting absence, so a re-created row is removed instead of
treated as evidence of a failed purge; or (b) fence the write: make
`set_organizer_watermark` refuse a scope with a live memory-mode intent (or route it through
`ErasureCoordinator.guard`), which also closes the prior F6 bullet. Whichever is chosen, a
test should cover "purge, then a completed deduplicate run for the owner, then verify".

### F-RR2 (medium, residual): the 60 s overlap assumes a write becomes visible within 60 s of
### its `updated_at` stamp

`_WATERMARK_OVERLAP` (`jobs.py:61`, `:125`) makes the next lower bound `ceiling - 60 s`, which
covers the normal build-then-insert gap (the engine's write queue awaits each write, so
`store()`/`ingest()` inserts finish before the caller returns,
`src/prme/storage/write_queue.py:86-119`). A durable write whose row appears *after* a run
scanned, with a stamp at or below `ceiling - 60 s`, is still never examined by incremental
deduplicate. The concrete path is the materialization queue, which stamps a materialized NOTE
with the event's time, not the insert time (`engine.py:2200-2210`,
`created_at=event.created_at, updated_at=event.created_at`); a backlog spanning two organizer
runs produces notes older than the window. No durable dedup loss results today, because
`duplicate_merge_allowed` requires equal `event_time`, `session_id` and metadata
(`merge_policy.py:8-32`), which two materialized notes effectively never share; but the scan is
not provably complete and would silently start missing those rows if that policy loosened.
Either state the assumption in the RFC, or page on an insert-visible column (or clamp the
materialized stamp to admission time).

### F-RR3 (low): two schema comments still describe `scope_key` as the raw user id

`src/prme/storage/schema.py:232-233` and `src/prme/storage/pg/schema.py:233-234` say
"`scope_key` is a user id, or the empty string". The real key is `"u:"` + user id
(`watermarks.py:43-49`, RFC-0015 "5.5"), and the erasure delete/verify depends on that prefix.
Fix the two comments.

### F-RR4 (low): `created_after` / `created_at_or_before` are now dead and their docstring still
### names `promote`, the job whose use of them was the F4 defect

`GraphStore.scan_changed_nodes` still documents `created_after` as "for jobs such as `promote`"
(`graph_store.py:289-290`) and both backends still build the OR branch
(`duckpgq_graph.py:487-492`, `pg/graph_store.py:552-555`), as does the facade
(`read_graph.py:180-181`). No caller passes either parameter. That branch is exactly the
non-monotonic keyset that caused F4 and is now untested; removing the parameters and the OR
branch avoids a future caller reintroducing it.

### F-RR5 (low): the `resume_id` clearing is not verified

`_purge_sql_step` clears `resume_updated_at`/`resume_id` on every row naming an erased node
(`erasure.py:682-690`), but `_verify_purge` only checks the owner's `scope_key`. The clear runs
in the same transaction as the deletes and the new test asserts it on all three layouts
(`tests/test_privacy_erasure_retirement.py:74-77`), so this is belt-and-braces only; one
`SELECT 1 ... WHERE resume_id=$1` per node target would make the guarantee symmetric with the
new owner check.

### F-RR6 (low): one RFC sentence describes a limitation the facade does not have

RFC-0015 says a disclosure-empty page is read as end-of-window. `AdmittedGraphView.scan_changed_nodes`
walks every page of the scope via `_all_nodes` and only truncates at the end
(`read_graph.py:163-185`), so it returns a short page only when fewer than `limit` rows match
the window. The real limitation is only the in-memory sort/per-page full scan, which the same
paragraph states correctly.

## Adversarial scenarios checked

- **Crash between the job's writes and the watermark write**: the window re-runs; merge and
  promotion are idempotent (`jobs.py:695-701`).
- **Two overlapping incremental runs (opportunistic pass + `prme organize`)**: the upsert is
  last-writer-wins and can move `completed_through` backwards, but every stored value was
  produced by a completed scan, so the next run re-reads a superset. No skip.
- **Stop/failure resume around equal stamps**: the page order is `(COALESCE(updated_at,
  created_at), id)` on both backends and in the facade, so a cursor taken from the last fully
  searched node cannot skip an equal-stamp sibling with a larger id
  (`duckpgq_graph.py:497-501`, `pg/graph_store.py:560-564`, `read_graph.py:184`).
- **First run, no watermark, protected store**: the facade now returns the whole backlog up to
  the ceiling (F1), which is what lets the first watermark advance be safe.
- **Overlap re-examination after a merge**: the retired node is `ARCHIVED`, is filtered out of
  the scan (`LifecycleState.TENTATIVE/STABLE`, `jobs.py:638`) and returns `None` from
  `get_node` in `merge_duplicates` (`deduplication.py:298-324`), so no second `SUPERSEDES`
  edge and no double archive.
- **Tenant isolation**: the scoped scan filters `user_id` in SQL and again in `_owned_view`;
  the unscoped operator key `""` is distinct from `"u:"` and from any owner UUID
  (`watermarks.py:43-49`, tests at `tests/test_organizer_incremental.py:355-368` and
  `tests/test_organizer_incremental_pg.py:149-164`).
- **Erasure vs a concurrent/later organizer run**: the re-creation path is real and is F-RR1;
  it is what makes the new verify check wrong.
- **Non-canonical owner id on a protected store**: `ReadContext.create` rejects anything but
  `str(UUID(user_id)) == user_id` (`src/prme/privacy/reads.py:73-78`), so a key that the
  erasure delete would not match cannot be produced by a run that did work.

## Confirmed non-issues

- `IncrementalWindow` no longer carries `completed_through`; no reference remains.
- `JobResult.status`/`stopped_on_budget` are additive with `completed`/`False` defaults
  (`models.py:24-31`), and the protected projection allow-lists the status.
- The `_resume` detail is always popped before callers see the result (`jobs.py:104`), so it
  never reaches `details` output or the protected projection.
- DuckDB `resume_id` UUID binding and PG `$5::uuid` both round-trip a `str` cursor
  (`duckpgq_graph.py:524`, `pg/graph_store.py:609`) and the erasure test asserts the clearing
  on the postgres layout.
- A completed run clears a previous resume cursor because both are written from the same
  upsert (`duckpgq_graph.py:551-556`, `pg/graph_store.py:610-620`), tested at
  `tests/test_organizer_incremental.py:128-135`.
- The default path is unchanged: `incremental_enabled=False`, no watermark read or write
  (`jobs.py:181-186`, tested at `tests/test_organizer_incremental.py:371-379`).
