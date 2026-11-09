# Security review: issue #518 (organizer job audit)

Status: COMPLETE
Review snapshot: ee6ad54bba5b8e9f3aaf04a0322b072eca42acd505361d48e2560b86e3fa6db4
Re-review snapshot: 4000d88cf56e54acf934244f1a6767b3f5388568e8fbc5a21a64c3b4662747d7
Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
Branch: issue-518-organize-job-safety
Reviewer: Agent 2 (security)

Checklist: tenant isolation in `_compact_postgres_indexes`; SQL injection; protected
privacy store and the `privacy_fakes` change; secrets/PII in logs, timing output,
docs and fixtures; error messages and swallowed exceptions; authorization,
ownership or privacy checks the new query and tests rely on but do not verify.

## Result table (first review)

| Area | Result | Details |
|---|---|---|
| Tenant isolation in `_compact_postgres_indexes` | PASS | Scoped branch binds `user_id` and filters `n.user_id = $4` (`src/prme/organizer/jobs.py:909-916`). Node ids come from `nodes.id` (UUID primary key, `src/prme/storage/pg/schema.py:156`) and are uuid4-generated, not supplied by the public store/ingest APIs (`MemoryObject.id` default `uuid4`, `src/prme/models/base.py:23`; `store()` takes no id, `src/prme/storage/engine.py:1117-1139`; a materialized note reuses its uuid4 event id, `src/prme/storage/engine.py:2200-2201`), so the subsequent id-only deletes (`src/prme/storage/pg/lexical_index.py:401-406`, `src/prme/storage/pg/vector_index.py:213-238`) cannot reach another owner's row. Unscoped orphan collection (`src/prme/organizer/jobs.py:917-924`) leaves any node row, so it cannot clear an active owner's vector. |
| Caller / owner-check bypass | PASS | No new entry point. `engine.organize(user_id=...)` is an operator/scope parameter as before; the REST route binds it to the authenticated identity and rejects a mismatch (`src/prme/api/routes.py:153-161`, `src/prme/api/routes.py:566-586`). The new helper only reads the caller-supplied scope. |
| SQL injection | PASS | Every value is bound. The only interpolated fragments are `$n` placeholder strings and the fixed `indexed` predicate (`src/prme/organizer/jobs.py:902-924`); `active` values come from the `ACTIVE_LIFECYCLE_STATES` enum (`src/prme/types.py:284-288`), never from input. `user_id` is a bound parameter (`jobs.py:910-911`). |
| Protected privacy store / `privacy_fakes` change | PASS | The fake now mirrors the real sanitizer (`source_snapshot=content.snapshot`, real detector `VERSIONS`), `tests/privacy_fakes.py:124-133`; the real code does the same (`src/prme/privacy/sanitizer.py:386-391`). `organize()` runs under `privacy_disclosure` and `protected_diagnostics` (`src/prme/privacy/admission.py:104-129`), so the module `ScopedLogger` (`src/prme/privacy/diagnostics.py:111-151`) redacts the new warning and its `node_id` in a protected scope. The query reads only `id`, `user_id`, `lifecycle_state`, embedding presence and `lexical_documents.node_id`, never content. No production boundary is weakened: the echo is stricter than the previous `None` (it enables `validate_source` on reload, `src/prme/privacy/admission.py:491-493`), and the fake does no redaction, so the new test proves scoping only, which the file already states (`tests/privacy_fakes.py:1`). |
| Secrets/PII in logs, timing JSON, docs, fixtures | PASS | `benchmarks/results/research/2026-11-09/ORGANIZER-JOBS-518.json` contains only counters, timings, `platform`, Python and the PostgreSQL version string; no owner ids, UUIDs, node ids or source text (the owner is generated at `benchmarks/diagnostics/organizer_timing.py:138` and never written; `seed()`'s return value is discarded). `docs/ORGANIZER-JOBS.md` records a local `127.0.0.1:55432` temporary server and machine model only. New fixtures use synthetic text. No credentials in any new file. `.env.bak` is an untracked transport artifact and must not be committed. |
| Error messages / swallowed exceptions | PASS | The per-node failure path logs a fixed event name with `node_id` and `exc_info` and counts the error (`jobs.py:940-949`), matching the DuckDB path (`jobs.py:855-864`); under protection the scoped logger emits only `privacy_operation_event`. The pool-missing branch returns a typed `note` instead of raising (`jobs.py:893-900`) and cannot fire for a real PostgreSQL engine (`PgGraphStore` always has `_pool`, `src/prme/storage/pg/graph_store.py:82`). Tests assert `errors == 0` and `nodes_modified > 0` rather than tolerating partial work (`tests/test_organizer_job_safety.py:55-56, 70-74`; `tests/test_organizer_privacy_scoping.py:88-91`). |
| Checks relied on but not verified | FAIL (coverage) | See Should Fix S1 and Consider C1-C4. The tests did not compare the foreign owner's index entries, never exercised the unscoped orphan branch, and one new docstring made a false ownership claim about orphaned rows. Resolved in the re-review below. |

## Must Fix (first review)

Nothing found. The new query is tenant-safe, injection-safe, and does not weaken
a protected boundary or expose protected text.

## Should Fix (first review)

S1. `src/prme/organizer/jobs.py:885-891` and `docs/ORGANIZER-JOBS.md:140-141`
claim that orphaned lexical rows "no longer name an owner". On PostgreSQL that is
false: `lexical_documents.user_id` is `VARCHAR NOT NULL`
(`src/prme/storage/pg/schema.py:233-245`), and the row was written with the
owning `user_id` (`src/prme/storage/pg/lexical_index.py:90-98`). The DuckDB
reasoning was carried over even though `vector_metadata.user_id` is also
`NOT NULL` (`src/prme/storage/vector_index.py:123-131`). Impact: in the hosted
per-scope deployment described in `docs/ORGANIZER-JOBS.md:5` ("one `organize()`
pass for each scope"), no unscoped run is planned, so a scoped owner's orphaned
lexical rows (crash drift, for example) are never collected. Smallest fix:
either reword both texts to say the rows do carry `user_id` and are deliberately
left for a future change, or add a scoped orphan branch keyed on the existing
column:
`SELECT DISTINCT d.node_id::text AS node_id FROM lexical_documents d WHERE d.user_id = $<owner> AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id::text = d.node_id)`.
Deleting by `node_id` after that filter is still owner-safe.

## Consider (first review)

C1. The scoping tests compare nodes, edges and events only
(`tests/organizer_job_safety_support.py:124-136, 176-185`;
`tests/test_organizer_privacy_scoping.py:76-99`). They do not compare the foreign
owner's `nodes.embedding` or `lexical_documents` rows, so a regression that
cleared another owner's vector or lexical entry would still pass these tests. The
property they claim covers the exact behavior of the new query. Smallest fix:
extend `graph_snapshot`/`assert_unchanged` with per-node index presence using the
existing `_index_entry_present` query (`tests/test_organizer_job_safety.py:192-205`).

C2. `tests/test_organizer_privacy_scoping.py:1-6` says the pass "must not read"
anything the second owner holds, but only changes are asserted; a job that reads
another owner's data without writing it would pass. Either reword to "must not
change" or state reads as a known limitation.

C3. The new unscoped PostgreSQL branch (`src/prme/organizer/jobs.py:917-924`) is
not exercised by any test: the retry test, the scoping tests and the timing
harness all pass `user_id` (`tests/test_organizer_job_safety.py:184-186`,
`benchmarks/diagnostics/organizer_timing.py:105-107`). It is the branch that
deletes rows with no node, so it deserves a test with an orphaned lexical row and
a live node that must survive.

C4. Test fidelity of the fake: `tests/privacy_fakes.py:127` still uses a plain
`sha256(fields)` for `private_checksum`, while the real checksum is
`sanitized_checksum` (`src/prme/privacy/sanitizer.py:38-42, 99-115`). Now that
`source_snapshot` is set, any path that reloads a fake-admitted source through
`AdmissionCoordinator.load_source` will run `validate_source` and refuse it as
`privacy_invalid_result` (`src/prme/privacy/admission.py:473-496`), where the
check was previously skipped. The delivered evidence covers only the two new
organizer test modules, so run the privacy suite (or at least
`tests/test_privacy_processing_work.py`, `tests/test_privacy_restore.py`,
`tests/test_privacy_ciphertext_storage.py`) before merge; if a path breaks, align
the fake's checksum and `source_role`/`source_time` with `sanitized_checksum`
rather than dropping the snapshot echo. This is a test-double gap, not a
production weakness.

C5. `src/prme/organizer/jobs.py:893` reaches into
`engine._graph_store._pool` while the engine already exposes the same pool as
`engine._pool` (`src/prme/storage/engine.py:438`). Not a security issue; using the
engine attribute would avoid coupling to the graph store's internals.

C6. Defense in depth: the id-only deletes have no owner predicate
(`src/prme/storage/pg/lexical_index.py:401-406`,
`src/prme/storage/pg/vector_index.py:213-238`). Isolation rests on the UUID
primary key and the scoped query. If node ids ever become caller-supplied or
content-derived, this becomes a cross-tenant delete. Passing `user_id` down when
the caller is scoped would make the invariant local.

## Re-review (snapshot 4000d88c)

Re-read the current working tree at the same paths. No new blocking security
defect found.

- **S1: closed.** The PostgreSQL compaction no longer has an orphan
  `lexical_documents` arm in either branch: the query selects only existing
  non-active nodes (`src/prme/organizer/jobs.py:895-917`). The ownership claim is
  gone; the docstring and docs now say a node-less lexical row is left alone
  because the PostgreSQL lexical index writes there before the graph row exists
  (`src/prme/organizer/jobs.py:790-797, 878-884`;
  `docs/ORGANIZER-JOBS.md:159-161`). The alternative (filter orphans by the
  existing `d.user_id`) was not taken; that is a retention follow-up, not a
  boundary issue, and it is not a regression because the old PostgreSQL branch
  collected nothing at all. Erasure still removes those rows
  (`src/prme/storage/erasure.py:666-667`).
- **C1: closed.** `graph_snapshot` now records per-node index presence
  (`tests/organizer_job_safety_support.py:162-189, 204`), `assert_unchanged`
  compares it (`:265`), `assert_no_loss` keeps an active node's entry (`:227-233`),
  and `add_stale_retired_node` gives the compaction job a real stale entry
  (`:116-143`). Residual, non-blocking: on DuckDB the presence check reads only
  `vector_metadata` (`:166-173`), not the Tantivy lexical index, so the DuckDB
  lexical side of isolation is still not directly asserted.
- **C2: closed.** The privacy test docstring now says "must not change, merge or
  link" (`tests/test_organizer_privacy_scoping.py:1-6`).
- **C3: closed.** The unscoped branch is exercised
  (`tests/test_organizer_job_safety.py:209-223`), and a scoped and unscoped case
  both run on both backends. Residual, non-blocking: that unscoped call runs over
  the shared test schema and can evict other owners' stale entries in the test
  database, which is test hygiene rather than a product boundary.
- **C4: closed.** The fake now uses `sanitized_checksum` with
  `source_role`/`source_time`, derives `source_id` from the source and reports
  the real `VERSIONS` (`tests/privacy_fakes.py:128-139`). The snapshot echo is an
  explicit opt-in that defaults off (`:111, 118, 138`), so existing fake-based
  tests keep their previous snapshot-less path; the privacy test turns it on
  (`tests/test_organizer_privacy_scoping.py:57-59`). Verified statically that the
  fake's construction now matches `LocalSanitizer`
  (`src/prme/privacy/sanitizer.py:385-392`) and passes `validate_source`.
  Residual, non-blocking: the double still omits the real sanitizer's
  `source_role` allow-list check (`src/prme/privacy/sanitizer.py:161`) and any
  real detection; `tests/privacy_fakes.py:1` labels it as a component double, so
  the new test proves scoping only.
- **C5: closed.** The helper prefers the engine pool and keeps the graph-store
  pool only as a fallback (`src/prme/organizer/jobs.py:886`).
- **C6: accepted, as reported.** Node ids are uuid4 primary keys
  (`src/prme/storage/pg/schema.py:156`, `src/prme/models/base.py:23`) and are not
  supplied by the public store/ingest APIs, so the id-only deletes remain
  owner-safe. The defense-in-depth suggestion was optional and no longer needs
  action for this change.
- **Evidence note.** The three restore-test failures reported by the fixer are an
  environment artifact, not code: `test_privacy_restore.py:70` calls
  `unquote(parts.password)` and the temporary server URL has no password, so
  `parts.password` is `None`. Verified statically; unrelated to this diff.
- Re-checked the re-review's own surface for secrets: the timing JSON is
  unchanged and still has no owner ids, node ids or source text; the new docs and
  tests contain only a placeholder `postgresql://user@127.0.0.1:55432/prme_test`
  and synthetic content.

## Notes

- `.env.bak` at the repository root is the excluded transport artifact used by
  the full suite; it is gitignored and untracked. Keep it out of the commit.
- No authorization, ownership or privacy invariant in the reviewed diff depends
  on an unverified assumption beyond those listed above.
