# Review: issue #137, flagging DeepSeek variant pairs answered outside the recorded A/A check

Branch `issue-137-flag-variant-pairs-outside-aa-check`, base `main`. Seven
review passes (correctness, security, quality, patterns, redundancy, wiring,
adversarial) read the first version of the change. This file records their
findings, what was done with each, and how the adversarial scenarios were
adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - New section "A/A checks (#137)": `_aa_record_path`, `_aa_named`,
    `_pair_conditions`, `_condition_differences`, `_complete_aa_pairs`,
    `_finished`, `_published_aa_pair`, `record_aa_check`, `_finish_time`,
    `_aa_lines`, `_check_aa_record`, `_check_aa_results`, `_aa_listed`,
    `_aa_coverage`, `_aa_warnings`, `_check_aa_ready`, and the constant
    `AA_CONDITIONS`.
  - `run_pair` records the pair id at every start (not only a variant's),
    refuses to start an A/A pair while the A/A record leaves out a complete
    one, refuses to start a variant's pair that no recorded A/A check covers
    on both benchmarks, and adds each published A/A pair to the record,
    saying how to add it by hand when that fails.
  - `compare` takes `results`, refuses a variant's pair outside every
    recorded A/A check, and reports the `aa_check` block and its warnings.
  - `run` and `run_pair` read `RESULTS` when called, as `compare` does.
  - CLI subcommand `record-aa-check --before --after`.
- `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-aa-checks.jsonl`:
  the tracked A/A record, holding the two 2026-09-24 A/A checks
  (LongMemEval-S pair 5 and LoCoMo pair 3 of `prme@46647825`), added with
  `record-aa-check`.
- `tests/test_gpt54_baselines.py`: A/A helpers (`aa_published`, `aa_logged`,
  `aa_checked`, `harness_conditions`, `harness_aa_checked`, `aa_record`,
  `published_paths`, `variant_results`), eight new tests, and existing variant
  tests given the A/A check they now need (`pair_arms` records it).
- `CLAUDE.md`: the default-change rule (A/A check, A/A record, current state,
  and what a flip pull request lists).
- `BENCHMARKS.md`: before-running note on Ollama updates, the A/A record
  bullet, the `record-aa-check` command, the `compare` paragraph and the A/A
  check section.

## Approach Summary

Nothing tied a variant pair to the A/A check that covers it: `same_model`
ignores the Ollama server version on purpose, and `compare` checked server
versions only within one pair. The owner decided on the issue that `compare`
must refuse a variant pair that falls outside the recorded A/A check (model
identity, server version, context budget, failure policy, answer settings),
and that a new server version needs a new A/A pair on both benchmarks before
further variant pairs count.

1. **Record.** Every published A/A pair gets one line in a tracked,
   append-only JSONL file: its conditions, published result paths and
   digests, whether `compare` accepted it, its interval, and whether it is the
   first accepted A/A pair on its benchmark under those conditions.
2. **Enforcement.** `compare` refuses a variant's pair unless an accepted A/A
   check under the same five conditions is recorded on both benchmarks, and
   `run-pair` answers no variant pair it would refuse.
3. **Integrity.** The record must list exactly the A/A pairs the private run
   logs show complete, in finish order, and each line must match its
   published results, so no A/A pair can be left out, redrawn out of sight or
   moved to other conditions.

Alternatives considered, with the evidence for rejecting each:

- Adding `server_version` to `_IDENTITY_KEYS` (`ollama_answers.py:59-62`).
  Rejected: calibration (`_passed_calibration`), arm binding
  (`_bind_answer_model`) and pair resumption would all refuse after any
  Ollama update, and it still would not tie a variant pair to an A/A check.
- Deriving A/A checks from the private run logs alone. Rejected: the pair
  `started` events record only a hash of each side's answer model (see the
  run logs under `data/ollama-answers-v1/.../runs/pairs/prme@46647825/`), so
  identity, settings and intervals are not recoverable once a pair folder is
  moved aside, which `BENCHMARKS.md` documents doing; and the owner and the
  issue ask for a record that is reviewable in a pull request.
- A warning instead of a refusal. Rejected by the owner's decision on the
  issue.
- Refusing when the A/A check excludes zero. Rejected: the rule in
  `CLAUDE.md` still allows the test then, with an extra margin; `compare`
  warns and #146 covers applying the margin.

Shared state: the new tracked record under `benchmarks/results/research/`
(written by `run_pair` and `record-aa-check`, read by `compare` and
`run_pair`), and an `id` in every pair `started` event in the private run
logs (read by `_pair_states`; the variant logic already handled it). No
`src/` file changed, so no production default changed and the evidence gate
does not apply.

## Must Fix

- M1 (correctness): two complete A/A pairs missing from the record on one
  benchmark could never be added, since each recording required the other to
  be listed first, and `compare` would then refuse every variant pair.
  Resolved: a recording needs only the complete pairs that finished before it
  to be listed, and the run logs are read under the record's lock. Tested.
- M2 (wiring): the record file must be committed with the change. Resolved in
  the commit.

## Should Fix

- S1 (quality, wiring, correctness, adversarial 1): a failed record step after
  publishing raised a bare error, and a stale record was found only after a
  full A/A pair was answered. Resolved: `run_pair` checks the record before an
  A/A pair starts, and a failure after publishing says the pair is published
  and prints the `record-aa-check` command. Tested.
- S2 (security, correctness): `refused` kept `compare`'s message, which can
  name local paths, and any passing error became a permanent refusal.
  Resolved: only a pair its run log records complete but invalid is recorded
  as refused, with the run log's reason; any other refusal records nothing
  and is raised. Tested.
- S3 (security, adversarial 5): `compare` trusted each record line as
  written. Resolved: every line on each benchmark must name published results
  under the checkout with the recorded digests, the same pair id, and the
  conditions those results record (`_check_aa_results`). Tested.
- S4 (correctness, adversarial 8): a variant pair answered before any A/A
  check under its conditions still took the variant's first pair or
  confirmation slot (#130) while `compare` refused it. Resolved: `run_pair`
  refuses to start it (`_check_aa_ready`). Tested.
- S5 (adversarial 2): the tracked-record test pinned exactly two lines.
  Resolved: it checks every line against its published results, finish order
  and first flags, and pins only the first two.
- S6 (correctness): the tracked record makes the tree dirty until its new
  line is committed, and `prepare` needs a clean tree. Resolved: documented in
  `BENCHMARKS.md` and `CLAUDE.md` (commit each line with its pair's results).
- S7 (patterns, redundancy): a copy of `_run_events`, names built in five
  places, `interval_excludes_zero` and `changed_verdicts` computed again,
  `_track_data` computed twice, the `--after` help and the `compare` usage
  message made wrong, refusals without `(#137)`, "DeepSeek" in one message,
  and `run`/`run_pair` binding `RESULTS` at import. Resolved: `_run_events`
  and `_aa_named` are reused, the values come from `compare`'s `repeat`
  block, the texts are fixed, and both functions read `RESULTS` when called.
- S8 (quality, wiring, patterns): one test undid the autouse fixture that
  keeps tests off the main checkout's private data; `pair_published` and
  `published_paths` repeated one glob; the record path was spelled out in
  tests. Resolved.
- S9 (quality): one refusal message covered four failures. Resolved:
  duplicates, run-log mismatches and order have their own messages.
- S10 (quality): untested branches. Resolved: `compare` refusing a recorded
  pair, the unknown benchmark, the CLI's value errors and an explicit
  `results` are covered.

## Consider

- The stored `first` flag repeats what `compare` derives, and a later change
  to condition matching could make old flags inconsistent (redundancy,
  correctness C6). Kept: the issue asks for it in the record, and a mismatch
  fails closed, which is the safer outcome in a repository that merges on
  green CI; a change to the conditions would have to migrate the record.
- Recording runs `compare` with per-category bootstraps it does not keep
  (quality). Accepted: about a second on LoCoMo, next to hours of answering.
- A record line's `difference` and interval are taken as written by
  `compare` (they are recomputed in CI for committed lines). Accepted here;
  #146 covers recomputing them with the margin.
- Test helper overlap with `logged_pair` and the parametrized CLI test
  (patterns). Left: the A/A helpers need a benchmark, a number and a data
  root that `logged_pair` hardcodes.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or local paths in the record | PASS | Relative result paths, loopback endpoint, no failure messages; `refused` holds only the run log's reason (S2) |
| Path traversal | PASS | Benchmark names are checked before any path; result paths must resolve under `results`, when recorded and when read back (S3) |
| Unsafe deserialization | PASS | `json.loads` only |
| Reaching a paid API | PASS | The new code only reads and writes files |
| Test isolation | PASS | The autouse fixture also redirects `RESULTS`; the one test that undid it is fixed (S8) |
| Tamper resistance of the record | FAIL, then fixed | S3 |

## Pattern Consistency Assessment

The record line (`kind`), refusals ending in the issue number, the `note` on
the `compare` block and the section layout follow the #127 and #130
machinery. After S7 the JSONL reader, naming helper and `repeat` values are
shared instead of copied, and `results` is read at call time everywhere.

## Redundancy Check

`_aa_entries` was removed in favor of `_run_events`; `published_paths` now
backs `pair_published`. The `record-aa-check` command stays: it recorded the
2026-09-24 pairs and is the recovery path when `run_pair` cannot record one.

## Wiring Findings

The CLI registers `record-aa-check`, `_check_args` refuses other flags, CI
runs the new tests, and no test writes into the real results folder. The
backfilled lines match the published results and the run logs. The
`prme-reader-rrf` variant pair being answered on this machine at Ollama
0.34.3 is covered by the recorded checks, so `compare` on this branch accepts
it; compare it from a checkout with this change, not from the worktree that
answers it.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "The guard held, but the first Ollama update
left every variant compare blocked by a per-checkout record and a pinned
test, and the quick hand edit to the JSONL, which compare never re-checks
against the published results, is what would let a default flip through."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | The record is per checkout while the run logs are shared, so a stale record blocks compares and a failed record step strands a published pair | Newly introduced | High | Medium | Fix now | `run_pair` checks the record before an A/A pair starts, says how to recover after publishing, and records need only earlier pairs listed (M1, S1); the copy-then-record path is documented |
| 2 | The tracked-record test pins exactly two lines and breaks on the first new A/A pair | Newly introduced | High | Medium | Fix now | The test now checks every line (S5) |
| 3 | The extra margin after an A/A check that excludes zero is only a warning and is never computed | Design choice | Low-Medium | High | Follow-up (#146) | Needs the #118 repeat recorded and a decision on whether the margin refuses |
| 4 | `compare` run from a checkout without #137 accepts a variant pair silently | Pre-existing | Medium-Low | High | Accept | Old code cannot be changed; the flip pull request must now list `compare`'s `aa_check` block (`CLAUDE.md`), whose absence shows old code |
| 5 | Hand-edited record fields are trusted | Newly introduced | Low | High | Fix now | Each line is checked against its published results' digests and conditions (S3) |
| 6 | The first pair and the confirmation can run under different model identities, each covered by its own A/A check | Pre-existing | Low-Medium | High | Follow-up (#147) | Needs an identity digest in each pair start; `CLAUDE.md` keeps the hand check until then |
| 7 | An A/A pair given up before it completes stays out of sight | Newly introduced (the gap; given-up pairs existed) | Low | High | Follow-up (#148) | Mirrors #130's `dropped` list; contained, but a new output block with its own rules |
| 8 | A variant pair outside every A/A check still takes the variant's first-pair slot | Newly introduced | Low-Medium | Medium | Fix now | `run_pair` refuses to start it (S4), which closes it for every pair answered from a checkout with this change; the diff made the refusal, so the fix belongs here |

Scenarios Agent 7 attacked that held: getting a variant past the gate under
a name `is_variant` rejects (the CLI forces `prme-<name>`), corrupting the
record through merges, duplicates, foreign pairs or reordering (all refused),
and concurrency, resumption and records written under the old rules.

## Follow-ups Raised

- #146: `compare` warns when an A/A check excludes zero but never applies the
  A/A margin to a variant's gain (scenario 3).
- #147: a variant's first pair and confirmation can run under different model
  identities without `compare` noticing (scenario 6).
- #148: an A/A pair given up before it completes stays out of sight next to
  the A/A check (scenario 7).

## Resolution Status

| Item | Status |
|---|---|
| M1, M2 | Resolved |
| S1 to S10 | Resolved |
| Consider items | Kept or accepted, as recorded above |
| Adversarial 1, 2, 5, 8 | Fixed |
| Adversarial 3 | Follow-up #146 |
| Adversarial 4 | Accepted |
| Adversarial 6 | Follow-up #147 |
| Adversarial 7 | Follow-up #148 |
