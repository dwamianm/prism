# LongMemEval-S factored metadata answer development v1

**Decision:** Reject named metadata factoring in its tested form. Do not run the
registered fill follow-up or expose this rendering in the product.

**Date:** 2026-09-18

**Registration SHA-256:**
`9d7b1bd5a74ed528e59681d2cdb140295ff0243970e31559ca106baf67501264`

## Question

Can PRME remove repeated audit fields from individual records, place their exact
values in a named section header and save context without reducing answer
quality?

## Protocol

The paired trial used the stable 119-question PRME LongMemEval-S development
split and the current balanced auditable contexts. The `control` and `factored`
arms contained identical records in identical retrieval order. The candidate
factored only exact section-common values for `type`, `scope`, `epistemic`,
`memory_lifecycle`, `source_type`, `representation` and `valid_to`. Every record
retained its UUID, content, event time, validity start and varying fields. A
preflight rehydrated every candidate and verified the complete field values and
record order against control.

The candidate saved a mean 725.471 tokens per question, with a median of 709 and
a range of 433 to 952. Arm order was counterbalanced by question-ID hash.
References were absent from reader inputs and became available only after all
238 reader calls completed. The registered DeepSeek 4.1 Flash reader and
calibrated GPT-OSS 120B judge each used one generation per distinct prompt and
forbade selective retries.

## Result

The run completed with zero reader or judge failures. It produced 238 unique
reader generations and 214 unique judge calls for 238 logical verdicts. The
candidate failed every answer-quality gate.

| Arm | Correct | Accuracy | Paired wins / losses |
| --- | ---: | ---: | ---: |
| Control | 73/119 | 61.34% | - |
| Factored | 69/119 | 57.98% | 4 / 8 |

Factoring regressed two categories. Multi-session fell from 15/30 to 11/30 with
zero wins and four losses. Temporal reasoning fell from 14/28 to 13/28 with one
win and two losses. Knowledge-update improved from 14/20 to 15/20, while the
other categories tied.

## Failure localization

The factored arm changed no evidence identity, value or relevance order, so the
12 answer transitions isolate presentation. Four of the eight losses refused to
combine separate records into a total even when control correctly combined the
same records. For example, control summed four distinct charity amounts to
`$3,750`; the factored reader listed the same four amounts and then declined to
confirm the total. Another loss declined to compute 20 of 100 leadership roles
as 20 percent because the values came from separate statements. A dated update
loss treated two time-indexed apartment-duration claims as an unresolved
contradiction, while control selected the latest value.

Repeated per-record fields therefore carry useful local cues for this reader.
Moving those values into an inheritance header is structurally reversible but
is not semantically lossless for answer generation. The trial does not identify
one field as causal, and its four wins show that the effect is not uniformly
harmful. The registered noninferiority rule still rejects the rendering, and a
fill arm cannot rescue a presentation that loses with identical evidence.

## Verification and artifacts

An independent pass verified the result self-hash, registration hash, execution
file hash, exact execution snapshots, all reader and judge response hashes,
complete arm coverage, recomputed metrics, native exit zero and zero failed
attempts. The result identity is
`c40bf805b4d31940f261532de5f6dd9e4d3820adead37c7699a7ad7627c25e63`.
The execution identity is
`e7be1d00d216e5a9505af17277cd3b0d2279c75238c3502b69ecf51837b0db91`.
The summary artifact is
[longmemeval-s-factored-answer-dev-v1-results.json](longmemeval-s-factored-answer-dev-v1-results.json).
Complete prepared inputs, references, responses, verdicts and resumable states
remain under
`data/benchmarks/longmemeval-s-factored-answer-dev-v1/`.

## Consequence

Remove the rejected assay implementation after preserving this result. Stop
tuning generic serialization on this inspected LongMemEval-S development split.
The repeated failures on multi-session totals point to a different boundary:
use complete typed aggregation when the store can prove completeness, and keep
ordinary retrieval explicitly non-exhaustive. Evaluate that path on a newly
registered workload with item-level provenance before changing product behavior.

This is development evidence from an already inspected benchmark, one hosted
reader alias and a custom calibrated judge. It is not an official LongMemEval
score, independent confirmation, competitor comparison or universal rejection
of compact memory formats.
