# Issue #414 security review (checklist 2)

Status: COMPLETE

Issue: dwamianm/prism#414 — "A very large max_profile_tokens can still stall the event loop during a profile build"
Branch: `issue-414-large-max-profile-tokens-stall` (uncommitted working tree)
Base: `main` at `deb11a68b73f299d847efabf0e27195fb433dceb`
Reviewed snapshot: `acb59ca9dad6f658893fc2adcc6c7d8d1446e7c19eb5027f54122e4944ce513a`
(previous snapshot in this report: `2c646c32c9bf1f36f3b2427ad11d6f97b47f55157d347e713274f090b0f4b64e`)
Tier: L

Scope: security checklist 2 only (ownership/tenant isolation, privacy journal
admission, fence checks, cached-hash staleness/TOCTOU, validation weakening,
error swallowing, protected-data leaks). Acceptance and performance are the
core reviewer's checklist; cross-area observations are marked.

## Checklist and results (new snapshot)

| Area | Result | Details |
|---|---|---|
| Ownership / tenant isolation across the thread move | PASS | Unchanged from the first review: the moved work is pure and receives already-scoped arguments. The owner check still runs inside `unpack` (`plan.node.user_id != user_id`) before `get()` compares the work row (`src/prme/storage/profile_work.py:59-66`, `:104-118`); `validate_work_row` still compares owner, scope, key and prepared operation (`:427-450`). `ProfileStageFence` still asserts `user_id == self.plan.node.user_id` (`:515-517`). No per-request state is retained on the shared executor. |
| Privacy journal admission still wraps every write | PASS | `prepare`, `commit_duckdb` and `commit_postgres` keep `@protected_mutation` (`profile_work.py:148`, `profile_publication.py:85`, `:206`), and the journal scope is entered around the awaited body (`src/prme/privacy/journals.py:411-509`). The new worker hops (`copy_plan`, `decode_plan`, `replayed_preparation`, `payload`, `validate_work_row`, `_validate`, `_replayed`, `_payload`, the PG row mapping, `build_plan`) do not read `_journal_scope` and do not call `journal_deadline`, so no admission or deadline check is skipped. |
| Context propagation of `run_profile_work` | PASS (fixed) | See Re-review: fresh `contextvars.copy_context()` per call, run via `context.run` in `asyncio.to_thread`-style (`src/prme/organizer/profiles.py:47-56`). No re-entry, journal-scope or cancellation defect found. |
| Fence verified on the committed object, under the write lock | PASS | `ProfileStageFence` still keeps its own JSON copy (`profile_work.py:508-513`) and compares content by `checksum`, not identity (`:519-521`). The authoritative generation check still runs inside `hold()` under the caller's lock (`:523-550`), via `run_to_completion`, which copies contextvars, so its `journal_deadline()` calls still see the scope. The new prechecks in `lexical_index.py:530-542` and `vector_index.py:471-490` keep the old order (validate, owner, fence, version, geometry). |
| Cached `_request_hash`: stale-data / TOCTOU window | PASS (documented) | Deterministic function of frozen plan fields; the cache only avoids recomputation. It is computed from in-memory source snapshots where it was before (`src/prme/storage/engine.py:4886-4894`), so no new storage read sits between hashing and publish. Publication revalidates dependency rows against the current graph under the transaction (`profile_publication.py:50-58`, `:237-252`) and the work row under the fence, so a stale plan fails closed. The mutability caveat is now documented at `models/profile.py:84-89`. |
| Validation of plans / embeddings read from storage not weakened | PASS | Each former inline validation is now the same call on a worker, and the new PG worker hops validate the same objects: `copy_plan`, `decode_plan`, `replayed_preparation`, `unpack`, `validate_work_row`, `_replayed`, `_validate`, `_payload` (`profile_publication.py:206-284`; `profile_work.py:33-72`, `:274`, `:303-304`, `:318`, `:491-495`). `vector_index.py:471-488` retains the owner, fence, model/version/dimension and zero-vector checks in the old order. |
| New failure paths swallow errors | PASS | No moved helper has an `except`; all propagate. `run_to_completion` (`src/prme/storage/_threading.py:11-42`) still delays cancellation and discards only a cancelled worker's exception, which is pre-existing and side-effect free for these pure calls (the only side effect, the PG INSERT, happens on the loop after the worker returns). |
| Protected data in logs or exception text | PASS | No logging call exists in any moved function and no new exception message carries profile text. `extra="forbid"` rejects a JSON-supplied `_request_hash`, so a stored payload cannot inject the cache (verified on pydantic 2.12.4). Context propagation now also carries `_protected_diagnostics` into the workers, so any future `ScopedLogger` call there is redacted. |
| Integrity of the stored publication / artifact schema | PASS | `_request_hash` is a pydantic private attribute excluded from `model_dump*`, so `checksum` and every serialized payload are byte-identical to `main` (verified on pydantic 2.12.4). `DERIVATION_VERSION` and stored artifacts need no change. |

## Findings

### 1. RESOLVED (was LOW) — private attribute and equality

The comment at `src/prme/models/profile.py:84-89` now states that the cached
hash is excluded from the model dump and the persisted checksum but not from
equality, and names the JSON-copy boundaries as the guard. That matches
pydantic 2.12.4 behaviour: `BaseModel.__eq__` compares `__pydantic_private__`,
while `model_dump*` omits it. The related caveat (nested `MemoryNode` objects are
mutable, `src/prme/models/base.py:21`) is covered by the "no caller edits its
nested nodes after it is warmed" clause; I re-checked `src/prme` and found no
in-place mutation of plan nodes or sources, and every boundary (`copy_plan`,
`ProfileStageFence.__init__`, `stage_profile`, `stage`, `build_plan` plus
`prepare`) either copies or constructs fresh objects before caching. Residual
exposure is latent only.

### 2. FIXED (was LOW latent) — contextvars now follow the profile workers

`run_profile_work` now takes `contextvars.copy_context()` on the caller and
dispatches `partial(context.run, function, *args, **kwargs)`
(`src/prme/organizer/profiles.py:47-56`), matching `asyncio.to_thread` and the
repo invariant documented in `src/prme/privacy/diagnostics.py:111-116`. The
moved validation and fence prechecks now see `_protected_diagnostics` and
`_journal_scope` as they should. Details and the re-entry/cancellation analysis
are in the Re-review section.

### 3. INFO — the PostgreSQL half of the new cross-thread path has no default test coverage

`commit_postgres` and `_prepare_pg` now take several worker hops
(`profile_publication.py:212`, `:229`, `:242`, `:252`, `:253`;
`profile_work.py:274`, `:303-304`, `:318`, `:491-495`). The regression tests are
DuckDB-only (`tests/test_profile_event_loop.py:77-155`, which also notes the
PostgreSQL vector-index path), and the PostgreSQL suites are skipped unless
`PRME_TEST_DATABASE_URL` is set (project `CLAUDE.md`, "Storage Backends"). Each
hop is a straight call move with the same checks, so I found no behavioral
difference, but CI without a database does not exercise them.

## Re-review (snapshot acb59ca)

### What changed since the first review

- `run_profile_work` propagates the caller's context (`organizer/profiles.py:47-56`).
- `ProfilePublication(...)` construction, which runs
  `validate_generated_publication` for protected plans, now runs through
  `run_profile_work` in `engine.consolidate_knowledge`
  (`src/prme/storage/engine.py:4901-4916`).
- `commit_duckdb` takes its validated copy before the connection lock
  (`profile_publication.py:85-98`); `_commit_duckdb` no longer copies.
- `commit_postgres` moves `_replayed`, the row-to-node mapping, `_validate` and
  `_payload` off-loop (`profile_publication.py:229-284`); `_prepare_pg` moves
  `replayed_preparation`, `unpack` and `payload` off-loop
  (`profile_work.py:274`, `:303-304`, `:318`); `validate_pg_work` moves
  `validate_work_row` off-loop (`profile_work.py:491-495`); the fence now uses
  `copy_plan` (`profile_work.py:508-513`).
- The `models/profile.py` comment was corrected, and the CHANGELOG now records
  the residual single-call serialization cost.

### Context propagation: re-entry, journal scope, cancellation

- Re-entry: `copy_context()` returns a fresh, unentered `Context` per call.
  `Context.run` raises only when the same context object is entered again;
  every `run_profile_work` call creates its own copy, and a synchronous worker
  function cannot call the async helper again, so nesting cannot happen. Two
  concurrent calls use two distinct context objects.
- Thread legality: `Context.run` is not thread-affine; running a copied context
  on an executor thread is exactly what `asyncio.to_thread` does
  (`/usr/lib/python3.11/asyncio/threads.py:23`). `ThreadPoolExecutor` runs one
  task per thread, so a context is never entered twice.
- Journal scope: the copied `_journal_scope` gives the worker the same
  `JournalScope` object the loop holds. `journal_deadline()` calls
  `scope.check()`, which does not depend on `owns_current_task()`, so it works
  off-loop; `owns_current_task()` is only used by `protected_mutation` re-entry,
  and no moved function invokes `protected_mutation`. `scope.active` is cleared
  when the loop exits the scope; a worker that outlives a cancelled scope would
  then fail closed (raise `PrivacyDenied`) if it ever called the deadline check,
  which is the safe direction and, today, unreachable because no moved function
  calls `journal_deadline`.
- Cancellation: `run_in_executor` does not cancel a running worker, same as
  before the fix. All moved work is pure; the context copy is dropped when the
  worker returns, and a cancelled future does not retrieve an exception or log
  it (asyncio's `_chain_future` returns early for a cancelled destination). No
  partial write is possible, because every database statement still runs on the
  loop after the worker result is awaited.
- Net effect: the previous latent bypass of `ScopedLogger` redaction and
  journal deadline checks in this executor is closed, and the existing
  `run_profile_work` callers (mention / naming / build_profile / matching) are
  pure and unaffected.

### New code review (security)

- The new off-loop PG work is inside `@protected_mutation`; `run_to_completion`
  copies context, so any `ScopedLogger` call in the worker (none present today) and any
  future deadline check see the scope.
- `_record_to_node` is `prme.storage.graph_rows.record_to_node`
  (`src/prme/storage/graph_rows.py:37-90`): pure row decoding with no connection
  or loop access, safe on a worker. `_validate`, `_replayed`, `_payload` and
  `payload` are also pure.
- Worker hops inside an open PostgreSQL transaction do not hold a lock the
  worker needs, and they do not change how long the `FOR UPDATE` rows are held
  (the same work previously ran inside the same transaction).
- `commit_duckdb`'s copy now happens before `store._conn_lock`, which shortens
  the lock hold and does not affect the fence: `_commit_duckdb` still
  revalidates the work row under the lock.
- `ProfileStageFence` now gets its copy through `copy_plan`, so its cached
  request hash is warmed at construction; `verify_plan` still compares
  `checksum`, and `hold()` still revalidates the work row under the lock, so no
  new identity or staleness reliance appears.
- No new error-swallowing path, no new log statement, no change to owner checks
  or to which rows are read for a given `user_id`.

### Residual risks (updated)

- `delete_profile_stage` in both indexes still validates the plan on the loop
  (`vector_index.py:591-593`, `lexical_index.py:739-741`). Availability only,
  and outside this diff; flag to the core reviewer.
- Two process-wide profile worker threads are shared by all tenants; a very
  large build can delay another tenant's profile prep. Fairness/latency, not an
  isolation break; the pool predates this change.
- The CHANGELOG now acknowledges that a single serialization C call can hold
  the interpreter lock for a fraction of a second; this is not a security
  boundary issue.
- `vector_index.stage` reads `self._provider.*` and `self._index.ndim` on a
  worker outside `_write_lock` (`vector_index.py:481-484`); `_provider` is never
  reassigned and the index is always rebuilt with `_provider.dimension`, so the
  dimension invariant holds and `_do_stage` re-reads the current index under
  both locks.

## Verification notes

- Confirmed on pydantic 2.12.4 that a private attribute is excluded from
  `model_dump()`/`model_dump_json()` (so `checksum` and payloads are unchanged)
  but included in `__eq__`, and that `extra="forbid"` rejects a `_request_hash`
  key supplied in JSON input.
- Confirmed `asyncio.to_thread` copies contextvars and a bare
  `loop.run_in_executor` does not; the fix now copies explicitly before
  `run_in_executor`.
- Did not run tests or modify source; findings are from source reading and
  read-only checks.
