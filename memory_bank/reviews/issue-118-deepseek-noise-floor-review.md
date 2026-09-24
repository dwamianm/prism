# Code Review: issue-118-deepseek-noise-floor

## Files Changed
- `benchmarks/integrations/gpt54_baselines.py`: `compare()` recognizes a repeat of the defaults and reports
  it in a `repeat` block (`changed_verdicts`, `interval_excludes_zero`, `note`; `None` for any other pair).
  New `_same_inputs()` and `_server_version()` helpers and an `ANSWER_MODULES` tuple. `compare()` also
  refuses the same answer run on both sides, names the model identity in its mismatch refusal, warns when
  the Ollama server version differs, and warns when two baselines are not shown to be a repeat. The
  module docstring, `baseline_arm`'s docstring and the `--after` help mention the repeat.
- `tests/test_gpt54_baselines.py`: an `answer_result` helper and unit tests for the repeat, the pairs that
  are not a repeat, the same-run refusal, the identity refusal and the server version warning; a CLI
  success path for `compare`; the published-baseline test now also covers the repeat arm; a test that
  pins the published A/A numbers.
- `benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-{locomo,longmemeval}-result.json`:
  the published repeat results.
- `BENCHMARKS.md`: step 3 (confirmation run, the floor and its current state), a repeat `compare`
  example, the `compare` paragraph, the repeat row, which baseline is current, and a new "Run-to-run
  floor" section.
- `CLAUDE.md`: the epic #77 default-change rule gains the confirmation run, model identity and run-to-run
  floor conditions the owner decided in #118, plus the current state (#129).

## Approach Summary
The owner's decision in #118: answer the defaults a second time on both benchmarks with the same model
identity and settings as the #126 baseline, report the A/A paired difference, interval and changed
verdicts, and add the confirmation-run and model-identity rules to `CLAUDE.md`. The owner named #123 as the
prerequisite "so that the defaults arm can be prepared again".

Premise: #123's later baseline (`prme@<commit>`) is the supported way to answer the defaults again. At
`46647825` (main, no `src/` change since the first baseline's `97c9402f`) it reads the same context text,
so pairing it with the first baseline measures run-to-run variation alone. I prepared and answered it in
a clean worktree at `origin/main`, so edits on this branch could not reach its provenance or module
digests.

`compare` shows the pair is a repeat when both results are baselines and `_same_inputs` holds: the same
budget, both preparations reproducing every saved 2026-09-23 context (row hashes cannot show identical
text, #125), the same `ANSWER_MODULES` digests and the same Ollama server version.

Alternatives considered:
- A dedicated repeat arm that copies the baseline's contexts: rejected. `prepare` accepts a `prme` arm only
  from a commit on `main` (`gpt54_baselines.py`, CLI `prepare`), so the new arm could not be prepared until
  this change merged, and it would duplicate #123's mechanism, which the owner named.
- A variant with a no-op `--set`: rejected. `_check_overrides` requires a real override, and #123 lists it
  as evidence-damaging.
- Per-row context text hashes in published results: deferred to #125. They cannot cover the #126 baseline,
  whose published receipt is never rewritten, while the saved-run match proves identical text for both
  sides today.
- Reusing `summarize_multi_run` (`benchmarks/scoring.py`): different schema; `paired_statistics` already
  returns wins, losses and ties.

Shared state touched: private arm folders and run logs for `prme@46647825` under
`data/ollama-answers-v1/<track>/` (gitignored, append-only); two new published results; the `compare`
output gains a `repeat` key. After the repeat, `prme@46647825` is the most recent complete baseline, so a
later `prepare prme` at another commit files `prme@<commit>` as before. No retrieval, packing or
representation code changed, so the evidence gate does not apply. The only model calls were the two
DeepSeek answer runs through local Ollama, which the epic rules allow.

## Measured result
| Benchmark | First baseline | Repeat | Paired difference, 95% interval | Changed verdicts |
|---|---:|---:|---:|---:|
| LongMemEval-S | 423/500 | 434/500 | +2.2 points, +0.4 to +4.2 | 23 (17 gained, 6 lost) |
| LoCoMo | 1,014/1,540 | 1,007/1,540 | -0.45 points, -1.43 to +0.52 | 61 (27 gained, 34 lost) |

The LongMemEval-S interval excludes zero, so under the owner's rule no default changes until the paired
test is revised. Raised as #129.

## Must Fix
1. The "Run-to-run floor" section was a placeholder while `CLAUDE.md` pointed at it (Agents 2, 3, 4, 5, 6).
   Resolved: filled with the measured numbers, and a test pins the changed verdicts and the
   excludes-zero flag of the published pair.

## Should Fix
1. The confirmation-run text overstated what it covers; it redraws the variant, not the baseline
   (Agent 3). Reworded in `CLAUDE.md`.
2. "The floor holds" was undefined (Agent 3). Defined in `CLAUDE.md` and `BENCHMARKS.md`: the A/A interval
   includes zero on both benchmarks.
3. `--after` help and the docs example assumed a variant (Agents 3, 4, 6). Help updated; a repeat example
   added.
4. Which baseline is current once the repeat completes was unclear (Agents 1, 6). `BENCHMARKS.md` now says
   the repeat is the most recent complete baseline and later variants pair with it.
5. Tests for the published repeat and the CLI success path (Agent 6). Added.

## Consider
Taken:
- `is_repeat` duplicated the baselines check and was public only for tests (Agents 3, 4, 5): replaced by a
  private `_same_inputs` and one `both_baselines` flag in `compare`.
- `agreement` duplicated `accuracy.ties / accuracy.queries` (Agent 5): removed. `changed_verdicts` stays,
  because it is the number the owner asked for.
- `repeat` now always present (`None` when not a repeat) for a stable output shape (Agent 3).
- The mismatch message now covers settings as well as identity and ends with a period (Agents 3, 4).
- Tests: order-independent warning checks, the same-run refusal in its own test, a negative interval
  that excludes zero, `identity is not None` in the helper (Agent 3).

Not taken:
- Catching `KeyError`/`TypeError` for malformed input files in the CLI (Agent 2): pre-existing and local
  only; the inputs are the harness's own results.
- Renaming `changed_verdicts` to `disagreements` (Agent 4): the issue and the owner use "changed verdicts".
- An excludes-zero verdict on every comparison, and conversation-level intervals (Agent 4): these change
  the default-change test itself, which #129 now has to revise.
- Checking that both preparations used the same saved-run archive (Agent 1): low risk; `--archive` is a
  manual override and both results record the same registration.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or personal data in output and published results | PASS | No paths, usernames, email or keys; failure messages are stripped before publishing |
| Private records | PASS | `data/` is gitignored |
| Path handling of `--before`/`--after` | PASS | Read-only, user-supplied, nothing written |
| Deserialization | PASS | `json.loads` only |
| Network | PASS | `compare` makes no calls; answer runs stay on the loopback Ollama endpoint |
| Paid API reachable | PASS | No client is built on the `compare` path |
| `CLAUDE.md` rule changes | PASS | Only add requirements; the paid-API ban is unchanged |

## Pattern Consistency Assessment
The new predicate and helpers follow the module's naming and the `note` convention of other result
blocks. `paired_statistics` and bootstrap seed 42 match the existing comparisons. The one structural
divergence (Agent 4): other A/A analyses in `benchmarks/diagnostics/` compare per-question input hashes;
this one relies on the saved-run match because the published rows cannot show text identity (#125).

## Redundancy Check
No new dependency or duplicate helper. `answer_result` has no existing equivalent (earlier compare tests
build results through full harness runs). The redundant `agreement` field and the public `is_repeat` were
removed.

## Wiring Findings
The CLI `compare` path prints the `repeat` block; no other caller of `compare` or its old error text
exists. CI runs `pytest tests/`, and the new tests need no network, Ollama or private data. File names with
`@` work in git, markdown links and CI runners. The `#run-to-run-floor` anchor matches its heading.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Default flipped on a DeepSeek 'gain' that was really an Ollama
auto-update: the variant and its confirmation both ran on the new server, the baseline ran on the old one,
`compare` saw the same identity, and the floor had been measured two hours apart."

| # | Scenario | Introduced | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | Drift between the baseline run and later variant runs is invisible: `same_model` ignores the server version, weights are unpinned, and a confirmation pairs with the same old baseline | New premise gap on pre-existing identity rules | Medium | High | Fix now (part) + Follow-up #129 | `compare` now warns on a different server version and a repeat requires the same one; `CLAUDE.md` says a matching identity does not prove the same weights. Redrawing the baseline next to each variant and logging the live server version in `started` events change the test design, which #129 must decide |
| 2 | The confirmation run can be shopped for by preparing more arms with the same settings | New | Medium | High | Fix now (rule) + Follow-up #130 | `CLAUDE.md` now says the confirmation is the first fresh run, a failure fails the variant, and the PR lists every arm with those settings. Harness enforcement needs a new `prepare` option and a `compare` report |
| 3 | The floor is recorded once and not tied to an identity; repeat detection stops after the first default flip; the placeholder could ship | New | Medium | Medium | Fix now + Follow-up (existing #125) | `CLAUDE.md` ties the floor to the identity and says to measure it again; the section is filled and a test pins it. Detection after a flip needs per-question text hashes (#125) |
| 4 | A repeat silenced the different-commits warning on context text alone, so a change to the answering code would read as noise | New | Low | Medium | Fix now | `_same_inputs` now also requires equal `ANSWER_MODULES` digests and the same server version |
| 5 | The floor rule rests on one A/A draw per benchmark: about a 10% chance of blocking with nothing wrong | New (owner's rule) | Low to Medium | Low, fails safe | Accept | It is the owner's protocol and it fails safe (it blocks flips). It has now fired on LongMemEval-S; whether one draw is enough is part of the revision in #129 |

Attacks that failed (Agent 7): the same-text premise for this pair (every context matches the saved run on
both sides; identical identity and server version; only `gpt54_baselines.py` changed between the
commits); faking a repeat by copying answers between arms (`_verified_row` binds each row to its own
capture); getting past the same-run refusal (published and private copies of one run have identical rows).

## Follow-ups Raised
- #129: The DeepSeek paired test fails its own A/A check on LongMemEval-S, so no default can change under
  the current rule (adversarial 1 and 5, and the measured result).
- #130: Nothing ties a DeepSeek confirmation run to the variant it confirms, so failed or repeated attempts
  stay out of the comparison (adversarial 2).
- Existing #125 covers repeat detection after a default flip (adversarial 3).

## Resolution Status
| Finding | Status |
|---|---|
| Must Fix 1 | Resolved |
| Should Fix 1 to 5 | Resolved |
| Consider items taken | Resolved |
| Consider items not taken | Recorded above with reasons |
| Adversarial 1 | Fixed in part; follow-up #129 |
| Adversarial 2 | Rule fixed; follow-up #130 |
| Adversarial 3 | Fixed; follow-up (existing #125) |
| Adversarial 4 | Fixed |
| Adversarial 5 | Accepted, recorded in #129 |

Adjudication tally: Fix now 4 (scenarios 1 to 4, two of them in part); Follow-up 3 (#129, #130 and the
existing #125); Accept 1 (scenario 5); Dismissed 0.
