# Review: issue #139, DeepSeek variant pairs crediting the variant with other merged changes

Branch `issue-139-deepseek-variant-pair-credits-merged-changes`, base `main`.
Seven review passes (correctness, security, quality, patterns, redundancy,
wiring, adversarial) read the first version of the change. This file records
their findings, what was done with each, and how the adversarial scenarios were
adjudicated.

## Files Changed

- `benchmarks/integrations/gpt54_baselines.py`:
  - `prepare` replays the defaults at a variant's commit too, without
    captures (`_replay_defaults`). Both replays must share `_REPLAY_INPUTS`
    (commit, dirty flag, worktree hash, Python, dependencies, saved run,
    datasets). The report is kept as `defaults-gate.json`, and each manifest
    entry gains `defaults_text_sha256`, with `defaults_gate_report_sha256` next
    to the existing `gate_report_sha256`.
  - `_row` adds `defaults_text_sha256` to a variant's reported rows.
  - `_context_changes` reports `changed_by_other_code` through the new
    `_other_code_changes`, which `run_pair` also uses on the manifests.
  - `compare` refuses a variant's pair with any other-code change, and one
    without the count that started at or after `DEFAULTS_REPLAYED_SINCE`
    (`_check_other_code`). The different-commits warning is left out when the
    count is 0, and names #139 for the variant pairs from before it.
  - `run_pair` refuses a variant whose manifest lacks the hashes, whose hashes
    do not match the kept report, or whose defaults read other text than the
    baseline (`_check_defaults_replayed`), after the #130 check and before any
    pair is opened.
  - Module docstring, `--variant` help, and the CLI progress line.
- `tests/test_gpt54_baselines.py`: `fabricate_variant` records the defaults'
  text and report, `answer_result` takes `defaults`, `text_sha256` is shared,
  and four new tests cover compare, run-pair and prepare.
- `BENCHMARKS.md`: step 2 of the DeepSeek instructions, the variant bullet and
  the `compare` paragraph.

## Approach Summary

A variant pair's before side is a baseline prepared at its own commit on
`main`, and the variant is prepared at a later commit or on a branch, so the
text the two sides differ on can come from the variant's settings or from any
code between the commits. The mitigation the issue proposed is built as
written: replay the defaults at the variant's commit when it is prepared, and
compare those defaults with the baseline's text question by question.

The issue left open whether `compare` should refuse or only report. It refuses,
following the owner's decision on #137 that guards on the default-change rule
must refuse, since pull requests merge on their own once CI passes. `run-pair`
refuses the same pairs before any question is asked.

Variant pairs answered before this change carry no split. Refusing them in
`compare` would strand `prme-reader-rrf`, whose first pair and confirmation are
complete on both benchmarks, since #130 fixes those two pairs. They are
accepted with a warning, and only pairs that started before
`DEFAULTS_REPLAYED_SINCE` (2026-09-24 18:00 UTC) are. Between the baseline's
commit (46647825) and the variant's (7b36b2b7), only
`benchmarks/integrations/gpt54_baselines.py` and `ollama_answers.py` changed,
and neither builds contexts.

Offline check: the defaults replayed at `main` read the same text as the
`prme@46647825` baseline on 1540 of 1540 LoCoMo and 500 of 500 LongMemEval-S
questions, both at f194a77e and after rebasing onto b0fe7992 (#111), so a
variant prepared at `main` today is not refused.

## Must Fix

None.

## Should Fix

1. The test of the real `prepare` path could not tell right from wrong: on the
   test pack every context text is the same, so the defaults' hashes equal the
   variant's (Agent 1). Added a case that marks the defaults replay's rows and
   checks each entry records its own question's mark.
2. The other-code refusal gave the wrong advice at one commit, or when the
   variant's branch is behind the baseline (Agent 3). The message now lists
   the dependencies and saved run as causes, says to prepare from a commit
   that descends from the current baseline's, names an unrecorded commit, and,
   in `run-pair`, names the other inputs the two manifests differ in.
3. `run-pair` and `compare` disagreed on variants prepared before #139, and
   the advice to prepare again under a new name left an unsplit first pair in
   place (Agents 3 and 4). Closed with the cutoff below (adversarial 1), and
   the reason for accepting the older pairs is written into the constant, the
   `compare` note and `BENCHMARKS.md`.
4. `_verified_row`'s docstring did not mention the new row field (Agent 3).
5. The other-code count was computed twice, once over manifests and once over
   rows (Agent 4). Both now call `_other_code_changes`.
6. `changed_by_settings` always equaled `differing` in any comparison `compare`
   returns, since pairs with other-code changes are refused (Agent 5). Removed;
   `changed_by_other_code` at 0 says the same.
7. `run-pair` ran the #139 check before the #130 check, so `prme-reader-rrf`,
   whose two counted pairs are complete, would have been told to prepare again
   only to be refused by #130 afterwards (Agent 6, adversarial 2). The #130
   check now runs first.

## Consider

- Taken: the replay mismatch names what changed and the folder to remove; the
  `_context_changes` docstring and `compare` note say the count is also None
  when the before side has no text hashes; `_hashed` became `_has_hash`;
  `prepare` keys the replay on `is_variant`; the docstrings no longer claim the
  baseline is always at another commit; the CLI progress counter restarts for
  the second replay and says "replayed"; the test helpers share `text_sha256`
  and `answer_result(defaults=...)`; the stderr check in the prepare test no
  longer depends on the line count; `--variant` help mentions the replay.
- Declined: trimming `_REPLAY_INPUTS` to the keys that can differ in one
  process (it documents the contract and costs nothing); a prepare-time
  warning against the current baseline, or reusing a baseline's hashes at the
  same commit (`run-pair` refuses before any answer, so only preparation time
  is at stake); shortening the repeated explanations (they follow the module's
  style); the traceback from a `run-pair` refusal in the CLI (every other
  `run-pair` refusal behaves the same).
- Left to the owner: the default-change rule in `CLAUDE.md` does not mention
  this refusal.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Local paths or PII in published results | PASS | `defaults-gate.json` stays in the gitignored `data/` folder; `_prepared_summary` keeps an allowlist, so the report hash and provenance are not published. |
| New fields in published output | PASS | Rows gain a SHA-256 of the defaults' text; `compare` gains an integer. |
| Logs and stderr | PASS | The new stderr line names the benchmark; refusals name arms and commits. The replay mismatch names the private folder on the local terminal only. |
| Path traversal | PASS | The new hashes are only compared; the report path is fixed. |
| Unsafe deserialization | PASS | JSON only. |
| Credentials | PASS | None added. |
| Paid API or credit spend | PASS | The second replay is the offline gate with no overrides; the run-pair check runs before any client is built. |
| Receipts replayable | PASS | The report is kept and its digest recorded; `run-pair` now checks the hashes against it. |

Considered and accepted: `_write_report` overwrites rather than creating
exclusively, as for `gate.json`; `prepare` already refuses an existing folder.

## Pattern Consistency Assessment

The new file and key names follow `gate.json` and `gate_report_sha256`. The
refusal is mirrored in `run-pair` and `compare` like the #125 and #137 checks,
through one counting helper and one error builder. The cutoff follows #132's
check on answers given under the registered policy after its amendment.

## Redundancy Check

`changed_by_settings` removed as redundant. `_has_hash`, `_other_code_changes`
and `_other_code_refusal` each have two or more callers. No existing helper
compares two gate reports' provenance; `gate.compare_gates` compares evidence
metrics and does not check commits.

## Wiring Findings

The CLI `prepare` reaches the second replay with the same archive and progress
callback; `run-pair` reaches the check before the run lock, the pair folder and
any Ollama request; `compare` output carries the new key. Readers of manifest
entries and rows (`_contexts_sha256`, `_prepared_identity`,
`_variant_preparations`, `load_prepared`, `estimate`, `record_aa_check`) ignore
the new field, and answers recorded before it still verify. `compare` on the
real `prme-reader-rrf` pairs, read-only, still accepts both pairs on both
benchmarks with the #139 warning.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "A variant pair answered from a pre-#139
worktree passed compare with a warning, and a default flipped on another
merged change's gain."

| # | Scenario | Label | Verdict | Reasoning |
|---|---|---|---|---|
| 1 | `compare` accepted any variant pair without the split, so a pre-#139 checkout (an open branch) bypassed the guard | newly introduced | Fix now | It bypasses the rule this issue enforces. `compare` refuses a variant pair without the split that started at or after `DEFAULTS_REPLAYED_SINCE`; every pair on record started before it. |
| 2 | The #139 refusal sent `prme-reader-rrf` to prepare again, then #130 refused the new arm | newly introduced | Fix now | One-line reorder: `_check_uncounted` runs first; the advice now applies only while a counted pair is still needed. |
| 3 | Code that changes only the variant's path between its pairs and the flip is never checked, so what ships can differ from what was measured | pre-existing | Follow-up #154 | Needs a decision on where a flip-time replay runs. |
| 4 | The recorded defaults' hashes were never checked against the kept report | newly introduced | Fix now | Contained: `run-pair` checks the report's digest, that it replayed the defaults at the variant's commit, and its rows. `compare` still reads the published rows, as it does for the #125 text hashes. |
| 5 | Environment drift (dependencies, saved run) gave a misleading remedy | newly introduced | Fix now | The message lists those causes, and `run-pair` names the inputs the two manifests differ in. |
| 6 | A tree change during the second replay is found only after it runs, leaving a partial folder | newly introduced | Accept | Rare and loud; the error now names the folder to remove. Checking before the second replay would not catch a change during it. |

Raised while adjudicating 1: a pair that `compare` refuses for reasons fixed
when it was answered (#139, #137, #125) still counts as a variant's first pair
or confirmation, which can strand the variant. Pre-existing since #137 and
reachable now through an older checkout. Follow-up #155.

## Follow-ups Raised

- #154: A DeepSeek default can flip on variant contexts that no counted pair read.
- #155: A DeepSeek variant pair that compare refuses still takes the variant's first-pair or confirmation slot.

## Resolution Status

| Finding | Status |
|---|---|
| Should Fix 1 to 7 | Resolved |
| Consider items taken | Resolved |
| Consider items declined | Recorded above |
| Adversarial 1, 2, 4, 5 | Fixed |
| Adversarial 3 | Follow-up #154 |
| Adversarial 6 | Accepted |
| Refused pairs taking counted slots | Follow-up #155 |
