# Review: issue #125, keeping DeepSeek `compare` inside the default-change rule

Branch `issue-125-deepseek-compare-default-change-rule`, base `main`. Seven
review passes (correctness, security, quality, patterns, redundancy, wiring,
adversarial) read the change. This file records their findings, what was done
with each, and how the adversarial scenarios were adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - New `RULE_BUDGET` (3,996), `RULE_TOKENIZER` (`cl100k_base`) and
    `REFERENCE_ARMS`.
  - New `_check_rule_budget`, used by `compare` and by `run_pair` before a pair
    is opened.
  - New `_context_changes`, which `_same_inputs` and `compare` both use.
  - `_row` gains a `reported` flag that adds `context_text_sha256`, and
    `_verified_row` returns the reported row.
  - `_prepared_summary` now records the tokenizer.
  - `compare` refuses duplicate question ids, adds a `contexts` block, adds a
    warning when nothing shows the context text, and makes the
    different-commits warning count the differing contexts.
  - The module docstring and the `--set` help are updated.
- `tests/test_gpt54_baselines.py`:
  - New tests pin the rule budget to the registration and cover the budget and
    tokenizer refusals, unknown arm names and the reference arms.
  - New tests cover the `contexts` block (text hashes, one preparation, saved
    run, unknown, null or empty hashes, partial hashes, duplicate rows), the
    reworded warnings, and a repeat shown by text hashes after the saved-run
    match is gone.
  - The variant integration test now pairs a budget-neutral variant
    (`scoring.fusion="rrf"`) and checks that `run-pair` refuses a budget
    variant before opening a pair.
  - The published-result tests pin `shown_by`, and the row key order is pinned.
  - New helper `one_pair`, reused by two older tests.
- `BENCHMARKS.md`: DeepSeek step 2, the `compare` paragraph and the repeat
  paragraph.

## Approach Summary

Since #125 was filed, #129 already made `compare` require the before side to be
a baseline of the defaults on the DeepSeek track. A pair needs a baseline before
side (`_pair_of`), and unpaired DeepSeek results must both be baselines. The
GPT-5.4 track cannot answer PRME's arms at all. So the before-arm part of the
issue was already closed. What remained, and what this change does:

1. **Budget.** `compare` refuses any result whose arm is not a plain or
   full-context reference arm unless it was prepared at 3,996 tokens counted by
   `cl100k_base`, the registered run's budget and the "4K budget" of the rule in
   `CLAUDE.md`. That covers the defaults, variants, and names the harness never
   makes. The reference arms stay exempt because #129 documents pairing them
   with the defaults, and full-context has no budget. `run-pair` applies the same
   check before opening a pair, so no one spends hours of Ollama usage on a pair
   `compare` will refuse. Preparing a budget variant is still allowed, so the
   evidence gate can measure budgets (#90).
2. **Text hashes.** Every reported row now carries `context_text_sha256` next to
   `context_sha256`. The hash is taken from the prepared entry after `_context`
   has checked it. The attempt's own `result.json` format is unchanged, so every
   answer recorded before this change still verifies and gains the hash when it
   is reported.
3. **Differing contexts.** `compare` reports `contexts.differing` and
   `contexts.shown_by`, with two fallbacks for results published before rows
   carried the text hash: "one preparation" and "saved run". Repeat detection
   now uses the same helper, so two baselines that no longer reproduce the saved
   run (after a default changes) are still recognized as a repeat when their
   texts match. The different-commits warning now gives the count, and is left
   out when every context text is the same.

Alternatives considered:

- Deriving the rule budget from `gate.context_limit` of the current defaults.
  Rejected: the rule is fixed at 4K in `CLAUDE.md:93`, so a later default change
  must not move it. The test pins the constant to the registration's
  `defaults.packing` instead.
- Refusing every non-3,996 result, as the issue text literally says. Rejected:
  it would break the full-context and plain reference pairs that #129 documents.
- Adding the text hash to the attempt record (`_row`). Rejected: every answer
  recorded before would then fail verification (`_verified_row` compares the
  record to `_row`).
- Refusing variant pairs prepared at different commits. Rejected: a branch
  variant can never share a commit with a baseline, because `prepare prme`
  requires a commit on `main`, so this would refuse every variant.
  Scenario 1 below covers what is left.

No `src/` file changed, so no production default changed and the evidence gate
does not apply (the same reasoning as #118, #123 and #129).

## Must Fix

None.

## Should Fix

- S1 (security): an arm name the harness never makes (`prme_wide`, `wide`)
  skipped the budget refusal, because the refusal only covered `is_prme` arms.
  Resolved: only `REFERENCE_ARMS` are exempt.
- S2 (security, correctness): the text-hash count paired rows by position. A
  duplicated question id could misalign them, and `zip` would stop silently.
  Resolved: `compare` refuses a result that lists a question twice, and the count
  uses `zip(..., strict=True)`.
- S3 (quality): `BENCHMARKS.md` said "each published row carries two hashes",
  which is not true of the results published so far, and "It reports" became
  ambiguous. Resolved.
- S4 (quality, patterns, correctness): the `contexts.note` said the count came
  from the text hashes even when a fallback decided it. Resolved: `shown_by`
  names the evidence.
- S5 (patterns, correctness): `same_contexts` repeated the `prepared_sha256`
  check with different None handling, so the output could contradict itself.
  Resolved: `same_contexts = pair is not None and contexts["differing"] == 0`.
- S6 (patterns): row field order lived in two places (a splice in
  `_verified_row`). Resolved: `_row(..., reported=True)`.
- S7 (patterns, redundancy): the test only restated `RULE_BUDGET == 3996`.
  Resolved: it is pinned to the registration's `token_budget - overhead_tokens`,
  and the tokenizer to its `tokenizer`.

## Consider

- Done:
  - Null or empty text hashes show nothing (security 3).
  - Hashes on some rows only are refused, matching how `_outcome_counts`
    handles its keys (patterns 8).
  - The refusal message now says what to do, and prints a missing budget as
    "no context budget" (quality 6, patterns 4, security 5).
  - Test literals use `RULE_BUDGET` (patterns 10, redundancy 5).
  - `answer_result` zips `texts` strictly (patterns 10).
  - The two inline pair marks now use `one_pair` (patterns 10).
  - The row key order is asserted (patterns 11).
  - The published and private rows are asserted to carry the hash (wiring 3).
  - The `--set` help says run-pair and compare refuse budget variants
    (wiring 1).
- Not done, with reasons:
  - The CLI catches only `OSError` and `ValueError`, so a malformed result file
    shows a traceback (security 4, wiring 5). This was already true, the output
    goes only to the local terminal, and real results always carry `arm`.
  - The budget check inside `_same_inputs` can no longer fail from `compare`
    (quality 5, redundancy 2). Kept on purpose, so the helper still defines
    "same inputs" if the refusal is ever relaxed.
  - `_context_changes` runs twice for an unpaired repeat (quality 4,
    redundancy 3). It is one O(n) pass. Kept so `_same_inputs` stays
    self-contained.
  - A `differing_questions` list (patterns 7). Not needed for the issue. The
    count plus `gained`/`lost` is enough to spot a mismatch.
  - The PR checklist in `CLAUDE.md` does not ask for `contexts.differing`
    (wiring 6). That is the owner's call, and `CLAUDE.md` is not edited here.
  - The `prepared_sha256` fallback (redundancy 1). Kept. It is what shows the
    same text for a pre-#125 A/A pair whose preparation no longer reproduces the
    saved run.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in published rows | PASS | The new field is a SHA-256 of public benchmark context; the saved GPT-5.4 run already publishes text hashes |
| Local paths in published output | PASS | `_publish` still strips failure messages; `compare` prints to stdout only |
| Paid runs or API spend | PASS | No network, subprocess or database calls added |
| Replayable receipts | PASS | Attempt records unchanged; rows verified before the hash is added |
| Existing published results | PASS | All 10 DeepSeek results are at 3,996 and still compare |
| Input validation of result JSON | PASS after S1-S2 and Consider | Unknown arms, duplicate rows, null and partial hashes handled |
| CLI error handling | PASS (pre-existing nit) | Non-`ValueError` input errors still give a traceback |
| Unsafe deserialization, credentials, authorization | N/A or PASS | JSON only; placeholder test values; local CLI |

## Pattern Consistency Assessment

The new constants follow the style of `UNSCORED_PERCENT` and `BOOTSTRAP_SEED`
(a comment saying why, then the value). The refusal reads like the other
`compare` refusals: it names the side, and ends with the issue number and a
next step. The `contexts` block follows the nested-block-with-note shape of
`repeat`, `cost` and `provider_tokens`. The literal 3,996 matches
`run_gpt54_comparison.py:265`, `analyze_gpt54_comparison.py:125` and
`longmemeval_s_temporal_view.py:46`, and the test ties it to the registration.
Field order is defined only in `_row`.

## Redundancy Check

- No new dependencies or imports.
- The text hash reuses the prepared entry's `text_sha256`, so nothing new is
  hashed.
- `one_pair` replaced two hand-built pair marks.
- Kept deliberately: the redundant budget clause in `_same_inputs` and the
  second `_context_changes` call (see Consider).

## Wiring Findings

- Every path that reports rows (`run`, `run_pair`, private and published) goes
  through `report()` and `_verified_row`, so all of them carry the hash.
- Every reader of rows tolerates both the new key and its absence.
- No CLI flag was added, and the `compare` refusal surfaces as a parser error.
- CI runs `pytest tests/` and `ruff check src/ tests/`.
- Historical review files that name #125 as open are left as they are.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "Variant default flipped on DeepSeek pair whose
gain came from an unrelated merged packing fix. The 'different commits' warning
was ignored as always-on, and `contexts.differing` counted both changes
together."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | A change merged on `main` after the baseline alters default contexts, and a later variant pair credits it to the variant. The commit warning fires on every branch variant, and `differing` counts both changes together | Pre-existing (trigger 3 of the issue); made visible, not closed | Medium | High | Fix now (partial) and Follow-up (#139) | Fixed now: the warning gives the count, and is left out when no context differs, so it stops being always-on noise. Separating the variant's changes from other code needs a second gate replay at prepare time plus new manifest and result fields, and whether to refuse needs a decision |
| 2 | `run-pair` answers a budget variant in full (hours of Ollama usage), then `compare` refuses it | Newly introduced (the refusal is new) | Medium (#90 plans 8K) | Medium | Fix now | The same check runs in `run_pair` before a pair is opened; tested with no requests and no pair folder. How #90's paired run at a new budget gets produced is flagged on #90 |
| 3 | `contexts.differing` is `null` with no warning, and could be read as "no change" | Newly introduced (new field) | Low | Low | Fix now | A warning is added whenever nothing shows the context text |
| 4 | A variant sets `packing.tokenizer`, so 3,996 means a different amount of text but the budget check passes | Pre-existing gap in the tokenizer dimension; the new check did not cover it | Low | Low (measured at about 1% of the budget) | Fix now | It bypasses the rule this issue enforces, and closing it is small: the tokenizer is recorded in the prepared summary and checked in `compare` and `run-pair` (legacy results name none and all used `cl100k_base`) |

Scenarios Agent 7 attacked that held:

- Broader repeat detection. It still needs both sides to be baselines, the same
  text on every question, and the same modules and server version.
- The `overhead_tokens` trick. The packed context is unchanged.
- Existing data and resumed runs. Published files are never rewritten, and all
  10 fall back correctly.

## Follow-ups Raised

- #139: A DeepSeek variant pair credits the variant with every context change
  merged since its baseline was prepared (scenario 1).
- Comment on #90: the paired run at a new default budget needs a decision on
  how it is produced, now that `run-pair` and `compare` read PRME's arms only at
  4K (scenario 2).

## Resolution Status

| Item | Status |
|---|---|
| S1 to S7 | Resolved |
| Consider (done items) | Resolved |
| Consider (not done) | Recorded above with reasons |
| Scenario 1 | Partly fixed; remainder in #139 |
| Scenario 2 | Fixed; #90 comment |
| Scenario 3 | Fixed |
| Scenario 4 | Fixed |
