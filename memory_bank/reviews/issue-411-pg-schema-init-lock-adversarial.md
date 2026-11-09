Status: COMPLETE
Checklist: 8
Snapshot: d320b495b116bb290974d7b1c3aee2bed1d1dd93d5df3857abfef9663635e9b8

# Adversarial review — issue #411 (PostgreSQL schema init lock)

## Premise and entry points

The change assumes that the only unsynchronized mutators of a PRME schema are concurrent
`initialize_pg_database` calls on the same `current_schema()`. Falsified in two directions: the
initializer is not the only DDL writer on a fresh database (`prepare_store`), and the lock is keyed
by a per-schema name while one of its statements (`CREATE EXTENSION`) is database-global.

Everything that reads or writes schema state:

| Entry point | Guard now | Note |
|---|---|---|
| `MemoryEngine._create_postgres` plain path (engine.py:936-947) | yes, new | default PG deployment; `prepare_store` runs before it, unguarded (scenario 4) |
| `prepare_namespace` (namespace.py:103-132) | yes, already at :107 | inner call nests as SAVEPOINT on the same key |
| `_restore_database` (privacy_export.py:596-599) and `_restore_postgres` (:766-768) | yes, same key | nested in the restore's own transaction |
| `_restore_bound_postgres` (:858-901) | yes, already at :860 | same key, same order (registry row lock then schema lock) |
| `WorkspaceCatalog.initialize` (workspace_catalog.py:17-48) | different key (`prme_workspace_catalog_v1`) | installs the same database-global extensions (scenario 5) |
| `validate_pg_runtime` (schema.py:70-122) | none, read-only | improved: DDL is now atomic to its snapshot |
| `prepare_store` erasure DDL (erasure.py:212-220) | none | scenario 4 |
| `model_vector_index` (session `pg_advisory_lock`, :41) | separate key | no shared object with the initializer |

Backward compatibility is clean: every in-transaction caller uses asyncpg's `conn.transaction()`
(or a `_ConnectionPool` over such a connection), so the new `conn.transaction()` at schema.py:276
becomes a savepoint, not the `InterfaceError` a manual `BEGIN` would raise. Sequential double
calls (tests/test_pg_schema_catalog.py:32-33) re-take the lock uncontended. `prme compact`,
`prme rebuild`, DuckDB paths and the CLI do not call this function.

## Guard loopholes (short)

The guard is a guard only for sessions that both take it. `current_schema()` is the correct key for
unqualified DDL (both resolve to the first *existing* schema in `search_path`), and the namespace,
restore and plain paths all compute the same text for the same schema, so I found no silent
key mismatch that lets two initializers of one schema proceed. The holes are elsewhere:
`CREATE EXTENSION` is database-global while the key is per schema; `prepare_store` never takes it;
and if `current_schema()` is NULL (`search_path` names no existing schema) the lock is a no-op, but
the very next `CREATE TABLE` fails with "no schema has been selected to create in", so that case is
loud rather than exploitable. A rolling deploy where old (unlocked) code is still running is also
outside the guard, for the duration of the rollout only.

## Scenarios, ranked by likelihood x impact

### 1. Every startup on a live store now holds ACCESS EXCLUSIVE on the core tables for the whole initializer — newly introduced

**Trigger.** Any second engine opens against an existing populated store while it is serving (a
second app instance, a supervisor restart while another replica serves, a `prme` CLI command that
constructs an engine). No upgrade or schema change is required.

**Mechanism.** schema.py:276 wraps the entire body in `conn.transaction()`. PostgreSQL acquires the
lock level a command requires *before* it discovers the statement is a no-op, and `ALTER TABLE ...
ADD COLUMN IF NOT EXISTS` requires ACCESS EXCLUSIVE. The initializer issues seven of those on
events/nodes/event_extractions (schema.py:307, 325, 328, 329, 332, 333, 341) plus two on
`profile_work` (profile_registry.py:219, 225). Previously each statement autocommitted and released
its lock immediately; now all of them are held to COMMIT, through the remaining DDL, the
`derivation_registry`/`profile_registry` recovery loops (derivation_registry.py:87-101,
profile_registry.py:215-246) and `_schema_fingerprint` (schema.py:62-67). While any one of those is
slow, or while one `ALTER TABLE` is queued behind a long-running reader on `nodes`, every SELECT on
nodes/events and every INSERT is blocked. Lock acquisition order is fixed (events first), so a wait
on `nodes` is served while still holding AX on `events`.

**Likelihood.** High. Every process start on an existing store; the stall is short (tens of ms)
when nothing is slow, but the trigger condition is a single concurrent long query, which a
reporting readout or a slow retrieval is enough to produce.

**Impact.** Read and write outage on the two hottest tables for the initializer's duration; with a
bounded pool (max_size 10 by default), queued requests turn into pool timeouts and a
cascade across the process.

**Detection.** Quiet. No error, no metric, no log until schema.py:381; only p99 latency and
occasional statement timeouts.

**Cheapest mitigation.** Before each `ALTER TABLE ... IF NOT EXISTS`, check `pg_attribute` /
`pg_indexes` and skip the statement when the object already exists, so a healthy store never
takes ACCESS EXCLUSIVE; keep the transaction for the first-time path. Contained (one helper plus
nine call sites), no schema change.

**Verdict.** Fix now.

### 2. A fleet or rolling restart serializes N initializers into N x the lock window — newly introduced

**Trigger.** Several instances of the same PG store start together (k8s rollout with replicas > 1,
a host reboot bringing several workers back, a scaling event).

**Mechanism.** schema.py:278-281 makes startup mutually exclusive per schema. Each winner runs the
full DDL under the transaction from scenario 1 and the losers queue holding a checked-out
connection. The tables are therefore under ACCESS EXCLUSIVE for the sum of the N initializers, not
for one. Nothing bounds the wait (no `lock_timeout`; the only `SET LOCAL lock_timeout` in the
codebase is privacy_export.py:848, a different path).

**Likelihood.** Medium. Requires a multi-instance deployment against one database, which is exactly
what the PostgreSQL backend is for.

**Impact.** A deploy window with retrieval and ingestion unavailable for seconds to minutes,
reported as "the service dies on deploy".

**Detection.** Loud at the symptom (startup probes time out, requests fail), misleading as to cause.

**Cheapest mitigation.** Same catalog pre-check as scenario 1 (removes most of the window), plus
`SET LOCAL lock_timeout` with a bounded retry around the advisory-lock acquisition so a loser fails
fast instead of stacking. Contained.

**Verdict.** Fix now (same edit as 1).

### 3. A store whose HNSW index is missing turns the upgrade into a full `nodes` read outage — newly introduced amplification

**Trigger.** An existing store with rows in `nodes` but without `idx_nodes_embedding_hnsw`
(populated before the index existed, an index dropped by an operator, or a store loaded from a dump
before the initializer ran), opened while serving.

**Mechanism.** schema.py:340-347: the no-op `ALTER TABLE nodes ADD COLUMN IF NOT EXISTS embedding`
takes ACCESS EXCLUSIVE and, because of the new transaction, keeps it while
`CREATE INDEX ... USING hnsw` scans and builds over every node. Before this change the ALTER
committed first, so the build held only ShareLock and readers were allowed. `CREATE INDEX IF NOT
EXISTS` will not rebuild an invalid index either, so a cancelled build is not repaired, it is
skipped (pre-existing behaviour, schema.py:56).

**Likelihood.** Low to medium (precondition is uncommon but not controlled by the code).

**Impact.** Critical: all `nodes` reads blocked for the whole build, minutes on a large store;
vector and graph retrieval fail, the pool exhausts.

**Detection.** Loud at the symptom, silent at the cause (the only signal is a gap before
schema.py:381).

**Cheapest mitigation.** The catalog pre-check from scenario 1 already removes the ALTER's lock;
moving the index build after the transaction commits would additionally restore the old lock
level. Contained.

**Verdict.** Follow-up (covered by the same edit as 1 if taken).

### 4. The erasure DDL still races outside the new lock, so acceptance criterion 1 is not met for protected stores — pre-existing race, guard does not cover it

**Trigger.** Two or more engines with a `privacy_runtime` open one fresh database at the same time.

**Mechanism.** engine.py:944 calls `prepare_store` *before* `initialize_pg_database` at :947. With a
runtime bound, erasure.py:217-220 executes `CREATE TABLE IF NOT EXISTS privacy_store_state` and
five siblings in its own transaction on a separate pool connection, taking no advisory lock. That
is the same catalog race (`pg_type_typname_nsp_index`) the ticket fixes, at the same moment in
startup. `preflight_format` (engine.py:939) only returns `protected_format = runtime is not None`
for an empty schema and creates nothing, so the first DDL on the fresh database is the unguarded
one. The new test uses no privacy runtime, so it cannot see this.

**Likelihood.** Medium-low. The privacy runtime is opt-in, but the scenario is exactly the ticket's
stated case.

**Impact.** Engine startup fails with a duplicate-key error on the type catalog.

**Detection.** Loud, but attributed to the wrong subsystem.

**Cheapest mitigation.** Move the erasure DDL under the same schema key (take the advisory lock in
`prepare_store`'s DDL transaction), or run `initialize_pg_database` before `prepare_store`. Contained.

**Verdict.** Fix now (it is the acceptance criterion, not a new feature).

### 5. `CREATE EXTENSION` is database-global but the key is per schema — pre-existing, guard gap

**Trigger.** A fresh database where pgvector/pgcrypto are not yet installed, and two initializers
resolve different schemas at the same time: a plain engine (`public`) plus a workspace bootstrap
(`prme_workspace_catalog_v1`, workspace_catalog.py:46-48), or two roles whose `search_path` begins
with different existing schemas.

**Mechanism.** schema.py:283-284 installs into the session's current schema but checks and creates
a database-global `pg_extension` row. `CREATE EXTENSION IF NOT EXISTS` is not concurrency-safe when
the extension is absent; the second session fails on the `pg_extension` unique index. Different
`current_schema()` values mean different keys, so the new lock does not serialize them.

**Likelihood.** Low (single-role deployments all resolve `public` and are serialized).

**Impact.** One engine fails to start; the error names pg_extension, not the lock.

**Detection.** Loud, confusing.

**Cheapest mitigation.** Install extensions once under a fixed database-wide key (as
`WorkspaceCatalog.initialize` already does) and make the plain path adopt that key. Contained,
touches one contract (who bootstraps extensions).

**Verdict.** Follow-up.

### 6. No lock timeout and no try-lock: one stalled session blocks every future startup of that schema — newly introduced for the plain path

**Trigger.** A session acquires the schema lock and then stalls before COMMIT: a process stopped in
a debugger or by a supervisor, a partitioned client the server has not yet reaped
(`idle_in_transaction_session_timeout` defaults to 0), or an extremely long DDL step behind a
blocked lock (scenario 1).

**Mechanism.** schema.py:278-281 uses blocking `pg_advisory_xact_lock` with no deadline. Before
this change nothing serialized plain-path startup, so a stuck session could not block anyone else's;
the namespace path already had this property at namespace.py:107.

**Likelihood.** Low (the window is the initializer's duration), but the consequence of the failure
is total for new starts.

**Impact.** No new engine can open the store; existing serving continues. Recovery needs an operator
to find and terminate the backend.

**Detection.** Silent. The process simply never logs schema.py:381; there is no "waiting for schema
lock" line.

**Cheapest mitigation.** `SET LOCAL lock_timeout` plus a bounded retry (or `pg_try_advisory_xact_lock`
in a deadline loop) and one log line on entry. One-liner plus a small loop.

**Verdict.** Fix now (cheap) or Follow-up.

### 7. The only detector exercises one call site on one schema, so a silent bypass ships unnoticed — pre-existing detection gap

**Trigger.** The guard is defeated without an error: `current_schema()` is NULL (lock no-ops), a
caller runs with a `search_path` that resolves differently, an old unlocked process participates in
a rolling deploy, or a future DDL writer (like `prepare_store` today) forgets the lock.

**Mechanism.** tests/test_pg_schema_init_race.py:63-99 synchronizes `prme.storage.pg.initialize_pg_database`
only, which is the plain path's import site (engine.py:923-930); `prme.storage.pg.schema.initialize_pg_database`
used by namespace.py:16 and privacy_export.py is the same function object but a different module
attribute, and the test configures one pool per engine on one schema with no privacy runtime. The
test is skipped without `PRME_TEST_DATABASE_URL`, which is the documented local `uv run pytest -q`
path; only CI covers it. No test asserts that a namespace open and a plain open, or two different
pool configurations, actually serialize.

**Likelihood.** Medium that a future mutator escapes the lock; the guard's absence would then be
invisible until a production start fails.

**Impact.** The original incident returns with no regression test catching it.

**Detection.** Silent until production.

**Cheapest mitigation.** Add a second case to the race test that opens a namespace (`initialize=True`)
and a plain engine against one server concurrently, and a unit assertion that the initializer's key
equals `hashtextextended(current_schema(), 0)`. Contained.

**Verdict.** Follow-up.

## Pre-mortem headline

"PostgreSQL backend outage on deploy: each starting instance's schema bootstrap held ACCESS
EXCLUSIVE on nodes and events for the whole DDL transaction, so a three-replica rollout serialized
into minutes of blocked reads and pool timeouts."
