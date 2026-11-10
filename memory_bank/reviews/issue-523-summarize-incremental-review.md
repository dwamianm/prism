# Consolidated review: issue #523, summarize round

Status: COMPLETE

## Review Scope

- Base commit: `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7` (`main`)
- Final reviewed snapshot: `d99b074054200260ee74313840266a5a0c5df298e1cd66aa4ac353262af6e392`
- Earlier reviewed snapshots: `65819c30a59078a9412ebb5e09141555fcbce156c7d7c680460ff76a212ee24a`
  (first round), `1abda232806d23170c6def5776607aa54c463716b349093bc9083c965e5f5742`
  (rereview round)
- Tier: M. Predicates: `NEW_SYMBOLS=true` (test class and helper only);
  `SECURITY=false`, `DATA=false`, `INFRA=false`, `PERSISTED_FIELD=false`.
- Required roster: core (checklists 1, 3, 4, 5) and adversarial (8), both run
  independently on the first snapshot and again on the delta. No dedicated
  security, infrastructure or data reviewer was required because the diff has
  no production executable change, no schema change and no new durable field.
- Completed checklists: 1 (correctness), 3 (quality and test coverage),
  4 (patterns and full-configuration comparison), 5 (redundancy), 8
  (adversarial), plus the core reviewer's basic security and wiring screen.
  Checklist 2 (dedicated security), 6 (infrastructure) and 7 (data safety) are
  not applicable.
- Evidence: `uv run ruff check src/prme/organizer/jobs.py
  src/prme/organizer/watermarks.py tests/test_organizer_incremental.py` passed.
  `uv run pytest -q tests/test_organizer_incremental.py::TestSummarizeStaysFullScope`
  passed (2 passed). The full `uv run pytest -q` suite was run on the final
  snapshot; see the resolution status below.

Reports:
- `memory_bank/reviews/issue-523-summarize-incremental-core.md`
- `memory_bank/reviews/issue-523-summarize-incremental-adversarial.md`
- `memory_bank/reviews/issue-523-summarize-incremental-core-rereview.md`
- `memory_bank/reviews/issue-523-summarize-incremental-adversarial-rereview.md`

The final wording edits after the rereview round were text-only (job docstring,
RFC, CHANGELOG, watermarks comment, test class docstring) and implement the
rereviewers' own suggested fixes. They were verified by source inspection and
by ruff; no third review round was run for them.

## Files Changed

- `src/prme/organizer/jobs.py` (docstring only)
- `src/prme/organizer/watermarks.py` (comment only)
- `docs/RFC-0015-Self-Organizing-Memory.md` (section 5.5)
- `CHANGELOG.md`
- `tests/test_organizer_incremental.py` (new test class, helper, pinned fixture
  thresholds)

## Approach Summary

The round asked whether `summarize` has a provably write-determined candidate
set for the #517 incremental contract, which is a per-scope watermark over node
`(updated_at, id)`. The decision is no, and the job stays full-scope.

The reason is now recorded precisely. A summary is one top-N selection over a
time period's active membership. A node-change window names the period a
changed node is in now, but not the period it left. `update_node` accepts
`event_time` on both backends, so a move writes the new event time and a new
`updated_at`; the window finds the node but its row names only the new day. The
vacated period is recoverable from the summary that recorded its sources
(`source_node_ids`, `selected_source_node_ids`, `DERIVED_FROM` edges) and from
the publication journal, which is a different read from the node window. A hard
delete leaves no node row; the erasure path clears the owner's watermark rows,
so a future incremental job would rescan that scope, but a delete with no such
marker is not in the window. A full run re-examines every period it reaches, so
it reflects the move or delete only where the period still meets its minimum and
the budget reaches it; a below-minimum period keeps its summary and its week and
month rollups keep the stale excerpt; a budget-truncated run reports
`completed` and the next run restarts at the oldest period.

The alternative, an incremental design built on a summary-membership read and a
cascade through the three levels, is recorded for a future design to weigh. It
is a cost and scope decision, not an impossibility proof.

## Must Fix

None outstanding. The first-round findings were documentation precision and
test robustness, all addressed.

## Should Fix

None outstanding.

## Consider

1. The below-minimum behavior (a stale summary persists, and its week and month
   rollups keep the stale excerpt) and the budget-truncation reporting are
   pre-existing behavior, now documented. They are carried as follow-ups.
2. An incremental summarize built on the recorded summary membership is a
   possible future design. It is carried as a follow-up so the decision can be
   revisited on measured cost.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets/PII in logs or responses | PASS | No executable production line added. |
| Authorization/ownership | PASS | No endpoint, tool or scoped read changed. |
| Input validation/injection | PASS | No SQL, command or path input added. |
| Unsafe deserialization | N/A | No serialization changed. |
| CORS/network binds | N/A | No network surface changed. |
| Credentials in fixtures | PASS | No credentials added. |

The erasure path cited by the docstring already clears the owner's watermark
rows (`src/prme/storage/erasure.py:691-699`) and nulls resume cursors that name
an erased node (`erasure.py:682-690`), so the documented future incremental
behavior does not depend on a gap in erasure.

## Pattern Consistency Assessment

- The new `_job_summarize` note follows the existing full-scope notes on
  `_job_promote` and `_job_snapshot_generation`.
- The `watermarks.py` comment keeps the existing roster comment's tone and
  stays adjacent to `INCREMENTAL_JOBS`.
- The RFC paragraph sits in section 5.5, next to the existing deduplicate
  paragraph, without contradicting the sentence that only `deduplicate` has a
  node-write-determined candidate set on this base.
- The test class follows `tests/test_organizer_incremental.py`'s existing
  fixtures and helper style; the pinned thresholds equal the current defaults,
  so the deduplicate tests in the same file are unaffected.
- `consolidate` is untouched.

## Redundancy Check

- The new test class is not a duplicate of `TestDefaultUnchanged`, which covers
  deduplicate only.
- The `_active_daily_summary` helper is used twice and has no existing
  equivalent in the file.
- The `MemoryNode` import, the `before_move` and `still_old_a` reads, and all
  new assertions are used.
- No new production abstraction or dependency was added.

## Wiring Findings

- No runtime config or environment value is read by the change.
- No module, route, CLI command or schema registration is needed.
- CI already runs `ruff check src/ tests/` and `uv run pytest -q`; the new test
  lives in an existing collected file.
- `INCREMENTAL_JOBS` remains `{"deduplicate"}` on this base. The test asserts
  that `summarize` stays out, so adding it later without a handler fails the
  test through the real dispatch path.

## Data Safety and Read/Write Proof

No new durable field, schema, key or migration. The change is a decision record
and tests. Existing records are untouched. The documented future incremental
behavior would need a summary-membership read plus a cascade, or period history;
the record says so. The test writes and re-reads existing node and summary
fields only.

## Break Scenarios (adversarial)

Adjudication of the adversarial reviewer's scenarios. The verdicts are the
worker's, with the reasoning recorded.

| # | Scenario | Label | Verdict | Outcome |
|---|---|---|---|---|
| 1 | A period below its minimum keeps its old summary, and the week and month rollups keep the stale excerpt. | Newly introduced wording over pre-existing behavior | Fix now for the text; Follow-up for the behavior | The text now states the below-minimum limit and its rollup consequence. The behavior is follow-up #531. |
| 2 | The impossibility proof was false: the summary node (and the publication journal) records the vacated period. | Newly introduced | Fix now for the text; Follow-up for reassessing the design | The rationale is rewritten: the vacated period is recoverable from the summary and the publication journal, the "three dependent cursors" claim is replaced by "a summary-membership read and a cascade through the three levels", and the decision is recorded as a cost and scope deferral. Reassessment is follow-up #533. |
| 3 | The event_time test did not exercise the mechanism and depended on default thresholds. | Newly introduced test | Fix now (done) | The fixture pins the thresholds; the test asserts the recorded membership, the `updated_at` bump, and the id moving between the two day summaries. The rereviewer verified every assertion is load-bearing. |
| 4 | A budget-truncated full run reports `completed`, and repeated runs restart at the oldest periods. | Pre-existing behavior, newly documented | Fix now for the text; Follow-up for the behavior | The text now says the job reports `completed` when the budget truncates and the next run restarts at the oldest period. The reporting and resume are follow-up #532. |

Accept and Dismissed: none.

Pre-mortem headline from the adversarial reviewer: "the record now admits the
vacated period is recoverable, but still says the alternative needs three
per-level cursors; a future implementer either builds resume state it does not
need or defers summarize forever, while a below-minimum vacated day keeps its
stale excerpt in the week and month summaries and every truncated full run still
reports `completed`."

## Follow-ups Raised

- #531 A period below its minimum keeps its stale summary and its rollups keep
  the stale excerpt.
- #532 A budget-truncated full summarize reports `completed` and restarts at the
  oldest period.
- #533 Reconsider an incremental summarize built on recorded summary membership.

## Resolution Status

| Item | Severity | Status |
|---|---|---|
| Core finding 1: "writes only the new event time" | Should Fix | Resolved |
| Core finding 2: "complete active membership" | Should Fix | Resolved |
| Core finding 3: "full run converges" overclaim | Should Fix | Resolved |
| Core finding 4: test pinned to default thresholds | Consider | Resolved |
| Core finding 5: helper return annotation | Consider | Resolved |
| Core rereview nit 1: "only from the summary" | Consider | Resolved |
| Core rereview nit 2: "active" weekly/monthly | Consider | Resolved |
| Adversarial scenario 1: below-minimum rollup excerpt | Should Fix | Text resolved; behavior follow-up #531 |
| Adversarial scenario 2: false impossibility proof | Must Fix | Resolved; design reassessment follow-up #533 |
| Adversarial scenario 3: test strength | Should Fix | Resolved |
| Adversarial scenario 4: budget truncation | Should Fix | Text resolved; behavior follow-up #532 |
| Full suite | Required | Passed on the final snapshot: 7320 passed, 1605 skipped, 2 xfailed (`uv run pytest -q`, with the checkout's `.env` moved aside; `.env` restored afterward). |
