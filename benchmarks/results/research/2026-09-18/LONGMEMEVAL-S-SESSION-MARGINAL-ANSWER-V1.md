# LongMemEval-S session-marginal answer diagnostic v1

**Decision:** Reject product promotion and further LongMemEval-S tuning of this policy.

**Date:** 2026-09-18

**Registration revision:** `4c6c3488d38024464df2640aab77af80fce0b1fd`

## Question

Does the source-level Pareto improvement from the mild session-marginal arm
produce a clear downstream answer benefit on the contexts it changes?

## Why this diagnostic ran

The preregistered 500-question source grid rejected every arm because none
increased fully covered questions. The mild `free_slots=8, decay=0.90` arm still
changed only 31 contexts, improved labeled-turn recall on one question and had
zero turn, session or category losses. Treating the complete-question threshold
as proof that this measured improvement was worthless would overinterpret an
aspirational gate. This follow-up therefore tested the downstream effect while
preserving the original rejection.

## Protocol

The cohort was fixed to all 31 changed contexts before any answer calls. It was
selected by context hashes alone, without answer outcomes. DeepSeek v4.1 Flash
through Ollama generated one answer for each control and candidate context in
counterbalanced order. References were stored separately and became available
only after all 62 reader answers completed. The previously calibrated
`gpt-oss:120b-cloud` judge then applied the fixed category rubrics. Exact prompt
hashes deduplicated identical judge requests.

A pass required complete error-free execution, candidate accuracy at least as
high as control, strictly more paired wins than losses, and no category
regression. Passing would only have retained the private hook for a different
workload; it could not expose an option or change the default. The registration
is
[longmemeval-s-session-marginal-answer-v1-registration.json](longmemeval-s-session-marginal-answer-v1-registration.json).

## Result

The registered gate failed.

| Metric | Control | Session-marginal |
| --- | ---: | ---: |
| Correct | 25/31 | 25/31 |
| Paired wins | — | 1 |
| Paired losses | — | 1 |
| Paired ties | — | 29 |
| Accuracy delta | — | 0.00 points |

The candidate gained one temporal answer: it combined a stored 7:00 AM wake-up
with “15 minutes earlier on Tuesdays and Thursdays” to answer 6:45 AM, while the
control declined to infer an exact time.

The registered judge also recorded one single-session-assistant loss. Both
answers explicitly named **Grilled Snapper with Mango Salsa** and both explained
that Escovitch Fish was Jamaican but did not contain fruit. The judge accepted
the control and rejected the candidate, incorrectly stating that the candidate
did not give the reference name. This is a visible judge inconsistency, so the
run does not establish a real answer regression. The frozen registered verdict
still fails the declared gate and is not rewritten after inspection.

The sole source-recall win from the preceding assay did not produce an answer
win. For question `8e91e7d9`, labeled-turn recall rose from 0 to 0.5, but both
readers still correctly reported that the context did not establish the number
of siblings and both were judged incorrect against the reference answer.

Even under a manual interpretation that converts the questionable loss to a
tie, the evidence is one answer win among 31 changed contexts and one source
win among 500 questions, on an already inspected development workload. That is
too small to justify adding a public configuration and receipt policy or taking
on another confirmation cost.

## Verification and artifacts

All 62 reader jobs and all 62 logical judge cases completed with zero failed
attempts. The judge used 51 distinct calls after exact prompt deduplication. The
reader state, judge state, execution record and final result are checksummed and
complete. The result self-identity is
`a56670043c70ab2ff88dd9adb8f631ce60c29d14ff45f784ed9f16e184d3a40f`.
The committed summary is
[longmemeval-s-session-marginal-answer-v1-results.json](longmemeval-s-session-marginal-answer-v1-results.json),
and the complete runtime artifacts remain under
`data/benchmarks/longmemeval-s-session-marginal-answer-v1/`.

## Consequence

Do not expose session-marginal packing or change the balanced default. Preserve
the experiment and the judge inconsistency in the research record. The next
packing experiment should target the localized missing-turn pattern directly,
use a new workload for confirmation, and require a material answer effect before
adding product surface area.
