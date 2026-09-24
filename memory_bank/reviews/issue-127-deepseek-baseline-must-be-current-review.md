# Review: issue #127, pairing DeepSeek variants only with the current defaults baseline

Branch `issue-127-deepseek-baseline-must-be-current`, base `main`. Seven
review passes (correctness, security, quality, patterns, redundancy, wiring,
adversarial) read the change. This file records their findings, what was done
with each, and how the adversarial scenarios were adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - New `_complete_baselines` and `current_baseline`: the baselines of the
    defaults whose own answer run is complete, in the order those runs
    finished; the last is the current baseline. The commit is looked up
    without raising, so one old record cannot break the ordering.
  - New `_recorded_commit`, shared by `_complete_run_commit` and
    `_complete_baselines`, so both take the commit from the same `finished`
    event.
  - New `_descends` (git `merge-base --is-ancestor`, full commit names only,
    a clear error without git) and `_check_descends`: a baseline not yet
    recorded must be at a commit that descends from every complete baseline's
    commit on either benchmark. `baseline_arm` calls it for the first baseline
    and for later ones, so `prepare` and the CLI `prepare prme` and `run prme`
    refuse an older commit. `run` calls it inside the arm lock, after the
    already-complete check and never for a sample, for a baseline answered
    directly.
  - New `_check_current_baseline`: the before side of a pair must be the
    current baseline, and in `compare` it must also have read contexts
    prepared at that baseline's recorded commit. `run_pair` calls it before
    any pair is opened, the CLI `run-pair` calls it first so a stale
    `--baseline` is a usage error, and `compare` calls it unless the two
    results are a repeat of the defaults.
  - `run_pair` checks the current baseline again when the pair finishes. If a
    newer baseline completed meanwhile, the pair is kept, its `finished` event
    names the reason, and nothing is published.
  - `compare` takes an optional `data` root, checks the benchmark a result
    names before it reaches a path, and reports a `baseline` block (every
    complete baseline and the current one; `null` on the GPT-5.4 track).
  - Module docstring, `baseline_arm`, `run_pair` and `compare` docstrings, the
    `--baseline` help and the run-pair usage message.
- `tests/test_gpt54_baselines.py`:
  - New tests: the ordering of complete baselines (samples, incomplete runs,
    pair logs, legacy records and bad finish times); the descent check across
    both benchmarks; `_descends` itself against a throwaway git repository
    (full names only, a tag named like a short commit, git missing); the `run`
    guard (and that a sample is not refused); `run_pair` and `compare` against
    the current baseline, a mismatched commit, a baseline that completes
    mid-pair, missing run logs, an unknown benchmark and the repeat exemption;
    the CLI `run-pair` refusal.
  - Older tests record a current baseline, pass the harness data root to
    `compare`, or give the ancestry of their fake commits. `recorded_baseline`
    now records the manifest's commit and can name a later baseline, and
    `answered` can place a run's finish in time.
- `BENCHMARKS.md`: DeepSeek step 2, the `--baseline` bullet, a new bullet on
  where a new baseline may be recorded, the `compare` paragraph (including
  that a pair can be compared again only while its baseline is current) and
  the paragraph after the baseline table (which also answers point 3 of the
  issue).

## Approach Summary

After #129, the before side of every variant comparison on the DeepSeek track
is a pair's fresh defaults run, and the pair's `--baseline` only supplies the
defaults' prepared contexts. The free choice the issue describes therefore
sits in two places: which commit a new baseline may be recorded at, and which
recorded baseline `run-pair` and `compare` accept.

1. **Where a baseline may be recorded.** `baseline_arm` named a later
   baseline for any commit other than the first baseline's, and `_on_main`
   accepts any ancestor of `origin/main`. Now a new baseline must be at a
   commit that descends from every complete baseline's commit, on both
   benchmarks. With only one later baseline allowed to be pending at a time,
   the order in which baselines complete then matches the order of their
   commits.
2. **Which baseline counts.** The current baseline is the one whose own answer
   run completed last on that benchmark. `run-pair` refuses any other, so no
   Ollama time goes into a pair that `compare` would refuse (the #125
   pattern). `compare` reads the track's run logs when it runs and refuses a
   pair whose before side is not current, so a pair answered before a newer
   baseline completed (for example before a default flip) no longer counts.
   Repeats of the defaults are exempt, since the #118 repeat compares an
   earlier baseline with a later one by design.
3. **Point 3 of the issue** (should the #118 repeat count as a baseline): it
   does. It holds the defaults prepared at a newer commit on `main`, the
   owner's A/A check (#138) was answered against it, and `BENCHMARKS.md`
   already named it the most recent complete baseline. The concern behind the
   question, a baseline's own favorable draw serving as the before side, was
   closed by #129: `compare` pairs a variant only with the fresh defaults run
   answered alongside it, never with a standalone baseline run.

Alternatives considered:

- Recording which baseline was current in each pair's record and checking
  only that in `compare`. Rejected as the sole mechanism: a pair answered
  against pre-flip defaults would still pass after the post-flip baseline is
  recorded, which is the case the issue asks `compare` to refuse. `compare`
  took two result dicts only (`gpt54_baselines.py`, `compare`), and nothing in
  a result can show a later baseline.
- Ordering baselines by git ancestry inside `compare`. Rejected: it needs every
  baseline commit's git objects wherever `compare` runs, CI checkouts are depth
  1 (`.github/workflows/ci.yml` uses `actions/checkout` without
  `fetch-depth`), and with the descent check the completion order already
  equals the ancestry order.
- Warning instead of refusing. Rejected: the owner's decision on #137 is that
  a rule only written down is not enough protection in a repository whose pull
  requests merge on green CI, and #125 showed that an always-on warning gets
  ignored.
- A separate arm kind for the #118 repeat (point 3). Not needed, for the
  reasons above, and it would orphan the recorded A/A check.

Shared state: the change reads the private run logs and prepared manifests
under the main checkout's `data/ollama-answers-v1/<track>/`, and runs read-only
git queries. It writes nothing new except one extra `finished` reason for a
pair whose baseline was superseded, in the pair's existing run log. No `src/`
file changed, so no production default changed and the evidence gate does not
apply.

## Must Fix

None.

## Should Fix

- S1 (quality, patterns, correctness, wiring): the descent check also ran for
  a baseline that was already complete, so `run prme` at an older complete
  baseline's commit, and a sample of it, were refused with "cannot be the
  newest baseline" instead of the real reason. Resolved: the check returns
  early for a complete baseline, and in `run` it runs inside the lock after
  the already-complete check and never for a sample. Tests cover both.
- S2 (correctness, quality, patterns, redundancy): `_complete_baselines` took
  the finish time from the first complete event and the commit, through
  `_complete_run_commit`, from the last, reading each log twice. And a legacy
  record whose manifest is gone made it raise, breaking every `compare` and
  `run-pair` on that benchmark. Resolved: `_recorded_commit` reads the commit
  from the same event without raising; only the descent check refuses a
  baseline with no recorded commit.
- S3 (security): the result's `benchmark` reached filesystem paths and glob
  patterns unchecked. Resolved: `compare` refuses an unknown benchmark first.
- S4 (security, quality): `_COMMIT` accepted short names, which git resolves
  as a branch or tag first. Resolved: full SHA-1 or SHA-256 names only; the
  test tags a short name to prove it.
- S5 (wiring, quality): a stale `--baseline` gave a traceback from
  `run-pair`, after the model announcement. Resolved: the CLI checks it first
  and reports a usage error.
- S6 (quality): the error messages for a missing commit and for no baselines
  on record gave no next step. Resolved.
- S7 (correctness, wiring): `BENCHMARKS.md` said `run-pair` and `compare`
  refuse the first baseline outright; `compare` still accepts it in a repeat.
  Resolved, and step 2 no longer says "refuse any other" without that
  exception.
- S8 (patterns): `_not_current` returned a message where the module's shared
  guards raise. Resolved: `_check_current_baseline` raises, and `_check_newest`
  is now `_check_descends`.
- S9 (patterns, redundancy): `before_is_current` in the `baseline` block was
  derivable from `arms.before` and `baseline.current`, and the helper built
  the block's note unlike the other blocks. Resolved: the helper returns the
  list, and `compare` builds the block with its note.

## Consider

- Done:
  - `git` missing from the path now gives a `ValueError` that names the cause
    (quality, correctness).
  - The "cannot tell" message mentions fetching the full history of a shallow
    clone (quality).
  - A finish time without a time zone, or no finish time, is refused with a
    clear message instead of a `TypeError` or `KeyError` (security,
    correctness).
  - `complete_baselines` is private, like the other record readers (patterns).
  - The `compare` docstring keeps the repeat exception next to the pairing
    rule (patterns); "current baseline" is used only in the new sense in the
    changed text (patterns).
  - The test that compared a dict with a copy of itself asserts fields
    directly, and the `answered` docstring is reflowed (quality).
  - `BENCHMARKS.md` lines are rewrapped (patterns, quality).
- Not done, with reasons:
  - One shared runner for `_descends` and `_on_main` (patterns, redundancy).
    Their error contracts differ, and changing how `_on_main` reports a
    missing `origin/main` is outside this issue.
  - `functools.cache` on `_descends` and a subprocess timeout (quality,
    correctness). At two or three baselines the extra git calls are
    negligible, and `_on_main` has no timeout either.
  - Editing `CLAUDE.md:94` ("against a recorded defaults baseline") (wiring).
    The sentence is still true, since the current baseline is a recorded one,
    and the rule text is the owner's; `BENCHMARKS.md` carries the detail.
  - Recording the complete baselines in each pair's record (patterns,
    correctness). With the finish-time recheck, a published pair was answered
    entirely while its `before` arm was current, so the record would repeat
    `pair.before`. See scenario 2 for the replay question.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Option injection into git | PASS | List arguments, fixed `cwd`, and only full hexadecimal commit names reach git (tightened in S4) |
| Git failure handling | PASS | Exit codes other than 0 or 1, and git missing, refuse with a `ValueError` |
| Path traversal via `model` | PASS | The track name keeps only `[a-z0-9.-]` after a fixed prefix |
| Path traversal via `benchmark` | PASS after S3 | Unknown benchmarks are refused before any path is built |
| Arm names reaching paths | PASS | Later baseline names must match `prme@[0-9a-f]{8}` |
| Unsafe deserialization | PASS | JSON only |
| Secrets, PII or local paths in output | PASS | The `baseline` block holds arm names, commits and finish times; messages go to stderr |
| Paid path | PASS | Every new check is on the Ollama track or in `compare`, before any client is built |
| Tests | PASS | The git test uses its own repository in `tmp_path`; the rest stub `_descends` |

## Pattern Consistency Assessment

The new guards follow `_check_rule_budget` and `_check_pair_baseline`: they
raise `ValueError` with the issue number and a next step, `run_pair` applies
the same check `compare` does before any pair is opened, and the CLI turns it
into a usage error as it does for `prepare` and `run`. The git helper follows
`_on_main` (local `import subprocess`, `cwd=study.ROOT`). The record readers
follow `_later_baselines` and `_complete_run_commit` and share one commit
lookup. The superseded-baseline ending of a pair follows the server-version
ending: the pair is kept, its `finished` event names the reason, and nothing
is published. The `baseline` block follows the nested-block-with-note shape of
`contexts` and `repeat`.

## Redundancy Check

- No new dependencies. No existing "current baseline" or git-ancestry helper
  to reuse; the similar wrappers in other benchmark runners are copies, not a
  shared utility.
- `_recorded_commit` removes the duplicated commit lookup and the second read
  of each run log.
- Kept deliberately: `run_pair`'s earlier "no complete answer run of its own"
  check (a clearer message than the current-baseline one), the check in `run`
  (it guards direct calls, which bypass `baseline_arm`), and `current_baseline`
  next to `_complete_baselines` (it names the concept the docs use).

## Wiring Findings

- Every command that picks or accepts a baseline reaches the new checks:
  `prepare` and `run` through `baseline_arm` (CLI) and directly, `run-pair`
  in the CLI and in `run_pair`, and `compare`. `estimate` does not apply.
- `compare` from any worktree finds the main checkout's run logs through the
  fixed `study.ORIGINAL`; git ancestry works from a worktree because worktrees
  share one object store.
- On the real data (read only), the current baseline is `prme@46647825` on
  both benchmarks, HEAD would be `prme@0657371d`, the parents of `46647825`
  are refused, and the published A/A pairs and #118 repeats compare with no
  warnings.
- CI runs `pytest tests/` on a depth-1 checkout; the new tests need neither
  the private data nor old commits.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "Default flipped on a redrawn pair: the
post-flip baseline voided the failing history, and `compare` could no longer
replay the merged evidence."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | A variant loses its pair, a new baseline is recorded, and the variant is paired again at pair 1 with no earlier pairs listed, because pair logs and `earlier_pairs` are kept per baseline | Pre-existing (#130 already notes that another baseline restarts at pair 1); the old refusal message pointed at the redraw | Medium | High | Follow-up (#130) plus a message fix now | The message no longer suggests answering again and says earlier pairs stay on record for the pull request to list. Listing pairs across baselines is the verdict step #130 proposes, and default flips stay blocked until #130 is done |
| 2 | After a flip, the post-flip baseline makes `compare` refuse the merged flip's own pairs, so that evidence cannot be recomputed, and it cannot be recomputed on a machine without the run logs | Newly introduced | High | Medium | Accept, documented | This is the behavior the issue asks for. Alternatives rechecked: a recorded snapshot plus a warning reopens the stale-defaults case the issue closes; a replay flag would be a bypass switch in a repository that merges on green CI; recording which baseline was current does not restore replay under strict checking. `BENCHMARKS.md` now says to keep `compare`'s output with the pull request before a newer baseline is recorded, and the result files still verify |
| 3 | The current baseline of one benchmark can hold older defaults than the other's (a new baseline completed on LoCoMo, not on LongMemEval-S), or a stale worktree records an older baseline on the other benchmark | Pre-existing gap, and the new docs overstated "newest defaults" | Medium | Medium to High | Fix now (partly) and Follow-up (#139) | Fixed now: the descent check spans both benchmarks and the first baseline too, and the docs say "newest recorded". A variant prepared at a newer commit than its benchmark's current baseline is #139's scenario: its fix splits the variant's own context changes from other code |
| 4 | A newer baseline completes while a pair is being answered; the pair publishes a result `compare` will refuse and counts as neither pass nor fail | Newly introduced | Low to Medium | Medium | Fix now | `run_pair` checks the current baseline again at the finish and handles it like a server-version change. Tested |
| 5 | A baseline prepared and answered through the library from a branch commit becomes current, and after a squash merge no commit on `main` descends from it, so every later baseline is refused | Pre-existing (the library path has no on-main check); the descent check makes the blocking new | Low | High | Follow-up (#141) | Closing it needs the on-main check in the library path, which needs a decision on how tests prepare baselines from feature branches |
| 6 | `compare` matched the current baseline by arm name only, so a result from another data root with the same name passed | Newly introduced | Low | Medium | Fix now | The before side must also have read contexts prepared at the current baseline's recorded commit. Tested |
| 7 | A baseline whose own complete run is invalid under the 1% rule (#132) still counts as complete and can become current | Pre-existing definition of complete | Low | Low | Accept | Pairs answer the defaults afresh, so the baseline's own answers never enter a variant's comparison. Excluding it would force another 40-minute baseline run for nothing |
| 8 | The first baseline's old run logs rely on their manifest for the commit; if that folder is moved aside, the commit is unknown | Pre-existing for `baseline_arm` | Low | Low | Accept (with S2) | Ordering and `compare` no longer need it (S2); only recording a new baseline refuses, loudly, with a next step |

Scenarios Agent 7 attacked that held:

- Clock skew and identical timestamps: only one later baseline can be pending,
  the first must complete first, and times are UTC with ties broken by name.
- Using the repeat exemption for a variant: a repeat needs both sides to be
  baselines with the same context text, and baselines refuse overrides.
- Existing data and published results: both benchmarks go `prme` then
  `prme@46647825` in a straight line, and the published A/A pairs and #118
  repeats still compare.

## Follow-ups Raised

- #130 (comment): pairs answered against an earlier baseline drop out of the
  history a new baseline's pairs report (scenario 1).
- #139 (comment): a benchmark's current baseline can hold older defaults than
  a variant prepared after a flip, when the new baseline has completed on only
  one benchmark (scenario 3).
- #141: a defaults baseline prepared through the library from a branch commit
  can become current and block every later baseline (scenario 5).

## Resolution Status

| Item | Status |
|---|---|
| S1 to S9 | Resolved |
| Consider (done items) | Resolved |
| Consider (not done) | Recorded above with reasons |
| Scenario 1 | Message fixed; remainder in #130 |
| Scenario 2 | Accepted and documented |
| Scenario 3 | Partly fixed; remainder in #139 |
| Scenario 4 | Fixed |
| Scenario 5 | Follow-up #141 |
| Scenario 6 | Fixed |
| Scenarios 7 and 8 | Accepted |
