# Review: issue #523, `consolidate` stays full-scope

- Branch: `issue-523-consolidate-incremental`
- Base branch and commit: `main`, `7748252426c754c71f72652449285354f9e95945`
- Reviewed snapshot: `a9d132a77a8d19cfd74e43094c42f835fd5c3e25b75defb1cc6190379fd9cbea`
- Final snapshot after review fixes: `6201423d0d45ad6136b03dd6954524976bc8396e8c05e0478e93eca426f3244e`
- Tier: M (5 files, 237 changed lines after fixes, NEW_SYMBOLS true; DATA, INFRA, PERSISTED_FIELD and SECURITY false)
- Merged coverage: checklists 3, 4 and 5 merged into the core reviewer (1)
- Not applicable: 2 (security), 6 (infrastructure), 7 (data), because the diff has no runtime, config, schema or durable-field change
- Completed required checklists: 1, 3, 4, 5 (core, Sonnet) and 8 (adversarial, Opus)
- Prior analogue: commit `c7991a32` (PR #535), the round that recorded why `summarize` stays full-scope

## Files Changed

- `CHANGELOG.md`
- `docs/RFC-0015-Self-Organizing-Memory.md`
- `src/prme/organizer/jobs.py`
- `src/prme/organizer/watermarks.py`
- `tests/test_organizer_incremental.py`

## Approach Summary

The issue asks, per remaining organizer job, whether a provably write-determined candidate set exists. This round covers `consolidate` only. The answer is no, and the job stays full-scope with the reason recorded in the `_job_consolidate` docstring, the `INCREMENTAL_JOBS` comment, RFC-0015 section 5.5 and the CHANGELOG. Tests pin that the job is not incremental, writes no watermark, converges on an unchanged second run, seeds clusters from nodes a change window need not contain, and reports `completed` without a cursor when truncated. No runtime behavior changes.

## Must Fix

None.

## Should Fix

Both were fixed in the same change.

- F1, wrong citation: the new RFC paragraph cited RFC-0006 for "the consolidation key is the exact member set". RFC-0006 is retrieval cost and context efficiency and contains no consolidation text. Replaced with `RFC-0015 section 5.3, consolidation_key`.
- F2, timing-sensitive test: the budget-truncation test used `engine.organize(budget_ms=1)`, which can mark the job `not_started` when `organize()`'s remaining-budget gate trips, making the next assertion fail for the wrong reason. Replaced with a direct `run_job(..., 0, USER)` call, which deterministically reaches the per-cluster budget check.

## Consider

All were addressed where the fix was contained and directly about the text this change writes.

- F3: pinned `consolidation_min_cluster_size` and `consolidation_similarity_threshold` in the `engine` fixture, as the same fixture already does for the summarization thresholds, so the tests do not depend on a `[HYPOTHESIS]` default.
- F4: added the hard-delete path (`delete_node` and the erasure path) to the reason.
- F5: corrected the older RFC sentence "without any node being written" to "without any member of the new cluster being written".
- F6: removed the assertion that restated the planted fake and moved its premise into a comment.
- F7: the second-run test now asserts the first run archived nothing and the second run found the same cluster; the truncation test is renamed to say it covers a fully truncated run.

## Security Audit Results

Not applicable. No config read, endpoint, credential, query, deserialization or external call is added. The only production edits are a docstring and a comment. The core reviewer's merged security screen found no secret, no PII, no authorization path and no injection vector in the diff.

## Pattern Consistency Assessment

The docstring, watermarks comment, RFC paragraph and CHANGELOG entry follow the `summarize` round (`c7991a32`): the same opening ("stays full-scope even when ... is on (#523)"), the same bullet-then-alternative shape, and the same closing warning that a later change must not assume the option covers the job. `consolidate` remains absent from `INCREMENTAL_JOBS`, and `incremental_enabled`'s description names only `deduplicate` and `alias_resolve`, so the code and the prose agree. The two divergences the core reviewer found (wrong citation, missing hard-delete path) were fixed.

## Redundancy Check

No new production constants, classes or functions. New test symbols are the module constant `CONSOLIDATION_CONTENTS` and the class `TestConsolidateStaysFullScope` with four tests. The four contents repeat strings already inlined in `tests/test_consolidation.py`; that file's convention is test-local literals, so no shared helper was introduced.

## Wiring Findings

No wiring change. `run_job` still routes only `INCREMENTAL_JOBS` members to `_run_incremental`, and `consolidate` dispatches directly to `_job_consolidate`. `_job_consolidate` takes no `window` parameter, so a later accidental addition to `INCREMENTAL_JOBS` would raise `TypeError` and also fail the new test.

## Data Safety and Read/Write Proof

Not applicable. No schema, key, migration, backfill, stored row or durable field changes. No existing record is read or written differently.

## Break Scenarios (adversarial)

| # | Scenario | Label | Verdict | Resolution |
|---|---|---|---|---|
| S1 | The record says "whole active set" for a job that reads a newest-500 page per type | Newly introduced (the false claim; the page is pre-existing) | Fix now | Fixed: all four texts now say the job reads the newest 500 active nodes per type, and the page is recorded as a full-run limit |
| S2 | A truncated or starved run reports `completed`, and the record omitted that operational consequence | Reporting is pre-existing; the omission was newly introduced | Fix now (documentation), Follow-up for the flag | Fixed in the docstring and RFC; the truncation flag and real error count are #536 |
| S3 | A second full run does create a summary once the first run retired sources | Behavior pre-existing; the unqualified claim was newly introduced | Fix now (qualification), Follow-up for the test and convergence decision | Fixed in the docstring, RFC and CHANGELOG; the regression test and convergence decision are #537 |
| S4 | The guard is prose with no executable link to the code it describes | Newly introduced (the guard shape) | Accept | The set membership, the missing `window` parameter (a `TypeError` tripwire) and four tests cover the main bypass; the watermarks comment already names `_job_consolidate`. The remaining drift risk is prose going stale, which no equality test fully removes |

Adversarial pre-mortem headline: "Two weeks out, someone builds an incremental `consolidate` from the phrase 'an emergent property of the whole active set', seeds it from changed nodes, and the store accumulates parallel summary lineages for the same sources, while every run reports `completed`." The phrase was corrected to the page the job actually reads, and parallel lineages are now named as the reason not to build that form.

## Follow-ups Raised

- #536: consolidate reports `completed` with `errors=0` when the budget truncates the run or every cluster fails.
- #537: a second consolidate run can publish an overlapping summary after a partial retirement.
- #538: consolidate reads only the newest 500 nodes per type and never reports what it left out.

## Resolution Status

| Item | Severity | Status |
|---|---|---|
| F1 wrong RFC citation | Should Fix | Resolved |
| F2 timing-sensitive budget test | Should Fix | Resolved |
| F3 unpinned `[HYPOTHESIS]` thresholds | Consider | Resolved |
| F4 hard-delete path omitted | Consider | Resolved |
| F5 older RFC sentence contradicted the new one | Consider | Resolved |
| F6 tautological fake assertion | Consider | Resolved |
| F7 advertised branches not pinned | Consider | Resolved |
| S1 whole-active-set misdescription | Adversarial | Fix now, resolved |
| S2 truncation omitted from the record | Adversarial | Fix now, resolved; flag is #536 |
| S3 unqualified idempotency claim | Adversarial | Fix now, resolved; test is #537 |
| S4 prose-only guard | Adversarial | Accepted |
| Pre-existing duplicate "Known limitation" fragment at RFC-0015 line 569 | Observation | Not changed; pre-existing and outside this issue |
| Pre-existing "Incremental ms" labels for full-scope jobs in `docs/ORGANIZER-JOBS.md` | Observation | Not changed; pre-existing from the #518 audit |

## Post-review Verification

After the fixes, the final snapshot was re-run through the targeted tests and lint:

- `uv run pytest -q tests/test_organizer_incremental.py -k Consolidate` -> 4 passed
- `uv run ruff check src/prme/organizer/jobs.py src/prme/organizer/watermarks.py tests/test_organizer_incremental.py` -> All checks passed
- `uv run pytest -q tests/test_organizer_incremental.py tests/test_consolidation.py tests/test_consolidation_publication.py` -> 71 passed, 7 skipped
