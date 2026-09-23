# GPT-5.4 + Jev on the 70 failed LongMemEval-S questions

Completed 2026-09-23 UTC. **No demonstrated answer-quality gain from temporal
augmentation in this diagnostic.** All 70 matched pairs completed, with no
provider or retrieval failures. The study preserves the earlier full benchmark
at **430/500 (86.0%)**; it does not establish a new full-cohort score.

## Answer results

| Arm | Correct among 70 historical failures |
|---|---:|
| Fresh GPT-5.4 control | **6/70 (8.57%)** |
| GPT-5.4 resolver + Jev, with the same GPT-5.4 reader/judge | **7/70 (10.00%)** |

The descriptive paired difference is **+1/70 (+1.43 percentage points)**:
three wins, two losses and 65 ties. The 95% paired bootstrap interval is
**−4.29 to +7.14 points**, identical for question and full-history-cluster
resampling (70 distinct histories; 10,000 draws, seed20260922).

**All five score changes occurred on byte-identical contexts.** They measure
reader/judge rerun variability, not a Jev context effect. The only question with
added temporal guidance stayed incorrect in both arms. Thus the nominal 7
versus 6 is not evidence that Jev repaired an answer. The selected historical
0/70 is not an appropriate fresh causal control.

| Category | Fresh control | Jev arm |
|---|---:|---:|
| knowledge-update | 3/12 | 4/12 |
| multi-session | 2/35 | 3/35 |
| single-session-assistant | 0/3 | 0/3 |
| single-session-preference | 1/3 | 0/3 |
| single-session-user | 0/1 | 0/1 |
| temporal-reasoning | 0/16 | 0/16 |

The feature's 16 routed questions are not the same set as the benchmark's 16
failed temporal-category questions. Routing uses the question text, not labels.
The routed subset scored 0/16 versus 1/16, but its sole score change was also on
unchanged context. The temporal benchmark category stayed 0/16 in both arms.

## What the feature actually did

| Outcome | Questions |
|---|---:|
| Did not trigger temporal reasoning | 54 |
| Resolver reported unsupported | 11 |
| Rejected by local evidence/date checks | 3 |
| Rejected by Jev | 1 |
| Accepted and repacked | 1 |

The accepted case retained both cited records and displaced one uncited record.
All 140 receipt rankings replayed exactly; every fresh control matched the
historical GPT benchmark context. Candidate ranking and candidate source sets
matched control before packing, and all immutable master packs stayed unchanged.

The three local rejections include two questions lacking required day precision
and one with unsupported durations/repeated source records. The Jev rejection
had a minimum operand probability of 0.78, below the frozen 0.85 threshold.
These are valid feature refusals, not provider failures. Unsupported responses
alone do not prove missing evidence or identify a particular retrieval defect.

A post-hoc audit found a possible reference inconsistency in the single accepted
case, `370a8ff4`. Its two annotated user turns date flu recovery to January 19,
2023 and the tenth outdoor jog to April 10, 2023. Both readers computed 81 days,
or 11 weeks and 4 days; the registered reference is 15 weeks. This discrepancy
has not been adjudicated upstream. **Both official incorrect scores are retained;
no question was excluded or relabeled, and no response was replaced.** This case also shows that adding
arithmetic guidance need not improve a reader already doing the same arithmetic.

## Latency, tokens, cost and omissions

| Measurement | Control | Jev arm |
|---|---:|---:|
| Retrieval p50 | 0.421 s | 0.435 s |
| Retrieval p95 | 0.568 s | 20.981 s |
| Mean context tokens | 3,964.67 | 3,964.11 |
| Maximum context tokens | 3,996 | 3,996 |
| Questions missing some annotated evidence | 46 | 46 |
| Annotated turn instances omitted | 72 | 72 |
| Reported conflict flags | 0 | 0 |

On the 16 routed requests alone, candidate retrieval was 8.161 s p50 and
46.215 s p95. These are sequential, local-host cold-retrieval observations;
engine-open time is excluded. The headline p50 mostly reflects non-routed cases.

There were **280 reader/judge HTTP calls, all HTTP 200 on the first attempt**.
Dataset enrichment used **19 OpenAI resolver calls** (including three permitted
schema repairs) and **two Jev calls**, all HTTP 200. The independent authored
preflight added one call to each provider. No provider failure, replacement run,
missing case or invalid verdict occurred.

Estimated OpenAI token cost, including preflight, was **$1.457309**. Dataset
resolver usage was 52,974 input and 19,681 output tokens; Jev reported 1,282
input and 98 output tokens. Jev's dollar price is unavailable and is not
invented. There was no new ingestion: both arms reused authenticated source
packs. Historical ingestion time for these 70 packs totals 10,300.155 seconds;
it is not a fresh latency or cost observation.

## Decision and next work

**Opt-in research only; do not promote this OpenAI + Jev variant as a default.**
The complete experiment did not demonstrate a context-caused answer gain and
incurred substantial latency on routed questions. It does not disprove the
separate, previously confirmed Ollama composition or establish behavior on the
430 questions that were originally correct.

Useful next work is (1) source-selection improvements for the 46 cases with
incomplete annotated evidence; (2) separately registered tests of precision-aware
temporal operations and distinct operands sharing a source record; and (3) an
independent source/reference consistency audit. These are hypotheses, not proven
fixes. Do not weaken the evidence checks, alter the official scores, tune on
these failures and call them confirmation, or add seven answers to 430.
A positive development result still needs a full regression cohort and an
untouched confirmation before any default change.

The explicit OpenAI adapter and its tests are implemented on research branch
`research/jev-gpt54-failures-2026-09-23`; production defaults are unchanged.
Local regression validation passed 80 tests with nine PostgreSQL cases skipped;
an additional ordinary-query no-call test passed on DuckDB and was skipped on
PostgreSQL. Live PostgreSQL validation was not performed. The old default
configuration checksum is unchanged. No feature flag was deployed globally.

## Reproduction and evidence

- [Prospective protocol](JEV-GPT54-FAILURES-PROTOCOL.md),
  [registration](jev-gpt54-failures-v1-registration.json), and
  [authored live preflight](jev-gpt54-failures-v1-preflight.json).
- [Complete result and paired category statistics](jev-gpt54-failures-v1-result.json),
  [140-receipt/context verification](jev-gpt54-failures-v1-context-verification.json),
  and [post-hoc explanatory audit](jev-gpt54-failures-v1-posthoc-audit.json).
- Frozen implementation `765cb871`; verifier/protocol commit `abf96282`.
  Source and dependency hashes, each pack checksum, exact question IDs, provider
  options and prompt hashes are in the registration. The result authenticates
  raw calls, captures, scores and the verifier source.
- Raw artifacts: `data/jev-gpt54-failures-v1/` in the isolated research worktree.
  The main-project archive retains requests, responses, contexts and receipts;
  disposable working pack clones remain in the research worktree, while their
  unchanged master packs remain in the main project.

The resolver uses OpenAI `gpt-5.4-2026-03-05` with medium reasoning and Flex;
the independent gate remains TypeSafe `jev-1.13.0` at 0.85. This changed variant
correctly reports `confirmation_protocol_aligned=false`. Both downstream arms
retain the registered GPT-5.4 reader and official LongMemEval judge, prompts and
scoring rules. No reference answer or evidence label entered either resolver or
Jev request. Product-alignment pair selection/proposals were not used.
