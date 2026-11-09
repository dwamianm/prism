# Adversarial review: issue #518 organizer job safety

Status: COMPLETE

Snapshot: ee6ad54bba5b8e9f3aaf04a0322b072eca42acd505361d48e2560b86e3fa6db4
Re-reviewed snapshot: 4000d88cf56e54acf934244f1a6767b3f5388568e8fbc5a21a64c3b4662747d7
Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
Branch: issue-518-organize-job-safety (the change is the working-tree diff; HEAD is the base commit)

Tier: L (adversarial). Review covers `src/prme/organizer/jobs.py`,
`tests/privacy_fakes.py`, the three new test/support files,
`benchmarks/diagnostics/organizer_timing.py`, `docs/ORGANIZER-JOBS.md`, the
recorded timing JSON, `CHANGELOG.md` and `docs/INDEX.md`, plus the unchanged
sources those touch: the PostgreSQL lexical/vector indexes, the retrieval
candidate merge, the lifecycle tables, `engine.organize`, and the privacy
sanitizer.

## Bottom line

Not a clean pass. The new PostgreSQL `index_compaction` branch does real work
(it clears `nodes.embedding` and orphaned `lexical_documents` rows), and the
scoping of the new SQL is correct. But it is described, in the code, the
CHANGELOG and the new doc page, as reconciling the PostgreSQL lexical index for
retired nodes, and it cannot do that: on PostgreSQL a node's lexical document
lives in the `nodes.content_tsv` generated column, which this job never
touches, and `PgLexicalIndex.search` does not filter lifecycle state. So the
drift the job exists to repair (a retired node still occupying a lexical
candidate slot) survives every pass, silently. No test asks the PG lexical
search whether a retired node still matches, which is why the suite passes.
There is also a scale hole (the stale query is unbounded and runs before the
budget check) and a multi-model non-convergence the docs state backwards.

The acceptance criteria themselves (timings recorded, scoped runs on both
backends, no-loss checks, promotion both ways) are otherwise met, with the
test-confidence gaps listed in S5. I did not find a default-configuration path
that deletes a live node, evicts a node in a forward-only lifecycle transition,
touches another tenant on a scoped run, or fails to converge after a partial
eviction.

## Checklist results

- **Premise challenge** — FAIL. The audit tests and times the jobs but treats
  the PostgreSQL lexical index as a table of node documents. It is not; node
  content is served from `nodes.content_tsv` (S1). No sibling organizer path has
  another backend-specific skip (`jobs.py:802` is the only `_conn` branch), so
  the sibling angle is clean.
- **Guard loopholes** — PASS for the default configuration. The query filters
  `lifecycle_state NOT IN (tentative, stable, contested)` (`jobs.py:902-915`),
  transitions are forward-only and archived is terminal (`types.py:81-104`),
  and no production caller sets a withdrawn state back to active through
  `update_node` (the only `lifecycle_state` writes are the lifecycle,
  supersedence, derivation and retirement paths). The one hole is multi-model
  mode (S2).
- **Existing data and existing installs** — MEDIUM. First upgrade run performs
  an orphan purge that has never run on PostgreSQL before, over a table whose
  design explicitly holds non-node documents (S4). Pre-existing drift in
  `nodes.content_tsv` is not repaired (S1).
- **State and lifecycle** — PASS. `CONTESTED`/`TENTATIVE`/`STABLE` stay
  active; superseded/deprecated/archived have no inbound transition;
  `ALLOWED_TRANSITIONS[ARCHIVED]` is empty. No "about to be reactivated" node
  can be selected.
- **Idempotency and retries** — PASS single-model, FAIL multi-model. Lexical is
  deleted before the vector is cleared (`index_eviction.py:27-35`), so a
  failed vector clear leaves `nodes.embedding` as the durable retry anchor and
  the node is reselected; a failed lexical delete leaves the node selected by
  its `embedding` or its document. Under `enable_multi_model_embeddings` the
  delete reaches only `node_embeddings`, so an adopted `nodes.embedding` value
  is reselected and re-"modified" on every pass forever (S2).
- **Ordering/timing/consistency** — FINDINGS. The stale query runs in full
  before the budget is first checked (`jobs.py:926-938`); there is no `LIMIT`
  or `ORDER BY`, so which stale nodes are reached before the budget is
  exhausted is plan-dependent (S3). Eviction order across passes is
  nonetheless stable enough to drain a backlog.
- **Scale (100x)** — FINDING. The scoped query is a tenant-wide `nodes` scan
  filtered by lifecycle, and the unscoped arm's `LEFT JOIN nodes n ON
  n.id::text = d.node_id` (`jobs.py:923`) defeats the `nodes` primary key and
  forces a scan of every node per pass; none of that is inside the budget
  (S3).
- **Partial failure** — PASS. Per-node exceptions are counted and logged with
  `exc_info` (`jobs.py:943-949`), the job reports `errors`, and the failed
  node stays selectable; the loop never aborts the whole pass on one failure.
- **Backward compatibility** — PASS with cosmetic caveats. `JobResult` fields
  are unchanged; the DuckDB `details` keys are preserved on PG as literal
  `0`/`False`, so a consumer reading `stage_cleanup_blocked` on PG always sees
  `false` (`jobs.py:958-959`). `project_organize` strips `details` for
  protected callers (`privacy/diagnostics.py:176-188`), so the fabricated
  values are not a protected-contract break.
- **Blast radius and side effects** — PASS on tenant isolation. The scoped arm
  filters `n.user_id = $4` and the orphan arm is skipped when scoped; the
  per-node delete helpers take a node UUID only. A scoped run cannot select or
  evict another owner's rows. The unscoped arm is whole-store by design and is
  the only path that removes orphaned documents.
- **Detection** — FINDING. S1 is silent: retired nodes already have their
  `embedding` cleared at retirement and have no `lexical_documents` row, so
  `stale_found` is 0 and `nodes_modified` is 0 while the BM25 drift grows. The
  timing table's `index_compaction` row is `processed: 0` at both sizes
  (`ORGANIZER-JOBS.md:69,79`), so it measures the idle query, not the eviction
  path (S6). No metric or alert covers PG lexical recall.

## Scenarios

### S1 — A retired PostgreSQL node never leaves lexical search; the job built to evict it cannot (pre-existing gap, newly claimed as fixed)

- **Trigger.** Any PostgreSQL store where `archive`, `decay_sweep`,
  `tombstone_sweep`, `deduplicate`, `alias_resolve`, `consolidate`, a
  supersedence or a derivation retires nodes, and questions still reach
  `retrieve()`. This issue's premise is a scheduled per-scope `organize()` that
  runs exactly those jobs continuously.
- **Mechanism.** `PgLexicalIndex.index` inserts into `lexical_documents` only
  when no `nodes` row exists (`src/prme/storage/pg/lexical_index.py:78-91`, doc
  at `:6-7`); a real node's document is the GENERATED `nodes.content_tsv`
  column. `PgLexicalIndex.search` unions `nodes` and `lexical_documents`
  (`:223-243`) with no lifecycle predicate (`nodes_where` at `:159`), so a
  superseded or archived node is returned from the `nodes` arm. Lifecycle
  eviction calls `PgLexicalIndex.delete_by_node_id`
  (`engine.py:3755` -> `index_eviction.py:27-35` ->
  `pg/lexical_index.py:401-406`), which deletes `lexical_documents` rows that
  do not exist for that node; `nodes.content_tsv` is untouched. The new
  compaction has the same anchor: it selects on `n.embedding IS NOT NULL OR
  EXISTS (lexical_documents ...)` (`jobs.py:905-908`) and then calls
  `_delete_from_indexes` (`jobs.py:941`), never `nodes.content_tsv`. The
  candidate merge later drops the retired row only after it has already
  consumed one of `lexical_k=500` slots
  (`retrieval/candidates.py:573-583`, `retrieval/config.py:656-659`), and
  `get_nodes` resolves active states only (`pg/graph_store.py:239-245`).
  Retrieval therefore loses lexical candidates in proportion to the retired
  fraction of an append-only store.
- **Likelihood.** High on PostgreSQL once scheduled archiving is in use: every
  archived/superseded node is permanently in the `nodes` arm, and the
  compaction job reports a clean pass.
- **Impact.** Silent recall degradation on the lexical path for long-lived
  tenants; rank fusion then leans on graph/vector only. DuckDB does not have
  the problem because the Tantivy document is physically deleted
  (`lexical_index.py:764-780`), so the two backends diverge in retrieval
  quality as a tenant ages.
- **Detection.** Silent. `index_compaction` reports `stale_found: 0` and
  `nodes_modified: 0` because the retired node has no embedding and no
  `lexical_documents` row. No test asserts that a retired node stops matching
  `PgLexicalIndex.search`; `_index_entry_present` (S5) checks only the two
  tables this job cleans.
- **Cheapest mitigation and size.** Small (about 5 lines plus a test): add the
  active-state predicate to the `nodes` arm of `PgLexicalIndex.search` (the
  same predicate `PgVectorIndex.search` already applies at
  `pg/vector_index.py:142`), or filter `resolved_nodes`-eligible candidates in
  SQL. Correct `jobs.py:885-891`, the CHANGELOG line and
  `docs/ORGANIZER-JOBS.md:29,135-145` in the same change unless the filter
  ships.
- **Recommended verdict.** Fix now. It is cheap, it is the behavior the PR,
  CHANGELOG and doc page assert, and it is the one production incident this
  audit makes more likely by institutionalizing scheduled archiving.

### S2 — With `enable_multi_model_embeddings`, the new pass never converges and the doc states the opposite (newly introduced, follow-up #520)

- **Trigger.** A PostgreSQL store with `enable_multi_model_embeddings` on (off
  by default) whose `nodes.embedding` column was adopted when the option was
  first enabled, then a retired node (or any scheduled `organize` with
  `index_compaction`).
- **Mechanism.** The query selects on `n.embedding IS NOT NULL` (`jobs.py:906`).
  Eviction goes through `MultiModelVectorIndex.delete_by_node_id`
  (`multi_model.py:268-275`), which deletes `node_embeddings` rows only; the
  column is cleared solely by the non-multi-model `PgVectorIndex`
  (`pg/vector_index.py:213-233`). The retired node therefore matches the query
  on every subsequent pass, `_delete_from_indexes` succeeds (zero rows removed
  from `node_embeddings` is not an error), and `nodes_modified` increments.
  `docs/ORGANIZER-JOBS.md:159-162` says the added compaction "clears the
  ordinary vector column and the lexical documents, not the other models'
  rows"; under multi-model it is the reverse.
- **Likelihood.** Medium only among stores that use the option; zero for the
  default store. The docs page presents the pass as safe without that
  qualifier.
- **Impact.** One wasted delete round per pass per stale node plus a
  permanently non-zero `stale_found`, i.e. the pass can never reach a clean
  state; no data loss.
- **Detection.** Quiet in production (no error, `nodes_modified > 0` looks
  healthy); only a rising `stale_found` across passes would show it, and there
  is no such monitor.
- **Cheapest mitigation and size.** Small: extend the `indexed` predicate
  with `EXISTS (SELECT 1 FROM node_embeddings ne WHERE ne.node_id = n.id)` and
  route those nodes through a column-clearing delete, or null
  `nodes.embedding` in `_compact_postgres_indexes` when the multi-model index
  is in use; plus correct the doc paragraph.
- **Recommended verdict.** Follow-up, folded into #520, with the doc sentence
  corrected now.

### S3 — The stale query is unbounded and runs before the budget; at 100x it can exceed a pass budget and make zero progress forever (newly introduced for PostgreSQL; the fetch-then-check shape pre-exists on DuckDB)

- **Trigger.** A large tenant (the change's own horizon is 1,000 to 10,000
  nodes; the prompt's scale angle is 100,000 to 1,000,000) where
  `index_compaction` is the ninth or tenth job in `DEFAULT_JOBS`, so
  `organize` hands it the small remainder of a shared `budget_ms`
  (`engine.py:4520-4531`; `prme organize --budget-ms` defaults to 5000,
  `cli.py:1692-1693`).
- **Mechanism.** `conn.fetch(node_query, ...)` (`jobs.py:926-930`) materialises
  every stale id, and the budget is first consulted inside the eviction loop
  (`jobs.py:936-938`). The unscoped arm's `n.id::text = d.node_id`
  (`jobs.py:923`) prevents the `nodes` primary key from being used, so that
  arm is a per-pass sequential scan/hash join over the whole `nodes` table; the
  `EXISTS` subquery (`jobs.py:907`) and `DISTINCT ... UNION` add per-row work.
  If the fetch alone outlasts the remaining budget, `processed` stays 0 and
  the next pass repeats the same fetch: the job reports success, never errors,
  and never drains the backlog.
- **Likelihood.** Low at the recorded 10k sizes (fetch is 2.99 ms); medium at
  100x. It depends on how much budget the earlier jobs leave, which the
  harness deliberately hides by giving each job the full 120 s
  (`organizer_timing.py:94-107`).
- **Impact.** A scheduled pass that misses its budget and delays the
  surrounding request (explicit `organize`) or the maintenance caller, and a
  drift that never clears at scale.
- **Detection.** Loud only if someone compares `duration_ms` to the budget or
  sees `stale_found > 0` with `processed: 0` in the per-job result; otherwise
  quiet, because the job is counted in `jobs_run`.
- **Cheapest mitigation and size.** Small: append `ORDER BY node_id LIMIT
  <bound>` (500, matching the stated per-pass bound) to the stale query, and
  reword `docs/ORGANIZER-JOBS.md:10-13` ("each job reads at most 500 nodes per
  pass ... the processed counts in the timing table show that bound"), which is
  false for this job today.
- **Recommended verdict.** Fix now (the query bound is a few lines); at
  minimum correct the documentation claim.

### S4 — The unscoped orphan purge deletes lexical documents the PostgreSQL design says are legitimate (newly introduced)

- **Trigger.** An unscoped `organize()` (single-tenant CLI use, or the
  documented operator run) on a PostgreSQL store that holds
  `lexical_documents` rows with no matching `nodes` row: the "non-node
  content (events, summaries)" the module documents (`pg/lexical_index.py:6-7`,
  `:45-46`, and the contract test `tests/test_pg_lexical_index.py:66-77`),
  including a document written at ingestion time "which may not have a
  matching node row yet" (`pg/lexical_index.py:65-67`, exercised by
  `tests/test_pg_lexical_index.py:92-104`).
- **Mechanism.** The unscoped arm adds
  `SELECT d.node_id ... LEFT JOIN nodes n ON n.id::text = d.node_id WHERE n.id
  IS NULL` (`jobs.py:921-923`) and then deletes each id through
  `_delete_from_indexes` (`jobs.py:941`). A document indexed just before its
  node is committed is deleted as an orphan and will not be reinserted, because
  `PgLexicalIndex.index` skips insertion once the node exists
  (`pg/lexical_index.py:81-93`). The DuckDB path protects the analogous
  not-yet-published state with `vector_staging` (`jobs.py:826-829`); the PG
  path has no grace period and no staging equivalent.
- **Likelihood.** Low in the current tree: the only production writer of
  `lexical_documents` is `PgLexicalIndex.index`, and every engine caller I
  traced creates the node first (`engine.py:2187-2251`, `:1470-1482`,
  `pipeline.py:1580-1592`), so the insert path is mostly reached by tests and
  by any external caller of the class. The hosted plan runs scoped passes,
  which never take this arm.
- **Impact.** Silent loss of full-text-only documents; `search()` and the
  deprecated `MemoryEngine.search()` (`engine.py:2895-2934`) return fewer
  documents. Ordinary `retrieve()` already drops lexical candidates with no
  graph node (`candidates.py:573-583`), so the user-visible loss is bounded.
- **Detection.** Silent. `nodes_modified` counts these as successful
  evictions, and no test checks that a non-node document survives.
- **Cheapest mitigation and size.** Small: add a grace predicate (for example
  `d.created_at < now() - interval '1 hour'`) to the orphan arm, or leave
  orphans to `prme rebuild` and drop the arm. Document the decision on the
  job.
- **Recommended verdict.** Follow-up with the grace predicate; the current
  behavior is safe enough for the default path but contradicts the module's
  own contract and its test.

### S5 — Several new tests pass while the behavior is wrong (false confidence) (newly introduced)

- **Trigger.** A future change that regresses the PostgreSQL compaction or a
  scoped job, with the suite as the only signal.
- **Mechanism.**
  (a) `_index_entry_present` (`tests/test_organizer_job_safety.py:192-205`)
  checks `nodes.embedding` and `lexical_documents` only; it never asks
  `PgLexicalIndex.search` whether the archived node still matches, which is
  exactly why S1 passes the suite. `nodes.content_tsv` is never inspected.
  (b) `EXPECTS_CHANGE` includes `snapshot_generation`
  (`tests/test_organizer_job_safety.py:41,73-74`), but that job increments
  `nodes_modified` per ephemeral snapshot it generates
  (`organizer/jobs.py:567,571-577`) while its own `details` note says the
  snapshots are read-only views; the assertion "modified > 0" therefore proves
  no mutation.
  (c) `test_one_full_pass_leaves_another_protected_owner_unchanged`
  (`tests/test_organizer_privacy_scoping.py:84-99`) asserts the jobs ran and
  had zero errors, but never asserts the scoped owner's records actually
  changed; a protected pass that changed nothing satisfies every assertion and
  the "changes, merges and links only that user's nodes" criterion vacuously.
  (d) `assert_unchanged` (`tests/organizer_job_safety_support.py:176-185`)
  compares the foreign owner's graph only; a scoped job that evicted another
  owner's vector or lexical rows would not be caught (the new PG SQL is
  correctly scoped, so this is latent).
  (e) `graph_snapshot` calls `query_nodes` with no `limit`
  (`organizer_job_safety_support.py:126-128`), so it compares the default
  100-node page; the probe corpora plus candidates are 23 to 63 nodes today,
  so it silently stops covering a job at 101+ nodes.
- **Likelihood.** High as a suite-quality issue: the tests are the audit's
  evidence, and one of them (a) is the reason the S1 defect is invisible.
- **Impact.** False green for the exact backend path this change adds; future
  regressions in the PG compaction, in protected scoping, or at corpus sizes
  over 100 nodes would not fail.
- **Detection.** Not detectable from the suite itself. The timing JSON
  (`index_compaction.processed: 0`) is the only hint that the eviction path is
  not exercised.
- **Cheapest mitigation and size.** Small: in
  `test_index_compaction_retries_failed_evictions_on_both_backends`, after the
  pass assert that `PgLexicalIndex.search` no longer returns the archived node
  (and keep the table checks); drop `snapshot_generation` from
  `EXPECTS_CHANGE` or assert a specific snapshot field; assert
  `nodes_modified > 0` for the seeded protected owner; pass an explicit large
  `limit` in `graph_snapshot`.
- **Recommended verdict.** Fix now for (a) and (c); the rest as follow-ups.

### S6 — The timing page can be read as measuring the scheduled pass and the eviction path; it measures neither (newly introduced)

- **Trigger.** An operator sizing a hosted per-scope pass or deciding that
  `index_compaction` is cheap from `docs/ORGANIZER-JOBS.md`.
- **Mechanism.** The harness runs each job in its own `organize()` call with
  the full 120 s budget (`organizer_timing.py:94-123`), while a real pass
  shares one budget (`engine.py:4520-4526`; CLI default 5000 ms,
  `cli.py:1692-1693`). At the recorded 10,000 nodes, the per-job numbers add
  to about 17.8 s of work (`benchmarks/results/research/2026-11-09/ORGANIZER-JOBS-518.json`),
  so with the default budget the later registry jobs, including
  `index_compaction`, are starved or partial every pass. The
  `index_compaction` row shows `processed: 0` at both sizes
  (`ORGANIZER-JOBS.md:69,79`): 2.99 ms is the empty query, not the eviction
  path this change adds. Lines 10-13 also claim "each job reads at most 500
  nodes per pass ... the processed counts in the timing table show that
  bound", which is false for `index_compaction` (S3).
- **Likelihood.** High: the page is the deliverable for acceptance criterion 1
  and reads as a readiness sign-off.
- **Impact.** Budget mis-sizing and a false belief that the compaction cost has
  been measured and the scheduled pass covers every job.
- **Detection.** Quiet; the doc is the only place this is recorded.
- **Cheapest mitigation and size.** Small, docs only: add a "one shared-budget
  pass" row/paragraph with a total at 10k and the starvation consequence, mark
  the `index_compaction` row as idle (no stale entries existed), and correct
  the 500-node claim to exclude `index_compaction`.
- **Recommended verdict.** Follow-up (documentation).

## Angles that held (attacked hardest, no finding)

1. **Can the new SQL select active or about-to-be-active data?** No.
   `ACTIVE_LIFECYCLE_STATES` is tentative/stable/contested
   (`types.py:284-289`), the `NOT IN` excludes exactly those, transitions are
   forward-only with archived terminal (`types.py:81-104`), and the only
   `update_node(lifecycle_state=...)` path is the unvalidated generic update
   (`pg/graph_store.py:594-660`) for which I found no production caller. A
   contested node promoted to stable was never selectable; a superseded node
   can only become archived.
2. **Can a scoped run reach another tenant?** No. The scoped arm binds
   `n.user_id = $4` (`jobs.py:909-916`), the orphan arm is skipped when
   `user_id` is set (`jobs.py:917`), and both delete helpers key on a node UUID
   only (`pg/lexical_index.py:401-406`, `pg/vector_index.py:213-233`). The new
   privacy test does not prove this by itself (S5c/d), but the SQL does.
3. **Retry after a partial eviction.** Converges. Lexical is deleted first and
   the vector clear is the anchor (`index_eviction.py:27-35`); a failed lexical
   delete leaves the node selected by `embedding` or by the document, and a
   node with only a document that loses the delete is reselected by the
   `EXISTS` clause. No half-evicted node becomes invisible to the job.
4. **The fake sanitizer change.** Faithful on the two fields it adds:
   `source_snapshot=request.snapshot` matches `sanitizer.py:385`
   (`source_snapshot=content.snapshot`) and `detector_versions=VERSIONS`
   matches `sanitizer.py:388` and the real `validate_source`
   (`sanitizer.py:100-107`). It does not fabricate a pass for this change,
   which never calls the sanitizer. Residual risk: the fake still skips
   `validate_source` and uses `sha256(fields)` instead of `sanitized_checksum`,
   so a future generated-output test could pass where the real sanitizer
   denies a boundary mismatch. Accepted as a test-double limitation.
5. **Crash between graph write and index delete.** The job is the intended
   backstop and now runs on PostgreSQL; the vector half is repaired, the
   lexical half is not (S1).

## Pre-mortem headline

Scheduled archival on PostgreSQL retired memories but never removed them from
BM25, and the job shipped to fix that reported `stale_found: 0` every night.

## Re-review (snapshot 4000d88cf56e54acf934244f1a6767b3f5388568e8fbc5a21a64c3b4662747d7)

Line references in the sections above are to the pre-fix snapshot; this section cites the current tree.

Verdict: no production `Fix now` remains. One test-only `Fix now` is still
open (the protected-owner change assertion), plus two one-line documentation
follow-ups. The acceptance criteria are met.

| Scenario | Now | Verdict |
|---|---|---|
| S1 PG lexical drift | claim corrected, behavior unchanged, #521 opened | Follow-up (#521), not a blocker |
| S2 multi-model | doc corrected, behavior unchanged, #520 | Follow-up (#520) |
| S3 unbounded stale query | `ORDER BY node_id LIMIT 500` added | Resolved; one-line doc leftover |
| S4 orphan purge | orphan arms removed from both branches | Resolved |
| S5 test false confidence | most fixed; one assertion still vacuous | Fix now (test-only, one line) |
| S6 timing page | shared-budget pass and idle row documented | Resolved; one-line doc leftover |

- **S1 — claim resolved, underlying behavior tracked.** `jobs.py:790-797` and
  `:878-884` now say plainly that the generated `nodes.content_tsv` is not
  cleared and that the lexical filter is dwamianm/prism#521;
  `docs/ORGANIZER-JOBS.md:165-170,189-192` and `CHANGELOG.md:12` say the same.
  The behavior is unchanged (`pg/lexical_index.py:159,230-243` still has no
  lifecycle predicate), and no test asserts the tsvector half — correctly so,
  because the job no longer promises it. My earlier `Fix now` was about the
  false claim; with #521 public and the docs accurate, I move this to
  Follow-up. #521 should be prioritized, because the retrieval risk it
  describes grows with every scheduled archival pass.
- **S2 — doc fixed.** `docs/ORGANIZER-JOBS.md:168-170,184-188` now states the
  non-convergence and names #520 (it had it backwards before). The mechanism is
  unchanged (`multi_model.py:268-275`, `pg/vector_index.py:213-233`), so
  #520 remains the right home. No code claim overstates it now.
- **S3 — resolved.** `jobs.py:915-917` appends `ORDER BY node_id LIMIT 500` to
  both branches, so the fetch is bounded to 500 rows and the eviction loop
  stays budget-bounded; the docs' 500-node sentence now excludes
  `index_compaction` (`ORGANIZER-JOBS.md:10-14`) and the shared-budget
  consequence is on the page (`:91-97`). Residual: the harness docstring at
  `benchmarks/diagnostics/organizer_timing.py:10-13` still says "Each job also
  reads at most 500 nodes per pass ... which the report shows as `processed`",
  which is false for `index_compaction` and now contradicts the page; one-line
  doc fix, follow-up. The scan itself is still outside the budget check
  (`jobs.py:919-923`), but with the LIMIT the fetch no longer grows with the
  backlog, so the zero-progress livelock I described is materially reduced.
- **S4 — resolved.** Both branches now select only existing nodes
  (`jobs.py:905-917`); the orphan `UNION` arm is gone, so a
  `lexical_documents` row with no node is left alone, as the design intends
  (`pg/lexical_index.py:78-91`). The module docstring (`jobs.py:786-797`) and
  the page (`ORGANIZER-JOBS.md:155-163`) explain the choice. This matches
  `test_index_non_node_content` (`tests/test_pg_lexical_index.py:66-77`).
- **S5 — mostly resolved; one assertion still vacuous (Fix now, test-only).**
  Fixed: `graph_snapshot` passes `limit=100000` and records index presence
  (`support:192-205`); `assert_unchanged` compares index presence
  (`support:260-269`); `assert_no_loss` keeps an active node's index entry
  (`support:227-233`) and checks a SUMMARY successor's source ids
  (`support:247-254`); `snapshot_generation` is checked by `nodes_processed`
  (`test_organizer_job_safety.py:64-67`); `EXPECTS_CHANGE` is
  `RETIRING_JOBS | {promote, summarize, index_compaction}` (`:31-32`); the
  compaction test inserts a `lexical_documents` row and covers scoped and
  unscoped (`test:172-223`, `support:116-143`); the promotion tests use unique
  owners and add the scheduled request path (`test:117-129`). Remaining Fix
  now: `tests/test_organizer_privacy_scoping.py:95-100` sums `nodes_modified`
  over every default job, and `snapshot_generation` alone reports
  `nodes_modified` per generated ephemeral snapshot (`jobs.py:563-574`) while
  its own details say "read-only views". The seeded owners have two ENTITY
  nodes each, so the sum is positive even if every mutating job changed
  nothing, which defeats the purpose of the assertion. Sum only
  `RETIRING_JOBS` (or `promote`/`summarize`) or compare the first owner's
  `graph_snapshot` before and after; one line.
  Minor, follow-up (test stability): the unscoped compaction case
  (`test_organizer_job_safety.py:209-223`) runs against the shared
  `PRME_TEST_DATABASE_URL` schema, and the new `LIMIT 500 ORDER BY node_id`
  (`jobs.py:917`) picks stale entries across all owners by UUID. After many
  local runs with more than 500 stale entries left by other tests, the target
  node can fall outside the window; a fresh CI database makes it negligible. A
  bounded retry loop or a per-test schema removes the flake.
- **S6 — resolved at page level; one stale copy.** `ORGANIZER-JOBS.md:91-97`
  now records the shared-budget pass, the ~17.8 s total at 10,000 nodes, the
  5,000 ms CLI default and the idle `index_compaction` row, and `:10-14`
  excludes it from the 500-node claim. The harness docstring
  (`organizer_timing.py:10-13`) is the only remaining copy of the old claim;
  fold it into the same one-line docs follow-up as S3.

New issues introduced by the fix: the privacy-test assertion above (S5
remaining, Fix now, test-only) and the harness docstring (S3/S6, follow-up,
one line). Nothing else. The `FakeSanitizer` is now more faithful, not less:
the snapshot echo is opt-in (`privacy_fakes.py:110-118`), the checksum uses
the real `sanitized_checksum` with `source_role`/`source_time`, and
`source_id` is copied from the source (`:127-139`), matching
`sanitizer.py:100-107,383-391`; the `echo_snapshot=False` default preserves
the historical snapshot-less ingress path for every other fake-based test. The
`_evict_index_entries` extraction (`jobs.py:941-966`) is behavior-preserving.
`engine._pool` is set for the PostgreSQL backend (`engine.py:438`), so the new
pool lookup is well founded and the graph-store fallback remains.

Final: the change holds up under the issue's acceptance criteria. The one item
I would still change before merge is the privacy-test assertion, because it is
the only evidence that the scoped protected pass changed its own owner and it
is currently satisfied by a read-only job's counter. The two remaining
production gaps (PostgreSQL lexical lifecycle filtering, multi-model
convergence) are correctly documented and tracked as dwamianm/prism#521 and
#520.
