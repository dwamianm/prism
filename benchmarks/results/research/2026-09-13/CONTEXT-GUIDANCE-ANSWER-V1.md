# Context-guidance answer study v1

Status: **automatic gate passed; expert audit did not approve product wiring**.

The prospectively registered development study added deterministic temporal,
current-state, or personalization guidance to every eligible balanced 4K
context. The guidance was counted inside the existing memory budget. Frozen
Qwen and Gemma model digests, prompts, options, case identities, contexts, and
pass criteria were recorded before generation. All 46 reader calls and all 46
unique judge calls completed without failures or retries.

## Result

| Guidance | Questions | Baseline correct | Guided correct | Gains | Losses |
|---|---:|---:|---:|---:|---:|
| Temporal | 37 | 27 | 28 | 3 | 2 |
| Personalization | 6 | 2 | 3 | 1 | 0 |
| Current state | 3 | 3 | 3 | 0 | 0 |
| **All** | **46** | **32** | **34** | **4** | **2** |

The automatic gate passed. Annotated-source recall was unchanged on all 46
development questions.

## Required audit

Three transitions are credible improvements: one personalized baking answer
used the user's successful lemon-poppyseed cake, one membership interval was
correctly computed as two weeks, and one date interval was corrected from ten
days to six days.

The remaining apparent gain is a benchmark-reference defect. On question
`gpt4_7ddcf75f`, a June 17 record says a rafting trip happened “three days ago”
and the question is dated June 20. The semantically resolved answer is six days
before the question, but the reference expects three. V1 received credit for
copying the record-relative phrase without resolving it to question time. That
outcome must not be treated as a memory-quality gain.

Both losses are real. One answer refused a one-week interval after failing to
resolve two record-relative dates; another subtracted April 15 from April 21 as
eight days. These failures show that generic prompt guidance is not a reliable
substitute for normalized temporal evidence and checked arithmetic.

V1 therefore remains diagnostic evidence. It is not wired into retrieval. The
next experiment must distinguish statement time from normalized claim time,
make question time explicit, preserve the balanced evidence set by consuming
the existing caller-overhead reservation, and treat record-relative phrases as
dates to resolve rather than answers to copy.

Artifacts:

- Registration: `context-guidance-answer-dev-v1-registration.json`
- Exact results: `context-guidance-answer-dev-v1-results.json`
- Results SHA-256: `fe0b1b85035792fc96f331bbf501f718ec7e3cd8127a78308d716ec752a402f4`

Limits: all 46 questions and their baseline outcomes were previously examined;
the judge is a calibrated local proxy rather than the official evaluator; the
cohort is development evidence and cannot support a competitive claim.
