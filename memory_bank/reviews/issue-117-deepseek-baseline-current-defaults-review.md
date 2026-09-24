# Code Review: issue-117-deepseek-baseline-current-defaults

## Files Changed
- `benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-locomo-result.json` (new):
  the complete DeepSeek baseline for the current defaults on LoCoMo, written by
  `gpt54_baselines run prme --benchmark locomo --provider ollama`. 1,014/1,540 (65.8%), question interval
  63.4% to 68.2%, conversation interval 63.5% to 68.4%.
- `benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-longmemeval-result.json` (new):
  the same for LongMemEval-S. 423/500 (84.6%), interval 81.4% to 87.6%.
- `BENCHMARKS.md`: the baseline row, its provenance, a per-category table, how it relates to the earlier
  DeepSeek 437/500 run, the restarted LongMemEval-S run, and procedure steps 2 and 3 brought up to date
  with the #122 work rules and with what the harness can do today.
- `tests/test_gpt54_baselines.py`: a test that the published baseline is complete, internally
  consistent, covers the registered questions in order, and was answered with the settings the track
  sends today (so a settings change that would make `compare` refuse the baseline fails CI instead).

## Approach Summary
The last open criterion of #117 was the DeepSeek baseline for the current defaults on both benchmarks at
the 4K budget. #121 built the track and #122 updated the work rules to allow Ollama answer runs. This
branch runs the documented commands from the main checkout at `97c9402f` (equal to `origin/main`, clean
tree): `prepare prme` for each benchmark (1,540/1,540 and 500/500 contexts match the saved 2026-09-23
run), `calibrate` (passed on attempt 1, same manifest digest as the #121 smoke run), then `run prme` for
each benchmark. No code or defaults change.

The first LongMemEval-S run stopped at 292/500: the reader answer for `gpt4_7abb270c` looped until it hit
the 8,192-token limit (`finish_reason` `length`), a final failure under the registered retry policy. I
followed the documented recovery, but moved the arm folder to
`data/ollama-answers-v1/discarded-attempts/ollama-deepseek-v4.1-flash-cloud/prme-longmemeval-run1/`
instead of deleting it (its `result.json` sha256 `3348b16f7d0dea2bbec5a07e2361ef2b9458825c4688d71e3151ec8a2b409fc0`),
prepared again from the same commit and ran again. The second run completed; the looping question got an
826-token answer that the judge rejected. The published result reports `runs_started` 2 and
`prepared_again` 1.

Alternatives considered:
- Reuse the #121 smoke folders (`data/ollama-answers-v1/smoke-issue-117*`): rejected, they were prepared
  at `7272b064`, which is not on `main`, and `BENCHMARKS.md` step 1 and `_on_main`
  (`gpt54_baselines.py:974-979, 1074-1076`) require the defaults baseline to come from `main`.
- The legacy `python -m benchmarks --llm` runner: rejected, it defaults to a paid OpenAI reader (#119) and
  writes no registered receipts.
- Escalating the truncated answer instead of restarting: the recovery is documented in `BENCHMARKS.md`
  and the restart is disclosed in the result and the docs; the scoring question is raised as #124.

Verification: re-running `report()` (which checks every call with `ollama_answers.verify_call`) over the
kept private records reproduced both published results exactly (rows, counts, accuracy, both intervals,
categories, provider tokens, prepared digest).

## Must Fix
None.

## Should Fix
1. Step 3 restated the CLAUDE.md default-change rule and dropped "overall accuracy" (Agents 1, 4, 5).
   Replaced with a pointer to the rule.
2. The baseline was not set apart from the earlier DeepSeek 437/500 (87.4%) LongMemEval-S run, which
   used the same model under another harness and settings (judge limit 64) (Agent 4). Added a paragraph.
3. The first run's kept records were not mentioned (Agents 1, 4). Added their location.
4. The baseline row had no date or commit (Agent 4). Labeled with `97c9402f` and 2026-09-24.
5. The category table had no percentages and mixed naming (Agents 3, 4). Rebuilt with benchmark,
   raw `question_type` keys and percentages.
6. The run-to-run note framed an incidental overlap as if it answered #118 (Agent 5). Moved into the
   restart paragraph and labeled as incidental.
7. No test guarded the committed baseline (Agent 3). Added
   `test_the_published_deepseek_baseline_is_complete_and_matches_the_current_answer_settings`.
8. Review record (Agent 4). This file.

## Consider
- "Replay exactly through the call verifier" had no committed command (Agent 4). Reworded to what holds:
  every row is checked against its calls when the result is written, and repeating that check
  reproduces the results.
- LoCoMo interval caveat (Agents 3, 4): added the conversation-level interval.
- "(1,540 and 500)" ambiguity and ordering (Agents 1, 3, 4): named the benchmarks.
- "identical contexts" (Agent 1): capture files differ by a receipt `request_id`, text is identical.
  Now "identical context text". The row-hash problem itself is in #125.
- Identity drift ends the baseline's usefulness (Agents 1, 6): stated next to the baseline.
- Smoke and baseline receipts show different calibration hashes (Agent 4): one sentence says the smoke
  checks used separate folders.
- Recovery wording "remove its folder" (Agents 1, 4): now "move its folder aside (or remove it)".
- `AGENTS.md` and `memory_bank/GOALS.md` still cite only the DeepSeek 87.4% run (Agent 4): not changed;
  earlier epic #77 PRs did not edit them.
- The #121 review's Resolution Status row still says the baseline was not run (Agent 6): review files
  are not edited after they are created; this file records the resolution.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets and credentials | PASS | No keys, tokens or authorization headers in either result |
| Ollama account identity, PII | PASS | Only loopback endpoint, `https://ollama.com` and model metadata; no user, email or home path |
| Local paths, env values | PASS | None in the results; failure messages are stripped and there are no failures |
| Private data published | PASS | Rows hold hashes, verdicts and counts only, the same exposure as the committed GPT-5.4 and smoke results |
| Paid API reachable | PASS | Cost $0, loopback only, no OpenAI client on this track |
| DeepSeek vs GPT-5.4 comparison | PASS | New text reports DeepSeek numbers only |

## Pattern Consistency Assessment
Matches the #121 smoke recording (docs row plus published results) and the epic #77 habit of one
review file per PR. The GPT-5.4 publication's separate report and verification JSON were not copied:
epic #77 result PRs record in `BENCHMARKS.md`. Result fields match the smoke results except the fields
the harness drops for samples; `answer_model` and `modules` are byte-equal.

## Redundancy Check
Step 3 no longer restates the CLAUDE.md rule. The category table repeats the results' `categories`
block with percentages for readability; the test pins the two together. The provenance paragraph follows
the GPT-5.4 section's convention.

## Wiring Findings
Both results are not gitignored and stage cleanly; `benchmarks/` is not in the sdist; no test globs the
results tree. Links resolve. `compare` reads the published JSON directly and has every field it needs
(`compare(baseline, baseline)` returns a zero difference). Private records stay in the main checkout's
`data/ollama-answers-v1/`, as documented for the track.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "DeepSeek gate approves an epic #77 default change on one of many free
rerolls, then the harness refuses the new baseline and the workaround erases the run log."

Attacks that failed against the baseline itself: the restart was forced (a final truncation) and run 2
scored lower on the overlap (239 against 243), so it is not a cherry-pick; context text matched 500/500
across preparations; `run` and `prepare` on the complete arm refuse before writing.

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | After a default flip or an identity change, `prepare prme` refuses, so no new baseline can be recorded; workarounds damage the run log | pre-existing, first activated here | High | High | Fix now (doc) + Follow-up #123 | Step 3 no longer promises what the harness refuses; the harness change needs a design choice |
| 2 | The rule passes a no-op variant about 1 time in 25, more across many variants; #118 alone does not change that | pre-existing policy | Medium-High | High | Follow-up (comment on #118) | Owner decision on the evidence standard; same root cause as #118, so added there instead of a new issue |
| 3 | A degenerate reader loop forces a full restart, and the looping question never counts as wrong | pre-existing | High | Medium | Follow-up #124 | Scoring truncation needs a registered amendment |
| 4 | Restarts and renames act as rerolls; the run log has no reason and `compare` ignores it | pre-existing, precedent set here | Medium | High | Follow-up #124 (grouped with 3); disclosure fixed now | This restart is disclosed with its reason and kept records; the harness change belongs with 3 |
| 5 | The baseline is frozen at `97c9402f` while `main` moves; published row hashes cannot show which contexts changed | pre-existing | Medium | High | Fix now (doc step 2) + Follow-up #125 | Step 2 now says to re-run the gate on the defaults at the variant's commit; the hash change is code |
| 6 | Hosted weights change behind the same manifest digest | pre-existing | Medium | High | Follow-up (existing #118) | Already the subject of #118 |
| 7 | `compare` accepts other context budgets and any "before" arm | pre-existing | Low-Medium | High | Follow-up #125 | Contained code change in a module this PR does not touch |
| 8 | Replay depends on one machine's gitignored `data/`; a later sample run on the complete arm changes the run log hash | pre-existing | Low | Medium | Accept | Same retention practice as the GPT-5.4 records; `compare` works from the published results |

## Follow-ups Raised
- #123: The DeepSeek defaults baseline cannot be recorded again after a default changes.
- #124: One looping DeepSeek reader answer forces a full arm restart, and nothing records why an arm was
  restarted.
- #125: DeepSeek compare pairs results outside the default-change rule and cannot show which contexts
  changed.
- Comment on #118: the 8-of-292 verdict disagreement between the two LongMemEval-S runs, the multiple
  comparisons point, and why a repeat arm is not possible yet.

## Resolution Status
| Item | Status |
|---|---|
| Must Fix | none raised |
| Should Fix 1 to 8 | resolved |
| Consider | resolved or recorded above |
| Adversarial 1, 5 (docs) | fixed |
| Adversarial 1, 3, 4, 5, 7 (harness) | follow-up issues #123, #124, #125 |
| Adversarial 2, 6 | #118 |
| Adversarial 8 | accepted |
| Issue #117 criterion 6 (DeepSeek baseline on both benchmarks at 4K) | done |
