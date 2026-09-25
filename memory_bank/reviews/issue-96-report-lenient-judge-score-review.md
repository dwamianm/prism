# Code Review: issue-96-report-lenient-judge-score

## Files Changed
- `benchmarks/integrations/lenient_judge.py` (new): a judge-only second pass over the saved answers of a
  complete LoCoMo answer run, through Mem0's LoCoMo judge prompt (`LENIENT_JUDGE`, copied verbatim from Mem0
  commit `aae5989e`, hash-pinned). `load_source` reads the registered 2026-09-23 GPT-5.4 run (verified against
  `gpt54-comparison-v1-verification.json`) or a complete DeepSeek answer run, and checks every saved answer and
  strict judge call against the hashes in its published row. `estimate` scales recorded strict judge usage.
  `run` grades through the run's own judge (GPT-5.4 paid, behind `--max-usd` and a typed confirmation; the
  DeepSeek model through Ollama, identity checked before and after), and `report` replays every call. The
  result reports both scores with intervals, each category under its upstream number (1 multi-hop, 2 temporal,
  3 open-domain, 4 single-hop) and the verdicts each judge alone accepted.
- `benchmarks/integrations/gpt54_baselines.py`: the worker loop of `_answer` moves into `drain_pending`, and the
  usage, attempt, coverage and cost blocks of `report` into `new_usage`, `count_calls`, `recorded_attempts`,
  `coverage` and `provider_cost`, so the answer runs and the lenient pass share one set of rules. `_confirm_spend`
  takes the kind of name to type and `_announce` a role. No behavior change for the answer runs.
- `tests/test_lenient_judge.py` (new): 41 tests over mocked OpenAI and Ollama endpoints, plus one that pins the
  published DeepSeek pass.
- `BENCHMARKS.md`, `README.md`: a "Strict and lenient LoCoMo judges" section with both prompts linked, the
  strict and lenient scores side by side, the category table with upstream numbers, caveats, and commands; the
  DeepSeek category table gains the upstream numbers; the README links both prompts.

## Approach Summary
Only the judge prompt changes: no reader is called, and the judge model and settings are the ones that gave the
strict verdicts. The registered harness (`run_gpt54_comparison.py`, `gpt54_budget.py`) is hash-pinned and stays
unchanged; the pass reuses its `call()`, `Ledger`, verifier, question rows and bootstrap, and the answer runs'
attempt, lock, ledger and publish helpers. The paid GPT-5.4 re-judge of the 2026-09-23 answers is not run (epic
#77 rules); the free DeepSeek pass over the current DeepSeek baseline (`prme@46647825`) is run and published.

Alternatives considered (Phase 2, step 9a):
- A `rejudge` command inside `gpt54_baselines.py`: rejected. That module is about arms, pairs and the
  default-change rule, and its `_check_args` (gpt54_baselines.py:3949-4026) is built around arms. A post-hoc
  pass over saved runs sits beside it, as `analyze_gpt54_evidence.py` does. Reviewer 4 agreed.
- The #44 `VerdictCache` / `benchmarks/llm_judge.py` path: rejected. It regenerates answers first
  (`benchmarks/runner.py:69-117`) and scores 0-1 floats; the GPT-5.4 harness does not use it.
- Adding the prompt to `run_gpt54_comparison.py`: rejected. Its digest is in the registration, and
  `registered_protocol` refuses a change (gpt54_baselines.py:313-344).
- Mem0's newer `memory-benchmarks` judge (partial credit, 14-day date tolerance): rejected as the target. The
  issue and the audit name the "be generous" prompt that Hindsight's harness derives from.
- Mem0's JSON response format: not available. `gpt54_budget.call` fixes the request body (gpt54_budget.py:103-104)
  and `verify_call` requires it (analyze_gpt54_comparison.py:68-71); `AnswerModel.body` is fixed too
  (ollama_answers.py:92-95). The label is read from the text.

## Must Fix
| # | Finding | Source | Status |
|---|---|---|---|
| M1 | The published-pass test and the BENCHMARKS link point at a result that did not exist yet | Reviewers 4, 6 | Resolved: the pass was run and its result is committed with the test; the test globs for the file instead of hard-coding its date |

## Should Fix
| # | Finding | Source | Status |
|---|---|---|---|
| S1 | One unreadable GPT-5.4 label made a paid pass permanently incomplete | Reviewer 1 S1, 3 C7, 7 S3 | Resolved: one policy on both tracks (`lenient-judge-policy-2026-09-25`): ask once more, then `verdict_unresolved` |
| S2 | "Only the prompt differs" was not true (session noise, policy, server version) | Reviewers 1 S2, 6, 7 S2 | Resolved: wording in docs and `judges.note`; the result records the source's policy and server versions and each row's `strict_outcome` |
| S3 | Replay accepted an unjudged answer as truncated; retry without its first call gave a raw error | Reviewers 1 S3, 3 S1-S2, 4, 5 | Resolved: `_scored` takes `truncated` and refuses an empty call list otherwise; `_recorded_calls` is reused |
| S4 | Date hard-coded in the test and doc link | Reviewers 1, 3, 6, 7 | Resolved: the test globs; the link is filled from the real path |
| S5 | `arm` from the result file used unchecked to build paths | Reviewer 2 S1 | Resolved: `_check_arm` |
| S6 | Absolute local path could land in the published result | Reviewer 2 S2 | Resolved: sources must be published in this checkout; the path is recorded relative to the results folder |
| S7 | API key looked up after `started` and the ledger | Reviewer 3 S4 | Resolved: resolved before the lock, and checked by the CLI before the confirmation |
| S8 | Confirmation said "arm name" and did not name the pass | Reviewers 3 S5, 4 S9, 6 | Resolved: the owner types `lenient-judge-gpt54-comparison-v1` |
| S9 | No run history in the result; log inside the folder | Reviewers 3 S6, 4 S7 | Resolved: run log in `runs/lenient-judge-<run>-locomo.jsonl`, `run_log` in the result |
| S10 | Worker loop and report blocks copied from `gpt54_baselines` | Reviewers 4 S1-S2, 5 S4-S5 | Resolved: `drain_pending`, `new_usage`, `count_calls`, `recorded_attempts`, `coverage`, `provider_cost` shared |
| S11 | Missing `registration_sha256`, failure policy id, calibration statement | Reviewer 4 S3-S6 | Resolved |
| S12 | `--archive` silently ignored for other sources | Reviewers 4 S8, 6 | Resolved: refused |
| S13 | Helpers re-implemented (`_server_versions`, `_ledger_path`, `_summary`, `_announce`, binding projection) | Reviewers 4 S10, 5 S3, S6 | Resolved |
| S14 | `_module_identity` missed modules | Reviewers 3 C5, 5 S1 | Resolved: uses `ANSWER_MODULES` plus the gate module |
| S15 | No test of a source shaped like the real baseline; GPT-5.4 arm branch untested; weak assertions | Reviewer 3 S8-S10 | Resolved: registered-policy source test; the GPT-5.4 arm kind is dropped (none exists and paid runs are off); matches, capsys checks, exact estimate |
| S16 | "Run it again" even when the pass can never complete | Reviewer 3 S11 | Resolved |
| S17 | Misleading `_scored` message | Reviewer 3 S3 | Resolved |

## Consider
- Private helper coupling (Reviewers 3 C1, 4 C1): the shared record helpers now have public names; the pass still
  reads a few private ones (`_status`-level helpers through `drain_pending`, `_run_lock`, `_ledger`, `_publish`).
  Left as is.
- JSON objects with braces inside strings (Reviewer 1 C2): resolved with `raw_decode`.
- Strict judge call hash checked (Reviewer 1 C3): resolved.
- Interval key names (Reviewers 1 C4, 3 C4, 4 C4): resolved (`strict_ci95_*`, `lenient_ci95_*`).
- Reader retry hash in rows (Reviewer 1 C5): resolved.
- Verdict rule not bound (Reviewer 1 C6): resolved (`verdict_rule_sha256`, `failure_policy` in the binding).
- Published name for other sources (Reviewer 4 C2): resolved (named after the source file).
- `REGISTERED_RUN` in the variant namespace (Reviewer 4 C3): resolved (`gpt54-comparison-v1`).
- `--provider` flag (Reviewer 4 C7): not added; the judge follows the source by design.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets in logs or results | PASS | The API key is never written; failure messages are stripped from published results |
| Paid call without approval | PASS | CLI needs a positive cap, the key, a terminal and the typed pass name; `run()` needs a cap |
| Path handling | PASS (after fix) | `arm` checked with `_check_arm`; sources must be published in this checkout |
| Deserialization | PASS | `json` only |
| Network | PASS | Ollama endpoint loopback only; no listeners |
| Test isolation | PASS | Every test uses temporary folders and mocked transports |

## Pattern Consistency Assessment
A separate module matches `analyze_gpt54_evidence.py`. After the fixes the pass shares the answer runs' worker
loop, report blocks, ledger and run-log paths, confirmation and announcement, and records the same fields
(registration, provenance, modules, answer model with its failure policy, server versions, run log, cost,
provider tokens, coverage). Its `failure_policy` block carries its own policy id and only the counts a judge-only
pass can have.

## Redundancy Check
No duplicate category mapping, bootstrap or publish helper. The copied worker loop and report blocks were
extracted into shared helpers. The GPT-5.4 baseline-arm source kind was removed as speculative.

## Wiring Findings
The CLI runs (`--help`, `estimate` with no model calls). New private folders (`lenient-judge/`, `runs/lenient-judge-*`,
`ledgers/lenient-judge-*`) match none of the harness globs; the frozen archive is only read. CI collects the new
test file; the published-pass test reads only committed files.

## Break Scenarios (adversarial)
Pre-mortem headline (Reviewer 7, verbatim): "The lenient LoCoMo score was published as "same judge, only the
prompt differs" and read as vendor-comparable, but the judge model, the parse rule and about 1% run-to-run
verdict churn all differ, and the pass could be quietly redrawn by moving one folder aside."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| S1 | Lenient number read as vendor-comparable | new | High | Medium-High | Fix now | Docs, README and `judges.note` now say it is not vendor-comparable and carry the audit caveats (6.4% wrong gold answers, 93.6% cap, 62.8% vague answers accepted) |
| S2 | "Only the prompt differs" is false | new | High | Low-Medium | Fix now, plus Follow-up | Wording fixed and the measured churn (5 of 444 identical-prompt verdicts) stated; source policy and server versions recorded. A same-session strict control is a follow-up |
| S3 | One unreadable GPT-5.4 label strands the paid pass | new | Low-Medium | Medium | Fix now | Changed the approach rather than accept it: one retry, then `verdict_unresolved`, on both tracks, as Mem0 scores any non-CORRECT label as wrong. Contained and documented before any approval |
| S4 | Pass redrawn silently by moving its folder | new | Low-Medium | Medium | Fix now | Run log outside the folder; a finished pass is refused even after a move; results report the runs |
| S5 | Receipts stop replaying after later edits; no verify command | new | Medium | Low-Medium | Follow-up | The binding now pins the verdict rule and policy; a `verify`/`publish` command is a follow-up |
| S6 | Publish date hard-coded | new | Medium | Low | Fix now | Test globs; link filled from the real path |
| S7 | `within_limit: false` published without warning | new | Low | Medium | Fix now | Warning printed and `invalid` recorded in the run log |
| S8 | Verdicts from a run whose identity changed are kept | pre-existing | Low | Medium | Follow-up | Same behavior as `gpt54_baselines.run`; fix both modules together |

Accept:
- The lenient prompt has no authored calibration of its own (Reviewers 1 C12, 4 S6). The result says so
  (`calibration.lenient_prompt: null`), and the DeepSeek pass's retry and unresolved counts over 1,540 real
  answers are stronger evidence of label compliance than two authored cases. Not a regression.

## Follow-ups Raised
Filled in after the issues are created.

## Resolution Status
| Item | Status |
|---|---|
| Must Fix (1) | Resolved |
| Should Fix (17) | Resolved |
| Adversarial Fix now (S1, S2 wording, S3, S4, S6, S7) | Resolved |
| Adversarial Follow-up (S2 control, S5, S8) | Issues raised (see above) |
| Accept (lenient prompt calibration) | Recorded |
