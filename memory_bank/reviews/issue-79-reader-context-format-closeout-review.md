# Review: issue-79-reader-context-format-closeout

## Review Scope

- Base: `main` at `60c37730`.
- Reviewed snapshot: `579e8fe1d7915a93d6cacd1616a9ad34bea6923533a6cd5036f7957ad0f97fcb` (3 files, 122 changed lines, tier M, no risk predicates, no new symbols).
- Final snapshot after the review fixes below: `42b310f595b63dcb0711b186d1a25dfc0416e546fe75150677b2b4b4a6214148`. Only `READER-FORMAT-CLOSEOUT.md` changed, with wording the reviewers proposed. The added numbers (conversation-level intervals) were verified by the core reviewer against the verdict record, and the added test names were checked with `grep` and a passing run (`tests/test_fold_repeated_text.py tests/test_graph_seed_count.py`: 60 passed, 9 skipped).
- Roster: core reviewer (checklists 1, 3 and 4 merged; Sonnet) and adversarial reviewer (checklist 8; Opus). Checklists 2, 5, 6 and 7 not applicable (no source, configuration, storage or new symbols).

## Files Changed

- `ROADMAP.md`: the #79 row under "Delivered under epic #77" moves to Closed and points at the record.
- `benchmarks/results/research/2026-11-04/READER-FORMAT-CLOSEOUT.md`: each acceptance criterion of #79 with its evidence.
- `benchmarks/results/research/2026-11-04/reader-format-gate-defaults-4k.md`: the offline evidence gate's summary at the current defaults on `60c37730`, as the gate wrote it.

## Approach Summary

Everything #79 proposed is on `main`: the reader format (#109), speaker labels (#190), the default flip under the DeepSeek default-change rule (#177) and balanced ordering (#187). The issue stayed open for criteria later pull requests met. This change re-runs the gate at today's defaults (LoCoMo 84.5 records per context, 96.6% memory text) and records the evidence, so the pull request can close the issue.

## Must Fix

None.

## Should Fix

1. Criterion 2 cited only version 14 receipt tests under weighted scoring. Fixed: the record also cites the version 22 tests in `tests/test_fold_repeated_text.py` and the replay tests in `tests/test_graph_seed_count.py`.
2. The roadmap row says Closed before the issue is closed. Resolved by the pull request carrying `Fixes #79`.

## Consider

1. State that the DeepSeek pair stands in for the issue's GPT-5.4 wording. Done in the record and the pull request (see S1).
2. Quote the LoCoMo conversation-level intervals. Done (+13.63 to +16.69 and +13.95 to +17.48).
3. #81 is open while criterion 1 names its ordering. The record already shows both orderings pass. No change.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or local paths in new files | PASS | None found |
| Code, auth, input handling | N/A | No source changes |

## Pattern Consistency Assessment

The record follows the dated research records (`2026-11-02/KEYFRAME-CALIBRATION.md`); the gate summary is stored as the 2026-09-25 candidate-limit summaries were. The roadmap row keeps the table's columns.

## Redundancy Check

Not applicable (no new symbols).

## Wiring Findings

None. No configuration, receipts or `DERIVATION_VERSION` affected.

## Data Safety and Read/Write Proof

Not applicable.

## Break Scenarios (adversarial)

Pre-mortem headline: "#79 closed on DeepSeek evidence; owner asks where he approved skipping GPT-5.4. Answer: #117, which named #79, though the closeout did not quote it."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| S1 | The GPT-5.4 substitution is stated without its owner provenance | Newly introduced wording | Low | Fix now | One sentence; the record now quotes #117, which names #79 and moves its paid run to the DeepSeek track |
| S2 | Undated extracted facts still show the ingestion clock as their date on the `ingest()` path | Pre-existing | Medium | Accept | Already tracked in #106 (open) and named in the record; #79's bullet about `valid_from` holds (`packing.py` prints a validity window only when a caller closed it) |
| S3 | Criterion 2 evidence predates the current default receipts | Pre-existing | Low | Fix now | Same as Should Fix 1; citations added |
| S4 | #81 stays open after #187 shipped its decision | Pre-existing | Low | Accept | #81 is its own open issue; its state is a separate decision and needs no new issue |

## Follow-ups Raised

None. S2 is already tracked in #106.

## Resolution Status

| Item | Status |
|---|---|
| Should Fix 1 / S3 | Fixed |
| Should Fix 2 | Fixed in the pull request (`Fixes #79`) |
| Consider 1 / S1 | Fixed |
| Consider 2 | Fixed |
| Consider 3, S2, S4 | Accepted, reasons above |
