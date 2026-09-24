# Review: issue #143, a DeepSeek variant's confirmation against another baseline or other code

Branch `issue-143-variant-confirmation-same-baseline`, base `main`.
Seven review passes (correctness, security, quality, patterns, redundancy,
wiring, adversarial) read the first version of the change. This file records
their findings, what was done with each, and how the adversarial scenarios were
adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - `_counting` selects the pairs that count together: alongside one
    baseline, on one context text hash. `_counted` takes that group, and its
    confirmation must repeat the first pair's settings (`_same_test`).
  - `_identity_differences` names what a pair does not repeat of another
    (context text, settings). Settings are compared only when both pairs
    recorded them (`settings_recorded`, carried by `_variant_preparations`
    and `_variant_pairs`), so a pair started before #130 is judged on its
    context text alone and never on settings this checkout re-derives from
    its overrides.
  - `_check_uncounted` (run-pair) takes the baseline and the variant's
    manifest, counts within that group, and also refuses a pair on a first
    pair's context text under other settings, naming the arm to answer
    instead.
  - `_variant_record` refuses a pair with no context text on record, gives
    three distinct reasons for a pair that is neither first nor
    confirmation, and adds `other_identities` (same baseline, same settings
    or text but not both, with `differs`) and `other_baselines` (complete
    pairs alongside other baselines, with `same_defaults_text`).
  - `_variant_warnings` drops the #130 "different baselines / different
    commits" warning, which can no longer occur, and warns about other
    identities, other baselines, and separately when another baseline
    prepared the same defaults' text.
  - `_same_variant` is renamed `_shares_identity`, since same settings on
    other text is no longer the same variant.
  - `_log_prepared_variant` prints one of three notices: same variant, same
    text under other settings, same settings on other text.
  - Module docstring, `run_pair`/`compare` docstrings, the `variant` note,
    and the #139 refusal wording.
- `tests/test_gpt54_baselines.py`: `logged_pair` records a context hash
  with settings, as real starts do; `logged_at`, `logged_results` and
  `LOGGED_CONTEXTS` are shared; the #130 tests are updated to the owner's
  decision; two new tests.
- `CLAUDE.md`: a "Which pairs count (#143)" sub-bullet in the default-change
  rule with the owner's three points.
- `BENCHMARKS.md`: DeepSeek steps 2 and 3, the identity and run-pair
  bullets, and the `compare` paragraph.

## Approach Summary

The owner decided the three open questions on the issue: a confirmation must
use the same baseline and the same variant identity (settings plus context
text hash) as its first pair; a new baseline starts every count again; a
code change that alters the context text makes a new variant, with earlier
attempts still listed.

Counting is keyed on (baseline, context text). Pairs on the same text under
other settings stay in that count, as #130 made them, so an inert setting
cannot buy a variant another first pair, but they can never confirm a first
pair answered under other settings. The owner did not rule on that case;
this keeps #130's protection and satisfies point 1.

The #130 warning becomes a refusal by construction: a pair alongside another
baseline, or on other text, is never read as a confirmation of an earlier
pair. It starts a count of its own. `run-pair` refuses the same pairs that
`compare` would, before any question is asked.

No `src/` file changed, so no production default changed and the evidence
gate is not needed. Read-only check on the real track data: `prme-reader-rrf`
pairs 1 and 2 alongside `prme@46647825` stay first pair and confirmation on
both benchmarks, with no warnings.

## Must Fix

None.

## Should Fix

| # | Source | Finding | Resolution |
|---|---|---|---|
| S1 | Agents 1, 7 | For a pair started before #130, settings are re-derived from the manifest's overrides by the current checkout. After a default flip they stop matching the confirmation's recorded settings, so `compare` would refuse `prme-reader-rrf` pair 2 from a flip branch | Fixed: `settings_recorded`; settings are compared only when both pairs recorded them. Test simulates the re-derivation changing |
| S2 | Agents 1, 3, 4, 5 | A pair with settings `None` listed itself in its own other attempts and gave "(None, not None)" messages | Fixed by S1, plus `pair is not this` in the list. Test with a legacy manifest whose overrides no longer parse |
| S3 | Agent 3 | A later pair was told "a confirmation must start after the first pair completed" | Fixed: third reason, "the confirmation is already complete" |
| S4 | Agents 3, 4, 1 | The prepare notice called a same-text, other-settings arm "the same variant", then run-pair refused it | Fixed: three notices; the refusal names the arm to answer instead |
| S5 | Agent 3 | `other_attempts` reused "attempt", which means an answer-run folder here | Renamed `other_identities`, with `differs` per pair (Agent 4) |
| S6 | Agents 1, 3, 4, 5 | `dropped` and its warning said "this variant" while covering same-settings, other-text pairs | Reworded. Scope restored to #130's (all baselines) after S9 |
| S7 | Agents 5, 6 | BENCHMARKS.md said compare refuses a confirmation alongside another baseline; it starts a new count | Fixed |
| S8 | Agent 4 | "Variant" meant three things across code and docs; `_same_variant` named the OR match | Docs say "stay in the same count"; renamed `_shares_identity`; note and docstring say "with the variant's settings or its context text" |
| S9 | Agent 7 | Narrowing `dropped` to one baseline hid a pair voided when a new baseline became current | Fixed: `dropped` and the unknown warning keep #130's reach across baselines |
| S10 | Agents 1, 2, 3, 6 | Docs said run-pair answers no pair "that could count as neither" | Reworded: "whose count is already decided as neither" |
| S11 | Agent 3 | Unknown-warning scope and a vacuous `dropped` assertion | Vacuous assertion removed; the scope was reverted (S9) and the test asserts the warning |

## Consider

| Source | Finding | Resolution |
|---|---|---|
| Agents 4, 5 | `_same_test` checked the baseline redundantly; `{**identity, "baseline": before}` fake pair | Done: baseline removed from the test, fake pair gone |
| Agent 4 | Settings rendered as raw dicts, in two orders | Done: `_shown_settings` (`key=value`), one phrasing |
| Agents 5, 4 | Nested `results`/`at` helpers duplicated; `LOGGED_TEXT` tied by comment to a literal | Done: module-level helpers and `LOGGED_CONTEXTS` |
| Agent 6 | #130 test name described the old behavior | Renamed |
| Agents 5, 6 | `_other_code_refusal` said the count restarts in every case | Scoped to the new-baseline case |
| Agent 5 | Rule restated in many places | Dropped the duplicate CLAUDE.md flip clause and the BENCHMARKS.md repeat |
| Agent 4 | CLAUDE.md "arm prepared with those settings" | Now "those settings or that context text" |
| Agents 3, 4, 5 | `contexts_sha256 is not None` guard in `_counting` is unreachable | Kept: a `None` hash would otherwise match identity-less pairs, and it costs nothing |
| Agents 3, 5 | Merge the "no identity" and "no context text" refusals | Kept separate: they tell the operator different things |
| Agent 3 | `_counted`'s baseline sort tiebreak is now constant | Kept: harmless, keeps the sort total for any input |
| Agent 3 | `_variant_record` is long | Left: splitting it would move #130 code this issue does not need to touch |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in messages | PASS | Settings come from `model_dump(mode="json")`, which masks secret fields; the same data is already in run logs |
| Authorization | N/A | Local CLI |
| Path traversal | PASS | `_baseline_text` reads `data/<baseline>/<benchmark>/prepared.json`, where the baseline name comes from the run logs' directory names and `_BASELINE` limits new ones |
| Run-log values | PASS | Only compared; bad timestamps raise |
| Deserialization | PASS | JSON only |
| Credentials in tests | PASS | Hash constants and settings dicts only |
| Default-change rule guard | PASS | Only the two cases the owner allowed can newly count; same-text, other-settings pairs are stricter than before |

## Pattern Consistency Assessment

The guard and `compare` share one grouping (`_counted(_counting(...))`), as
`_check_aa_ready` shares `_aa_coverage`. `other_identities` names how each
pair differs, like `_aa_coverage`'s "(differs in ...)". The CLAUDE.md
sub-bullet follows "A/A record (#137)". Issue references at the end of
messages follow the module's style.

## Redundancy Check

No new dependency. `_same_test` is the all-keys counterpart of
`_shares_identity`. `other_identities` is not redundant with `arms`, whose
entries carry no per-pair identity. The removed warning's text survives
nowhere.

## Wiring Findings

`_check_uncounted` has one production caller (`run_pair`), updated; the only
other reference is a `lambda *args` monkeypatch. `compare` reaches
`_variant_record` and `_variant_warnings` unchanged. CLI help reads the
module docstring, which is updated. CI runs `pytest tests/` and ruff; the new
tests use tmp data roots and a mocked Ollama. Four untracked
`prme-reader-rrf` result files in the worktree are not part of this change
and were not staged.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "Default flipped on a variant that failed the
same test one baseline earlier: a re-baseline with unchanged defaults reset
its count and compare raised no warning."

| # | Scenario | Label | Verdict | Reasoning |
|---|---|---|---|---|
| 1 | A new baseline recorded without a default change (a #118 repeat, a model identity change, any later main commit) resets every variant's count, and `compare` said nothing | newly introduced | Fix now (detection) + Follow-up #158 | Restarting at a new baseline is the owner's decision 2, so refusing would contradict it. `compare` now lists `other_baselines` and warns separately when an earlier baseline prepared the same defaults' text. Whether the count should restart only when the defaults' text or the model changed is a product decision: #158 |
| 2 | A pair voided because a new baseline became current dropped out of `dropped`, which the first version narrowed to one baseline | newly introduced | Fix now | Reverted the narrowing; `dropped` and the unknown warning reach across baselines as under #130 |
| 3 | Settings re-derived for pre-#130 pairs made the real confirmation fail after a flip (found in the first version) | newly introduced | Fix now | `settings_recorded`; verified on the real data and in a test |
| 4 | Other context text from a dirty tree or nondeterminism, not a code change, gives a new first pair | newly introduced | Accept | The owner's point 3 covers any code change, committed or not; `compare` already warns about a dirty preparation, and `other_identities` names the earlier attempt |
| 5 | Two arms on the same text under different settings run at once; whichever finishes first sets the settings, and the other counts as neither | newly introduced | Accept | Needs a deliberate race, and nothing is mis-counted: the loser is refused and listed |
| 6 | `run-pair` still answers a pair that later ends as neither (started before the first pair completed) | pre-existing | Accept | `compare` refuses it; docs no longer claim otherwise |

Attacks that held: fishing within one baseline by code change, other settings,
renaming or re-preparing; older checkouts sharing the data root (the start
event format is unchanged and #130's run-pair is stricter).

## Follow-ups Raised

- #158: a new DeepSeek baseline with unchanged defaults gives every variant a
  fresh count.

## Resolution Status

| Item | Status |
|---|---|
| Must Fix | none |
| S1 to S11 | resolved |
| Consider items | resolved or declined with reasons above |
| Break scenario 1 | detection fixed; restart key is follow-up #158 |
| Break scenarios 2, 3 | fixed |
| Break scenarios 4, 5, 6 | accepted |
