# Review: issue #411 schema-init advisory lock (data/storage safety, checklist 7)

Status: COMPLETE

Checklist: 7

Snapshot: d320b495b116bb290974d7b1c3aee2bed1d1dd93d5df3857abfef9663635e9b8
Base: 2014d15aa86fedc307b18d2af49a57af2ca774a9 (main)

## Checklist results

- Schema and lock-key compatibility: PASS. `src/prme/storage/pg/schema.py:277-281` keys on
  `pg_catalog.current_schema()`, which is the same text `namespace.py:107` and
  `privacy_export.py:860` pass literally, because those callers install the target schema as the
  creation schema first (`namespace.py:119`, `privacy_export.py:866`). Re-acquisition by the same
  session is reentrant, so nesting is not a self-deadlock. `current_schema()` returning NULL would
  make `hashtextextended` return NULL and take no lock, but no DDL can succeed in that state
  either, so it is not a silent unlocked-success path.
- Migration and backfill safety: PASS, no schema change. The diff adds no DDL statement, so
  `_schema_fingerprint` (`schema.py:37-59`), the stored runtime body (`schema.py:62-67`), and
  `validate_pg_runtime` (`schema.py:70-122`) are byte-identical; no backfill and no
  `DERIVATION_VERSION` bump is required (no derived record changes).
- Preservation of existing records: PASS. The diff adds only `conn.transaction()` and two SELECTs
  (`schema.py:276-281`). No INSERT/UPDATE/DELETE/DROP on existing tables is added; the existing
  `INSERT ... ON CONFLICT DO UPDATE` at `schema.py:67` and the registry recovery writes are
  unchanged in content.
- Retry behavior: ACCEPTABLE, one note. There is no retry, and the change replaces a fail-fast
  catalog collision with a blocking wait. The plain path sets no `lock_timeout` or
  `statement_timeout` (`storage/pg/pool.py:29-33` sets none either), unlike the restore path which
  sets `SET LOCAL lock_timeout='5s'` (`privacy_export.py:848`). A stuck peer now turns startup
  into an unbounded wait instead of an error. See Consider 2.
- Lock held across all DDL / released on success and error: PASS. Every statement from
  `schema.py:283` to `schema.py:379` is inside the transaction; `logger.info` and `return` are
  outside it (`schema.py:380-383`), so the commit happens first. A transaction-level advisory lock
  is released at the end of the enclosing top-level transaction, which is guaranteed on both the
  success and error paths by `namespace.py:106`, `privacy_export.py:761-787`, and
  `privacy_export.py:843-930`; asyncpg's nested `transaction()` becomes a savepoint and cannot
  outlive those outer transactions.
- Transactional-only DDL: PASS. The reachable DDL (schema.py, `privacy.py:16-23`,
  `derivation_registry.py:14-17`, `profile_registry.py:216-226`,
  `consolidation_publication.py:181-189`, `privacy_format.py:18`) is CREATE TABLE / CREATE INDEX /
  ALTER TABLE only. The only `CREATE INDEX CONCURRENTLY` in the tree is
  `storage/pg/model_vector_index.py`, reached after initialization (`engine.py:964`), not inside it.
- Deadlock with the workspace catalog, namespace, or restore locks: no inversion found. Order is
  row lock then schema advisory then relation locks in both `workspace_catalog.py:71-82` and
  `privacy_export.py:851-866`; nothing that holds a schema advisory lock then waits on
  `prme_workspace.namespaces`. `prme_workspace_catalog_v1` (`workspace_catalog.py:22`) is never
  held together with a schema lock. The widened relation-lock hold is discussed in Should Fix 2.
- Test cleanup: PASS with one gap. Pending tasks are cancelled and gathered, created engines are
  closed, and the fresh database is dropped with `WITH (FORCE)`
  (`tests/test_pg_schema_init_race.py:92-99`). See Consider 3.

## Findings

### Must Fix

None found in this checklist's area.

### Should Fix

1. Privacy-enabled stores still race before the advisory lock, so "concurrent engines all start"
   holds only for stores without a privacy runtime. `MemoryEngine._create_postgres` runs
   `prepare_store(initialize=True)` before `initialize_pg_database`
   (`engine.py:938-947`), and that path executes the seven `CREATE TABLE IF NOT EXISTS
   privacy_*` statements in its own transaction (`storage/erasure.py:209-220`). Two engines on one
   fresh database therefore still collide in the PostgreSQL catalog there, which is the same
   failure the ticket describes; only `privacy_runtime is None` returns early
   (`erasure.py:198-201`). The new test constructs engines with no runtime
   (`tests/test_pg_schema_init_race.py:81`), so it cannot see this. This is pre-existing and
   outside the changed file, so a follow-up issue is a reasonable outcome, but the limitation
   should be stated in the PR rather than implied by the acceptance criterion. The same applies to
   any direct multi-schema caller that skips the workspace bootstrap extension install at
   `workspace_catalog.py:47-48` before `workspace.py:461` runs ahead of `workspace.py:477`; there,
   two per-schema locks would not serialize database-global `CREATE EXTENSION`
   (`schema.py:283-284`). In the workspace flow the extensions already exist by then, so it is a
   no-op.

2. Making initialization atomic widens relation-lock hold time on existing databases, which is a
   behavior change against the "opening an existing database behaves as before" criterion.
   `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes ACCESS EXCLUSIVE even when the column exists,
   and those locks now last until the single commit at `schema.py:380` instead of until the end of
   their own statement: events at `schema.py:307`, event_extractions at `schema.py:325`,
   nodes at `schema.py:328-333` and `schema.py:341`. The per-module transactions inside the
   initializers (for example `profile_registry.py:216`) become savepoints, so their locks also
   now run to the end of the whole initialization. Any index that still has to be built holds its
   ShareLock for the rest of initialization too. I traced the current multi-table writers for
   inversion and found none: the erasure purge acquires privacy_admissions, then events, then
   nodes, then profile_work (`erasure.py:627-692`), which is prefix-compatible with the
   initializer's order, so the observed effect is longer blocking, not a deadlock. Worth stating
   in the PR, and worth pairing with a bounded `lock_timeout` on this path.

### Consider

1. No bound on the new wait. Consider `SET LOCAL lock_timeout` on the initialization transaction,
   following the restore path's precedent (`privacy_export.py:848`), so a peer holding a relation
   lock cannot turn engine startup into an indefinite hang. This can be scoped as a separate
   issue.

2. Test cleanup can skip the database drop. `for engine in created: await engine.close()` runs
   before `_drop_database` in the same `finally` (`tests/test_pg_schema_init_race.py:96-99`), so a
   raising `close()` leaves a `prme_race_*` database behind on failure. Wrapping the close loop in
   its own try/finally, or using `return_exceptions=True`, keeps teardown unconditional. Likewise,
   if `_create_fresh_database` creates the database and then fails before returning
   (`tests/test_pg_schema_init_race.py:33-41`), nothing drops it.

3. The test exercises only the fresh-database path and only the no-runtime configuration. The
   "behaves as before" half of the acceptance criteria (reopening an existing database, a second
   initializer taking the lock and finding everything present) is covered by
   `tests/test_pg_schema_catalog.py:32-33`, which is fine, but a line asserting idempotence on the
   already-initialized race database before dropping it would pin the new lock's fast path. Not
   required.

4. Docstring accuracy on the failure the test reproduces. With the barrier, the first collision on
   an unlocked tree may be `CREATE EXTENSION` (a database-global catalog insert) rather than
   `pg_type_typname_nsp_index` as stated (`tests/test_pg_schema_init_race.py:3-5`). The test still
   fails without the lock either way, so this is wording only.

5. The same class of catalog race was already worked around at the test harness level, per
   database per xdist worker (`tests/conftest.py:30-60`). That workaround can stay, but its
   comment now describes a production race this change closes.

## Notes on verification limits

The PostgreSQL savepoint and lock-hold analysis is from source reading and PostgreSQL documented
semantics; no database was started and no test was executed in this review (the worker owns test
execution). The claim that ACCESS EXCLUSIVE is taken for a no-op `ADD COLUMN IF NOT EXISTS` is the
load-bearing assumption of Should Fix 2; the practical impact of the widened hold still needs a
measurement if it is judged material.

Status: COMPLETE
