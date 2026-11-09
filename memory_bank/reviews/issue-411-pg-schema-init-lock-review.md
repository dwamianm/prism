# Review: issue #411 PostgreSQL schema initialization advisory lock

## Review Scope

- Base commit: `main` at `2014d15aa86fedc307b18d2af49a57af2ca774a9`
- Reviewed snapshot: `d320b495b116bb290974d7b1c3aee2bed1d1dd93d5df3857abfef9663635e9b8`
- Tier: L (`DATA`, `PERSISTED_FIELD`, `NEW_SYMBOLS` predicates; `INFRA` not applicable)
- Merged coverage: checklists 3, 4 and 5 into the core report; checklist 6 not applicable
- Completed required reports:
  - Core, checklists 1, 3, 4, 5: `memory_bank/reviews/issue-411-pg-schema-init-lock-core.md` (Opus)
  - Security, checklist 2: `memory_bank/reviews/issue-411-pg-schema-init-lock-security.md` (Opus)
  - Data, checklist 7: `memory_bank/reviews/issue-411-pg-schema-init-lock-data.md` (Sonnet)
  - Adversarial, checklist 8: `memory_bank/reviews/issue-411-pg-schema-init-lock-adversarial.md` (Opus)

## Files Changed

- `src/prme/storage/pg/schema.py`: `initialize_pg_database` takes a transaction-scoped advisory lock keyed to `pg_catalog.current_schema()` before creating extensions, tables and indexes.
- `tests/test_pg_schema_init_race.py` (new): creates a throwaway database, synchronizes four engine initializers with an `asyncio.Barrier`, opens four `MemoryEngine` instances concurrently, verifies each can read the schema, then closes them and drops the database.
- `CHANGELOG.md`: one `### Fixed` bullet under `[Unreleased]` (added after the review, see Resolution Status).

## Approach Summary

`CREATE TABLE IF NOT EXISTS` and `CREATE EXTENSION IF NOT EXISTS` are not safe against a concurrent initializer: two sessions can both observe the object missing and the second fails on a catalog unique index. The plain `MemoryEngine` path (`engine.py:947`) called `initialize_pg_database` with no lock, while the namespace path (`namespace.py:107`) and the workspace catalog already serialize with transaction-scoped advisory locks. The fix adds the same schema-keyed `pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))` at the start of the initializer, inside a transaction that spans the existing DDL. All callers benefit, including the privacy restore paths, and nesting under an already-held lock is reentrant on the same session.

## Must Fix

None.

## Should Fix

1. No CHANGELOG entry (core). Resolved: a `### Fixed` bullet was added under `[Unreleased]`.
2. Test docstring named `pg_type_typname_nsp_index` while the stashed-fix run failed first on `pg_extension_name_index` for `CREATE EXTENSION pgcrypto` (core). Resolved: the docstring now names both and says the extension constraint comes first in practice.

## Consider

1. `current_schema()` may return NULL with an empty `search_path`; the lock then takes no key and the next `CREATE TABLE` fails loudly. Not a silent-success path. Left unchanged to keep the reviewed production source stable; recorded here.
2. Database-global `CREATE EXTENSION` under a per-schema key. The plain path resolves the same `current_schema()` for every engine in a store, and the workspace bootstrap installs both extensions under its own key before any namespace exists, so the supported paths serialize. Accept.
3. Test helper duplication (`_create_fresh_database`/`_drop_database`) matches existing test-local helpers; no shared helper exists. Not extracted.
4. `tests/conftest.py`'s per-xdist-worker database comment now describes a production race this change closes. The per-worker isolation is still useful for test isolation; wording left for a follow-up if desired.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets/PII in logs or responses | PASS | No logging added; only `embedding_dim` is logged, pre-existing. |
| Authorization/ownership checks | PASS | No endpoint or capability added. The lock is mutual exclusion, not authorization. |
| SQL injection | PASS | Lock key is a bind parameter; query is `pg_catalog`-qualified. Test database name is `prme_race_` plus a locally generated hex UUID. |
| Command/path/XSS | N/A | No shell, path or HTML input added. |
| Unsafe deserialization | PASS | Unchanged; existing `json.loads` path only compares expected keys. |
| CORS/network binds | N/A | No listener or bind changed. |
| Credentials in code/fixtures | PASS | Test reads `PRME_TEST_DATABASE_URL` and skips without it; `database_url` is a `SecretStr`. |
| Test database safety | PASS | Creates and drops only a generated `prme_race_<hex>` database, never the URL's database. |

No Must Fix or Should Fix security findings. Five Consider items were recorded by the security reviewer; the lock-timeout item is handled as a follow-up below.

## Pattern Consistency Assessment

- The lock key derivation matches `namespace.py:107`, `privacy_export.py:860` and `workspace_catalog.py:22` (bare `hashtextextended(schema, 0)`), which is required for re-entrancy with the namespace and restore paths.
- The transaction scope matches `namespace.py:106-131` and `workspace_catalog.py:21-22`.
- Error and rollback behavior matches the analogues: a transaction rollback releases the lock and undoes partial DDL.
- Test conventions match `test_multi_model_postgres.py` and `test_pg_graph_store.py` (module-level skip on `PRME_TEST_DATABASE_URL`, `pytest.importorskip("asyncpg")`, issue citation).
- The one departure noted by the core reviewer was hand-built identifier quoting; resolved by using `quote_identifier`.

## Redundancy Check

No new production symbols. The test helper names are all used; `created` is required because `gather` yields nothing on failure. The disposable-database helpers duplicate a pattern with no shared equivalent; not extracted.

## Wiring Findings

- `initialize_pg_database` is the single initializer the plain path calls, and the lock is inside it, so every caller is covered.
- No new config, environment value, route, export or migration is added.
- The new test file is picked up by the default `pytest` discovery used in CI.
- No schema change and no `DERIVATION_VERSION` bump: the stored runtime fingerprint, the runtime body and `validate_pg_runtime` are unchanged.

## Data Safety and Read/Write Proof

- No new durable field. The stored schema fingerprint and `prme_runtime_schema` body are byte-identical; no backfill is required.
- No INSERT, UPDATE, DELETE or DROP on existing data is added.
- The advisory lock is released on commit, rollback and cancellation because it is transaction-scoped.
- Lock order is consistent with the workspace catalog and privacy restore paths (registry row lock, then schema lock, then relation locks); no inversion was found.
- The one behavioral difference is atomicity and a longer relation-lock hold on an existing database (see Adjudication).

## Break Scenarios (adversarial) with adjudication

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1+2 | Whole initializer is one transaction, so no-op `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` holds ACCESS EXCLUSIVE on `events`/`nodes` until commit; a fleet restart serializes N initializers | Newly introduced | Medium | Longer startup lock window | Accept | Measured re-initialization of an existing database at about 5.9 ms on PostgreSQL 16.11, so the added hold is small on a healthy store. Alternatives re-checked: a session-level advisory lock would avoid the hold but departs from the namespace-style xact lock the issue requests and moves release correctness onto explicit unlock on a pooled connection; catalog pre-checks for no-op ALTERs need a helper and nine TOCTOU-prone guards and still hold ACCESS EXCLUSIVE during a genuine migration or index build. The residual is recorded in the PR. |
| 3 | A missing HNSW index makes the transaction hold ACCESS EXCLUSIVE on `nodes` through the whole index build, blocking reads for minutes | Newly introduced amplification | Low to Medium | Critical if it happens | Follow-up | Closing it needs the index build outside the initialization transaction or `CREATE INDEX CONCURRENTLY`, a larger change that weakens the fresh-database serialization guarantee. Filed. |
| 4 | `prepare_store` creates the privacy tables before the lock, so protected stores still race on a fresh database | Pre-existing | Medium-Low | Startup fails with the same catalog error | Follow-up | Outside the changed file and the demonstrated path; needs the erasure DDL under the same schema key. The ledger file lock incidentally serializes most opens. Filed. |
| 5 | `CREATE EXTENSION` is database-global while the key is per schema | Pre-existing | Low | One engine fails to start | Accept | The plain path resolves the same `current_schema()` for every engine in a store, and the workspace bootstrap installs both extensions under its own key before any namespace exists. Different-search_path plain engines are not a supported multi-tenant path. |
| 6 | Blocking advisory lock with no timeout: one stalled session blocks all future starts | Newly introduced for the plain path | Low | No new engine can open | Follow-up | A timeout would contradict the acceptance criterion that a second engine waits for the first. A logged wait and an optional operator-configured bound are the safe hardening. Filed. |
| 7 | The test exercises only the plain path, so a future DDL writer could escape the lock unnoticed | Pre-existing detection gap | Medium over time | Regression returns unseen | Follow-up | Folded into the lock-wait/detection follow-up: add a cross-path concurrency case and a lock-key assertion. |

Pre-mortem headline (Agent 8): "PostgreSQL backend outage on deploy: each starting instance's schema bootstrap held ACCESS EXCLUSIVE on nodes and events for the whole DDL transaction, so a three-replica rollout serialized into minutes of blocked reads and pool timeouts." Adjudication reduces this to Accept with a measured 5.9 ms per initializer on an existing database and a Follow-up for the missing-index build case.

## Follow-ups Raised

1. #481: build a missing HNSW index without holding ACCESS EXCLUSIVE on `nodes` for the whole initialization (scenario 3).
2. #482: put the erasure DDL in `prepare_store` under the same schema advisory lock so protected stores also start safely on a fresh database (scenario 4).
3. #483: add a logged advisory-lock wait, an optional operator-configured bound that preserves the wait-for-first semantics, and a cross-path concurrency test (scenarios 6 and 7).

## Resolution Status

| Item | Severity | Status |
|---|---|---|
| CHANGELOG entry | Should Fix | Resolved |
| Test docstring constraint name | Should Fix | Resolved |
| Test cleanup when `close()` raises | Consider (data) | Resolved |
| Identifier quoting in the test | Consider (core) | Resolved |
| Widened relation-lock hold on existing databases | Adversarial Fix now | Accept with the 5.9 ms measurement, recorded in the PR |
| Missing-index build amplification | Adversarial Fix now | Follow-up #481 |
| Privacy DDL before the lock | Adversarial Fix now / Data Should Fix | Follow-up #482 |
| Database-global extension key | Adversarial Follow-up | Accept |
| No lock timeout / detection gap | Adversarial Fix now or Follow-up | Follow-up #483 |
| NULL `current_schema()` guard | Consider (core, security) | Open Consider, no regression |
| per-xdist comment wording | Consider (data) | Open Consider |

Final adversarial tally: Fix now 0, Follow-up 4 scenarios grouped into 3 issues (#481, #482, #483), Accept 2, Dismissed 0. Production source has not changed since the reviewed snapshot; the post-review edits are the CHANGELOG entry and test cleanup and wording. The full suite passed except the known local `.env` derivation test, which passes with `.env` moved aside (2 passed).
