# Issue #411 Security Review (checklist 2)

Status: COMPLETE
Checklist: 2
Snapshot: d320b495b116bb290974d7b1c3aee2bed1d1dd93d5df3857abfef9663635e9b8
Base: main at 2014d15aa86fedc307b18d2af49a57af2ca774a9
Branch: issue-411-pg-schema-init-lock

## Scope reviewed

- `src/prme/storage/pg/schema.py` (production change: `initialize_pg_database` now
  runs inside `pool.acquire()` + `conn.transaction()`, reads
  `pg_catalog.current_schema()`, and takes
  `pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))` before the
  existing DDL).
- `tests/test_pg_schema_init_race.py` (new; creates a throwaway database, opens four
  engines against it, drops it).

Callers and neighbours read for context: `src/prme/storage/engine.py:916-951`,
`src/prme/storage/pg/namespace.py`, `src/prme/storage/pg/workspace_catalog.py`,
`src/prme/storage/privacy_export.py:747-787, 830-931`,
`src/prme/storage/pg/vector_sql.py`, `src/prme/storage/pg/pool.py`,
`src/prme/storage/{privacy,privacy_format,derivation_registry,profile_registry,consolidation_publication}.py`
initialize hooks, `tests/conftest.py:30-60`, `tests/multi_model_support.py:104-126`,
`docker-compose.yml`, `.github/workflows/ci.yml`. Note: the repository CLAUDE.md has
no dedicated security section; this review applies its general constraints (no
secrets or whole transcripts in captured notes/output, local-only test database,
no paid providers) plus general practice.

## Checklist results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs or responses | PASS | The change adds no logging. The only log call in the function is pre-existing and prints only `embedding_dim` (`src/prme/storage/pg/schema.py:381`). The new statement's only parameter is the schema name, which is not a secret and is never logged or echoed. The new test logs and asserts nothing about the DSN (`tests/test_pg_schema_init_race.py:63-99`). |
| Missing authorization or ownership checks | PASS | No new endpoint, operation or capability. The advisory lock is mutual exclusion, not an authorization control, and no privilege check is bypassed: every DDL statement keeps its own `CREATE`/ownership check, now merely inside a transaction. The lock is database-local and cannot be used to reach another database or schema's objects. |
| Input validation and SQL injection | PASS | The lock schema is passed as bind parameter `$1`, never interpolated (`src/prme/storage/pg/schema.py:278-281`), and the whole query is schema-qualified `pg_catalog.*`, which also defeats search_path function hijacking. DDL strings are constants (only `embedding_dim` is interpolated, an `int` validated at `schema.py:274-275`). In the test the only interpolated identifiers are `prme_race_` + `uuid4().hex[:16]` (`tests/test_pg_schema_init_race.py:35,38,47`), i.e. hex-only and locally generated, so no injection surface. |
| Command injection, path traversal, XSS | N/A | No shell, filesystem path, HTML or template input is added by either file. The test writes only under pytest's `tmp_path` (`tests/test_pg_schema_init_race.py:52-60`). |
| Unsafe deserialization | PASS (unchanged) | `json.loads` of `prme_runtime_schema.body` at `src/prme/storage/pg/schema.py:80` and `json.dumps` at `:58,65-67` are pre-existing and untouched. They are now inside the transaction; only a role that can already write that table could influence the payload, and the parsed value is only compared to expected keys and lengths (`:82-86`). |
| Overly permissive CORS or network binds | N/A | No listener, CORS setting or bind address is changed. The CI test database remains loopback-only (`docker-compose.yml:1-4,12-13`) and the test connects only through `PRME_TEST_DATABASE_URL`. |
| Credentials in code or test fixtures | PASS | The new test adds no credential; it reads `PRME_TEST_DATABASE_URL` (`tests/test_pg_schema_init_race.py:76`) and skips without it (`:28-30`). The only checked-in credentials are the pre-existing, documented loopback-only test values in `docker-compose.yml:9-11`. |
| Test database create/drop safety (cannot target a non-test DB) | PASS | `_create_fresh_database` uses the env URL only as an admin connection and creates a fresh database named `prme_race_<uuid4 hex>` (`tests/test_pg_schema_init_race.py:33-41`); `_drop_database` drops only that generated name, never the database named in the URL (`:44-49,99`). Same pattern as the existing `tests/conftest.py:49,54-55` and `tests/multi_model_support.py:115-125`. A collision would require guessing 64 random bits and would only fail `CREATE DATABASE`. Drop runs in `finally` after engines close (`:86-99`). |
| Test URL or credentials logged or persisted | PASS | The test has no logging and writes no URL to disk; `PRMEConfig.database_url` is `SecretStr` (`src/prme/config.py:742-745`), so config reprs and dumps mask it. The plaintext `url`/`base` locals exist in memory only and are not asserted or printed; without `--showlocals` (not configured in `pyproject.toml`) they do not appear in output. Same handling as existing tests. |
| Advisory lock key derived from `current_schema()` / untrusted `search_path` | PASS | The key follows `pg_catalog.current_schema()`, which is exactly the resolution PostgreSQL uses for the unqualified `CREATE TABLE IF NOT EXISTS` that follows, so the lock always covers the schema the DDL targets; a search_path cannot make the lock cover schema A while tables land in schema B. A hostile or empty search_path can redirect where tables are created, but that placement is pre-existing behavior of the same unqualified DDL and gives no new privilege. If no schema resolves (`current_schema()` NULL), the strict advisory function takes no lock, and the next `CREATE TABLE` fails with "no schema has been selected to create in", so no DDL runs unprotected. In the namespace path the caller pins `search_path` to the private schema first (`src/prme/storage/pg/namespace.py:119`) and the inner read returns the same name `namespace.py:107` locked, so the two lock acquisitions are the same reentrant key. |
| Transaction wrapping vs. privilege behavior and error exposure | PASS | Each statement keeps its own privilege check; wrapping in `BEGIN`/`COMMIT` does not grant or bypass anything. Errors propagate unchanged (the added `SELECT`s are constants plus one bound schema name); the function still raises the sanitized `postgres_schema_bootstrap_required` rather than exposing catalog detail. The behavioral change is atomicity (a mid-way failure now rolls the schema back instead of leaving a partial one) and longer lock hold time, see Consider. No statement in the wrapped set needs autocommit: nothing uses `CONCURRENTLY`, `VACUUM`, `CREATE DATABASE`, or `ALTER TYPE ... ADD VALUE` (`rg` over `src/prme/storage` finds `CONCURRENTLY` only in `pg/model_vector_index.py`, which runs after initialization). |
| Lock ordering / deadlock with existing advisory-lock users | PASS | Only initialization paths take this key, always before their DDL; the workspace registry uses a different constant key (`pg/workspace_catalog.py:22`). `workspace_catalog.py:71-82` and `privacy_export.py:842-866,897-901` take the registry row lock first and the schema key second, the same order as the new code, so no inversion. The private-schema lock in `namespace.py:107` is re-entered, not waited on, by the nested call at `namespace.py:131`. |
| Availability: unbounded wait on the advisory lock | CONSIDER | `pg_advisory_xact_lock` blocks with no timeout (`src/prme/storage/pg/schema.py:278-281`). An unrelated session on the same database (or a hash collision, which only serializes) can stall engine startup indefinitely. The restore path already bounds this with `SET LOCAL lock_timeout='5s'` (`src/prme/storage/privacy_export.py:848`), and the new call inherits it when it runs inside that transaction (`:901`); the plain `MemoryEngine.create` path does not. `namespace.py:107` has the same gap today, so this is not a regression, only an opportunity. |
| Longer DDL lock hold | CONSIDER | The whole bootstrap is now one transaction, so `ACCESS EXCLUSIVE`/`SHARE` locks taken by the upgrade-path `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and index creation (`schema.py:307,328-347`) are held until commit instead of being released per statement. On a fresh database (the ticket's case) there is no concurrent traffic; on an existing populated database a concurrently serving process can now block longer during startup. Not a security defect and arguably safer (atomic bootstrap), but worth recording. |
| Test-only privilege assumption | CONSIDER | The test opens a fresh database, so the CI/dev role needs `CREATEDB`; with a role that lacks it the test errors instead of skipping. CI's `prme_test` is the container superuser (`docker-compose.yml:9`), and `tests/conftest.py:54-55` already requires the same right under `-n`, so no new grant is implied. |
| `current_schema()` NULL defensive check | CONSIDER | As above, a NULL schema makes the lock a no-op before the DDL fails. A one-line `if schema is None: raise ValueError(...)` would make the invariant explicit, but it changes no security property. |

## Must Fix

None. No secret, credential, authorization, injection, deserialization, CORS/bind or
test-database-safety defect is introduced by the changed code.

## Should Fix

None.

## Consider

1. `src/prme/storage/pg/schema.py:278-281` - consider `SET LOCAL lock_timeout` (for
   example the same `'5s'` the restore path uses at
   `src/prme/storage/privacy_export.py:848`) before the advisory lock, and translate
   the timeout into a clear startup error. Today a stale or hostile session holding
   `hashtextextended(<schema>, 0)` blocks `initialize_pg_database` without bound. This
   matches an existing gap in `src/prme/storage/pg/namespace.py:107`, so it is a
   hardening follow-up rather than a regression of this change.
2. `src/prme/storage/pg/schema.py:276` - the single transaction lengthens how long
   upgrade-path `ALTER TABLE`/`CREATE INDEX` locks on `events`/`nodes` are held on an
   already-populated database. If a long startup stall is observed in production,
   consider bounding the init (lock_timeout/statement_timeout) or splitting the
   migration step from the create step. Fresh-database startup, the ticket's case, is
   unaffected.
3. `src/prme/storage/pg/schema.py:277` - an explicit
   `if schema is None: raise ValueError(...)` would document that an empty
   `search_path` is a configuration error rather than a silently unlocked bootstrap.
4. `tests/test_pg_schema_init_race.py:33-49` - keep the generated-name-only create/drop
   pattern; if the suite ever gains a host guard for `PRME_TEST_DATABASE_URL`, apply
   it here too. The test currently requires `CREATEDB` and will error rather than skip
   for a read-only role.
5. `tests/test_pg_schema_init_race.py:52-60,76-77` - the raw DSN is a plain `str` local
   rather than a `SecretStr`. It is never printed, and this matches existing tests, but
   wrapping it (or reading `.get_secret_value()` only at the connect site) would keep
   the password out of `--showlocals` output.

## Positive observations

- The lock and `current_schema()` calls are `pg_catalog`-qualified, and the schema is a
  bind parameter, so the change adds no search_path- or injection-driven surface.
- The transaction is scoped to one pooled connection and one schema, and it reuses the
  exact key and nesting pattern already established by
  `src/prme/storage/pg/namespace.py:106-107,119,131`, so concurrent initializers
  serialize without changing lock order or privilege.
- The new test derives its database name locally and drops only that name, so it cannot
  drop or point at the database named in `PRME_TEST_DATABASE_URL`.
