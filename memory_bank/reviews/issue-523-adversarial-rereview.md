# Agent 8 adversarial re-review: alias_resolve incremental (#523)

Status: COMPLETE

- Base commit: `origin/main` at `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7`
- Branch: `issue-523-alias-resolve-incremental` (HEAD `11b70989`)
- Reviewed snapshot: `ecf8c59a07acab57d51a3e75deee0dcd437f4278911689e7b1b34400f3155e0c`
- This is a re-review of the fixes after `issue-523-adversarial.md`; its
  adjudication is `issue-523-alias-resolve-incremental-review.md`. The prior
  scenarios are not repeated unless a fix is incomplete.

## Assigned checklist

1. At-least-one-entity progress: can a tiny/overrun budget still stall, loop
   forever, skip an entity, or advance the cursor past an unexamined one?
   (full overrun page, short page, first entity name-ok/semantic-fail,
   second-run resume)
2. Resolution failure: can it still advance the cursor or be reported as
   `completed`? (failure after several pages, failure on a fully examined
   page, failing pair whose changed endpoint is in the examined prefix)
3. `content_equals_any=[]`: does the admitted-view change create a fail-open
   path, including on a protected store?
4. Wording: do the docs now match the code, or is a "provably"/"found" claim
   still overstated?
5. Regression scan of the fixes' side effects (deduplicate untouched, config
   and docs consistent, new SQL filter not reachable with an empty form set).
6. Test coverage of the new progress, failure, and resume paths.

## Verdict on the fixes

The two must-fix behaviors hold. I could not construct a stall, an infinite
loop, a skipped entity, or a cursor advance past an unexamined one, and a
resolution failure is never reported as `completed` and never advances the
cursor. The remaining items are one wording sentence that still overstates
equivalence and two low-severity residuals of the new no-skip/fail-open
contracts; none is a blocker for a ticket whose Must Fix list is empty.

---

## Scenarios

### 1. The empty-list contract is fail-open on the admitted view, and the docstring's justification is false there

- **Label: newly introduced by fix 3** (the admitted view was fail-closed
  before this pass; the SQL stores were already fail-open).
- **Trigger.** Any caller that computes a set of exact contents and passes an
  empty list, e.g. `query_nodes(content_equals_any=sorted(matches))` where
  `matches` came out empty, on a privacy-admitted (protected) store.
- **Mechanism.** `src/prme/privacy/read_graph.py:128-131` now maps `[]` to
  `equals_set = None`, so no predicate is applied and the view returns the
  first `limit` admitted nodes instead of none. That intentionally matches the
  SQL stores (`src/prme/storage/duckpgq_graph.py:1381`,
  `src/prme/storage/pg/graph_store.py:428`). The added docstring
  `src/prme/storage/graph_store.py:230-233` says this "matching
  content_contains_any()", but the same view is **fail-closed** for
  `content_contains_any`: `src/prme/privacy/read_graph.py:150-153` uses
  `not any(...)`, so an empty list makes every node `continue` and returns
  `[]`. The rationale is therefore wrong on the protected path, and the
  chosen contract is the widening one.
- **Likelihood: Low.** `_alias_name_forms` always returns at least one form
  (possibly `{""}`, `src/prme/organizer/alias_resolution.py:156-161`), so the
  one production caller (`:385`) cannot pass `[]`.
- **Impact: Low.** Not a privacy or tenancy bypass: the view still enforces
  `_owner` (`:123`), projects (`:92`, `:168`) and discloses (`:158`), so the
  widening stays inside the caller's authorized scope. The risk is a future
  caller treating "no names" as "match nothing" and receiving a full page that
  it then feeds into a decision (for example a future alias/merge rule).
- **Detection: silent.** No log, no error; the caller sees rows where it
  expected none.
- **Cheapest mitigation.** Either make all three backends treat `[]` as match
  nothing (about six lines, the prior recommendation, fail-closed), or keep the
  SQL contract and fix `graph_store.py:230-233` to say it matches the SQL
  stores only and to record the admitted-view divergence for
  `content_contains_any`. The current tests (`tests/test_organizer_incremental.py:289-293`,
  `:348-353`) already enshrine the widening behavior, so a contract change
  needs those two assertions updated.
- **Recommended verdict: Follow-up** (doc correction is one line; the contract
  decision is the team's). Not blocking: no current caller, no cross-tenant
  read.

### 2. A persistent per-entity lookup/search failure wedges the job at that keyset position forever

- **Label: pre-existing in the reviewed diff (not a regression of the fixes),
  but the no-skip contract this pass relies on makes it durable.** My prior
  report covered resolution failures (scenario 4), not search failures.
- **Trigger.** One lifecycle-active ENTITY whose `index.search(entity.content,
  ...)` or `engine.get_node` raises deterministically (content the embedding
  model rejects, a corrupted index entry, a backend bug), on a store with the
  flag on.
- **Mechanism.** `src/prme/organizer/alias_resolution.py:413-428` returns
  `(candidates, completed, True)` on the first search failure, so `completed`
  never reaches that entity. `src/prme/organizer/jobs.py:896-915` sees
  `lookup_failed`, sets `failed`, and deliberately does not advance the cursor;
  the `_resume` cursor is the previous page's last entity
  (`jobs.py:954-955`). The failing entity's own `updated_at` does not change,
  so on every later run it is again the first entity of that page and fails
  again: the job makes progress up to the failing page and never past it.
- **Likelihood: Medium-Low.** The full-scope path explicitly tolerates
  per-entity search failure (`alias_resolution.py:298-302` logs and continues),
  so such failures are anticipated; they are usually transient, in which case
  the retry recovers.
- **Impact: Medium for that scope.** No memory is corrupted and nothing is
  lost (the pairs stay eligible), but alias resolution stops converging until
  the index is repaired. The result is visible as `status: "error"` with
  `errors >= 1` on every run (`jobs.py:940-947`, printed by
  `src/prme/cli.py:617-622`), so this is not silent.
- **Detection: visible but easy to misread.** Repeats the same error hourly and
  the full-scope job still reports `completed`, so on-call may read it as a
  flaky index.
- **Cheapest mitigation.** Keep the no-skip rule but make the wedge legible:
  log at `warning` and add the cursor entity to `details` (for example
  `stalled_at`) so the operator sees which entity needs repair. A bounded
  alternative is to skip an entity that has failed on N consecutive runs,
  record the error and advance, mirroring the full-scope skip; that trades an
  accepted miss for convergence. About 10-15 lines either way.
- **Recommended verdict: Follow-up** (bounded, reported, not on the acceptance
  path; do not expand this ticket).

### 3. The equivalence sentence in the CHANGELOG is still stronger than the code for a node in the newest-100 page

- **Label: incomplete fix (wording, fix 4).**
- **Trigger.** One user scope: create `"JavaScript"`, then 150 unrelated
  ENTITY nodes, then `"js"`. `"js"` is in the newest 100 by `created_at`;
  `"JavaScript"` is not.
- **Mechanism.** The full job (`src/prme/organizer/alias_resolution.py:252-257`,
  `limit=100`, newest first) sees only the newest 100 and cannot pair `"js"`
  with `"JavaScript"`; `"js"` stays `TENTATIVE`. The first incremental run has
  no watermark, examines `"js"`, and `_alias_name_forms("js")` finds the older
  twin anywhere in the scope, so the pair merges and `"js"` becomes
  `SUPERSEDED`. `CHANGELOG.md:15` says "an incremental run and a full run end
  with the same store within the full job's newest-100 page". Read as "the
  nodes on that page agree", it is false for `"js"`; read as "for a pair whose
  two endpoints are both in the page", it is true. The tests' module docstring
  repeats the unqualified form (`tests/test_organizer_incremental.py:9`).
- **Likelihood: High** that an operator reads the sentence as the broader
  claim, because the same paragraph then describes finding a twin outside the
  page.
- **Impact: Low.** Wording only; the behavior is the intended feature and is
  covered by the escape-hatch and discovery bounds later in the paragraph.
- **Detection: not applicable** (documentation).
- **Cheapest mitigation.** Reword to "for a pair whose two endpoints are both
  in the full job's newest 100" and qualify the test module docstring the same
  way (two sentences).
- **Recommended verdict: Fix now** (two sentences, no behavior change).

---

## Hardest attacks that failed

1. **Advance past an entity whose name lookup ran but whose semantic search
   did not.** Phase 1 can reach `name_checked` entities while phase 2 completes
   only `completed < name_checked`; the cursor is `examined[:completed]`
   (`jobs.py:908-915`), still a strict prefix of the keyset page, and the
   already-resolved name candidates for the extra entities are idempotent on
   the next run (merge replay returns `applied=False`). No skip.
2. **A failure on a pair whose changed endpoint is in the already-examined
   prefix.** `resolve_failures[0]` suppresses the whole page's `done` update
   (`jobs.py:903-915`) and breaks the loop (`:932-934`), so the `_resume` cursor
   is the previous page's last entity, never the failing page's. The changed
   endpoint is on the failing page, which is strictly after that cursor.
3. **Infinite loop or no progress under a 1 ms budget.** The scan loop always
   appends the first entity (`jobs.py:865-872`), each alias phase always
   processes its first item (`alias_resolution.py:371-376`, `:414-417`), and the
   keyset advances by at least one entity in `(COALESCE(updated_at,
   created_at), id)` order (`duckpgq_graph.py:464-484`), so repeated 1 ms runs
   walk the scope. `tests/test_organizer_incremental.py:520-548` proves
   1 -> 2 -> completed.

## Checklist coverage

- At-least-one progress: holds for a full overrun page, a short page, and a
  resumed second run; a name-ok/semantic-fail page records no cursor and
  returns `error` (that is scenario 2, not a stall in the fix).
- Resolution failure: no cursor advance, `status: "error"`, retried; holds
  after several pages, on a fully examined page, and when the failing pair's
  changed endpoint is in the examined prefix. `engine.get_node` raising outside
  the per-pair `try` is caught by the page handler (`jobs.py:916-921`), so it
  also blocks the cursor rather than skipping.
- Empty list: behavior is consistent across the three backends but widens the
  admitted read; not a tenancy bypass; sole caller cannot pass `[]`
  (scenario 1).
- Wording: bounds, protected-store cost and the full-run 100-entity limit now
  match the code; the one remaining overstatement is scenario 3.
- Side effects: `_deduplicate_incremental` and the full-scope alias path are
  untouched by this pass; `content` is still not in `_UPDATE_ALLOWED_FIELDS`
  (`duckpgq_graph.py:1183-1199`) and `update_node` always bumps `updated_at`
  (`:1221-1224`), so the "write-determined eligibility" claim holds.
- Tests: progress, cross-page, cross-window, semantic-outside-window,
  tiny-budget, name-lookup failure, merge failure, neighbor failure, and the
  scoped tenant tests are present. Missing but non-blocking coverage: an alias
  failure after several successful pages (deduplicate has
  `tests/test_organizer_incremental.py:588-629`, alias does not) and an alias
  resume on PostgreSQL.
- Minor, recorded not scored: a run can have `status: "error"` and
  `stopped_on_budget: true` together (`jobs.py:906-907`, `:946-947`), and
  `src/prme/cli.py:624-625` then prints "Stopped on the budget; the next run
  resumes here" for an error. The resume text is factually right; the stop
  reason is not.

## Pre-mortem headline

Six weeks later: one entity's embedding makes the vector search raise, so
`alias_resolve` stops at the same cursor on every hourly run, the scope stops
being alias-resolved after that page, and on-call dismisses the repeating
`error` as a flaky index because the full-scope job still reports `completed`.

Status: COMPLETE
