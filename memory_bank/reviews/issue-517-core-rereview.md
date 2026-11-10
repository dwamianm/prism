# Issue #517 Incremental Organizer: Core Re-Review (checklists 1, 3, 4, 5)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot id: 68700cdab0cf41f480e2d7bc753fd9c09be29aa52914d73f2c2bde0a61a74847 (uncommitted work tree)
- Reviewer: core (correctness/logic, code quality and test coverage, pattern consistency/DRY with the full-scope comparison, redundancy)
- Prior report: `memory_bank/reviews/issue-517-core.md`
- No file was edited except this report.

Snapshot caveat: the work tree changed while I reviewed it (mtimes 17:04-17:05; `git diff --stat` went from 869 to 842 insertions). The fixes I verify below are against the on-disk state after those edits. The `created_after` / `created_at_or_before` scaffold was removed from `graph_store.py`, `duckpgq_graph.py`, `pg/graph_store.py` and `read_graph.py` in that interval, and the `_verify_purge` watermark check the data re-review calls F-RR1 was removed from `storage/erasure.py`. Those two are therefore not findings here.

## Assigned checklist

1. Correctness and logic of the incremental path.
3. Code quality and test coverage.
4. Pattern consistency and DRY against the existing full-scope jobs, with a full-configuration comparison.
5. Redundancy and unnecessary code.

All four were addressed. No blocking finding remains in the current tree.

## Round-1 findings: verification

### B1. Protected view with both lower bounds absent: CONFIRMED FIXED

`in_window` is now true when no lower bound is given (`src/prme/privacy/read_graph.py:168-173`), matching the SQL stores' absent window predicate. Verified at the view level by `tests/test_organizer_incremental.py:218-242` and at the SQL level by `:205-215`. The engine only routes scoped protected reads through the view (`src/prme/storage/engine.py:3188-3196`), so the fix covers the case the organizer actually invokes.

### B2 and S1. promote age-union not keyset-bounded / false snapshot justification: CLOSED BY REMOVAL

`INCREMENTAL_JOBS` is now only `deduplicate` (`src/prme/organizer/watermarks.py:24-36`), with the reason documented at `src/prme/organizer/jobs.py:205-207` (promote) and `:780-784` (snapshot_generation). The incremental handlers are gone; no `window=` parameter remains outside `_job_deduplicate` (grep `window` in `jobs.py`). The unused `created_after` alternate lower bound, which only served those jobs, was then removed from all three stores and the protocol (`graph_store.py:271-290`, `duckpgq_graph.py:408-418`, `pg/graph_store.py:497-507`), so no dead scaffold is left.

### B3. An errored page advanced the cursor: CONFIRMED FIXED

The loop now breaks on `stopped or failed` before the `updated_after`/`after_id` advance (`src/prme/organizer/jobs.py:700-702`), and a neighbor-search failure is classified as `failed` rather than `stopped` (`:681-685`), so the run reports `status="error"` instead of `budget_exhausted`. This also closes the reporting inaccuracy I would otherwise have raised (a search failure used to be reported as a budget stop). `_resume` is emitted only from the prefix that was actually searched (`:686-692`, `:723-724`), so a failed page is retried, not skipped.

### Data finding. A node whose vector search raised was counted as examined: CONFIRMED FIXED

`completed` increments only after `index.search` returns, and the loop breaks on the first failure (`src/prme/organizer/deduplication.py:203-223`). The full-scope caller keeps its earlier "skip the node" behavior through `stop_on_search_error=False` (`:96-104`, `:127-135`, `:216-219`), so the refactor does not change the default path. The exact-content budget return reports zero examined nodes (`:165-169`), so a partly processed page cannot move the cursor.

### S3. Coverage: mostly added, with three gaps

Added and verified: multi-page completion (`tests/test_organizer_incremental.py:259-267` asserts `nodes_processed == 5` across three pages, which would fail if the cursor repeated a page), protected view absent bound (`:218-242`), tenant isolation on both backends (`:378-390`, `tests/test_organizer_incremental_pg.py:149-164`), budget stop + resume (`:272-302`), erasure purge (`tests/test_privacy_erasure_retirement.py:55-77`), neighbor-search failure (`:328-346`), projection (`:414-427`). Gaps S-R2, S-R3, S-R4 below.

### Minor findings from round 1

- M1 (RFC said a failed run writes nothing): fixed; the RFC now says a stopped or failed run keeps `completed_through` and records the prefix (`docs/RFC-0015-Self-Organizing-Memory.md:486-493`).
- M2 (CHANGELOG claimed the watermark is not rewritten): fixed; it now says "changes no memory", and the 60-second wording was corrected to "becomes visible within 60 seconds of its stamp" (`CHANGELOG.md:12`).
- M3 (CLI resume message could be false): fixed; printed only when `stopped_on_budget and nodes_processed` (`src/prme/cli.py:619-625`).
- M5 (erasure hardcoded `"u:"`): fixed; the purge delete derives the key from `scope_key` (`src/prme/storage/erasure.py:691-698`), and a test seeds and checks both the owner row and an unscoped resume cursor naming the erased node.
- M7 (`scope_key("")` collides with unscoped): fixed; `""` is the unscoped key and any user id gets `u:` (`src/prme/organizer/watermarks.py:43-49`), tested at `tests/test_organizer_incremental.py:160-164`.
- M11 (full-path budget clock moved): fixed; the clock starts after `generation_search` and before `query_nodes`, as on main (`src/prme/organizer/deduplication.py:87-90`).
- M4 (NULL `updated_at`): mitigated; both cursor sites fall back to `created_at` (`src/prme/organizer/jobs.py:690`, `:703`), matching the SQL `COALESCE`. `MemoryNode.updated_at` is still non-optional, so materialization of a NULL row still fails loudly, as the SQL comment says.
- M6 (`isinstance(resume_updated_at, str)` always true): still present (`src/prme/organizer/jobs.py:112`) and still redundant with `_resume` built as `[isoformat(), str]` (`:724`). Harmless; left as a cleanup candidate.
- M12: `MemoryEngine.scan_changed_nodes(self, **kwargs)` remains untyped (`src/prme/storage/engine.py:3148`). Style only.

## Remaining findings

### S-R1. The "nothing new" 10 000-node tests do not exercise the new 60-second overlap, so criterion 6's bound is conditional

`_run_incremental` now writes `completed_through = ceiling - 60 s` on every completed run (`src/prme/organizer/jobs.py:61`, `:121-126`). Both 10k tests instead write a watermark the code never writes, `completed_through = datetime.now()` (`tests/test_organizer_incremental.py:447-451`, `tests/test_organizer_incremental_pg.py:179-183`), and insert the nodes a day old (`:434`, `:169`). Consequently the window is empty by construction and the tests prove nothing about the overlap tail.

With the shipped semantics, a second run over a scope whose 10 000 nodes were written within 60 s of the previous ceiling re-examines all of them, which cannot finish under one second (`:459`, PG `:189`) and cannot satisfy criterion 3's "finishes quickly". This is the same class of gap as the round-1 S3: the test manufactures a state the code does not produce. Recommendation: either set the watermark through a real first `organize()` run (which also asserts the written value is `ceiling - 60 s`), or document the bound as "no writes in the last 60 seconds" and cap the tail by count. This is a deliberate skip-vs-rework tradeoff, so it needs a decision rather than a silent test.

### S-R2. No regression test reproduces B3's actual skip

The two new failure tests both fail before any page is completed: `test_failed_scan_does_not_advance_the_watermark` raises on the first scan (`tests/test_organizer_incremental.py:307-325`), and `test_failed_neighbor_search_is_reported_and_retried` fails the first vector search, so `completed == 0` and `last_cursor` stays `None` (`:328-346`). In both, no resume cursor is written at all, so the old code (which advanced only its local `updated_after`) would also not skip anything; both tests pass against the pre-fix behavior. To prove `src/prme/organizer/jobs.py:700-702`, set `_INCREMENTAL_PAGE_SIZE = 2`, let page 1 succeed, fail the search on the first node of page 2, then let a third page succeed, and assert the stored cursor sits at the end of page 1.

### S-R3. The budget-stop test is timing-fragile

`test_deduplicate_budget_stop_does_not_advance_past_unsearched_nodes` budgets 60 ms total, 30 ms for the scan, against 20 ms searches (`tests/test_organizer_incremental.py:272-296`). If `generation_search` plus the scan exceeds ~30 ms before phase 2 (a loaded CI runner), `find_duplicates_in_page` returns `completed == 0`, no `last_cursor` is set, and `assert watermark.resume_cursor is not None` at `:296` fails for reason unrelated to the property under test. Widen the margin or make the phase-2 budget deterministic.

### S-R4. The "keyset tie" test has no tie

Every node in `test_window_and_keyset_tie_break` has a distinct `updated_at` (`tests/test_organizer_incremental.py:175-184`); the second query only compares a node with itself at the cursor stamp, so the `id > after_id` branch never selects or excludes a different id at the same stamp. Add two nodes with an identical `updated_at` and page across them, on DuckDB and PostgreSQL. Correctness looks right in SQL (both `ORDER BY ... id ASC` and `id > ?::UUID` are UUID-typed), but it is unproven.

### Minor

- M9 (partial): the full `deduplicate` still reports `nodes_processed = len(duplicates)` in pairs (`src/prme/organizer/jobs.py:579`) while the incremental one reports nodes examined (`:710`), and the docs' "examined and changed counts" (`CHANGELOG.md:12`) do not say the two modes differ.
- M10 is moot now that only one job is incremental; no shared page iterator is needed.
- The RFC's protected-store limitation is accurate and explicit (`docs/RFC-0015-Self-Organizing-Memory.md:511-514`), so S4 is documented follow-up, not fixed.

## Criterion-by-criterion result

| # | Criterion | Result |
|---|---|---|
| 1 | Per-scope, per-job watermark written after the job's writes | Met. Store, schema, key encoding and ordering are correct; B1 is fixed; only `deduplicate` is incremental. |
| 2 | Budget stop records a resumable point; the next run continues | Met for deduplicate. A stop or error keeps `completed_through`, records the searched prefix, and the next run prefers it (`jobs.py:85-120`, `:681-702`). |
| 3 | A second run with nothing new changes nothing and finishes quickly | Met for `nodes_modified == 0` and for scopes with no writes in the last 60 s; the overlap re-examines the recent tail (S-R1). |
| 4 | `organize()` reports examined, changed, finished/stopped per job | Met: `status` and `stopped_on_budget` are set, projected and printed. Full-scope jobs still default to `completed` when they break on budget, which is pre-existing. |
| 5 | Tests prove convergence, resume, incremental == full | Mostly met. Multi-page, protected view, tenancy, erasure, budget stop and equality are covered; the true tie (S-R4) and the B3 regression (S-R2) are not, and the 10k tests dodge the overlap (S-R1). |
| 6 | Under 1 second at 10 000 nodes with nothing new, DuckDB and PostgreSQL | Passes for the committed setup (empty window, unprotected store). It is not measured for the overlap tail (S-R1) or for a protected store (documented limitation, RFC `:511-514`). |
| 7 | Defaults and a single explicit `organize()` call unchanged | Met: option off by default (`src/prme/config.py:630-639`), `run_job` skips the wrapper (`jobs.py:181-184`), no watermark is written, and the full deduplicate clock is back to main's behavior. |

## Unresolved risks

- Concurrent erasure and an incremental run: the purge deletes the owner's watermark row and clears a matching resume id (`src/prme/storage/erasure.py:682-698`), but an in-flight run writes the watermark at the end of the job (`src/prme/organizer/jobs.py:114-120`), so it can re-create the row or a `resume_id` naming a node the purge removed. Only opaque UUIDs are retained, and the data reviewer owns this surface.
- Whether `updated_at` can be NULL in a supported pre-migration pack; the loops now fall back to `created_at`, but `MemoryNode` still rejects a NULL on materialization.
- I did not run any test (the worker owns execution); the PostgreSQL coverage needs `PRME_TEST_DATABASE_URL` and was not exercised here.

## Clean results (one line each)

- Watermark DDL and upsert on both backends, including the PG `_RUNTIME_TABLES` entry and bootstrap, are consistent (`src/prme/storage/schema.py:231-248`, `src/prme/storage/pg/schema.py:27-29`, `:233-245`, `:376-378`).
- DuckDB and PG `scan_changed_nodes` agree with each other and with the protocol: keyset only under a present `updated_after`, `COALESCE(updated_at, created_at)` ordering, UUID-typed tie-break, parameter order and placeholder numbering correct (`duckpgq_graph.py:434-485`, `pg/graph_store.py:497-557`).
- The protected facade mirrors the SQL window semantics, including the ceiling and the exclusive tie-break (`read_graph.py:153-180`).
- `get/set_organizer_watermark` on both backends use the same upsert and clear the resume cursor on a completed write; the DuckDB read normalizes naive timestamps (`duckpgq_graph.py:487-540`, `pg/graph_store.py:559-620`).
- `INCREMENTAL_JOBS` names a job that exists in `ALL_JOBS` and the dispatch table; the other jobs keep their full-scope signatures (`watermarks.py:36`, `jobs.py:163-186`).
- `JobResult`'s new fields are additive and the privacy projection whitelists the status values it republishes (`src/prme/organizer/models.py:17-34`, `src/prme/privacy/diagnostics.py:179-190`).
- The full-scope and incremental dedupe paths share `_find_duplicates_in_nodes`, so the similarity, provenance and admission rules cannot drift (`deduplication.py:127-232`).
- No import cycle was introduced; `watermarks.py` is imported by `jobs.py`, and the stores import `OrganizerWatermark` lazily or under `TYPE_CHECKING` (`graph_store.py:11-14`).
- No caller passes the removed `created_after` parameters; the protocol, both stores and the facade now agree.
- RFC 5.5 and the CHANGELOG describe the reduced job set, the 60-second overlap, the option-off behavior and the protected-store limitation accurately (`docs/RFC-0015-Self-Organizing-Memory.md:469-514`, `CHANGELOG.md:12`).
