# Review: issue #130, tying a DeepSeek confirmation to the variant it confirms

Branch `issue-130-tie-confirmation-runs-to-variant`, base `main`. Seven
review passes (correctness, security, quality, patterns, redundancy, wiring,
adversarial) read the first version of the change. This file records their
findings, what was done with each, and how the adversarial scenarios were
adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - New `is_variant`, used where the module spelled out "a prme arm that is
    not a baseline".
  - New `variant_settings`: what a variant's `--set` overrides change from
    the defaults' configuration at the preparing commit, by dotted setting
    name, as the configuration reads them. Another spelling, an override that
    repeats a default, or a setting the configuration fills in (`rrf_k` under
    rank fusion) makes no new variant.
  - New `_contexts_sha256`: the hash of a preparation's context text on every
    question. Two preparations with the same hash are one variant whatever
    their settings, which catches a setting retrieval never reads.
  - `prepare` refuses a variant whose settings are all defaults, stores
    `variant_settings` in the manifest, and logs a `prepared` event (settings,
    context hash, commit, manifest digest) in the arm's run log through
    `_log_prepared_variant`, which also names any other arm of the same
    variant on stderr.
  - `run_pair` records the pair id and the variant identity at every start of
    a variant's pair, refuses a variant whose first pair and confirmation are
    already complete (`_check_uncounted`), and lists the arm's pairs under
    every baseline in `earlier_pairs` (entries gain `baseline`);
    `pairs_started` counts them all.
  - `_pair_states`, `_pairs_on_record` and `_earlier_pairs` replace the old
    per-log `_earlier_pairs`. A pair keeps what its first complete run
    recorded, and `_pair_blocker` treats a pair the run log records complete
    as complete even when its folder was moved aside.
  - New `_variant_preparations`, `_variant_pairs`, `_counted` and
    `_variant_record`: `compare` reads every variant preparation and pair on
    the track for the benchmark, finds the pairs of the same variant (same
    settings or same context text, any arm name, any baseline), and accepts a
    variant's pair only as its first pair or its confirmation (the next to
    complete that started after the first completed). The `variant` block
    lists every arm and pair of the variant, `related` variants, `unknown`
    pairs and `dropped` pairs; `_variant_warnings` adds the warnings.
  - `_recorded_time` is shared with `_complete_baselines`; `_track_data`
    resolves the track root for `compare`; `_prepared_summary` carries
    `variant_settings`.
  - Module, `run_pair` and `compare` docstrings.
- `tests/test_gpt54_baselines.py`: new tests for `variant_settings`, for the
  first pair and confirmation across arm names and baselines through
  `run_pair` and `compare`, and for ordering, freshness, invalid, unknown,
  dropped, folder-only, legacy and naive-time cases from run logs. Existing
  tests updated for the `earlier_pairs` shape, the variant's own run log, the
  prepared summary and the complete pair that stays complete. New helpers
  `logged_pair`, `fabricate_variant`, `contexts_sha256` and `MARKED`.
- `BENCHMARKS.md`: DeepSeek step 3, the run log and pair bullets, and the
  `compare` paragraph.

## Approach Summary

Since #129 a confirmation is not a new arm: it is the next `run-pair` pair of
the same variant. So the issue's suggested `prepare --confirms <variant>`
would link something that is no longer created, and as an optional flag it
could not catch an arm prepared without it, which is the redraw the issue
describes. The owner's two comments on the issue ask instead for a step that
reads every pair log for arms of the same settings, across baselines and arm
names, and accepts only the first complete pair and the first complete
confirmation after it. That is what this change builds.

1. **Identity.** A variant is what it changes: the settings whose values
   differ from the defaults, and the context text it sends the reader. Both
   are recorded when it is prepared and at every start of its pairs, outside
   the arm's folder.
2. **Counting.** Every pair of the same identity counts, under any arm name
   and alongside any baseline, in the order the pairs completed. The first is
   the first pair; the next complete pair that started after it completed is
   the confirmation. Pairs given up, not published or invalid under the 1%
   limit count as neither, as `CLAUDE.md` says.
3. **Enforcement.** `compare` refuses any later pair and any pair it cannot
   place, and `run-pair` refuses to answer a variant that already has both.
   The owner's decision on #137 is that a written rule is not enough in a
   repository that merges on green CI.

Alternatives considered:

- `prepare --confirms <variant>` (issue body). Rejected: since #129 the
  confirmation is the next pair of the same arm (`run_pair` docstring,
  "how a confirmation redraws both sides"; `CLAUDE.md` "running `run-pair`
  again after a complete pair starts the next one"), and an optional flag
  cannot bind an arm prepared without it.
- Refusing a second arm with the same settings in `prepare`. Rejected: an arm
  with a complete pair can never be prepared again (`prepare` refuses it), so
  after a default flip the same settings need a new arm name; `run-pair` and
  `compare` enforce the count instead.
- Warning, not refusing, on a later pair (the first version). Rejected on
  review: the owner's comment says "accepts only", and #137 and #125 record
  that a warning alone is not enough protection.
- Matching on the raw overrides (the first version) or only on settings.
  Rejected on review: a repeated default or a setting retrieval never reads
  made a new variant. The diff from the defaults and the context hash close
  both.

Shared state: new append-only events in the private run logs under the main
checkout's `data/ollama-answers-v1/<track>/` (a `prepared` event in a
variant's own run log; `id`, `variant_settings` and `contexts_sha256` in a
variant pair's `started` events) and a new `variant_settings` key in variant
manifests. Older code reads events by type and ignores unknown keys; baselines
and A/A pairs get nothing new. No `src/` file changed, so no production
default changed and the evidence gate does not apply.

## Must Fix

- M1 (correctness, wiring): once a complete pair's folder was moved aside,
  the next `run-pair` logged it `abandoned: its pair.json record is missing`,
  and the new state replaced `complete`, so a failed first pair dropped out of
  the count. A second `finished` event could also move its completion time.
  Resolved: `_pair_states` keeps what the first complete run recorded, and
  `_pair_blocker` reads completion from the run log first. Tested in the
  lifecycle test and in the run-log test.
- M2 (correctness, security, adversarial 1): a `--set` equal to the default,
  or a setting retrieval never reads, made a new variant whose next pair was a
  clean `first`. Resolved: settings are the diff from the defaults at the
  preparing commit, `prepare` refuses a variant with no difference, and the
  context hash makes arms with the same context text one variant. Tested.

## Should Fix

- S1 (patterns, adversarial 5): a later pair was only warned about.
  Resolved: `compare` refuses it and `run-pair` answers none.
- S2 (correctness, issue body): nothing checked that the confirmation started
  after the first pair completed. Resolved: `_counted` requires it.
- S3 (correctness, adversarial 2 and 4): first pair and confirmation could
  come from different baselines or commits with no sign. Resolved: each
  carries its baseline, the after side's commit and the server version, and
  `compare` warns when they differ. Whether that should refuse is Follow-up.
- S4 (security, adversarial 3): pairs left unfinished or given up by hand
  dropped out silently. Resolved: `dropped` lists them, with a warning.
- S5 (redundancy, security, correctness): `compare` filled a pair's settings
  from the result file itself. Resolved: removed; a pair with no recorded
  identity is refused, and a legacy manifest the configuration rejects gives
  no settings rather than raw ones.
- S6 (quality, wiring): one unreadable manifest of another arm blocked every
  variant `prepare` and `compare`. Resolved: manifests that do not parse, are
  not complete or name another arm are skipped.
- S7 (patterns, redundancy, quality): discovery globs hardcoded the layout and
  searched twice; time parsing was copied; `completed_at` duplicated
  `finished_at`; the record helper built its own note; `related` grouped by
  arm; the unfinished default was written twice. Resolved: `_pairs_on_record`
  builds on `_pair_log_path` and `_pair_root`, `_recorded_time` is shared,
  the key is `finished_at`, `compare` builds the note, `related` groups by
  settings with `_arms_of`, `_pair_states` reads folders and the log once.
- S8 (quality): reused names and an ambiguous `settings` event key. Resolved:
  `variant_settings`, `mark`, `found`, `identity`.
- S9 (quality, security): the refusal gave one message for four cases and an
  absolute path. Resolved: it names the recorded state and no path.
- S10 (quality): untested branches (legacy fallback, folder-only pairs, an
  arm with two identities, notices). Resolved: covered.
- S11 (patterns, wiring): published results did not state the settings.
  Resolved: `_prepared_summary` carries `variant_settings`.

## Consider

- Recorded settings could be edited in a manifest before `run-pair` (security
  C2). Accepted: the context hash comes from entries that answering verifies
  against the context files, so an edited manifest still matches its variant.
- A legacy pair with no identity takes its arm's identity when every
  preparation on record agrees (quality C11). Accepted: only code before #130
  wrote such pairs, and the real track has no variant pairs.
- A crash between writing both results and logging `finished` leaves a pair
  complete on disk and unfinished in the log (correctness C8). Accepted: a
  sub-second window, as #129 recorded; the pair is listed as unfinished.
- `prepare` on the CLI is not wrapped in `parser.error` (wiring C4).
  Pre-existing; left as is.
- Two prepares of the same variant started together miss each other's notice
  (correctness C9). Accepted: `compare` still counts them together.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets in new records | PASS | The gate refuses every secret-holding key; dumps mask secrets |
| Published results | PASS | Only settings, arm and baseline names, states and hashes |
| Path traversal and glob injection | PASS | Result values are only compared; discovered names pass `is_variant` |
| Unsafe deserialization | PASS | `json.loads` only |
| Paid calls and spend confirmation | PASS | No network, subprocess or environment reads added |
| Existing refusals | PASS | None removed; new refusals added |
| Re-running a variant under a new identity | FAIL, then fixed | M2 |

## Pattern Consistency Assessment

The events, notices, refusal wording and test helpers follow the module's
neighbors (`new-baseline`, `_check_current_baseline`, `answered`,
`recorded_baseline`). After S7 the discovery and time helpers are shared and
`compare` builds the block's note, as #127 laid it out.

## Redundancy Check

`is_variant` and `_track_data` remove duplication. The settings are kept in
three places on purpose: the manifest (what `run-pair` reads, since the diff
depends on the preparing commit), the `prepared` event (survives the folder
being moved aside) and each pair start (fixes each pair's identity even if
the arm is prepared again). The legacy fallbacks stay because every worktree
shares the private data and older code can still write records.

## Wiring Findings

The CLI `compare` resolves the same track root as `run-pair`; older code
ignores the new events and keys; the published A/A results and the failure
policy amendment are untouched; CI runs the new tests. Resolved M1 and S6
above.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "Default flipped on a confirmation that
`compare` labeled clean: a renamed arm with one no-op `--set` (or re-prepared
at a new commit) restarted the variant's pair count."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | A `--set` equal to a default, or a nudged value, starts a fresh count | Newly introduced | Medium | High | Fix now | Settings are the diff from the defaults, a no-op variant is refused, and the context hash catches settings retrieval never reads. A nudged value is a variant of its own; it is listed as related, with a warning when it has complete pairs |
| 2 | The first pair and the confirmation come from different code | Newly introduced | Medium-Low | High | Fix now (warning) and Follow-up (#143) | `compare` shows each pair's commit and warns when they differ; whether that should refuse, or restart the count, is a product decision |
| 3 | Partial results are read, then the pair is left unfinished or given up by hand | Newly introduced (the label; the rule gap existed) | Low-Medium | High | Fix now | `dropped` lists such pairs and `compare` warns; the confirmation must also start after the first pair completed |
| 4 | The confirmation is answered alongside a newer baseline | Newly introduced | Low-Medium | Medium-High | Fix now (warning) and Follow-up (#143) | `compare` warns; the rule for a confirmation across a default flip needs a decision |
| 5 | Roles are guidance only and never saved; a flip can cite a pass after a failed first pair | Pre-existing (honor system) | Medium | High | Fix now (partly) and Follow-up (#144) | `compare` now refuses later pairs and `run-pair` answers none. Recording each pair's verdict where the flip is decided needs a design decision |
| 6 | A result names its own pair number; a pair log can be deleted | Newly introduced (first half) | Low | High | Fix now (first half), Accept (second) | Each start records the pair id and `compare` checks it. The run logs are private, append-only by convention, and outside any automated write path, so deleting one is not guarded |

Scenarios Agent 7 attacked that held: older worktrees on the shared data
(fallbacks and `unknown`), crashes between writes (manifest fallback and
folder-only pairs), running `compare` twice, tied timestamps, and the new
refusal adding no dependency beyond #127's.

## Follow-ups Raised

- #143: a variant's confirmation can be answered alongside a newer baseline
  or read contexts built by other code than its first pair, and nothing
  decides whether it still counts (scenarios 2 and 4).
- #144: nothing records whether a variant's first pair passed, so a pull
  request can cite a passing confirmation on its own (scenario 5).

## Resolution Status

| Item | Status |
|---|---|
| M1, M2 | Resolved |
| S1 to S11 | Resolved |
| Consider items | Accepted or left, as recorded above |
| Adversarial 1, 3 | Fixed |
| Adversarial 2, 4 | Warning now; Follow-up #143 |
| Adversarial 5 | Partly fixed; Follow-up #144 |
| Adversarial 6 | Fixed (pair id); log deletion accepted |
