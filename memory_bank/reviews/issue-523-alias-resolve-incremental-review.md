# Review: issue 523, incremental alias_resolve

## Review Scope

- Base commit: 57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7 (origin/main after #526 merged)
- Reviewed snapshots: first pass 8126027abc080b61ab7ff363ecd545559759909cc14d0d4678188134b1626c1b; re-review pass ecf8c59a07acab57d51a3e75deee0dcd437f4278911689e7b1b34400f3155e0c at head 11b70989; final tree eabd00b9147e08746edb7246ddc2190eef6ad6c0247f2ba12c3ca2f52f79cc8a at head 48d00ea0. The final tree differs from the re-reviewed snapshot only in documentation text and one added test, both revalidated with ruff and the incremental test files.
- Final checks: full suite 7332 passed, 1608 skipped, 2 xfailed; DuckDB incremental 34 passed; PostgreSQL incremental 8 passed; DuckDB organizer/privacy subset 252 passed and 110 skipped; PostgreSQL graph store plus organizer safety and privacy scoping 100 passed; ruff passed on src/ and tests/. The full suite ran before the documentation-only and test-only edits; the source behavior is unchanged.
- Branch: issue-523-alias-resolve-incremental
- Tier: L, from `housekeeping.py review-plan --base origin/issue-517-organize-incremental-converges` and again against origin/main. Predicates: NEW_SYMBOLS true, SECURITY true, DATA false, INFRA false, PERSISTED_FIELD false.
- Roster: core (Agent 1, merged checklists 3, 4, 5), security (Agent 2), adversarial (Agent 8). Infrastructure and data reviewers not applicable.
- Completed checklists: correctness, quality and test coverage, pattern consistency and full-configuration comparison, redundancy, security, adversarial.
- Reports: issue-523-core.md, issue-523-security.md, issue-523-adversarial.md, and the re-review reports for the final snapshot.

## Files Changed

- CHANGELOG.md
- docs/ORGANIZER-JOBS.md
- docs/RFC-0015-Self-Organizing-Memory.md
- documentation/configuration.md
- src/prme/config.py
- src/prme/organizer/alias_resolution.py
- src/prme/organizer/jobs.py
- src/prme/organizer/watermarks.py
- src/prme/privacy/read_graph.py
- src/prme/storage/duckpgq_graph.py
- src/prme/storage/graph_store.py
- src/prme/storage/pg/graph_store.py
- tests/test_organizer_incremental.py
- tests/test_organizer_incremental_pg.py

## Approach Summary

alias_resolve joins deduplicate in INCREMENTAL_JOBS. A scoped run scans only ENTITY nodes created or updated since its per-scope watermark in (updated_at, id) order, processes bounded pages, resolves each page before advancing the cursor, and records a resumable cursor on a budget stop or a failure. Each changed entity is matched two ways: an exact whole-content query for its alias-equivalent names, which finds an unchanged name twin outside the page, and the existing vector search, which finds semantic neighbors. query_nodes gains content_equals_any on DuckDB, PostgreSQL and the admitted view. The full-scope path keeps its behavior.

## Must Fix

None.

## Should Fix

1. Core F1: the RFC, job docstring, watermarks comment and CHANGELOG claimed the candidate set was fully discovered, but the vector search keeps a top-10 bound. Fixed by qualifying eligibility versus discovery in all four places.
2. Core F2 / adversarial 8: content_equals_any=[] returned every row on SQL and no rows in the admitted view. Fixed by treating an empty list as no filter in the view, matching content_contains_any, and documenting it.
3. Core F3: the incremental-versus-full alias test was masked by the 60-second overlap. Fixed by a cross-window test with the overlap set to zero, which asserts only the newly written entity is examined and its older twin is merged.
4. Core F4: coverage gaps for cross-page twins, semantic twins, failure and retry, and PostgreSQL resume. Fixed with new tests listed in the Resolution Status table.

## Consider

1. Core F5: the admitted view used casefold and strip where SQL uses lower and trim. The view now uses strip().lower(), closer to SQL; exotic Unicode differences remain.
2. Core F7: seen_pairs is shared across pages, unlike deduplicate. Kept, with the reason recorded in the handler docstring.
3. Core F8 / adversarial 7: the 500-match name lookup bound. Documented in the RFC and CHANGELOG; filed as #529.
4. Core F9: canonical tie-break at identical timestamps is shared with deduplicate and not a regression. Accepted.
5. Security F2 and F3: the no-progress stall and the unbudgeted resolve half. The stall is fixed by at-least-one-entity progress; the resolve half stays bounded by page size and is recorded here.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Tenant isolation | PASS | Scan, name lookup and vector search bind user_id; the name lookup also binds scope. Merge and proposal publication revalidate owner and scope. Watermark scope keys keep an empty user id distinct from the operator scope. |
| Privacy / admission | PASS | On the admitted view the filter runs on projected nodes and every returned node is projected and disclosed. The empty-list case now matches the SQL contract. Error paths return fixed codes and counts. |
| Authorization | PASS | No new endpoint, CLI flag, tool or job. The option is off by default and organize() scope rules are unchanged. |
| Injection | PASS | Every new SQL fragment is parameterized. Alias forms are derived from a fixed table and never concatenated into SQL. |
| DoS / scale | PASS with follow-ups | Per-entity admitted-view name lookup and the full-scan bound are filed as #527 and #529. The no-progress stall is fixed. |
| Sensitive data in logs | PASS | New messages carry node ids or fixed codes only. Details carry counts and a timestamp plus node id cursor. |
| Secrets / PII in tests | PASS | Synthetic language names and user ids only. |

## Pattern Consistency Assessment

The page contract, half-budget split, error-versus-budget distinction, per-job watermark, details and _resume mirror deduplicate. The one deviation, a run-level seen_pairs set, is justified because two alias phases can rediscover a pair and is documented in the handler. The full-configuration comparison is clean: the only config change is the description of incremental_enabled; no field, default or environment variable changed.

## Redundancy Check

No unjustified duplication. alias_name_forms, _string_alias_candidate and _semantic_alias_candidates extract previous inline logic and are shared by both paths. find_aliases_in_page mirrors deduplicate's public and private split. content_equals_any has no existing equivalent.

## Wiring Findings

INCREMENTAL_JOBS and run_job dispatch are the only routing changes, and the existing nothing-new test iterates the set. The option is documented in configuration.md and the config description. No schema change; the organizer_watermarks table and its PostgreSQL grant note are unchanged from #517. The CLI and HTTP routes build their query arguments explicitly and do not expose content_equals_any.

## Data Safety and Read/Write Proof

No new durable field. The watermark row is reused and holds a timestamp and a node id. The new read filter is additive and applies inside the existing query. Merges and proposals use the existing deterministic operation identities, so a re-scan is a no-op. A failed resolution now keeps the page resumable instead of advancing past it.

## Break Scenarios (adversarial) and Adjudication

| # | Scenario | Label | Likelihood | Impact | Verdict | Reason |
|---|---|---|---|---|---|---|
| 1 | Incremental reaches old pairs that the full job's newest-100 page never reaches, and the escape-hatch wording overstates what a full run re-evaluates | Newly introduced | High | Medium | Fix now for the documentation; Follow-up #527 for paging the full scan | The docs now state the 100-entity full-run bound and the discovery bounds. Paging the full scan changes its cost profile and needs its own ticket. |
| 2 | A budget smaller than one entity's work leaves no cursor and repeats the page forever | Newly introduced for alias | Medium | High for affected schedules | Fix now | At-least-one-entity progress in the scan, name phase and semantic phase records a cursor; a test with a 1 ms budget proves it. |
| 3 | A protected store pays one whole-scope admitted read per changed entity | Newly introduced | Medium | High on protected stores | Follow-up #527 | Pushing the filter into the admitted scan is separate work. The RFC known limitation now names the per-entity cost. |
| 4 | A transient resolve failure is swallowed, the run reports completed, the watermark advances and the pair is never retried | Newly introduced | Medium-Low | Medium | Fix now | resolve_aliases now reports failure counts, and the incremental job keeps the page resumable and reports an error. |
| 5 | Clock skew larger than 60 seconds skips a newly written entity permanently | Pre-existing from #517, newly applies to alias | Low | Medium | Follow-up #528 | Shared watermark mechanism; not expanded here. |
| 6 | The single flag changes alias_resolve for installs that set it for deduplicate | Newly introduced | Medium-High | Low-Medium | Accept | Intended design. The RFC now has an explicit upgrade note and the CHANGELOG states it. |
| 7 | The 500-match name lookup can hide the only twin | Newly introduced | Low | Medium | Follow-up #529 | Documented bound; paging the lookup is its own ticket. |
| 8 | content_equals_any=[] fails open on SQL and closed on the admitted view | Newly introduced | Low | Low | Fix now | The view now treats an empty list as no filter, matching SQL and content_contains_any. |

Agent 8 pre-mortem headline, verbatim: "An operator enables incremental, changes the alias table, follows the documented full-run re-evaluation, sees a clean `completed`, and old name variants stay unmerged forever because the "full" alias job never reads past the newest 100 entities."

The documentation correction and follow-up #527 address that headline; the algorithm's discovery bounds are now stated.

## Re-review Outcomes

Core re-review: all four first-pass Should Fix items closed. One new documentation error (D1) was fixed: with the option on, clearing the watermark starts a paged whole-scope incremental run, while the option-off full job remains limited to the newest 100. The parity phrase in the GraphStore docstring (D2) was removed. The multi-page failure coverage gap (C2) was closed with a test where page 1 succeeds, page 2 fails and the cursor stays at the end of page 1. C1 (a lookup failure also sets stopped_on_budget), C3 (the post-page over-budget branch lacks a deterministic test) and C4 (deduplicate lacks the progress guard alias now has) remain recorded considerations. None is a Must Fix or Should Fix.

Security re-review: no Must Fix and no new security issue. The empty-list filter, at-least-one path, failure reporting and all bindings hold. N1 (a persistently failing pair keeps the page resumable and repeats the error) is accepted as the intended no-skip contract; it is loud (status error), bounded to one page per run, and mirrors the existing #517 failure stop. N2 documentation nits about citing follow-up numbers and the "whole scope once" wording were corrected in the RFC and CHANGELOG.

Adversarial re-review: the two must-fix behaviors hold; it could not construct a stall, skipped entity, infinite loop or cursor advance past unexamined work. The remaining equivalence-sentence overstatement was fixed by limiting the claim to a pair whose two endpoints are both in the newest-100 page, in the CHANGELOG and the test module docstring. The residual empty-list fail-open concern (admitted view returns rows for an empty list) is accepted because the only production caller always passes a non-empty form set and the view still enforces owner, projection and disclosure; the docstring no longer claims parity with content_contains_any. The persistent search-failure wedge is the same accepted N1 contract.

## Follow-ups Raised

- #527 Page the full alias scan and push exact-content filters into the admitted scan.
- #528 Take organizer watermark ceilings from the store clock.
- #529 Page the alias name lookup past its 500-match bound.

## Resolution Status

| Item | Severity | Status |
|---|---|---|
| Semantic discovery wording | Should Fix | Fixed |
| Empty-list filter contract | Should Fix | Fixed |
| Overlap-masked equivalence test | Should Fix | Fixed |
| Coverage gaps (cross-page, cross-window, semantic, failure, PostgreSQL resume) | Should Fix | Fixed |
| No-progress tiny budget | Adversarial Fix now | Fixed |
| Transient resolve failure advances watermark | Adversarial Fix now | Fixed |
| Full-run 100-entity escape hatch | Adversarial Fix now for docs | Documented; #527 |
| Protected-store per-entity lookup | Security Should Fix / Adversarial Follow-up | Documented; #527 |
| 500-match lookup bound | Consider / Follow-up | Documented; #529 |
| Clock skew | Follow-up | #528 |
| Flag covers alias on upgrade | Accept | Upgrade note in RFC and CHANGELOG |
| Canonical tie-break at identical timestamps | Consider | Accepted, shared with deduplicate |
| D1: clearing the watermark versus the option-off full page | Should Fix (re-review) | Fixed |
| D2: empty-list parity claim in the GraphStore docstring | Consider (re-review) | Fixed |
| C1: lookup failure also sets stopped_on_budget | Consider (re-review) | Accepted; no functional impact, recorded |
| C2: no multi-page failure test | Consider (re-review) | Fixed |
| C3: post-page over-budget branch not deterministically tested | Consider (re-review) | Accepted; logic verified by reading |
| C4: deduplicate lacks the progress guard | Consider, outside #523 | Recorded; not expanded here |
| N1: persistent failure keeps the page resumable | Consider (re-review) | Accepted as the intended no-skip contract |
| N2: follow-up citations and whole-scope wording | Consider (re-review) | Fixed |
