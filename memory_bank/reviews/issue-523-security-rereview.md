# Issue 523 Security Re-review (after fixes)

Status: COMPLETE

- Base commit: `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7` (origin/main, PR #526 merged)
- Reviewed snapshot: `ecf8c59a07acab57d51a3e75deee0dcd437f4278911689e7b1b34400f3155e0c`
- Branch: `issue-523-alias-resolve-incremental` (tracked tree at HEAD `11b70989`, clean)
- Scope: the five fixes made after `issue-523-security.md`: empty-list filter semantics and
  `strip().lower()` normalization, at-least-one progress and the one-over-budget-page stop,
  the `failure_count` cursor hold, the documented #527 name-lookup limitation, and the
  docs/tests around them. I read the first report, the diff against origin/main for the
  privacy, storage, organizer and test files, the changed docs, and then followed the
  facade and admission paths. I did not rerun the worker's suites; I spot-checked the
  reported counts and test names against the test files and the saved evidence outputs.

## Verdict

No **Must Fix** security defect and no new security issue. All four requested conclusions
still hold: the empty-list filter can only return records the admitted view already projects
and discloses, the at-least-one path stays behind the same owner and scope bindings, the
failure path carries counts and timestamps only, and the new tests are synthetic. The first
report's **F2 (no cursor progress when one page cannot fit the budget) is fixed**. **F3 and
F4 remain as previously reported**; they are not regressions and remain non-blockers. The
only new effects are two availability/documentation notes, both non-security.

## Area Table

| Area | Result | Details |
|---|---|---|
| Tenant isolation | PASS | The at-least-one path still scans `scan_changed_nodes(user_id=...)` (`src/prme/organizer/jobs.py:846-856`) and binds the changed entity's own `user_id` and `scope` on every name lookup (`src/prme/organizer/alias_resolution.py:380-387`), gates pairs with `compatible_admitted_pair` (`:399`), and `resolve_aliases` revalidates `(user_id, scope)` before any merge/link (`:483-489`). The vector search binds owner and scope and `get_node` binds the entity's owner (`:190-212`). The empty-list change does not add a read path. |
| Privacy/admission | PASS | `content_equals_any=[]` becomes `equals_set = None` (`src/prme/privacy/read_graph.py:128-131`), so the filter is skipped exactly as when the argument is absent; the nodes it can return are the ones `_all_nodes` already produced through `scan_nodes` with `context.project_node` and `None` dropped (`read_graph.py:80-103, 105-115, 133-158`). No hidden record can reach the comparison, and the result is disclosed again. The only production caller passes a non-empty sorted set (`alias_resolution.py:385`; `_alias_name_forms` always contains at least the trimmed name). |
| Authorization | PASS | Unchanged from the first report: no new endpoint, CLI option, MCP tool or job name; `alias_resolve` is explicit-invocation only and the opportunistic pass still excludes pairwise jobs (`src/prme/organizer/maintenance.py:12-15`); incremental mode is still gated by `OrganizerConfig.incremental_enabled` (default `False`). |
| Injection | PASS | DuckDB builds only placeholder counts and binds every value (`src/prme/storage/duckpgq_graph.py:1381-1386`); PostgreSQL binds one `text[]` array (`src/prme/storage/pg/graph_store.py:428-431`). User and scope conditions remain bound (`duckpgq_graph.py:1340-1346`, `pg/graph_store.py:385-392`). No content is interpolated. |
| DoS / scale | PASS with residual findings | F2 is fixed. F3 (resolve half unbudgeted) is unchanged. The at-least-one guarantee means every run now does at least one entity's work, so a protected store pays at least one whole-scope admitted read per run even at a 1 ms budget (the F1 amplification, now guaranteed rather than possible); it is bounded to one over-budget page and operator-triggered. N1 below records a new failure-stop progress edge. |
| Sensitive data in logs/results | PASS | Failure paths add counts and fixed strings only (`jobs.py:900-921, 940-957`); `_run_incremental` adds window timestamps (`jobs.py:128-131`); `resolve_aliases` logs node ids, alias type and confidence (`alias_resolution.py:547-552`). On a protected store `privacy_disclosure` wraps the job in `protected_diagnostics` (`src/prme/privacy/admission.py:119-123`), so `ScopedLogger` suppresses messages and tracebacks, and `project_organize` drops all `details` from the returned result (`src/prme/privacy/diagnostics.py:165-190`). |
| Secrets/PII in tests/fixtures | PASS | The new tests use language names, `Alpha`/`Beta`/`Gamma` and `uuid4()`-suffixed owner ids only (`tests/test_organizer_incremental.py:272-353, 520-750, 826-836`; `tests/test_organizer_incremental_pg.py:151-260`). No credentials, secrets or real personal data. |

## Requested checks

1. **Empty list cannot disclose hidden records.** The admitted view applies no filter, but every
   node in `matches` came from `_all_nodes` -> `scan_nodes`, which projects each row and drops
   `None` (`read_graph.py:80-103`), and returns through `disclose` (`:158`). `_owner` also
   rejects any `user_id` other than `None`/`context.user_id` (`:31-34`), and `_all_nodes` scans
   `context.user_id` only (`:105-115`). The engine reaches this view only for a protected owner
   (`src/prme/storage/engine.py:3186-3196`). The only production caller that supplies the filter
   passes a non-empty set (tests also call it, but with no stored hidden records), and `/v1/nodes`
   and `prme nodes` still build their kwargs explicitly and cannot pass it
   (`src/prme/api/routes.py:773-809`, `src/prme/cli.py:265-297`).
   Minor consistency note (not security): on the admitted view `content_contains_any=[]` still
   matches nothing (`read_graph.py:150-152`) while both SQL backends treat it as no filter, so
   the new docstring phrase "matching content_contains_any()"
   (`src/prme/storage/graph_store.py:230-233`) is accurate for `GraphStore` but not for the
   admitted facade. The alias path never passes an empty list, so nothing reaches this asymmetry.
2. **At-least-one path respects user and scope.** `examined` always contains the first scanned
   entity (`jobs.py:864-872`), the scan is owner-bound (`:846-856`), and each lookup is bound to
   that entity's own owner and scope (`alias_resolution.py:380-387`); the pair gate and the
   pre-merge revalidation still refuse any different `(user_id, scope)` (`:399`, `:483-489`).
   An unscoped run remains the documented operator mode on an unprotected store; a protected
   store without a scope still raises in `generation_search` before any page (`jobs.py:819-833`).
   The new tenancy test covers the scoped case (`tests/test_organizer_incremental.py:826-836`).
3. **Failure path exposes no content.** `_find_aliases_in_page` returns a fixed failure flag
   (`alias_resolution.py:388-392, 424-428`), the job turns it into counts and a status
   (`jobs.py:900-905`), and `details` holds `aliases_found`, `aliases_resolved`,
   `pairs_not_applied` and the `_resume` cursor, which `_run_incremental` pops before returning
   (`jobs.py:104, 128-131, 940-957`). Exception text exists only in log calls; on a protected
   store those are suppressed and `details` are stripped (`diagnostics.py:165-190`). The new
   failure tests assert only counts, status and watermark shape (`tests/test_organizer_incremental.py:654-748`).
4. **New tests use synthetic data only.** Confirmed above; the Postgres tests use a fresh
   `uuid4()` owner per test (`tests/test_organizer_incremental_pg.py:154, 185, 206`).

## Findings

### F2 (previous) - Fixed

- `find_aliases_in_page` now always examines the first entity in each phase
  (`alias_resolution.py:372-376, 413-417`), and the job always keeps at least one entity in
  `examined` (`jobs.py:864-872`). With `completed >= 1`, `done` is non-empty and `last_cursor`
  is set (`jobs.py:906-915`), so a budget stop records `_resume` and the watermark is written
  (`jobs.py:104-120`). The regression test is
  `test_alias_resolve_makes_progress_with_a_tiny_budget` (`tests/test_organizer_incremental.py:520-546`).
- The one-over-budget-page stop (`jobs.py:923-934`) bounds the new work: a run that starts over
  budget processes exactly one page and breaks, and the cursor moves only over the examined
  prefix (`jobs.py:908-915`). No cross-owner read is added; the cost is one entity's name
  lookup plus its resolve at minimum.

### F3 (previous, unchanged) - Consider

- The resolve half is still not time-budgeted (`jobs.py:890-895`, `alias_resolution.py:442-554`).
  The at-least-one fix guarantees at least one page's resolve even when `remaining_ms` is 1 ms,
  so the worst case is one over-budget page rather than an unbounded run. A page can still carry
  up to 500 name matches per examined entity (`alias_resolution.py:309, 385-386, 394-410`), each
  resolved with two `get_node` reads and a merge/proposal transaction. Bounded, operator-only,
  no isolation impact; remains follow-up work.

### F4 (previous, unchanged) - Consider, latent, not exploitable

- `resolve_aliases` still receives `user_id` only when it is truthy (`jobs.py:893`), so an
  empty-string owner leaves the two `get_node` reads unscoped (`alias_resolution.py:476-477`).
  The candidates can only be `""`-owned (scan and lookup bind `user_id=""`) and the
  `(user_id, scope)` revalidation (`:483-489`) refuses any other owner, so no cross-owner merge
  or link is reachable. Pre-existing in the full-scan path.

### N1 (new behavior, intended) - Consider: a persistently failing pair now pins the alias cursor

- `failure_count` marks the page failed and `jobs.py:903-915, 932-934` then refuses to advance the
  cursor. That is the intended fix (do not silently skip a pair that was not retried), but a pair
  or page that fails every time blocks the incremental alias job from ever advancing past it;
  each run reports `error` and retries the same page. Work per run stays bounded to one page, the
  failure carries no content, and this mirrors the existing #517 failure-stop. Worth a follow-up
  skip/quarantine or a bounded retry if a durable failure is plausible. Not a security finding and
  not a blocker for this ticket.

### N2 (documentation, minor, non-security)

- The #527 limitation is documented accurately and no data is disclosed: the RFC says the name
  lookup repeats the whole-scope admitted read once per changed entity and that pushing the
  exact-content filter into the admitted scan is follow-up work
  (`docs/RFC-0015-Self-Organizing-Memory.md:523-530`), and the CHANGELOG repeats it
  (`CHANGELOG.md:15`). Two nits: those paragraphs do not cite the filed follow-up numbers
  (#527/#528/#529) the way other follow-ups are cited in the CHANGELOG, and "its first alias run
  has no watermark and examines the whole scope once" (`RFC:532-535`, mirrored in `CHANGELOG.md:15`)
  reads as one run covering the whole scope, while a budget stop can leave a resume cursor and
  continue on the next run. Neither affects behavior or exposure.

## Negative results worth recording

- No new endpoint, CLI flag, tool or job name; `alias_resolve` remains in `ALL_JOBS`/`DEFAULT_JOBS`
  and the explicit-only dispatch (`jobs.py:38, 49, 163-186`), and `end_session` still runs only
  `promote` (`engine.py:5243-5247`).
- The empty-list semantics are consistent across DuckDB, PostgreSQL and the admitted view for
  `content_equals_any` (all no filter); the alias name lookup always supplies at least one form.
- `_alias_name_forms` returns only strings derived from the node's own content and the fixed
  `KNOWN_ALIASES` table (`alias_resolution.py:149-161`), so the lookup value list is bounded and
  never caller-controlled SQL.
- The `failure_count` change is backward compatible: the full-scope call site does not pass it
  (`jobs.py:773`) and `resolve_aliases` keeps its return value and logging shape.
- The worker's evidence is plausible against the source: the PostgreSQL incremental file has
  exactly 8 test functions, matching the reported 8 passed; I take the 33-passed DuckDB
  incremental count as given and did not rerun it. The regression outputs (100 passed; 252
  passed, 110 skipped) match the organizer/privacy scopes they name.

## Limits of this audit

- I did not execute the test suites; the worker's pass counts are taken as given and spot-checked
  against the named tests. The protected-store behavior is reasoned from the admission code, not
  exercised live.
- I did not re-review the unchanged parts of the #523 change beyond the dependencies of these five
  fixes; the first report's conclusions for them still stand.
