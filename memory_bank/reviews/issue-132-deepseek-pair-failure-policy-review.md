# Review: issue #132, DeepSeek pair failure policy

Branch `issue-132-deepseek-pair-failure-policy`, base `main`. Seven review agents
(correctness, security, quality, patterns, redundancy, wiring, adversarial) read
the change; this file records their findings, what was done with each, and the
adjudication of the adversarial scenarios.

## Files Changed

- `benchmarks/integrations/ollama_answers.py`: `answer_record` returns what a
  response answered, marking a response that ended early as `truncated` only for
  a caller that passes `truncated_ok`, and recording an empty answer only with
  `empty_ok`. `call` and `verify_call` take both flags; `verify_call` requires the
  recorded text and truncated mark (type included) to be exactly what the
  response gives. `response_text` keeps its contract.
- `benchmarks/integrations/gpt54_baselines.py`: the amended failure policy
  (`FAILURE_POLICY`, `OLLAMA_RETRY_POLICY`, `policy_parameters`,
  `failure_amendment`, `normalized_verdict`, `_scored`, `_ask_amended`,
  `_outcome_counts`, `_invalid`), bound into every Ollama answer model, pair
  record and run log start; `report` verifies each arm under the policy it is
  bound to; `compare` refuses mixed policies, different amendments, more than 1%
  unscored questions and registered-policy results answered after the amendment;
  `_pair_blocker` gives up pairs started under another policy.
- `benchmarks/results/research/2026-09-24/ollama-failure-policy-amendment.json`
  (new): the registered amendment.
- `tests/test_gpt54_baselines.py`, `tests/test_ollama_answers.py`: new and
  updated tests.
- `BENCHMARKS.md`, `CLAUDE.md`: the amended rules, the compare refusals, the six
  stopped pairs and the current state of the A/A check.

Local data (gitignored, not in the diff): the six stopped pairs were moved from
`data/ollama-answers-v1/ollama-deepseek-v4.1-flash-cloud/pairs/prme@46647825/prme@46647825/`
to the same layout under
`data/ollama-answers-v1/discarded-attempts/ollama-deepseek-v4.1-flash-cloud/`.
Their run logs stay in place.

## Approach Summary

The owner's decision on #132 (issue comment of 2026-09-24): on the Ollama track
only, a truncated reader answer is sent once more and then scored incorrect as
`truncated`; a verdict is normalized and otherwise judged once more, then scored
incorrect as `verdict_unresolved`; at most one retry per call; more than 1% of
either arm unscored makes the pair invalid; a registered amendment; the six
stopped pairs given up and moved aside.

The policy id is added to the answer settings the harness binds
(`answer_model.failure_policy`), not to `AnswerModel.settings()`, so the passed
calibration still matches and every existing guard (binding, pair blocker,
compare) sees the policy. Interpretations made where the decision was silent:
"letters" means A to Z (the observed stray characters were CJK, which Unicode
counts as letters); a judge response that ends early or is empty is an
unaccepted verdict; the authored calibration stays strict; standalone runs use
the amendment too (#124 says its truncation rule is decided here).

## Must Fix

| # | Finding | Source | Resolution |
|---|---|---|---|
| M1 | An empty verdict still stopped the run ("Missing answer text"), which the rule does not allow | Agents 1, 3 | Fixed: judge calls pass `empty_ok`, an empty verdict is judged once more and then `verdict_unresolved` |
| M2 | `failure_amendment()` did not check the registration digest | Agent 4 (also 1, 2, 6) | Fixed: kind, id, registration, policy text, parameters, issue and registration time are checked |
| M3 | The amendment JSON had to be staged with content before commit | Agent 6 | Fixed at commit |

## Should Fix

| # | Finding | Source | Resolution |
|---|---|---|---|
| S1 | `verify_call` trusted the recorded `truncated` flag; `"truncated": 1` passed and scored differently | Agents 1, 2 | Fixed: the record must equal `answer_record` of its response, types included; scoring tests `is True` |
| S2 | An arm with only old-policy failures was silently rebound to the new policy and could never finish | Agent 1 | Fixed: a policy switch is refused once any attempt exists |
| S3 | One message ("Retry outside the registered policy") for four different faults | Agents 3, 4 | Fixed: specific messages for each call-order fault |
| S4 | `compare` could crash with KeyError on amended rows missing fields, and accepted unknown policy ids | Agents 1, 3, 4 | Fixed: `_policy_of` validates ids everywhere; `_outcome_counts` raises ValueError |
| S5 | The amended path was chosen from the track on write and from the binding on read | Agents 3, 4 | Fixed: `run` and `run_pair` take it from the bound answer model |
| S6 | Unscored counts computed three ways; registered-policy scoring built twice | Agents 4, 5 | Fixed: `_outcome_counts` returns `unscored`; `_registered_scored` shared |
| S7 | Standalone runs did not mark a complete result over the limit | Agents 1, 4, 6 | Fixed: `_invalid` records `unscored` and `invalid` in every finished event, with a warning |
| S8 | `compare` did not check both sides used the same amendment | Agents 1, 2, 4, 6 | Fixed |
| S9 | Missing tests: unknown policy, cut-off judge, amendment mismatches, retry without first call, compare edge cases, stderr warning, published block | Agent 3 | Fixed: tests added for each |

## Consider

| # | Finding | Resolution |
|---|---|---|
| C1 | Quadratic strip regex | Fixed: a full-match pattern with ASCII case folding (linear; `ſ` and the Kelvin sign are not folded) |
| C2 | `call` parsed each response twice; `truncated_text` unused | Fixed: one `answer_record`; `truncated_text` removed |
| C3 | `pair.json` did not name its policy | Fixed |
| C4 | `_row` sets `correct` twice | Kept for key order, now commented |
| C5 | `_pair_blocker` reason said "answered" | Changed to "started under another failure policy" |
| C6 | `unscored` in finished events duplicates result counts | Kept: the log is the record read without the result files |
| C7 | `_ask_amended` argument order differs from `_answer` | Left as is; it follows the `ask` call signature |
| C8 | Fake server replies consumed in request order | Tests that script verdicts run at a concurrency of one |

## Security Audit Results

| Area | Result | Notes |
|---|---|---|
| Secrets or private data in published results | PASS | New fields are ids, hashes, counts and booleans; failure messages stay private |
| Paid OpenAI path reachable | PASS | The amended path is Ollama only; the GPT-5.4 path and rows are unchanged |
| Credentials or endpoint | PASS | Retries reuse the loopback client |
| Path traversal | PASS | Retry file names are constants |
| Unsafe deserialization | PASS | JSON only |
| Receipt tampering (truncated flag) | FAIL, fixed | S1 |
| ReDoS | Consider, fixed | C1 |

## Pattern Consistency Assessment

The amendment follows the loader amendment's pattern (checked against the
registration digest at runtime) and records `registered_at`, `changes`,
`validation` and `defaults_changed` like the 2026-09-22 amendments, plus the
digests of the stopped pairs' run logs. Run log events, pair blocker reasons and
`earlier_pairs` states follow the existing formats. Test helpers extend
`ollama_provider` and `answer_result`.

## Redundancy Check

No existing helper could be reused: the registered `study.verdict` is a frozen
source, `benchmarks/diagnostics/reader_judge.py` parses another response format,
and the MemoryArena truncation recovery continues an answer rather than resending
the request. Duplicates found in review were consolidated (S6, C2).

## Wiring Findings

`run`, `run_pair`, `report` replays and `compare` all apply the amendment; the
calibration stays strict on purpose. The amendment file is tracked
(`benchmarks/results/` is not ignored) and CI's `pytest tests/` picks up the
tests, none of which read `data/` or the network. After the six pairs were moved
aside, an offline check of `_pair_blocker` against the real run logs gives up
LongMemEval-S pair 4 and LoCoMo pair 2 as "started under another failure
policy", so the next pairs are 5 and 3.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7, verbatim): "Stale pre-#132 checkout gave up the
amended A/A pair and published a pair under the old rules that the new `compare`
accepted."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | A checkout without #132 gives up an amended pair and publishes one under the registered rules that `compare` accepts | Newly introduced | Low to Medium | High (a check on record under the wrong rules) | Fix now | `compare` refuses a DeepSeek result under the registered rules that started after the amendment was registered; BENCHMARKS.md says to run `run-pair` only from a checkout with #132 |
| 2 | The docs say the six pairs were moved aside while they were not | Newly introduced | High | Low | Fix now | The pairs were moved; the docs say the last two are given up on record by the next `run-pair` |
| 3 | The scoring values are not pinned to the policy id, so a wider verdict rule could ship under the same id | Newly introduced | Medium | Low | Fix now | The amendment pins the verdict pattern and flags, the retry file names, the unscored outcomes and the limit, and `failure_amendment()` checks them |
| 4 | A retry that gets no answer makes the question start over, so a truncated or garbled draw drops out of the counts and the cap | Newly introduced | Low | Low | Follow-up (#134) | Closing it needs resuming attempts mid-question or counting replaced attempts, which overlaps #124 |
| 5 | Judge format drift is absorbed by retries and repairs with no warning | Newly introduced | Medium | Low | Follow-up (#135) | The warning threshold needs a decision |
| 6 | Only one amended policy id is recognized | Newly introduced | Medium to Low | Low (loud) | Accept | Any later amendment needs code anyway, and the failure is a loud refusal, not a wrong result |

Rechecked for the Accept (6): a registry of policy ids now would be an
abstraction with one entry; the refusal names the unknown id, so nothing is
scored wrongly.

Scenarios the adversarial review attacked hardest and found holding: directional
bias from asymmetric retries or gaming the 1% limit (both sides follow the same
rules, interleaved); normalization flipping a verdict's meaning (the full match
refuses "Not", "Nope", "yes and no" and tagged layouts); old data and replays
(`report` verifies under the bound policy, so the published 2026-09-24 results
and the #118 repeat still compare).

## Follow-ups Raised

- #134: A DeepSeek question whose retry gets no answer is asked again from
  scratch, and its first truncated or garbled draw drops out of the counts.
- #135: `compare` says nothing when the DeepSeek judge starts needing retries or
  stray-character repairs.

## Resolution Status

| Item | Status |
|---|---|
| M1 to M3 | Resolved |
| S1 to S9 | Resolved |
| C1 to C5 | Resolved |
| C6 to C8 | Kept, with reasons above |
| Scenarios 1 to 3 | Fixed now |
| Scenarios 4 and 5 | Follow-ups #134 and #135 |
| Scenario 6 | Accepted |
