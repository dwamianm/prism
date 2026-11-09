Status: IN_PROGRESS
Checklist: 1,3,4,5
Snapshot: d320b495b116bb290974d7b1c3aee2bed1d1dd93d5df3857abfef9663635e9b8
Base: main at 2014d15aa86fedc307b18d2af49a57af2ca774a9
Ticket: PRME #411, branch issue-411-pg-schema-init-lock

## Summary

The production change is small, matches the established `pg_advisory_xact_lock(pg_catalog.hashtextextended(schema, 0))` pattern, and is correct: the schema-keyed advisory lock is taken inside the same transaction as the DDL, so it is held for the whole DDL window and released on commit, rollback or cancellation; nesting is safe (asyncpg savepoints) and re-entrancy with `namespace.py` is safe (same session, same key). The new test reproduces the reported race with four engines behind an `asyncio.Barrier` and is skipped without `PRME_TEST_DATABASE_URL`. No Must Fix finding. The remaining findings are documentation/convention and defensive-robustness items, all non-blocking.

## Must Fix

None found.

## Should Fix

1. **No CHANGELOG entry.** `src/prme/storage/pg/schema.py` changes user-visible startup behavior (concurrent engine opens no longer fail), but the working tree touches no CHANGELOG. Every recent behavior fix in this repository does: `git show --stat a200cf3f` and `2014d15a` both add a `CHANGELOG.md` line under `### Fixed` (CHANGELOG.md:16). Suggested fix: one "Fixed" bullet under Unreleased naming the duplicate-key failure on concurrent `initialize_pg_database`, the schema-scoped advisory lock, and the fact that existing stores are untouched.

2. **The test docstring records the wrong failure mode for "fails on main".** `tests/test_pg_schema_init_race.py:5` says the second engine fails on `pg_type_typname_nsp_index`, but the recorded main-side repro (implementation evidence) fails first on `pg_extension_name_index` while running `CREATE EXTENSION IF NOT EXISTS pgcrypto` (the first statement the lock now covers, `src/prme/storage/pg/schema.py:126` and `:283-284`). The test is still valid, but the documented evidence does not match what it reproduces. Suggested fix: name the extension constraint (or say "a catalog unique index, for example `pg_extension_name_index`") so the "fails on main" evidence and the docstring agree.

## Consider

1. **`current_schema()` can return NULL and the code does not guard it.** `src/prme/storage/pg/schema.py:277-281` passes the raw `fetchval` result to `hashtextextended`/`pg_advisory_xact_lock`. With an empty `search_path` the value is NULL and the lock is not taken (the subsequent `CREATE TABLE`/`CREATE EXTENSION` would fail with "no schema has been selected to create in", so this is not a silent-success path, but the failure is not explicit). The repository already has the defensive form for exactly this: `src/prme/storage/pg/model_vector_index.py:40-41` uses `coalesce(current_schema(), '')`. Suggested fix: fail fast (`if schema is None: raise RuntimeError(...)`) or coalesce before hashing. Also note the key is a bare schema hash rather than `hashtextextended('prme.schema_init:' || ..., 0)`; matching `namespace.py:107` (bare hash) is the right choice here because the two must share a key for re-entrancy, so this is not a defect.

2. **Residual, pre-existing: protected stores do DDL before the lock.** In `MemoryEngine._create_postgres`, `prepare_store` runs before `initialize_pg_database` (`src/prme/storage/engine.py:943-947`). With a `privacy_runtime`, `prepare_store` executes `CREATE TABLE IF NOT EXISTS` statements (`src/prme/storage/erasure.py:212-220`) outside any PostgreSQL advisory lock. Four protected engines on one fresh database could still collide there. In practice `LocalErasureLedger.guard` (`src/prme/privacy/ledger.py:72-92`) serializes those opens via its ledger file lock, but that is incidental, not guaranteed. Not a regression from this change and outside the ticket's demonstrated path; recommend a follow-up note or a follow-up ticket rather than expanding this PR.

3. **Residual: extension creation is database-global, the new lock is schema-scoped.** `CREATE EXTENSION` (`src/prme/storage/pg/schema.py:125-127`) is per-database, while the lock is per-schema. Two *different* schemas opening one fresh database concurrently take different keys and can still race on `pg_extension_name_index`. The real multi-tenant path is covered incidentally because `WorkspaceCatalog.initialize` creates both extensions before any namespace exists, under a different key (`src/prme/storage/pg/workspace_catalog.py:46-48`), and later `CREATE EXTENSION IF NOT EXISTS` calls are no-ops. This is pre-existing and not reachable in the tested path; record it as a known limitation if the behavior is documented.

4. **The new test adds the fourth copy of a disposable-database helper.** `tests/test_pg_schema_init_race.py:33-49` re-implements create/drop logic that already exists at `tests/conftest.py:51-57`, `tests/test_pg_workspace.py:230-281` and `tests/privacy_portability_support.py:27-36`; no shared helper exists, so this is duplication of a pattern rather than a missed import. Two concrete nits: use `quote_identifier` from `prme.storage.pg.vector_sql` (as `tests/test_pg_workspace.py:234` does) instead of the hand-built `f'CREATE DATABASE "{name}"'` / `DROP DATABASE IF EXISTS "{name}"` strings, and consider lifting the helper into `tests/` support so it is written once.

5. **Test error paths (minor).** `tests/test_pg_schema_init_race.py:33-41` errors instead of skipping when the role lacks `CREATEDB`; the established analogue skips (`tests/test_pg_workspace.py:235-236`). If one of the four engines fails before reaching the barrier (for example in pool creation), the other three wait the full 60 s `asyncio.wait_for` timeout (`:71`) before the test fails; a shorter timeout or `asyncio.gather` around an explicit failure would fail faster. `assert len(engines) == 4` (`:88`) cannot fail independently of `gather` and is redundant. `_config` (`:52-60`) is used once and could be inlined.

## Checklist 1: Correctness and logic

- Lock scope: `async with pool.acquire() as conn, conn.transaction():` (schema.py:276) wraps the lock (277-281), extensions (283-284) and every DDL statement through `_record_runtime_schema` (379). The lock is therefore held for the whole DDL window and released at commit; on any exception the `Transaction.__aexit__` rollback releases it. Verified against the asyncpg `Transaction` contract and the analogous `namespace.py:106-131`.
- Nested transactions / callers already in a transaction: `namespace.py:106` opens an outer transaction and calls `initialize_pg_database` at `:131`; `privacy_export.py:761-768` and `:847-901` do the same with `transaction.start()`. asyncpg implements a nested `conn.transaction()` as a SAVEPOINT, and this nesting is already exercised in CI through `ErasureRepository.session(transaction=True)` (`src/prme/storage/extraction_work.py:109-116`) inside `_restore_database`. No breakage.
- Lock key / collision / re-entrancy: `namespace.py:107` keys on `hashtextextended(namespace_schema(identifier), 0)` and sets `search_path` to that schema at `:119`, so `current_schema()` at schema.py:277 yields the identical key; `privacy_export.py:860` plus `:866` do the same. PostgreSQL advisory locks are reentrant per session ("additional requests by it will always succeed"), so the inner acquisition does not deadlock; it just extends the hold to the outer transaction.
- Lock order: `WorkspaceCatalog.prepare` takes the registry row lock (`workspace_catalog.py:73`) before `prepare_namespace` takes the schema lock (`namespace.py:107`), and the new lock is always taken after that same key is already held. No new lock-order cycle.
- Sentinel review: the only new call whose contract matters is `conn.fetchval("SELECT pg_catalog.current_schema()")` returning `str | None`; the `None` case is not handled (Consider 1). `resolve_vector_sql` (`vector_sql.py:36-44`) raises rather than returning a sentinel and is unchanged; all sub-initializers return `None`.
- Breaking change scan: no new imports, no signature change, no return-value change. Behavior differs only in atomicity (all-or-nothing DDL, previously partial state) and in that a concurrent reader can no longer observe a half-built schema; both are improvements and no caller depends on partial state. `tests/test_pg_schema_catalog.py:32-33` (idempotent double call) and `tests/test_pg_namespace_pool.py:130-145` (rollback then retry) still exercise the changed paths.
- Concurrency scenarios checked: same-schema concurrent init (protected by the lock), nested call under the same key (reentrant), error/cancellation release (transaction rollback), different-schema extension race (Consider 3), pre-lock DDL for protected stores (Consider 2).

## Checklist 3: Code quality and test coverage

- Production diff: 12 added lines, one read + one lock statement plus an expanded docstring; no dead code, no new abstraction. Complexity unchanged. The `logger.info` and `return vector_sql` stay outside the transaction, which is correct.
- Test coverage of the criteria: criterion 1 is asserted by `asyncio.gather` (`:87`) raising on any failure; criterion 2's "at least four engines concurrently" is guaranteed by `asyncio.Barrier(4)` (`:67`, `:71`) rather than by scheduling; the skip condition (`:28-30`) matches the criterion exactly; criterion 3 is covered by the existing reopen tests rather than this one (acceptable, and confirmed by the worker's 100-passed PostgreSQL run); criterion 4 is CI's job.
- "Fails on main": the test is a positive assertion and cannot prove its own counterfactual, but the worker's stashed-fix run failed with `UniqueViolationError` on `pg_extension_name_index` while running `CREATE EXTENSION pgcrypto`, so the requirement is met and the evidence should be preserved in the PR body. The barrier makes the four `CREATE EXTENSION` calls overlap, which is the reliable mechanism asked for; residual scheduling sensitivity exists but the observed failure is strong evidence.
- The test asserts the shared schema is usable, not merely that no exception was raised (`count_nodes` per engine, `:89-91`), which is a meaningful positive check.
- Minor quality items are listed under Consider 4 and 5 (helper duplication, quoting, error-path skips, redundant length assertion, single-use `_config`).

## Checklist 4: Pattern consistency and DRY

- Categorization: one storage-layer initialization module (`src/prme/storage/pg/schema.py`) and one PostgreSQL integration test.
- Lock key derivation: (a) new code uses `pg_catalog.hashtextextended(current_schema(), 0)` with no purpose prefix and no NULL handling; (b) established patterns are `pg_catalog.hashtextextended(<explicit schema>, 0)` (`namespace.py:107`, `privacy_export.py:860`, `workspace_catalog.py:22`) and `hashtext('prme.<purpose>:' || coalesce(current_schema(), ''))` (`model_vector_index.py:40-41`); (c) suggested fix: keep the bare schema hash (it must equal `namespace.py`'s key), but adopt the `coalesce`/guard from `model_vector_index.py` so a NULL schema fails loudly.
- Transaction scope: (a) new code takes the lock inside the transaction that creates the relations; (b) identical to `namespace.py:106-107` and `workspace_catalog.py:21-22`; (c) no change suggested.
- Error/rollback behavior: (a) transaction rollback releases the lock and undoes the DDL; (b) `namespace.py` relies on the same behavior (covered by `tests/test_pg_namespace_pool.py:130-145`); (c) consistent, no change.
- Docstring/comment style: (a) the new docstring explicitly cites `namespace.py` as the analogue and explains why `IF NOT EXISTS` is unsafe; (b) matches the prose style used in `namespace.py:92-98`; (c) consistent.
- Import patterns: no new imports; `logging`, `hashlib`, `json` already present. Consistent.
- Test conventions: (a) the new test uses `pytest.importorskip("asyncpg")`, a module-level `skipif` on `PRME_TEST_DATABASE_URL`, and a first-line issue citation; (b) exactly the pattern of `tests/test_multi_model_postgres.py:11,20` and `tests/test_pg_graph_store.py:21-22`, `tests/test_pg_lexical_index.py:19-20`; (c) consistent. The one departure is the hand-built database identifier quoting versus `quote_identifier` in `tests/test_pg_workspace.py:234` (Consider 4).

## Checklist 5: Redundancy and unnecessary code

- New symbols in the test: `_create_fresh_database`, `_drop_database`, `_config`, the `synchronized` closure, and the `created` list. All are used; none is dead. `created` is necessary because `gather` yields nothing when it raises.
- Existing equivalents: the disposable-database helpers duplicate the logic at `tests/conftest.py:51-57`, `tests/test_pg_workspace.py:230-281` and `tests/privacy_portability_support.py:27-36`, but no callable shared helper exists, so this is a missed extraction rather than a missed import (Consider 4).
- Over-engineering: the `synchronized` wrapper could use `functools.wraps` but is otherwise minimal; `_config` is single-use (Consider 5). The `assert len(engines) == 4` is redundant with `gather`.
- No production symbol was added; the change reuses existing SQL constants and sub-initializers.

## Callers of `initialize_pg_database` (all read)

- `src/prme/storage/engine.py:947` — plain engine path, not inside a transaction; new lock applies directly. `MemoryEngine.create` rewrites `config.embedding.dimension` from the supplied provider (`engine.py:564-568`), so the test's 16-dimension `small()` provider is consistent with the `vector(16)` column.
- `src/prme/storage/pg/namespace.py:131` — inside the outer transaction; lock already held on the same key; savepoint nesting.
- `src/prme/storage/privacy_export.py:597`, `:768`, `:901` — `_restore_database` and both restore adapters; `:768` and `:901` run inside a caller-started transaction (`:761`, `:847`) and `:901` already holds the same key at `:860`.
- Tests and benchmark harness (`tests/test_pg_*.py`, `tests/test_neighborhood_depth.py`, `benchmarks/diagnostics/pg_vector_modes.py`) — sequential calls, unaffected.

## Verification notes

- I did not re-run the worker's commands. The worker's evidence (ruff clean; 1 pass with the fix against PostgreSQL 16.11; 1 failure with the fix stashed, `UniqueViolationError` on `pg_extension_name_index`; 100 passed / 10 skipped on the targeted PostgreSQL suites) is consistent with the source I read.
- No transaction-incompatible DDL (`CREATE INDEX CONCURRENTLY`, `VACUUM`, `CREATE DATABASE`, `ALTER TYPE`) exists in `initialize_pg_database` or in any sub-initializer it calls (`derivation_registry.initialize_pg`, `profile_registry.initialize_pg`, `consolidation_publication.initialize_pg`, `privacy.initialize_pg`, `install_pg_format`, `profile_publication.HEADS_DDL`, `profile_work.PROFILE_WORK_DDL`), so wrapping them in a transaction cannot fail on that class of statement.
- Every DDL statement in the path uses unqualified relation names, so it lands in `current_schema()`, which is exactly the schema the lock is keyed to.
- Unresolved risk: the two residual gaps in Consider 2 and 3 are not covered by the new test and are not regressions; they should be recorded as follow-up rather than folded into this ticket.

Status: COMPLETE
