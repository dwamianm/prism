# Review: issue #129, recording the interleaved DeepSeek A/A check

Branch `issue-129-record-deepseek-aa-check`, base `main`. Seven review passes
(correctness, security, quality, patterns, redundancy, wiring, adversarial) read
the change; this file records their findings, what was done with each, and the
adjudication of the adversarial scenarios.

## Files Changed

- `benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-longmemeval-pair-5-{before,after}-result.json`:
  the published sides of the LongMemEval-S A/A pair (new).
- `benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-locomo-pair-3-{before,after}-result.json`:
  the published sides of the LoCoMo A/A pair (new).
- `tests/test_gpt54_baselines.py`: `check_published_defaults_run` (the
  completeness checks the baseline test already made, now shared),
  `published_deepseek_pair`, `AA_PAIRS`, and two tests: one pins each A/A side's
  completeness, answer settings, identity, server version, failure-policy counts
  and given-up earlier pairs; the other pins the comparison (counts, intervals,
  per-category intervals, changed verdicts, `interval_excludes_zero`).
- `BENCHMARKS.md`: the "Interleaved A/A check" section records the result, its
  scope and the earlier attempts in the past tense; the run-to-run floor
  paragraph points to it.
- `CLAUDE.md`: the A/A bullet of the default-change rule says which A/A pair
  counts; the "Current state" bullet records the passed check and its scope.

## Approach Summary

#133 shipped the revised paired test and #136 the failure-policy amendment it
was waiting on. The one open acceptance item was step 4 of the owner's decision
on #129: answer one interleaved A/A pair on both benchmarks. Both pairs of
`prme@46647825` against itself were answered through Ollama from a clean tree at
`5f89121c` (model identity `e04da138`, Ollama 0.34.3, amended failure policy):

| Benchmark | Before | After | Difference, 95% interval | Changed verdicts |
|---|---:|---:|---:|---:|
| LongMemEval-S, pair 5 | 432/500 | 431/500 | -0.2 points, -1.4 to +1.0 | 9 (4 gained, 5 lost) |
| LoCoMo, pair 3 | 1,018/1,540 | 1,012/1,540 | -0.39 points, -1.30 to +0.52 | 50 (22 gained, 28 lost) |

Both intervals include zero, so under the owner's step 4 the margin condition is
not added. No retries, repaired verdicts or unscored questions, and all 8,160
calls returned HTTP 200 on the first attempt. No `src/` or benchmark code
changed, so no default changed and the evidence gate does not apply.

## Must Fix

- M1 (patterns): the A/A test did not pin the answer settings, which the rule
  requires to match across pairs. Resolved: each side's `answer_model` without
  `identity` equals `OLLAMA_MODEL.settings()` plus the failure policy.

## Should Fix

- S1 (correctness, patterns): the failure-policy check read only `unscored` and
  `within_limit`, while the docs say the counts are pinned. Resolved: each side's
  `failure_policy` equals the amendment plus `NOTHING_UNSCORED`, `retry_policy`
  equals `OLLAMA_RETRY_POLICY`, every row's outcome is `judged`, and `compare`'s
  own recount from the rows equals `NOTHING_UNSCORED` on both sides.
- S2 (patterns): the A/A test skipped the baseline test's completeness checks
  (registration, cohort ids, totals, rows adding up, categories, prepared
  overrides and dirty flag, cost). Resolved: extracted
  `check_published_defaults_run`, used by the baseline test and the A/A test.
- S3 (quality): one test mixed structural and comparison checks. Resolved: split
  into two tests, following the baseline and #118 tests.
- S4 (redundancy): the #118 disagreement figures were written out twice in the
  A/A section. Resolved: the history paragraph now refers to them briefly.

## Consider

- C1 (quality, patterns): assert `interval_unit`. Done.
- C2 (quality): match the neighboring test's signature wrapping. Done.
- C3 (quality): awkward "can be used as the rule states" sentence. Rewritten.
- C4 (patterns): generic pair loader instead of one tied to the A/A pair. Done
  (`published_deepseek_pair`). Reusing the harness helper `pair_published` was
  rejected: it globs every date folder and returns an empty list for a missing
  file.
- C5 (patterns, correctness): pin the exact `earlier_pairs` states and that no
  category interval excludes zero. Done.
- C6 (correctness): pin that the pair read the #118 repeat's prepared contexts.
  Done (`prepared_sha256` and `contexts_matching_saved_run`).
- C7 (correctness): the scope should include answer settings and the failure
  policy, not only identity and server version. Done in BENCHMARKS.md and the
  CLAUDE.md "Current state" bullet.
- C8 (correctness): the new sentence in "The floor does not hold" split the
  explanation of the old test. Moved to the end of the paragraph.
- C9 (patterns): step 3 of the workflow repeated the outcome. Reverted to a
  plain link.
- C10 (patterns): bold on both differences did not match the floor table, which
  bolds only the interval that excluded zero. Removed.
- C11 (patterns, adversarial): the identical-reader-text counts come from the
  private answer records, not the published rows. Kept, and the docs now say
  where they come from; the earlier 46 of 1,229 figure has the same source.
- C12 (security): `_publish` strips only failure messages; a future field under
  `pair` or `run_log` should get the same scrutiny. Kept as a note; nothing in
  this change adds such a field.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets, tokens, credentials in the four results | PASS | None; only token-count keys |
| Hosts and IPs | PASS | `127.0.0.1:11434` and the documented `https://ollama.com` remote host only |
| Local paths and usernames | PASS | None; provenance keeps only commit, dirty flag, worktree digest and dependencies |
| Failure messages | PASS | `failures` is empty in all four |
| Answer or conversation text | PASS | Rows hold ids, types, correctness and digests only |
| New field kinds against the published baselines | PASS | Only pair, failure-policy and server-version metadata |
| Spend | PASS | `cost.usd` is 0; answer runs went through the local Ollama server |
| Test: deserialization, paths, network | PASS | `json.loads` on fixed repo paths; `compare` is in memory |

## Pattern Consistency Assessment

The A/A tests now follow the published-baseline test (shared completeness
checks, answer settings, policy) and the #118 test (comparison, interval unit,
intervals, changed verdicts). The docs table follows the run-to-run floor table.
The A/A results stay out of the DeepSeek run table, which lists baselines.

## Redundancy Check

No duplicated helpers remain: the completeness checks are shared, and the pair
loader is generic. The CLAUDE.md "Current state" bullet restates the key numbers
at the same level of detail as before, which is the file's existing pattern.

## Wiring Findings

No issues. In a clean `main` worktree with only this change applied, the tests
pass and read only tracked files. `compare` needs nothing outside the two
results. The new files are not ignored, there is no LFS or attributes config,
and their sizes (0.35 MB and 0.96 MB) are in line with other published results.
The four links in BENCHMARKS.md match the file names.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "A default flipped under auto-merge on DeepSeek
pairs answered by Ollama 0.34.4, which the A/A check (measured only on 0.34.3)
never covered."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | Ollama 0.34.4, already downloaded on the answering machine, installs on restart; later variant pairs run on a server version the A/A check never measured, and nothing flags it | Newly exposed (gap from #133) | High | Medium | Fix now (docs), Follow-up (#137, code) | The docs now name the identity, server version, settings and policy the check covers and say to answer a new A/A pair when any differs; a guard in `compare` changes its contract, so it is #137 |
| 2 | A new A/A measurement has no stopping rule, so a failed A/A pair could be redrawn until one passes | Pre-existing wording, relied on now | Medium | Medium | Fix now (rule text), Follow-up (#137, code) | One sentence in the A/A bullet, mirroring the confirmation run: the first accepted A/A pair counts, later ones are recorded, and any that excludes zero brings in the margin |
| 3 | With the A/A check passed, the gaps in #125 (budget and before-arm checks), #127 (choice of baseline) and #130 (confirmation shopping) are the remaining guards, and loop pull requests can auto-merge | Pre-existing | Medium to Low per variant | High | Follow-up (#125, #127, #130; question in #137) | Each gap is tracked already, and #130 says it stays open once the test is revised; whether default flips should wait for them is an owner decision, asked in #137 and flagged in the pull request |
| 4 | One A/A pair per benchmark cannot establish the test's error rate | Pre-existing (the owner's step 4) | n/a | Medium | Accept | This is the check the owner specified; the confirmation run still guards each variant, and pooling pair 5 with the stopped LongMemEval-S attempts is also consistent with zero |

Rechecked for the Accept (4): answering several A/A pairs and pooling them was
option 3 in the issue and was not chosen; widening it here would change the
owner's decision rather than record it.

Scenarios the adversarial review attacked hardest and found holding: bias from
taking the first pair to complete after the given-up ones (they were given up
for reasons unrelated to scores, and the pair logs hash to the digests the
amendment recorded, so they were only appended to); running the two pairs at the
same time and the fixed answer order (each pair has its own queue, and no
consistent position effect shows across pairs); the recorded evidence being
wrong (the 8,160 first attempts, the identical-text counts and the run log
digests check out against the private data).

## Follow-ups Raised

- #137: A DeepSeek variant pair answered after an Ollama update is not flagged
  as outside the recorded A/A check (scenarios 1 and 2, code side, and the open
  question from scenario 3).

## Resolution Status

| Item | Status |
|---|---|
| M1 | Resolved |
| S1 to S4 | Resolved |
| C1 to C10 | Resolved |
| C11, C12 | Kept, with reasons above |
| Scenarios 1 and 2 | Docs fixed now; code guard in #137 |
| Scenario 3 | Tracked in #125, #127, #130; question in #137 |
| Scenario 4 | Accepted |
