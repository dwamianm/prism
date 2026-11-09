# Issue #518 review: organizer job audit

## Review Scope

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Initial reviewed snapshot: ee6ad54bba5b8e9f3aaf04a0322b072eca42acd505361d48e2560b86e3fa6db4
- Final snapshot: ceb37bf177967b398cb7d661c6fa5bda973a8f999dff091e64111323a1e033f7
- Tier: L
- Predicates: NEW_SYMBOLS true, SECURITY true; DATA, INFRA and PERSISTED_FIELD false
- Merged coverage: checklists 3, 4 and 5 in the core report. Checklist 6 is not applicable (no runtime, config, route or deploy wiring). Checklist 7 is not applicable (no schema, key, migration, backfill or new persisted field); the storage writes are the PostgreSQL index rows the change reads and deletes, covered by the core and security reviews and the new tests.
- Excluded path: `.env.bak`, a local gitignored environment file temporarily moved aside for the full suite; not implementation or tests.
- Coordinator input after the reviews (monorepo PR #652): `deduplicate` measured at 20 to 26 seconds on 240 memories with the production embedding model regardless of `budget_ms`. The docs now record the code reason and a 240-node local timing point; the resumable stop is dwamianm/prism#517. No production code changed for this note.
- Reports:
  - `memory_bank/reviews/issue-518-organize-job-safety-core-review.md`
  - `memory_bank/reviews/issue-518-organize-job-safety-security-review.md`
  - `memory_bank/reviews/issue-518-organize-job-safety-adversarial-review.md`

## Files Changed

- `src/prme/organizer/jobs.py`
- `tests/privacy_fakes.py`
- `tests/organizer_job_safety_support.py` (new)
- `tests/test_organizer_job_safety.py` (new)
- `tests/test_organizer_privacy_scoping.py` (new)
- `benchmarks/diagnostics/organizer_timing.py` (new)
- `benchmarks/results/research/2026-11-09/ORGANIZER-JOBS-518.json` (new)
- `docs/ORGANIZER-JOBS.md` (new)
- `docs/INDEX.md`
- `CHANGELOG.md`

## Approach Summary

The audit adds a deterministic store built from the three probe conversations, a scoping check per job on two owners and both backends (including a protected store), a no-loss check per job that keeps every node, event, evidence reference, edge and provenance record reachable, and promotion checks for the explicit, automatic and scheduled request paths. It records per-job PostgreSQL timings at 1,000 and 10,000 nodes and fixes the PostgreSQL `index_compaction` no-op for existing non-active nodes.

## Must Fix

### M1. The fake sanitizer snapshot echo broke fake-based protected extraction (fixed)

`FakeSanitizer` now gates `source_snapshot` behind `echo_snapshot=False`, and only the new protected scoping test turns it on. The fake also computes `private_checksum` with `sanitized_checksum(fields, role, time)` and sets `source_role`, `source_time` and a source-derived `source_id`, matching `LocalSanitizer`. Existing fake-based protected extraction tests keep the snapshot-less ingress path and their evidence-free extractor.

## Should Fix

- Core S1 and adversarial S4 (orphan lexical rows): fixed by dropping both orphan arms. The PostgreSQL compaction now selects only existing non-active nodes with an `embedding` or a `lexical_documents` row; a document row with no node is left alone because the index also writes there before the graph row exists.
- Core S2 and adversarial S1 (lexical claim): the code, docs and CHANGELOG now say the pass clears the stored vector and `lexical_documents` rows, and that a retired node's generated `nodes.content_tsv` remains and is dwamianm/prism#521.
- Core S3 and adversarial S3 (unbounded query): the stale query now appends `ORDER BY node_id LIMIT 500`.
- Core S4 (successor fact): the no-loss check now asserts a summary successor lists the retired node in `source_node_ids` or `selected_source_node_ids`.
- Core S5 and adversarial S5 (false confidence): `graph_snapshot` uses `limit=100000`; `summarize` and `index_compaction` join `EXPECTS_CHANGE`; `prepare_for_job` injects a stale retired node for `index_compaction`; `snapshot_generation` is checked by `nodes_processed`; the privacy test asserts the scoped owner changed.
- Core S6 (lexical half untested): the compaction test inserts a `lexical_documents` row for the archived node and adds an unscoped case.
- Security S1 (orphan wording): fixed with the orphan arm removal; the docstring and docs say what is actually cleared.
- Security C1-C5: index presence is compared for the foreign owner and kept for active nodes; the privacy docstring says "must not change"; the unscoped branch is tested; the fake checksum is aligned; `engine._pool` is used.
- Core C1-C7: extracted `_evict_index_entries`; use `engine._pool`; derive `JOB_NAMES` from `DEFAULT_JOBS`; unique promotion owners; added a scheduled-pass promotion test; the privacy test asserts a first-owner modification.
- Core C8-C10 and adversarial S6: the docs state the configured embedding provider, the per-owner store, the shared-budget pass and the `index_compaction` idle row.
- Adversarial S2: multi-model is dwamianm/prism#520 and the doc no longer describes it backwards.

## Consider

- Security C6: accepted. The index deletes are keyed on uuid4 primary keys that no public API accepts, so tenant isolation holds. A future API that accepts caller-supplied node ids must revisit this.
- Core C11: the markdown table remains in the doc; the harness prints it and writes the JSON, which is enough for the issue's recording requirement.

## Security Audit Results

| Area | Result |
|---|---|
| Tenant isolation in the PostgreSQL compaction | PASS: scoped query binds `user_id`; the orphan arm that could delete a pre-publication row was removed. Residual, non-blocking: the DuckDB index-presence check reads vector metadata only, not the Tantivy lexical index |
| Caller or owner-check bypass | PASS: no new entry point; the REST route binds the scope to the authenticated identity |
| SQL injection | PASS: every value is bound; only generated placeholders are interpolated |
| Protected store and fake sanitizer | PASS: the echo is opt-in and the fake now mirrors the real checksum, role and time; the protected test proves scoping, not production sanitization. Residual, non-blocking: the double omits the real sanitizer source-role allow-list check and real detection |
| Secrets or PII | PASS: the timing JSON holds counters, timings, platform and versions only |
| Error handling | PASS: per-node failures are counted and logged with the fixed event name |

## Pattern Consistency Assessment

The PostgreSQL query placeholders, the `JobResult` keys and the shared eviction helper match the DuckDB path. The test fixtures reuse the existing `config`/`user` fixtures. No duplicated helper remains.

## Redundancy Check

The duplicated eviction loop was extracted to `_evict_index_entries`. No existing PostgreSQL stale-index query exists to reuse.

## Wiring Findings

Not applicable: no runtime configuration, route, CLI, plugin or deploy registration changes.

## Data Safety and Read/Write Proof

Not applicable: no schema, key, migration, backfill or new persisted field. The PostgreSQL branch only evicts derived index rows for nodes the graph already holds in a non-active lifecycle state, and a failed eviction stays selected for retry.

## Break Scenarios (adversarial) and adjudication

| Scenario | Likelihood | Impact | Verdict |
|---|---|---|---|
| S1 Retired PostgreSQL nodes still consume lexical candidate slots | High on PostgreSQL | Silent lexical recall loss as the store ages | Follow-up dwamianm/prism#521; the in-scope claim is corrected. The lifecycle filter is a retrieval change outside this diff and needs the offline evidence gate. |
| S2 Multi-model pass never converges | Medium with the option on; zero by default | Wasted delete per pass; no data loss | Follow-up dwamianm/prism#520; the doc sentence is corrected. |
| S3 Unbounded stale query at 100x | Low at the recorded 10k sizes | Budget miss or no progress at scale | Fixed now: `ORDER BY node_id LIMIT 500`; docs corrected. |
| S4 Orphan purge deletes legitimate pre-publication rows | Low in the current tree | Silent loss of full-text-only documents | Fixed now: the orphan arms are removed. |
| S5 False confidence in the new tests | High as a suite-quality issue | Future regressions would pass | Fixed now for the lexical table, the scoped-owner change, the index presence, the snapshot generation signal and the page limit. |
| S6 Timing page misread as the scheduled pass and eviction path | High | Budget mis-sizing | Fixed now in the docs: shared-budget paragraph, idle `index_compaction` row, 500-row bound. |

Pre-mortem headline: "Scheduled archival on PostgreSQL retired memories but never removed them from BM25, and the job shipped to fix that reported `stale_found: 0` every night." The claims are corrected, and dwamianm/prism#521 tracks the search filter.

## Follow-ups Raised

- dwamianm/prism#517: an existing issue that gives each organize job a per-scope watermark and a resumable stop, including `deduplicate`.

- dwamianm/prism#519: archival organizer jobs only examine the newest 500 active nodes.
- dwamianm/prism#520: `index_compaction` does not reconcile `node_embeddings` with multi-model embeddings.
- dwamianm/prism#521: retired PostgreSQL nodes still consume lexical candidate slots.

## Resolution Status

| Item | Status |
|---|---|
| Core M1 | Fixed |
| Core S1-S6 | Fixed |
| Core C1-C7, C9, C10 | Fixed |
| Core C8 | Partial: the docs name the configured provider; the harness still uses it, which is representative of the hosted store |
| Core C11 | Accepted nit: the table is in the doc and the JSON is written |
| Security S1 | Fixed |
| Security C1-C5 | Fixed |
| Security C6 | Accepted |
| Adversarial S1 | Follow-up #521; claim corrected |
| Adversarial S2 | Follow-up #520; claim corrected |
| Adversarial S3 | Fixed |
| Adversarial S4 | Fixed |
| Adversarial S5 | Fixed, including the post-re-review vacuous nodes_modified sum; the privacy test now sums only RETIRING_JOBS |
| Adversarial S6 | Fixed in the docs |
| Core N1 (new) | Accepted: the unscoped compaction test runs over the shared schema; CI gives each xdist worker its own database and the run is sequential, so this is test hygiene, not a product defect |
| Re-review verification | All three re-reviews complete. The final snapshot ceb37bf1 differs from the reviewed 4000d88c in the privacy-test assertion, the harness docstring and the coordinator-requested 240-node timing row and deduplicate notes, all test/documentation/measurement-only; no production code changed after the re-review. The affected privacy test passes (2 passed) and ruff passes. No blocking defect remains. |
