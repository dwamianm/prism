# Review: issue #144, a recorded verdict for each DeepSeek variant

Branch `issue-144-record-deepseek-pair-verdicts`, base `main`.
Seven review passes (correctness, security, quality, patterns, redundancy,
wiring, adversarial) read the first version of the change. This file records
their findings, what was done with each, and how the adversarial scenarios were
adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - New "Pair verdicts (#144)" section. `record_pair_verdict` records a
    pair the `compare` command accepted as a variant's first pair or
    confirmation, or as an A/A pair: a `compared` event in the pair's run log
    and one line per pair in the tracked verdict record
    (`ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl`, next to the A/A
    record). `_check_verdict_results` checks a line against the published
    results it names. `verdict` reads the lines for one variant on both
    benchmarks and prints `pass`, `fail` or `incomplete`
    (`_verdict_lines`, `_judged`, `_role_verdict`, `_aa_margin`,
    `_accepted_aa_lines`, `_sequential_repeat`, `_same_answers`).
  - Shared helpers extracted from the A/A record code: `_published`,
    `_recorded_sides`, `_checked_aa_lines`, `_check_numbers`; and from
    `compare`: `_paired_questions`, `_accuracy` (which `compare` now uses for
    its overall accuracy), `_excludes_zero`, `BOOTSTRAP_SAMPLES`.
  - CLI: a `verdict` command; `compare` records after printing.
  - Module docstring and help text.
- `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl`:
  new, empty, tracked from the start.
- `tests/test_gpt54_baselines.py`: a "Pair verdicts (#144)" section (16
  tests) and a #118 repeat assertion.
- `CLAUDE.md`: a "Verdict (#144)" bullet in the default-change rule, the flip
  pull request must cite a `pass` verdict checked against the run logs, and
  the first-pair and confirmation model check is no longer by hand.
- `BENCHMARKS.md`: DeepSeek step 3, two bullets on the record and the
  command, and the command examples.

## Approach Summary

The owner's decision on the issue is implemented as written. The pure
`compare()` function is unchanged in behavior; the CLI command records, so
`record_aa_check` (which calls `compare()` under the A/A record's lock) and
the many direct callers have no new side effects. A refused pair is never
recorded, so it counts as neither; `verdict` follows the current baseline, so
a new baseline starts the count again.

`verdict` is fail-closed. On the machine that answered the pairs it takes the
current baseline and the arm's current context text from the run logs,
requires each recorded role to be the pair the run logs count (`_counted`),
requires the A/A record to list every complete A/A pair, and repeats
`compare`'s current warnings for each recorded pair (`_variant_record`), and
it reports `checked_against_run_logs: true`. Everywhere it checks every line
against the published results it names: digests, pair mark, the variant's
settings and context text hash, and the numbers, recomputed with `compare`'s
own function and bootstrap.

The A/A margin follows the rule text: it applies when an accepted A/A pair
under the pair's conditions excluded zero on either benchmark ("if either
excludes zero"), and a gain must then be larger than the largest absolute A/A
difference on its benchmark, the #118 sequential repeat included, which is
recomputed from its published results.

No `src/` file changed, so no production default changed and the evidence
gate is not needed. A dry run on scratch copies of the real run logs and the
`prme-reader-rrf-sd06` pair results gives `pass` (LoCoMo +14.8 and +14.7
points, LongMemEval-S +0.4 and -0.2, no margin) with the run logs, and the
same result flagged unchecked without them.

## Must Fix

| # | Source | Finding | Resolution |
|---|---|---|---|
| M1 | Agents 1, 2 | The recompute check used the line's own `bootstrap_samples`, so a line edited to a tiny sample count, with the interval that count gives, turned noise into a gain; a huge count hung `verdict` | Fixed: lines must record compare's bootstrap (2000 samples, seed 42) and are recomputed with the constants; `record_pair_verdict` refuses any other comparison. Tested |

## Should Fix

| # | Source | Finding | Resolution |
|---|---|---|---|
| S1 | Agents 2, 4 | An A/A line's `accepted` flag was trusted, so flipping it hid an A/A pair that excluded zero and removed the margin | Fixed: with run logs it must match the pair's state; without them a refused line's results must be over the 1% limit. Tested both ways |
| S2 | Agents 2, 4, 6, 1 | A line's variant identity, `correct`, `questions` and split intervals were never checked | Fixed: `_check_numbers` compares all of them, and the settings and context text hash are checked against the after side; with run logs the identity is also compared with the counted pair |
| S3 | Agents 1, 6, 7 | First pair and confirmation under different model identities or answer settings passed, while CLAUDE.md said "check by hand" and the new bullet said "in full" | Fixed: `_same_answers` refuses it; CLAUDE.md now says `verdict` refuses it. Tested |
| S4 | Agent 1 | A recorded loss on one benchmark left the role undecided until the other benchmark was compared | Fixed: a loss decides the role as soon as it is recorded. Tested |
| S5 | Agents 3, 1, 7 | The no-run-logs baseline came from the last line of any role, so an older baseline's A/A pair compared later moved it | Fixed: only first pairs and confirmations set it, since compare records those only while their baseline is current; a warning names every baseline those lines hold. Tested |
| S6 | Agent 3 | An arm prepared again on other context text made `verdict` refuse forever | Fixed: with run logs the arm's latest preparation picks the text; lines on other text are listed and warned about. Tested |
| S7 | Agents 4, 3 | `record_comparison` trusted that the comparison belonged to the paths | Fixed: the pair mark and benchmark must match, and the line is checked against the files before it is written |
| S8 | Agents 4, 7 | The line dropped compare's warnings and the pair's start and finish times | Fixed: both recorded; `verdict` shows the stored warnings without run logs and compare's current ones with them, and checks offline that a confirmation started after its first pair finished |
| S9 | Agents 5, 4, 3 | The recompute-and-check block was duplicated, `_aa_differences` repeated `_aa_coverage`'s loop, and `_accuracy` copied `compare` | Fixed: `_check_numbers`, `_checked_aa_lines` (used by both), and `compare` uses `_accuracy` |
| S10 | Agent 6 | The record did not exist on main, so the first line was an untracked `.jsonl` that `prepare`'s dirty check ignores | Fixed: an empty record is committed; the tracked-record test requires it |
| S11 | Agent 1 | Re-comparing a published A/A pair on a machine without the run logs now exited 2 | Fixed: nothing is recorded there, and `compare` says so and exits 0. Tested |
| S12 | Agents 4, 5 | `_pair_named` and an inline string re-implemented `_named`; `VERDICT_ROLES` duplicated a literal in `_variant_record` | Fixed |
| S13 | Agent 4 | Duplicate detection matched on the pair id only | Fixed: also on baseline, arm and number. Tested |
| S14 | Agent 3 | Untested branches: several refusals, the unchecked-settings warning, nothing recorded without run logs | Tests added for each refusal and for the no-run-logs message (now once per benchmark) |

## Consider

| Source | Finding | Resolution |
|---|---|---|
| Agent 1 | Gain or loss read from the sign of the difference | Done: read from the interval |
| Agent 3 | Role names read badly ("no first of the variant") | Done: "first pair" and "confirmation" |
| Agent 3 | `_verdict_on` did eight jobs | Split into `_verdict_lines` (no recomputation), then judging in `verdict`; the settings check now runs before the expensive recomputation |
| Agents 3, 6 | "Recorded" printed on a re-compare; only the file name printed | Done: "Already recorded", and the path relative to the checkout |
| Agents 4, 3 | Naming drifted (`ollama-pair-comparison`, `record_comparison`, `compared_at`) | Done: `ollama-pair-verdict`, `record_pair_verdict`, `_check_verdict_results`, `recorded_at` |
| Agents 4, 6 | `verdict` output lacked each pair's `aa_check` and the A/A record digest | Done, and each pair's recorded check must be an accepted check under its conditions |
| Agent 6 | `--sample 0` passed the flag check | Done: numeric flags checked with `is not None` |
| Agents 3, 2 | Malformed lines raise KeyError | Partly: kind and role are checked up front; the A/A record code has the same exposure and is left alone |
| Agents 5, 4 | Shared lock-and-append helper with `record_aa_check`; shared record path helper; repeated key tuples | Key tuples extracted (`_MARK_KEYS`, `_SPLIT_INTERVALS`); the lock block and path helper stay parallel to `record_aa_check` rather than refactoring #137's code |
| Agents 5, 4 | Test helpers repeat `aa_published` and `logged_pair` | Left: `logged_pair` hardcodes pair 1 on LoCoMo and many tests rely on it; the new helper is self-contained |
| Agents 2, 3 | Pin the #118 repeat files' digests | The existing #118 test already pins their verdict counts and intervals, and now also asserts `_sequential_repeat` equals `compare`'s delta for them |
| Agent 6 | Require a matching `compared` event in the run log for each line | Not done: with run logs the counted pairs and ids are already required |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets, local paths in the tracked line | PASS | Paths are relative to the results folder; no messages that can name local paths are copied; pair ids are random |
| Path traversal via tracked lines | PASS | `_recorded_sides` resolves and requires the path inside the results folder |
| Deserialization | PASS | JSON only, `allow_nan=False` |
| Locking | PASS | `flock` on the record file itself, read under the lock, as for the A/A record |
| Paid API or network | PASS | `compare`, `record_pair_verdict` and `verdict` never build a client or call an endpoint |
| Integrity of the record | PASS after M1, S1, S2 | Edited numbers, bootstrap, variant identity, accepted flags and roles are refused |

## Pattern Consistency Assessment

The verdict record follows the A/A record: a tracked JSONL next to it, written
under a lock on the file itself with sorted keys, lines naming published
results with digests, a checker per line (`_check_verdict_results` beside
`_check_aa_results`), and a CI test over committed lines. The CLAUDE.md and
BENCHMARKS.md bullets follow the "A/A record (#137)" bullets.

## Redundancy Check

No new dependency. `_published`, `_recorded_sides`, `_checked_aa_lines`,
`_paired_questions` and `_accuracy` remove duplication that the first version
added or that already existed. `BOOTSTRAP_SAMPLES` names the 2000 that
`compare` used inline.

## Wiring Findings

`verdict` is in the argparse choices, validated after the generic checks, and
dispatched with errors as usage errors. `compare` records with the same data
and results roots it compared with. The tracked record is not ignored by git.
Run from a worktree, the event goes to the shared run log and the line to that
checkout's record, as for the A/A record. CI runs `pytest tests/`, which picks
up the new tests.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "Verdict said `pass`: after a re-baseline or an
Ollama update, a compliant agent flipped a default on a variant that had
already failed, because the verdict only knew about the pairs someone chose to
compare."

| # | Scenario | Trigger / likelihood / impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | A new baseline with unchanged defaults gives a failed variant a fresh count, and the verdict hid the earlier pairs | Re-baseline after a model change; Low-Medium; High | pre-existing (#158), newly hidden | Fix now (detection) + existing #158 | With run logs, `verdict` now repeats `compare`'s current warnings for each recorded pair, including "prepared the same defaults' context text ... weigh their results". Refusing is the #158 product decision |
| 2 | First pair and confirmation under different model identities pass | Ollama update between them; Low-Medium; High | newly introduced | Fix now | `_same_answers` refuses it (S3) |
| 3 | Nothing mechanical ties a flip pull request to a recorded `pass` verdict | Unattended flip PR pastes a verdict; Low-Medium; High | pre-existing | Follow-up #165 | Needs a CI gate over default changes, a contract decision. The tracked-record test now requires every committed confirmation's first pair line to be committed too |
| 4 | Compare's dropped-pair warning was not stored or repeated | Pair given up mid-run; Low; High | newly introduced | Fix now | Warnings stored, and repeated fresh with run logs (S8). Tested |
| 5 | Without run logs the verdict reads a stale baseline | Old A/A pair compared late, or a new baseline with no lines yet; Low; Medium | newly introduced | Fix now + Accept residual | A/A lines no longer set it (S5). A new baseline with no recorded pairs yet is invisible offline; accepted because the flip must cite `checked_against_run_logs: true`, which the output and CLAUDE.md now require |
| 6 | The margin was triggered per benchmark, while the rule says "if either excludes zero" | One-benchmark A/A pair excludes zero; Low; High | newly introduced | Fix now | The owner's decision asked for the margin "exactly as the rule says", and the rule text reads "either"; the stricter reading is implemented and tested |
| 7 | A change to the shared bootstrap code makes every tracked line look edited | Edit to `paired_statistics` or `cluster_statistics`; Low-Medium; Medium, loud | newly introduced (extends #137's pattern) | Fix now (message) + Follow-up #166 | The refusal now says the bootstrap code may have changed. Freezing or versioning the bootstrap for the track is #166 |

Attacks that held (Agent 7): choosing which pairs to compare or relabeling
roles on the answering machine (refused, or `incomplete`); the new `compared`
event (every reader of pair run logs ignores it); a missing LoCoMo interval,
a reused arm name and settings that differ between benchmarks (never `pass`).

Accepted, recorded for later readers:

- Settings for pairs started before #130 are re-derived by the checkout that
  compared them, so the cross-benchmark settings check could refuse such a
  variant after a default flip. The only such variant, `prme-reader-rrf`, was
  superseded.
- A complete first pair that `compare` refuses still holds the first-pair
  slot (`_counted`), so the variant stays `incomplete`. Pre-existing;
  `run-pair` refuses the known causes before answering.
- Two worktrees that compare the same pair each add a line; after a merge the
  tracked-record test's uniqueness check fails loudly.
- `verdict` finds a variant by arm name, so a variant answered under
  different arm names on the two benchmarks stays `incomplete`.

## Follow-ups Raised

- #165: a DeepSeek default can flip without CI checking that the variant's
  recorded verdict passes (scenario 3).
- #166: a change to the paired bootstrap code makes every tracked DeepSeek
  verdict and A/A line look edited (scenario 7).
- Scenario 1's refusal belongs to the existing #158.

## Resolution Status

| Item | Status |
|---|---|
| M1 | resolved |
| S1 to S14 | resolved |
| Consider items | resolved or declined with reasons above |
| Break scenarios 1, 2, 4, 6 | fixed (1: detection; refusal is #158) |
| Break scenario 5 | fixed, residual accepted |
| Break scenarios 3, 7 | follow-ups #165 and #166 (7: message fixed) |
