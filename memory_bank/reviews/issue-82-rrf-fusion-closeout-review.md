# Code Review: issue-82-rrf-fusion-closeout

## Review Scope

- Base: `main` at `60c37730e398d47c70e12fc574895d93654cfeb0`.
- Reviewed snapshot: `8b31e1cda6e32a525fd0ee1082c49a12e6a5e56e6485cc4bb02d33812d892486` (staged tree before
  the review fixes below; the fixes change wording in the closeout record only).
- Tier M from `review-plan`: 6 files, 464 changed lines, no risk predicates (DATA, INFRA, SECURITY,
  PERSISTED_FIELD and NEW_SYMBOLS all false). Documentation only; no source or test changes.
- Reviewers: core (checklists 1 correctness, 3 quality and 4 patterns merged into one report) and
  adversarial (8). Not applicable: 2 security, 5 redundancy (no new symbols), 6 wiring, 7 data.
- Evidence supplied to both: six offline evidence gate runs at `60c37730` and the four `gate-compare`
  outputs; `uv run pytest -q` on the six rank fusion and determinism test files (166 passed,
  23 skipped).

## Files Changed

- `benchmarks/results/research/2026-11-04/RRF-FUSION-CLOSEOUT.md` (new): each acceptance criterion
  of #82 with its evidence, the proposed change as it stands on `main`, and the gate comparison.
- `benchmarks/results/research/2026-11-04/rrf-fusion-gate-{expansion,no-expansion}-{4k,8k}-comparison.md`
  (new): the `gate-compare` summaries, unedited.
- `ROADMAP.md`: the #82 row reads Closed.

## Approach Summary

Everything #82 proposed is on `main`: rank fusion (#112), the semantic `min_score` (#152, #162), the
rank fusion session decay (#153), the recency boost and tie-break (#172), and the default flip under
the DeepSeek default-change rule (#177). #112 left the first criterion open because LongMemEval-S
lost about a point with session expansion on. The gate was run again with the shipped defaults
against the weighted composite (`--set scoring.fusion="weighted"`), with rank fusion's session
expansion on and off, at 4K and 8K. Every overall all-evidence share rises with a 95% interval over
questions above zero, and no category is below the composite.

## Must Fix

None.

## Should Fix

1. "With every other setting the same" did not hold: the weighted run keeps the 0.85 decay and lacks
   the boost and tie-break, and one weighted run (expansion on) serves both comparisons. Resolved:
   the criterion row and the gate section now say what differs and how "with and without session
   expansion" is read.
2. The sentence on session expansion's cost implied it still cost LongMemEval-S evidence, and used a
   difference of rounded percentages. Resolved: rewritten from the counts (a net 2 of 470
   LongMemEval-S questions at both budgets; 36 and 25 of 1,536 LoCoMo questions).

## Consider

All applied: cite the #112 review's finding G and the knowledge-update answer result (#169) under the
category criterion; cite the version 22 receipt tests; correct the `min_score` pull request numbers
(#152, #162); name `config.py` for the default; say graph proximity enters only through the seed
count list; say recency came back through #168 and not #83; list the 8K preference category; note
that LoCoMo's question-level intervals are narrow. Not changed: the latency caveat (already stated).

## Security Audit Results

Not applicable (documentation only). No secrets, paths outside the repository or private data in the
new files; the comparison summaries contain no absolute local paths.

## Pattern Consistency Assessment

Matches the reader format closeout for #79 (status line, criteria table, evidence, still open
elsewhere) and the existing `*-gate-comparison.md` summaries under `benchmarks/results/research/`.

## Redundancy Check

Not applicable: no new symbols.

## Wiring Findings

Not applicable: no code, configuration or CI change.

## Data Safety and Read/Write Proof

Not applicable: no storage, schema or persisted field change.

## Break Scenarios (adversarial)

Pre-mortem headline: "#82 closed cleanly; the only scar is a closeout doc that says the recency boost
passed a test it never took on its own."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| S1 | The record said the recency boost "passed the answer run", while the issue asked for recency only if the gate shows it helps; on the gate it moved LoCoMo 1,308 to 1,305 of 1,536 (#172), and no answer run isolated it | Newly introduced (wording) | Medium | Low, a misleading audit trail | Fix now | One paragraph; the record now says the boost was the owner's decision in #168 for conflicting memories, gives the gate result and says no answer run measured it alone |
| S2 | "Addressed before #177" holds against the weighted composite in the same reader format, not against that day's auditable default | Newly introduced (wording) | Low | None today: no category is below the composite in any comparison | Fix now | Contained; the row now says "above the weighted composite in the same reader format" |
| S3 | "Every other setting the same" contradicts the gate section | Newly introduced (wording) | Low | Low | Fix now | Same fix as Should Fix 1 |

Attacks that failed: weighted scoring surviving on a sibling path (cross-scope hints, `min_score`,
ranking profiles, request multipliers, `feedback_apply` and PostgreSQL all follow the fused score or
refuse; only code-built `ScoringWeights()` stays weighted, a deliberate choice in #177); an unfair
baseline (the weighted run in the reader format is stronger than the old auditable default, so the
gains are conservative); closing early because of #81 or #111 (the ordering decision was made in
#187 and the session decay shipped in #177).

The adversarial reviewer also suggested closing #111 or stating what it waits for. That is issue
hygiene outside this change; it is noted in the pull request, not raised as a new issue.

Tally: Fix now 3, Follow-up 0, Accept 0, Dismissed 0.

## Follow-ups Raised

None.

## Resolution Status

| Finding | Severity | Status |
|---|---|---|
| Should Fix 1, S3 | Should Fix | Resolved |
| Should Fix 2 | Should Fix | Resolved |
| Consider 3 to 10 | Consider | Applied |
| S1 | Fix now | Resolved |
| S2 | Fix now | Resolved |
