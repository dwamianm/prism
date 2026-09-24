# Code Review: issue-129-deepseek-paired-test-interleaved-baseline

## Files Changed
- `benchmarks/integrations/gpt54_baselines.py`:
  - New `run_pair()` and `run-pair` CLI command. A variant (or a plain arm, or the baseline itself for
    the A/A check) is answered together with a fresh run of a recorded defaults baseline, in one session,
    from one queue interleaved question by question (after side, then before side). Pairs live under
    `pairs/<baseline>/<arm>/<benchmark>/pair-<N>/{before,after}` with a `pair.json` record, and each
    baseline and arm has one append-only pair run log under `runs/pairs/`.
  - `_answer` takes a list of `_Side`s that share one queue; `report()` takes the folder the answers live in.
  - `run()` refuses a named variant on the Ollama track, and logs the live Ollama server version at every
    start, finish and model change; results carry `server_versions`.
  - `compare()` accepts only the two sides of one pair (defaults as `--before`) on the Ollama track, or a
    sequential repeat of two standalone baselines with the same inputs; refuses any recorded server version
    change; resamples LoCoMo conversations (reusing `cluster_statistics`), with the LoCoMo `interval_95`
    spanning the conversation-level and question-level intervals.
  - `prepare()` refuses an arm with a complete pair and records a new preparation after an unfinished pair.
- `tests/test_gpt54_baselines.py`: existing tests moved to the new behavior; new tests for interleaving,
  resume and next-pair rules, abandoned pairs, samples, the A/A repeat, server versions, the prepare guard,
  intervals, locks, CLI parsing and dispatch, and the published #118 intervals.
- `CLAUDE.md`: the default-change rule rewritten to the owner's #129 decision.
- `BENCHMARKS.md`: the DeepSeek workflow, safeguards, commands, `compare` description, the #118 floor
  section, and a new "Interleaved A/A check" section.

## Approach Summary
The owner decided in #129: (1) each variant run gets its own defaults run in the same session, interleaved
question by question, and `compare` pairs a variant only with that run; the confirmation repeats the whole
pair; (2) LoCoMo intervals resample conversations, LongMemEval-S keeps questions; (3) record the live Ollama
server version at each start and resume, and have `compare` refuse a pair whose version changed; (4) answer
one interleaved A/A pair on both benchmarks, and add a margin condition if it still fails.

Premise: the #118 A/A failure came from pairing every variant with one fixed baseline draw answered hours
apart. Answering both sides of every comparison in the same session removes the shared baseline draw and
the drift between sessions. It does not remove chance (a 95% interval still excludes zero 1 time in 20
when nothing changes), which is why step 4 keeps a margin condition.

Sibling entry points checked: `run` (a standalone variant run is now refused, so it cannot produce a
pairable result), `compare` (API and CLI share one function), `prepare` (guard against re-preparing a
scored arm now also reads pair logs), `baseline_arm` (unchanged: a baseline still needs its own `run`,
which `run_pair` now requires, so the baseline chain keeps working). The GPT-5.4 track has no defaults
arms, so its standalone comparisons are unchanged.

Alternatives considered:
- A `--paired-with` flag on `run`: rejected, because `run` is built around one arm folder, one lock and one
  run log (`run()`, `_run_lock(folder)`), and a pair needs two answer folders under one lock and one log.
- New `prme@<commit>` baselines for every variant: rejected. `prepare prme` works only from `main` and once
  per commit (`baseline_arm`), so a variant on a branch could not get one.
- A new cluster bootstrap: replaced by `cluster_statistics` in
  `benchmarks/diagnostics/compare_public_captures.py`, which gives bit-identical results on the #118 pair.
  Moving it into `benchmarks/compare_evidence.py` was rejected, because other registrations pin that
  file's digest (for example `benchmarks/mem0_raw_eval.py`).
- Conversation-level interval alone for the LoCoMo decision: rejected after review (see break scenario 1).

Shared state touched: private folders and logs under `data/ollama-answers-v1/<track>/pairs/` and
`runs/pairs/` (gitignored, append-only); arm run logs gain `server_version` fields and, for pair-only
arms, `prepared-again` events; published pair results under `benchmarks/results/research/<date>/`; the
`compare` output gains `pair`, `server_versions`, `interval_unit` and LoCoMo interval fields. No `src/`
file changed, so no product default changed and the evidence gate does not apply.

## Must Fix
1. Broken `#interleaved-aa-check` anchor in `BENCHMARKS.md` (Agents 3, 4, 5, 6, 1). Resolved: the section
   is added and records the A/A attempts.
2. `CLAUDE.md`, `BENCHMARKS.md` and the `repeat` note disagreed on when a default may change (Agent 3).
   Resolved: all three say the test can be used once the A/A check is recorded, with the margin condition
   when an A/A interval excludes zero.

## Should Fix
1. Reuse the existing cluster bootstrap instead of a fourth copy (Agents 4, 5). Resolved.
2. `_Side.arm` was dead (Agents 3, 4, 5). Removed; `folder` renamed `prepared`.
3. Pair `finished` events lacked `prepared_commit`, and `started` recorded only one side's answer model
   sha (Agents 3, 4). Resolved.
4. Pair results' `run_log` lacked the arm's history, and re-preparing a pair-only arm left no record
   (Agents 3, 4, 6). Resolved: `run_log.arm` holds the arm's history, and `prepare` logs `prepared-again`
   when a pair log shows an earlier start.
5. A baseline answered only in pairs would block `baseline_arm` for later commits (Agents 3, 4, 6).
   Resolved: `run_pair` requires the baseline's own complete run.
6. A pair was stuck after a model identity change or a re-preparation, and was resumed after a server
   version change only for `compare` to refuse it (Agents 1, 3, 6, 7). Resolved: `_pair_blocker` starts the
   next pair and logs an `abandoned` event with the reason.
7. A server version change within one session published a pair `compare` always refuses (Agent 1).
   Resolved: the pair is kept but not published, and its `finished` event says why.
8. Pairs were dropped with no reason on record (Agent 1). Resolved: `abandoned` events and an
   `earlier_pairs` list in every pair result.
9. Calibration was checked after `pair.json` was written; `pair.json` was not written atomically; each
   side computed provenance separately; one side could be written before the other was verified (Agents 3,
   4, 6). Resolved.
10. CLI: the paid branch was a catch-all, messages were shared or stale, `run-pair` returned from inside
    another branch (Agents 2, 4, 6). Resolved.
11. `_pair_of` used one message for five failures and accepted keys missing on both sides (Agents 3, 4).
    Resolved.
12. Docs overstated what `compare` checks across pairs, miscounted `pairs_started`, and described only
    `run` where `run-pair` also applies (Agents 3, 4, 6, 7). Resolved.
13. Missing tests (all agents). Resolved: see Files Changed.

## Consider
- Two server version accessors and a duplicated sort key; a shared numbered-folder helper; lock message
  naming the pair; loading the A/A arm once; `pairs_started` from a pair log path helper (Agents 3, 4, 5).
  Applied.
- `repeat.interleaved` duplicates `pair is not None`; `pair.json` has both a random id and a digest
  (Agent 5). Kept for readability; the id is a stable handle independent of formatting.
- `_run_history` generalization for pairs (Agent 5). Not applied; pair and arm histories have different
  keys, and pair results now include the arm's history.
- A fixed after-then-before order inside each pair could favor one side on serving effects (Agent 1).
  Kept: the owner specified the order.
- No lock across the whole track, so two concurrent pairs can exceed four requests (Agents 4, 6).
  Accepted: pre-existing for two standalone runs, and it affects both sides of each pair equally.
- A failed record check freezes a pair (Agent 1). Accepted: pre-existing for `run`, and only reachable
  through corrupted records.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Paid API through `run-pair` | PASS | Refused without `--provider ollama`; `run_pair` refuses a non-Ollama model; the paid CLI branch is now explicitly `run` |
| `OPENAI_API_KEY` reads | PASS | Only `_api_key`, reached only from `run()` on the openai provider |
| Path traversal (arm, variant, baseline, benchmark) | PASS | Full-match name patterns and argparse choices before any path is built |
| Glob patterns | PASS | Names contain no glob metacharacters; one arm's log cannot match another's (tested) |
| Published content | PASS | No paths or secrets in the new fields; failure messages stay private (tested) |
| Deserialization | PASS | JSON only |
| Locking | PASS | One non-blocking lock per baseline, arm and benchmark covers pair choice and all pair writes (tested) |

## Pattern Consistency Assessment
`run_pair` mirrors `run` step by step (calibration, binding, identity check before and after, model-changed
event, sample handling, verification, publishing), shares `_label_sample`, `_publish`, `_complete_result`
and `_append_event` with it, and records the same run log fields plus the pair number. Published names
follow the existing `-vs-` convention. Differences from `run` are deliberate: one lock and log per pair
root, `earlier_pairs`, and no publishing when the server version changed.

## Redundancy Check
The cluster bootstrap reuses `cluster_statistics`; numbered folders share `_numbered`; server version
lookups share `_version_of` and `_sorted_versions`. No new dependency (`random` was dropped again).

## Wiring Findings
`run-pair` is in the argparse choices and dispatched; every flag combination is validated and tested;
folders are created before writes; `data/` is gitignored; CI runs the new tests; the #118 comparison still
passes `compare` with the numbers quoted in `BENCHMARKS.md`, now pinned by a test.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "a default flipped on a lucky LoCoMo draw, because ten-conversation
intervals label about 91% coverage as 95% and `run-pair` hands out fresh pairs until one passes."

| # | Scenario | Trigger | Likelihood | Impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|---|---|
| 1 | LoCoMo decision interval too narrow | Any LoCoMo pair judged on a percentile bootstrap over 10 conversations (simulated 8.5% to 8.9% false exclusions instead of 5%) | High | A default flips on noise through the LoCoMo leg | Newly introduced | Fix now | The LoCoMo `interval_95` now spans the conversation-level and question-level intervals, so it is never looser than either; simulated false gains fall to about 2.8% (target 2.5%). Conversation resampling stays, as the owner decided |
| 2 | Re-drawing pairs until one passes | A failed pair is rerun, the baseline switched or the arm renamed, and the passing pair cited | Medium | High | Pre-existing (honor system), made easier | Follow-up (#130) | Every pair result now lists every earlier pair of that baseline and arm and how it ended, and gives-ups are logged. Tying pairs across baselines and renamed arms is #130's scope; I added the pair-specific angle there |
| 3 | Resuming a pair after an Ollama upgrade | A pair stops and resumes under a new server version | Low-Medium | A wasted pair `compare` refuses | Newly introduced | Fix now | `_pair_blocker` starts the next pair and logs why |
| 4 | `CLAUDE.md` promised a cross-pair identity check `compare` cannot make | Identity or server version changes between the A/A, a first pair and its confirmation | Low-Medium | The A/A gating the test comes from another model or server | Newly introduced | Fix now | Rule reworded: `compare` checks within a pair; the flip PR lists every pair's identity and server versions; the A/A is measured again when the identity or server version changes |
| 5 | A pair that can never finish blocks later runs | Model identity change or re-preparation after an unfinished pair | Low | Loud, stuck runs | Newly introduced | Fix now | `_pair_blocker` covers both and logs the reason |
| 6 | A complete pair that is never published | Crash between the `finished` event and publishing | Low (sub-second window) | The pair exists only privately; the next run starts a new pair | Newly introduced (mirrors `run`) | Accept | Same window as `run()`; private results stay complete and verifiable, and the pair is listed in later results' `earlier_pairs`. Reordering only moves the window |

Also raised by Agents 1 and 3: an A/A pair sends byte-identical requests back to back, which could make
its two sides agree more than a real pair's would. Agent 7 checked the saved answers: the hosted model
rarely repeats itself on identical requests (reader text matched on 20 of 292 questions 15 minutes apart).
Verdict: Dismissed on evidence from the A/A attempts below (identical reader text on 46 of 1,229
questions, differing verdicts on 30).

## A/A attempts (step 4 of the owner's decision)
After committing the code, I answered `run-pair prme --baseline prme@46647825` from a clean tree on both
benchmarks (model identity `e04da138`, Ollama 0.34.3, the #118 settings). Four LongMemEval-S pairs and two
LoCoMo pairs each stopped at a final failure, so none published a result:

| Pair | Answered by both sides | What stopped it |
|---|---:|---|
| LongMemEval-S 1 | 264 of 500 | Both sides' reader answers to `gpt4_7abb270c` ran to the 8,192-token limit |
| LongMemEval-S 2 | 267 of 500 | After side: `gpt4_7abb270c` ran to the limit |
| LongMemEval-S 3 | 261 of 500 | Before side: `gpt4_7f6b06db` ran to the limit |
| LongMemEval-S 4 | 314 of 500 | Before side: verdict on `gpt4_385a5000` had a stray leading character |
| LoCoMo 1 | 61 of 1,540 | Before side: verdict on `conv-26-q0060` had stray leading characters |
| LoCoMo 2 | 62 of 1,540 | After side: verdict on `conv-26-q0062` had a stray leading character |

`gpt4_7abb270c` ran to the limit in 4 of its 11 reader answers so far (#124), and stray-character verdicts
appeared 3 times in 2,470 judge calls but never in the 4,080 of the published runs. A pair doubles the
answers one final failure can stop, and the registered retry policy never asks such a question again, so an
interleaved pair rarely completes today. I stopped after these attempts rather than keep drawing pairs
against the account's usage limit, and raised #132 for the failure-policy decision. The A/A criterion of
#129 stays open, so the pull request says `Part of #129`.

The machinery behaved as designed during the attempts: every stopped pair was given up with its reason on
the next run, the next pair started from the first question, and nothing was published.

## Follow-ups Raised
- #132 (new): DeepSeek pairs rarely complete, because one truncated answer or garbled verdict on either
  side stops the whole pair. Blocks the A/A check.
- #130 (existing): commented with the pair-specific redraw angle (scenario 2).

## Resolution Status
| Item | Status |
|---|---|
| Must Fix 1-2 | Resolved |
| Should Fix 1-13 | Resolved |
| Consider items | Applied or recorded above |
| Break scenarios 1, 3, 4, 5 | Fixed |
| Break scenario 2 | Follow-up on #130 |
| Break scenario 6 | Accepted |
| Identical-request A/A concern | Dismissed on evidence |
| A/A check (owner step 4) | Attempted; blocked by final failures, #132 |
