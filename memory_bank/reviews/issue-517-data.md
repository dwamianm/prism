# Issue #517 Data/Storage Safety Review (checklist 7)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d (uncommitted work tree, not edited)
- Reviewer: DATA/STORAGE SAFETY (checklist 7)
- Worker evidence used as a starting point: DuckDB and PG incremental tests pass (incl. the
  10,000-node nothing-new test), dedup/promote equivalence pass, erasure tests pass on both backends.

## Verdict

Not ready as-is. The schema/migration story is sound and needs no backfill, but four code paths can
skip records permanently or fail to converge (finding 1 on the protected read facade, findings 2-5 in
the incremental job loops), and the erasure of the new table is neither verified nor fenced
(finding 6). Findings 7-9 are documentation/reporting/test-coverage defects.

## Checklist 1: table creation on both backends, PG fingerprint contract

PASS, with one operational note.

- DuckDB: `src/prme/storage/schema.py:237` (`CREATE TABLE IF NOT EXISTS organizer_watermarks` inside
  `create_schema`, `:22`), reached from `initialize_database` (`:566`, `:580`) which the engine calls on
  every open, new or existing (`src/prme/storage/engine.py:734`). Existing directory packs and
  single-file `.prme` packs therefore get the empty table on open; no backfill is needed because a
  missing row means "first run, no lower window bound".
- PostgreSQL: `src/prme/storage/pg/schema.py:235` (`_ORGANIZER_WATERMARKS_TABLE`) executed at `:377`
  inside `initialize_pg_database`'s advisory-lock transaction, together with the other
  `IF NOT EXISTS` DDL.
- Fingerprint contract: `organizer_watermarks` is added to `_RUNTIME_TABLES`
  (`src/prme/storage/pg/schema.py:29`), and `_schema_fingerprint` covers columns, index validity and
  constraints (`:36`), so an existing deployment validated through `validate_pg_runtime`
  (`:70`, read-only path in `src/prme/storage/engine.py:951`) now fails with
  `postgres_schema_bootstrap_required` until the privileged bootstrap re-runs. That is the same
  contract every prior table addition used, and `docs/WORKSPACES.md:175-181` documents the recovery
  path.
- Operational note (not a defect, but not stated in the CHANGELOG): the runtime role now needs
  SELECT/INSERT/UPDATE on `organizer_watermarks`. `_RUNTIME_TABLES` is only the fingerprint list; it
  does not grant privileges. `initialize_pg_database` is documented as `src/prme/storage/pg/schema.py:7`
  ("Called once on startup"), so a deployment that grants privileges separately must add this table.
- Pack tools: `prme migrate` copies with `COPY FROM DATABASE` (`src/prme/storage/pack_tools.py:228`),
  so the table (and its rows) survive a migration; `prme compact` is unaffected.

## Checklist 2: every durable column has a writer and a reader

PASS. Columns written by the runtime: `scope_key`, `job`, `completed_through`, `resume_updated_at`,
`resume_id` (plus the audit-only `updated_at`).

| column | writer | reader |
|---|---|---|
| scope_key | duckpgq_graph.py:534 `set_organizer_watermark`; pg/graph_store.py:592 | duckpgq_graph.py:504 `get_organizer_watermark`; pg/graph_store.py:570; erasure.py:695 |
| job | same | same |
| completed_through | same | same (mapped to `OrganizerWatermark.completed_through`, `watermarks.py:41`) |
| resume_updated_at | same | same (`resume_cursor`, `watermarks.py:50`) |
| resume_id | same | same |
| updated_at | same | not read by any code |

- `updated_at` has a writer but no reader; it is an audit/upsert column with a DDL default
  (`schema.py:243`, `pg/schema.py:243`), so a writer that omitted it would still work. Acceptable.
- Callers: `src/prme/organizer/jobs.py:77` reads; `:108` and `:116` write.
- Type round-trip is compatible on both backends: DuckDB stores `resume_id` as `UUID` and normalizes
  naive times to UTC (`duckpgq_graph.py:511-527`); PG stores `UUID` with an explicit `::uuid` bind
  (`pg/graph_store.py:604-620`) and returns tz-aware `timestamptz`. The engine forwards without a
  read projection (`engine.py:3157-3181`), which is intentional (the table holds no memory content).
- No other GraphStore implementation needs the new Protocol members: the protocol is
  `runtime_checkable` but nothing in `src`/`tests` does an `isinstance(..., GraphStore)` check, and the
  only implementations are `DuckPGQGraphStore`, `PgGraphStore` and `AdmittedGraphView`.

## Checklist 3: watermark written after the job's writes; stop/error semantics

PASS for the ordering; two convergence defects below.

- `run_job` routes the three change-driven jobs through `_run_incremental` (`jobs.py:175-178`), which
  awaits the handler and only then writes (`jobs.py:95-120`). The ceiling is captured before any work
  (`:76`), so anything written during the run is picked up by the next window. A completed run sets
  `completed_through=ceiling` with no resume fields, and the upsert clears the cursor
  (`duckpgq_graph.py:546-558`, `pg/graph_store.py:604-620`) - covered by
  `tests/test_organizer_incremental.py:141-148`.
- A stopped run keeps the previous `completed_through` and records the keyset prefix
  (`jobs.py:100-114`); with no prefix it leaves the previous row untouched, and a handler exception
  propagates before any write, so the window simply re-runs.
- **Finding 1 (high): the protected read facade returns no page on a first run, then the watermark
  advances past the whole backlog.** `AdmittedGraphView.scan_changed_nodes`
  (`src/prme/privacy/read_graph.py:153-183`) initializes `in_window = False` and only sets it when
  `updated_after` or `created_after` is not None (`:170-181`). On a first run the watermark is absent,
  so `_run_incremental` passes `updated_after=None` (`jobs.py:79-84`) and `promote` passes
  `created_after=age_floor=None` (`jobs.py:268-272`). The SQL stores (`duckpgq_graph.py:473-478`,
  `pg/graph_store.py:543-551`) omit the window predicate in that case and return everything up to the
  ceiling; the protected view returns `[]`. The job then returns `completed` and writes
  `completed_through=ceiling` (`jobs.py:115-120`), so every record that existed before the option was
  enabled is never examined by `promote`, `deduplicate` or `snapshot_generation` on a protected store.
  The two implementations must agree that "no lower bound" means "everything at or below the ceiling".
  No test covers `AdmittedGraphView.scan_changed_nodes` or incremental mode on a protected store.
- **Finding 4 (high): `_promote_incremental` pages on a predicate that is not the keyset, so it can
  stop making forward progress.** The scan is
  `(COALESCE(updated_at, created_at), id) > cursor OR created_at > age_floor`
  (`jobs.py:283-294`; the OR term is built at `duckpgq_graph.py:488-493`, `pg/graph_store.py:553-559`).
  The `created_at > age_floor` branch is not bounded by the cursor, so a page can be filled entirely
  with nodes already scanned (their stamps are below the start cursor). The loop then sets the next
  cursor to `nodes[-1]` (`jobs.py:319-320`), which can be behind the cursor it started from; with
  `_INCREMENTAL_PAGE_SIZE` (500) or more still-tentative nodes inside the age band
  `(completed_through - promotion_age_days, ceiling - promotion_age_days]`, every page repeats the same
  nodes until the budget stops the run, and each later run resumes behind the previous start. The
  result is repeated rework that never reaches records beyond the band, not a data write, but a
  scheduled loop cannot converge. A monotonic guard (break when the returned page's last key is not
  greater than the bound that produced it) plus a bounded second pass for the age band is needed.
- **Finding 5 (high): a first-run budget stop loses nodes that newly cross the promotion cutoff.**
  A stopped first run has no prior watermark, so it writes `completed_through=NULL`
  (`jobs.py:108-114`). On the resume, `age_floor` is therefore `None` (`jobs.py:268-272`) and the scan
  has no age term. Nodes whose `created_at` was above the first run's `cutoff` (not yet old enough)
  but is at or below the resumed run's `cutoff`, and whose stamp is behind the resume cursor, are
  excluded by both the keyset and the missing age term, and are skipped permanently until something
  updates them. Fix by persisting the age floor (or the run's original ceiling) in the watermark for a
  stopped run, or by deriving it from `resume_updated_at`.

## Checklist 4: keyset ordering and NULL updated_at

PASS for the scan itself.

- Both backends order by `COALESCE(updated_at, created_at) ASC, id ASC` and page with an exclusive
  cursor `(expr > c OR (expr = c AND id > id))` (`duckpgq_graph.py:473-500`,
  `pg/graph_store.py:543-564`), with the ceiling inclusive (`<=`). The key is total and stable because
  `id` is unique. `tests/test_organizer_incremental_pg.py:90-124` asserts exclusivity and the
  tie-break; the DuckDB path exercises `after_id` through the resume test
  (`tests/test_organizer_incremental.py:204-234`).
- NULL `updated_at`: no runtime writer ever sets it NULL and both DDLs default it to the current
  timestamp (`schema.py:135`, `pg/schema.py:171`), so existing rows have a value. The `COALESCE` is
  therefore defensive only. Worth knowing: if such a row ever existed, the scan would not misorder, it
  would raise, because `MemoryObject.updated_at` is a required `datetime`
  (`src/prme/models/base.py:35`) and `_row_to_node`/`record_to_node` pass the column straight in
  (`duckpgq_graph.py:2395`, `graph_rows.py:65-70`); `_promote_incremental`/`_deduplicate_incremental`
  would then report `error` on every run. The comment in `duckpgq_graph.py:474` claims legacy rows
  without `updated_at` are readable, which is not true of the model.

## Checklist 5: erasure of the new table

PARTIAL. The statements run on both backends and are guarded, but the effect is unverified and fenced
only at purge time.

- Both backends run them: `_purge_sql_step` (`src/prme/storage/erasure.py:608-710`) uses the shared
  `_Session` shim (`src/prme/storage/extraction_work.py:74-121`), so the same SQL runs on DuckDB and
  asyncpg. The `resume_id` UPDATE (`erasure.py:682-690`) and the `scope_key` DELETE (`:691-697`) are
  each guarded by `"organizer_watermarks" in tables`, which matters because `prepare_store` runs
  before `initialize_database`/`initialize_pg_database` on the open path
  (`src/prme/storage/engine.py:731-734`, `:954-957`), so an upgrade purge on a pre-#517 pack finds no
  table and skips cleanly.
- **Finding 6 (medium-high): purge verification does not check the table, and nothing fences a later
  watermark write.**
  - `_verify_purge` (`erasure.py:826-874`) checks `nodes`, `vector_metadata`, `vector_staging`,
    `lexical_documents`, `edges` and `operations` for a node target (`:862-870`) but never queries
    `organizer_watermarks`. A scope row that survived the DELETE (wrong spelling of the owner id, a
    failed statement reported as storage_unavailable and retried, a future schema change) therefore
    passes verification.
  - `engine.set_organizer_watermark` writes through `self._graph_store` directly
    (`engine.py:3168-3181`), bypassing `_ordinary_graph` and the erasure authority guard. A run that
    is in flight when a purge commits, or a later `organize(user_id=<erased owner>)` from a cached
    tenant list, re-inserts a plaintext `scope_key` row for the erased owner (and can re-insert an
    erased node id as `resume_id`). The erasure is then not durable: the comment at
    `erasure.py:692-693` states the owner's rows "must not survive the purge", and nothing enforces
    that after the purge transaction.
  - Recommend: add an `organizer_watermarks` check to `_verify_purge` keyed on
    `scope_key = str(intent.owner_id)`, and have `set_organizer_watermark` refuse (or drop) a scope
    with a memory-mode erasure intent, or route the write through the coordinator guard.
  - Residual, lower risk: `scope_key` is the raw `user_id` string passed to `organize`, while the
    DELETE uses `str(intent.owner_id)` (canonical UUID). On a protected store `ReadContext.create`
    already requires the canonical form (`src/prme/privacy/reads.py:68-74`), so a successful scoped
    run matches; a non-UUID owner id used on an unprotected store would not match, but such a store
    has no UUID erasure identity to begin with.
- Other durable copies: `prme migrate` keeps the table (`pack_tools.py:228`); the protected export
  path re-creates an empty table via `initialize_*` (`privacy_export.py:589-601`), so no stale copy
  travels with an artifact. The scope identity is otherwise only in `nodes.user_id`, which the purge
  deletes.

## Checklist 6: retry / idempotency

PARTIAL. The happy-path retry is idempotent; two failure paths are not.

- Re-running the same window is safe by construction: `promote` only scans `TENTATIVE`
  (`jobs.py:287`), so an already-promoted node is not re-selected, and a duplicate merge retires the
  archived node out of the `TENTATIVE/STABLE` scan (`jobs.py:727`), so the pair disappears on the next
  pass. `tests/test_organizer_incremental.py:176-201,319-368` assert convergence and equivalence with
  the full jobs on DuckDB; `tests/test_organizer_incremental_pg.py:126-147` on PG.
- A crash between the job writes and the watermark write re-runs the window; nothing else writes the
  table, so this only costs time.
- **Finding 2 (high): `_deduplicate_incremental` advances its cursor past a page that failed.**
  A page-level exception sets `failed = True` (`jobs.py:774-779`) but does not break the loop; the
  loop then moves the in-run cursor to `nodes[-1]` (`:783-784`) and continues. If a later page
  succeeds, `last_cursor` (`:772-773`) is past the failed page, and `_resume` (`:803-804`) tells the
  retry to start after it. The unexamined nodes in the failed page are never deduplicated unless
  something updates them. The error path must stop at the failed page.
- **Finding 3 (high): a node whose vector search raised is counted as fully searched.**
  `_find_duplicates_in_nodes` increments `completed` before `index.search` (`deduplication.py:180-198`);
  the `except Exception: continue` still leaves the node counted, and nodes after it are counted too.
  The caller advances `done = examined[:completed]` and the cursor (`jobs.py:768-773`), so a node whose
  neighbour search failed is skipped on the retry, contrary to the function's own docstring ("how many
  leading input nodes were fully searched"). Increment only after a successful search and stop the
  prefix at the first failure.
- Residual: the watermark upsert is last-writer-wins with no version or fence. Two concurrent
  organizer runs for one scope can move `completed_through` backwards (rework, because each value was
  produced by a completed run over a superset of that value), so I found no skip from that race; worth
  noting because the opportunistic pass and an explicit `prme organize` can overlap.

## Findings 7-9: documentation, reporting, coverage

- **Finding 7 (low): the docs overstate the write behavior.** CHANGELOG.md:12 says "A second run over
  an unchanged scope writes nothing" and RFC-0015:486-489 says "A failed run writes neither, so the
  window re-runs". The code writes the watermark on every completed run (`jobs.py:115-120`, an UPDATE
  of `completed_through` and `updated_at`), and it *does* write the processed prefix on an errored run
  (`jobs.py:100-114`). Only node writes are zero. The RFC/code disagreement is the one that matters:
  the RFC promises a full re-run on failure, the code resumes from the prefix.
- **Finding 8 (low): protected callers cannot see the new `status`/`stopped_on_budget`.**
  `project_organize` rebuilds each `JobResult` from numeric fields only
  (`src/prme/privacy/diagnostics.py:178-188`), so an `organize`/`end_session` result projected for a
  protected store (`src/prme/privacy/reads.py:683-685`) reports the defaults `completed`/`False` for a
  stopped or failed run. The CLI message added at `src/prme/cli.py:624-625` is correct only on the
  unprotected path. These two are typed outcomes, not free-form `details`, so they can be added to the
  projection.
- **Finding 9 (low): the erasure behavior added here has no assertion.** Every erasure test store now
  contains an empty `organizer_watermarks` table, so the two new statements execute (which is why the
  erasure suites stay green), but no test seeds a watermark row for an erased owner or node, so
  deleting the owner's rows and clearing `resume_id` is unverified. Add one DuckDB and one PG case
  (an owner scope plus an unscoped resume cursor naming the erased node).

## Verified non-issues

- No backfill is required: the table starts empty and "absent row" means a first run with no lower
  bound. Records are not rewritten by enabling the option; only `organize()` call paths change, and a
  default `organize()` reads and writes no watermark (`jobs.py:175`, tested at
  `tests/test_organizer_incremental.py:371-390`).
- `JobResult.status` / `stopped_on_budget` are additive with defaults, so existing constructors and
  serialized results stay valid (`src/prme/organizer/models.py:24-31`).
- `_owned_view_sync` on the DuckDB scan preserves row count and order
  (`src/prme/storage/owner_reads.py:41-54`), so it cannot make a short page look like end-of-window.
- `scan_changed_nodes` on PG uses `_NODE_COLUMNS` (`pg/graph_store.py:497-564`), which is valid after
  the existing `event_time`/`ttl_days` migrations (`pg/schema.py:341-342`); a runtime-login deployment
  that lacks those columns already fails the fingerprint check.
- `snapshot_generation` failures only skip an ephemeral, non-persisted view
  (`jobs.py:971-973`), so its cursor advance past a failed snapshot is not a durable loss. Its
  `except (ValueError, Exception)` is redundant with `except Exception`, not a defect.
