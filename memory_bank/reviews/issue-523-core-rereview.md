# Re-review: issue-523 alias_resolve incremental (core: correctness, quality/test coverage, pattern consistency + full-config, redundancy)

Status: COMPLETE

- Base: origin/main at 57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7 (PR #526 merged)
- Reviewed snapshot: ecf8c59a07acab57d51a3e75deee0dcd437f4278911689e7b1b34400f3155e0c (commit 11b70989)
- First review: `memory_bank/reviews/issue-523-core.md`
- Scope: the fixes listed in the re-review request, plus the code they touch. Worker evidence (full suite 7332 passed / 1608 skipped / 2 xfailed; DuckDB incremental 33; PostgreSQL incremental 8; DuckDB organizer/privacy subset 252; PostgreSQL graph+organizer 100; ruff) was taken as supplied and not rerun.

## Verdict

The four Should Fix items from the first review are closed, and the mechanism fixes (at-least-one progress, resolve-failure cursor, empty-list filter) are correct as written. One new documentation error was introduced by the wording fix (D1), and there are four coverage/consistency considerations below. No code-correctness regression found.

## Checklist results

### Agent 1 (correctness)

- **At-least-one progress cannot loop forever and never advances past an unexamined node.** Verified: the scan loop always examines `position 0` (`jobs.py:864-872`); `_find_aliases_in_page` always performs the first name lookup and first semantic search (`alias_resolution.py:371-376, 408-420`), so with no failure `completed >= 1`; each iteration either breaks or moves `after_id` strictly forward in the `(updated_at, id)` keyset (`jobs.py:933-938`). No unexamined node can sort before the stored cursor, because the cursor is always `done[-1]` from the examined prefix (`jobs.py:908-915`).
- **Post-page stop with full and short pages.** Verified correct: a full page over budget sets `stopped` and breaks with a resume cursor at the page's last entity (`jobs.py:923-934`; `last_cursor` was set at `:911-915`); a short page never trips the condition (`:927`) and falls through to the normal end-of-scope break (`:937-938`). Stopping on the last full page costs one extra empty-scan run, not a skipped node.
- **Resolve failure never advances the cursor, including after completed pages.** Verified: `failure_count` increments per failed pair (`alias_resolution.py:544-546`), and the job only assigns `last_cursor`/`processed` when `not lookup_failed and not resolve_failures[0]` (`jobs.py:908-915`), so after page 1 the cursor stays at page 1's last entity and `_resume` carries it (`:954-955`). `_run_incremental` then writes `completed_through=prior_completed` plus that cursor (`:106-120`), and the next run resumes strictly after it because the resume cursor outranks the completed bound (`:85-91`). Correct, but untested for the multi-page case (C2).
- **Empty-list `content_equals_any`.** Verified consistent: DuckDB `and content_equals_any` (`duckpgq_graph.py:1381`), PostgreSQL `and content_equals_any` (`pg/graph_store.py:428`), admitted view `None if not content_equals_any` (`read_graph.py:128-131`) all apply no filter. The view now normalises with `strip().lower()` (`read_graph.py:154`), matching `LOWER(TRIM(content))`.
- **Name-lookup predicate parity.** Unchanged and still a superset: `_alias_name_forms` (`alias_resolution.py:149-161`) covers all four `_is_abbreviation_match` branches and case variation; results are re-filtered by `_string_alias_candidate` and `compatible_admitted_pair` (`:394-406`). No new false pairs: two different abbreviations of one canonical are returned by the lookup but rejected by the rules, exactly as in the full scan.
- **Reporting.** Alias now mirrors deduplicate's `status`/`stopped_on_budget`/`nodes_processed`/`_resume` shape, but a lookup failure also raises `stopped_on_budget` (C1).

### Agent 3 (quality / test coverage)

- Cross-window name twin with `_WATERMARK_OVERLAP=0` (`tests/test_organizer_incremental.py:397`) genuinely isolates the name-lookup path: the only windowed entity is `js`, `nodes_processed == 1`, and the unchanged `JavaScript` is still merged. Claim holds.
- Cross-page twin (`:460`): page size 2 and `[JavaScript, alpha, js]`; the pair is found from page 1's lookup, and the post-page scan is empty after the merge. Claim holds.
- Semantic twin outside the window with a patched vector search (`:416`) and idempotent re-proposal (`:450-457`): holds; `propose_alias` no-ops on the identical pair and only one RELATES_TO edge exists.
- Failure/retry tests (`:654, :682, :710`) prove first-page failure recovery and that no watermark is written; they do not exercise a failure after a completed page (C2).
- PostgreSQL budget stop/resume (`tests/test_organizer_incremental_pg.py:202`) is timing-safe: a 20 ms search against a 20 ms scan half forces a stop with `1 <= nodes_processed < 4`.
- DuckDB 1 ms progress test (`:520`) is robust (scan plus generation lookup exceeds the 0.5 ms scan half), and the 120 ms test's assertions are bounded on both sides.
- Equivalence test (`:781`) still runs its incremental side inside the 60 s overlap, so it is effectively a whole-scope run; the isolation claim is carried by the dedicated overlap-zero test above, so this is not a defect.

### Agent 4 (pattern consistency + full-configuration comparison)

- Full-configuration comparison unchanged: only the `OrganizerConfig.incremental_enabled` description string moved (`src/prme/config.py:630-640`); no field, default, env var or retrieval config changed, and the audit JSON is unaffected. `documentation/configuration.md:225` matches.
- `INCREMENTAL_JOBS` now contains both jobs (`watermarks.py:41`) and `run_job` routes both (`jobs.py:181`); the default (option off) path is unchanged and covered by `TestDefaultUnchanged`.
- The alias handler intentionally adds at-least-one progress and a post-page stop that `_deduplicate_incremental` lacks (C4); a missing `elif` also makes alias report `stopped_on_budget` on a lookup failure where dedupe does not (C1).

### Agent 5 (redundancy)

- No new dead code. `_ALIAS_NAME_LOOKUP_LIMIT`, `failure_count`, and the `index=None` default are all used. `_semantic_alias_candidates` and `_string_alias_candidate` correctly remove duplication between the full and page paths. The handler body still parallels `_deduplicate_incremental` (~130 lines); acceptable, but the two copies now differ in budget/failure semantics (C1, C4).

## Findings carried from the first review

- F1 (wording) **closed**: RFC `docs/RFC-0015-Self-Organizing-Memory.md:499-509`, jobs docstring (`jobs.py:748-755`), watermarks comment (`watermarks.py:28-33`) and CHANGELOG now separate write-determined eligibility from bounded discovery (top-10, 500-name bound, full-job newest-100). One residual new error in the same paragraph: D1.
- F2 (empty-list) **closed**: all three stores no-filter an empty list and the Protocol docstring documents it; residual parity claim in D2.
- F3 (masked incremental test) **closed** by the overlap-zero test at `tests/test_organizer_incremental.py:397`.
- F4 (coverage) **closed** for cross-window, cross-page, semantic-outside-window, idempotent link, three failure/retry paths, and PG stop/resume; residual multi-page failure gap in C2.
- F5 (case/lower) addressed for the new filter (`read_graph.py:154`); `content_contains_any` keeps `casefold()` and the view still trims all whitespace vs SQL `TRIM` spaces, both pre-existing and harmless here.
- F8 (500-row bound) now documented in RFC `:528-530` and CHANGELOG.
- F9 (canonical tie-break) unchanged, shared with deduplicate.

## Findings

### D1 - Should Fix (documentation): "clearing the watermark" is described as running the newest-100 full job, but it runs the paged incremental scan
`src/prme/organizer/jobs.py:755-758` says "running with the option off or clearing the watermark re-evaluates only that page for aliases; paging the full scan over an older scope is follow-up work", and `docs/RFC-0015-Self-Organizing-Memory.md:514-518` says "Running with the option off, or clearing that scope's row, re-evaluates existing data ... with the full-scope job ... an older scope needs a paged full scan, which is follow-up work."
With `incremental_enabled` on, clearing the row makes `_run_incremental` start with no watermark, so `updated_after` is `None` (`jobs.py:83-99`) and `_alias_resolve_incremental` scans the whole scope oldest-first in `_INCREMENTAL_PAGE_SIZE` pages, resuming on budget (`jobs.py:844-938`). That is a paged whole-scope scan and it already exists today (the first run of every incremental test exercises it, e.g. `tests/test_organizer_incremental.py:385-394`). Only the option-off path is limited to the newest 100. The CHANGELOG entry is more careful (it says a cleared watermark is a remedy and separately states the full job's newest-100 limit); the two code/doc sentences misdirect an operator who wants to re-evaluate an older scope under a new threshold or alias table. The fix is wording only: distinguish "option off = newest 100" from "clear the row = paged whole-scope incremental run".

### D2 - Consider: the new docstring claims empty-list parity with `content_contains_any()`, which the admitted view does not honour
`src/prme/storage/graph_store.py:230-233` documents `content_equals_any` with "An empty list applies no filter, matching content_contains_any()." That is true for both SQL stores but not for `AdmittedGraphView`: `read_graph.py:150-153` treats `content_contains_any=[]` as match-nothing (`any([])` is `False`), whereas the new filter correctly maps `[]` to `None` (`:128-131`). The new filter itself is consistent across all three backends; the parenthetical parity claim is the only inaccuracy. Either drop "matching content_contains_any()" or fix the view's existing `content_contains_any` empty-list branch.

### C1 - Consider: a lookup failure reports `stopped_on_budget=True` alongside `status="error"`
`jobs.py:906` sets `stopped=True` whenever `completed < len(examined)`, which is also true on a name-lookup failure (`completed=0`, `lookup_failed=True` at `:900-902`), so the result is `status="error"` with `stopped_on_budget=True` (`:946-947`). This contradicts `find_aliases_in_page`'s docstring "the caller reports an error rather than a budget stop" (`alias_resolution.py:330-334`) and makes the CLI print "Stopped on the budget; the next run resumes here" on an error (`src/prme/cli.py:624-625`). Deduplicate avoids it with `elif` (`jobs.py:681-685`). No functional impact: `_run_incremental` branches identically on either flag (`:106`). A merge failure on a fully completed page leaves the flag `False` unless the separate post-page budget condition also fires (`:926-930`), so the flag is also inconsistent between failure types. A one-word `elif`/guard at `:906` closes it.

### C2 - Consider: no multi-page failure test for alias_resolve
The three new failure tests (`tests/test_organizer_incremental.py:654, :682, :710`) all use two entities, so page 1 fails and `last_cursor` is `None`; the branch that preserves a completed page's prefix and writes `_resume` (`jobs.py:908-915, :954-955`; `_run_incremental:106-120`) is only exercised by deduplicate's `test_failed_page_does_not_move_the_cursor_past_it` (`tests/test_organizer_incremental.py:589`). I traced the alias path and it is correct (page 2's failure leaves page 1's cursor and re-scans page 2); an alias analogue with page size 2 and a failing entity on page 2 would lock it.

### C3 - Consider: the post-page over-budget stop is not deterministically covered
`jobs.py:923-930` is new behaviour; the 1 ms test uses a short page (3 < 500, so the branch cannot fire), and the 120 ms / 40 ms tests may stop through the inner `examined` guard (`:869`) or the phase guard (`alias_resolution.py:408-410`) instead. The logic is correct (verified by reading), but a test that forces one full page to complete and then exceeds `scan_budget_ms` (e.g. a patched clock) would pin it.

### C4 - Consider (follow-up, outside #523): deduplicate lacks the progress guard alias now has
`_deduplicate_incremental` has no at-least-one guard (`jobs.py:653-658`) and no post-page stop (`:700-706`), so with a budget smaller than one page scan it examines zero nodes, writes no resume cursor, and repeats the same page on every run. #523 added the guard to alias (`:864-872`, `:923-930`) and its 1 ms test demonstrates the failure mode (`tests/test_organizer_incremental.py:520`). Not a #523 regression; worth a follow-up or backport so the two incremental jobs do not diverge.

### Nit
`tests/test_organizer_incremental_pg.py:1-6` still says "(dwamianm/prism#517)" and lists #517 coverage only, while the DuckDB module docstring was updated to "#517, #523" and covers both jobs.

## Verification notes

- Did not rerun the worker's passing commands. Read the changed source, both SQL stores, the admitted view, `_run_incremental`/watermark store, `scan_changed_nodes`, `resolve_aliases` and the new tests line by line; checked the rebase (`git diff origin/main` is the 14-file change on one commit 11b70989, no uncommitted source/test edits).
- No paid API and no owner PostgreSQL port 5432 used by this review.
