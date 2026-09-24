# Code Review: issue-123-rerecord-deepseek-defaults-baseline

## Files Changed
- `benchmarks/integrations/gpt54_baselines.py`: later defaults baselines filed under their commit
  (`prme@<first 8 characters>`). Adds `SHORT_COMMIT`, `_BASELINE`, `is_baseline`, `baseline_arm`,
  `_check_baseline_name`, `_later_baselines`, `_complete_run`, `_complete_run_commit` and
  `_checked_out_baseline`. `prepare()` checks a later baseline's name before and after the replay and logs
  `new-baseline` once. `prepared-again` now keys off a `started` event. The `finished` run-log event
  records `prepared_commit`. `_run_history` reports `new_baseline`. The CLI resolves `prepare prme` and the
  Ollama `run prme` through `baseline_arm`.
- `tests/test_gpt54_baselines.py`: an autouse fixture that keeps every test off the main checkout's
  private data; a shared `gate_cases` helper for the three gate-backed tests; two gate-backed tests (a
  later baseline prepared, answered and compared without touching the first; the restart and give-up
  flows with their run-log records); a resolution unit test (samples, incomplete runs, per benchmark,
  unfinished later baselines, legacy logs); a CLI resolution test; the paid-track refusal now covers
  `prme@...`.
- `BENCHMARKS.md`: the arm table, procedure steps 2 and 3, and three safeguard bullets.

## Approach Summary
Every piece of arm state (folder, run log, published result) is keyed by the arm name, and the `prme`
arm's complete run closed it for good (`prepare` at :245-249 on main, `run` at :690-691). Premise: a new
defaults baseline needs its own arm, keyed by the commit that prepared it, so the complete `prme` arm and
its record stay as evidence.

`baseline_arm(benchmark, commit, data=)` returns `prme` until `prme` has a complete non-sample run from
another commit, then `prme@<commit[:8]>`. The CLI resolves `prme` through it for `prepare` (after the
clean-tree and on-main checks) and for the Ollama `run`, so both act on the checked-out commit's
baseline. The GPT-5.4 track still refuses every `prme*` arm (`is_prme` covers `prme@`).

Alternatives considered:
- Reopen the `prme` folder when the commit differs: rejected. `prepare` refuses an existing folder (:238-240
  on main), `write_new` never overwrites (`gpt54_budget.py:34`), and a shared run log would mix two
  baselines' counts (`_run_history`, :531-536 on main). Moving the folder aside is one of the workarounds
  the issue names as damaging.
- A no-op variant (`prme-<name>`): rejected. `_check_overrides` requires a `--set` (:194-195 on main), and
  variants skip the on-main check (:1074 on main).
- Key every arm's folder and log by commit: rejected. It changes the GPT-5.4 ledgers (:460-462 on main) and
  every existing log path, and would need a migration of the recorded baseline.
- An explicit `--commit` for `run`: not needed. The arm name encodes the commit, and `run prme` from any
  other commit now refuses and names the unfinished baseline (see adversarial 1).

Shared state touched: new folders and run logs under `data/ollama-answers-v1/<track>/` (private,
gitignored); one new field in every Ollama `finished` run-log event (read only by this module); published
result names with `@`. No retrieval, packing or representation code changes, so the evidence gate does
not apply. No answer runs were made.

## Must Fix
None.

## Should Fix
1. A stopped `prme@X` could not be resumed after `main` moved, and the "run prepare first" hint created a
   second baseline (Agents 1, 3, 6; adversarial 1). Fixed: `baseline_arm` refuses while another later
   baseline is prepared and not complete, and names the commit to check out or the folder to move aside.
2. The name rule was written three ways (full equality, prefix, slice), and the name check ran only after
   the replay (Agents 1, 3, 4, 5). Fixed: one rule, `_check_baseline_name` built on `baseline_arm`, run
   before the replay against the checked-out commit and after it against the commit the gate recorded.
3. The `new-baseline` event used `commit` for the value the `finished` event calls `prepared_commit`
   (Agent 4). Renamed.
4. Untested branches: `prepared-again` keyed off `started`, `new-baseline` logged once, and a later
   baseline refused while `prme` is incomplete (Agent 3). Covered by the restart and give-up test and the
   first gate-backed test.
5. `BENCHMARKS.md` step 2 said "the baseline's contexts match the saved run", which a post-flip baseline
   will not (Agents 3, 6; adversarial 4). Limited to the first baseline, with advice for later ones.
6. The gate-pack setup was repeated in three tests (Agent 4). Extracted `gate_cases`.

## Consider
- Taken: the stderr notice now prints for `run` as well as `prepare` (Agent 4); the two `if arm == "prme"`
  blocks in the prepare branch are merged (Agent 4); `_answered_commit` renamed `_complete_run_commit`
  (Agent 3); `log_event` replaced by `baselines._log_run` (Agents 4, 5); `test_variant_names_are_checked`
  renamed `test_arm_names_are_checked` (Agent 4); an autouse fixture points `DATA` and `OLLAMA_DATA` at a
  temp folder (Agents 3, 4, 6); the paid-track refusal test lists `prme@0123abcd` (Agent 2);
  `_run_history` reads the event with `.get` (Agent 2); the dead "unknown commit" fallback is gone
  (Agents 3, 5); the error for a missing commit says to run from a git checkout (Agent 3).
- Not taken: harden `_on_main` to check the named commit instead of HEAD in `study.ROOT` (Agent 6).
  The CLI imports from the cwd checkout, so both git calls see the same repository today; this was true
  before this change too.
- Not taken: move `first_baseline_commit` into the manifest instead of a run-log event (Agent 5). The
  issue asks for the re-recording to be logged, and the run log is the record that survives a folder
  being moved aside.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Path traversal through `prme@` names | PASS | `_BASELINE` is used with `fullmatch` only; the CLI cannot type the name; the commit is checked as 8 lowercase hex characters before it reaches a path |
| `prme*` arms on the paid track | PASS | `run()` refuses `is_prme` arms before any key or client; the CLI refuses `prme` without `--provider ollama` |
| Secrets or personal data | PASS | New records hold commits and timestamps only |
| Unsafe deserialization | PASS | `json.loads` only |
| Credentials in tests | PASS | None added |
| Subprocess injection | N/A | No new subprocess calls |

## Pattern Consistency Assessment
The new arm kind follows the variant pattern (a regex plus `is_*` helpers). The event follows the existing
append-only run-log events. CLI errors go through `parser.error`, like `_check_args`. The `@` separator
keeps baselines out of the variant namespace (`prme-0123abcd` is a legal variant name).

## Redundancy Check
No existing helper duplicated. `_complete_run` extracts the predicate `prepare` already had. The fallback
to the manifest is required: the real `prme` run logs have no `prepared_commit`.

## Wiring Findings
Every consumer of the arm name handles `@` (`_check_arm`, `load_prepared`, `_run_lock`,
`_bind_answer_model`, `report`, the publish path, `gate.run_gate(capture_dir=...)`). No script parses
published result names. CI runs the tests from a checkout with a git HEAD, which the gate-backed tests
need, as the existing prme test already did.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "The first post-flip DeepSeek baseline was re-prepared at a newer `main`
commit after a looping answer, and a default flipped against whichever of several same-defaults
baselines scored lowest, with every harness check green."

| # | Scenario | Introduced | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | A restart after `main` moves quietly starts a new baseline and hides the failed attempt | New | High | Medium-High | Fix now | Contained. `baseline_arm` refuses while another later baseline is prepared and unfinished; giving one up is recorded in the next baseline's `new-baseline` event (`abandoned`) and its published result |
| 2 | Several same-defaults baselines can coexist, including one prepared at an older `main` commit, and `compare` accepts any as the before side | New | Medium | High | Fix now (docs) + Follow-up #127 | Docs now define the current baseline as the most recent complete one. Enforcing it needs a `compare` contract change (overlaps #125) and a decision on how the #118 repeat is classed |
| 3 | The #118 repeat at a later commit could measure a code change, since rows cannot show identical context text | Pre-existing (row hashes) | Medium | High | Follow-up (existing #125) | Today `contexts_matching_saved_run` on the later baseline shows whether its contexts equal the first baseline's; after a flip this needs #125 item 2 |
| 4 | After the first flip, the documented drift check against the saved run always fails | Pre-existing | High after a flip | Medium-High | Fix now (docs) + Follow-up (existing #125) | Step 2 now limits the claim to the first baseline and says to prepare variants at the later baseline's commit |
| 5 | A model identity change at a commit that already has a complete baseline cannot get a new baseline until `main` moves | New (commit keying) | Low-Medium | Medium, loud | Accept | Alternatives re-checked: keying by identity would need the identity at prepare time (a call to the Ollama server) and a naming contract change. Before this change no new baseline was possible at any commit; `main` moves with every merge. Documented |
| 6 | The real `prme` run logs predate `prepared_commit`, so resolution depends on the `prme` folders' manifests | New | Low | Medium, loud | Accept | Complete folders are never moved by the documented flows; the error names the missing manifest |

Attacks that failed (Agent 7): `run prme` answering the wrong arm (every mismatch is loud); HEAD moving
during prepare (the post-replay check refuses); existing data (the `started` rule keeps every existing
log's counts; smoke roots hold samples only; `discarded-attempts/` is outside the data root).

## Follow-ups Raised
- #127: Several DeepSeek defaults baselines can coexist, and compare accepts any of them as the baseline.
- Existing #125 covers adversarial 3 and 4 (context text hashes and drift reporting).

## Resolution Status
| Finding | Status |
|---|---|
| Should Fix 1 to 6 | Resolved |
| Consider items taken | Resolved |
| Consider items not taken | Recorded above with reasons |
| Adversarial 1 | Fixed now |
| Adversarial 2 | Docs fixed now; follow-up #127 |
| Adversarial 3 | Follow-up (existing #125) |
| Adversarial 4 | Docs fixed now; follow-up (existing #125) |
| Adversarial 5, 6 | Accepted, documented |

Adjudication tally: Fix now 1 (scenario 1, plus the docs parts of 2 and 4); Follow-up 3 (scenario 2 as
#127, scenarios 3 and 4 under the existing #125); Accept 2 (scenarios 5 and 6); Dismissed 0.
