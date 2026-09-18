# LongMemEval-S compact serialization localization

**Decision:** the positional compact renderer remains rejected. Exact-record
compact serialization produced a paired regression on the observed failure
cohort, while the fresh control also exposed substantial hosted-reader
variability. Additional-record limits alone cannot make this renderer safe.

## Question

The monotonic compact answer trial changed two things at once: it serialized
the complete control set as positional compact arrays, and it admitted 4 to 15
additional records. This post-hoc diagnostic asked whether compact syntax still
changes answers when both arms contain exactly the same records at exactly the
same representations, with identical guidance and coverage notices.

## Protocol boundary

The cohort contains all 20 questions whose correctness changed in the failed
119-question trial. Those questions, answers and prior outcomes were observed
before registration. This is causal localization for choosing another
experiment, not validation or product-promotion evidence.

For every question the runner reopened the frozen PRME pack and reproduced the
auditable control byte for byte. It then rendered only the selected control
records in compact form. The record IDs, representation levels, record count,
guidance inclusion and token budget matched. DeepSeek v4.1 Flash generated one
fresh answer per arm in counterbalanced order. The same calibrated GPT-OSS 120B
judge scored all 40 answers after generation completed.

## Result

| Metric | Auditable control | Same-set compact |
|---|---:|---:|
| Correct | 9/20 | 6/20 |
| Paired wins / losses / ties | — | 1 / 4 / 15 |

Three losses were multi-session questions and one was temporal reasoning. The
one gain was a single-session user question. Reader and judge executions both
completed with zero failed calls.

The compact arm used a mean 2,784 memory tokens versus 3,970 for auditable,
saving a mean 1,186 tokens while retaining the same mean 24.25 records. Savings
ranged from 962 to 1,324 tokens. Token efficiency therefore remained real, but
did not preserve answer behavior.

Result identity:
`acdcbb6748cddd32476a154197accb974afc5c398c97c110ef7af6b1ae987628`.

## Interpretation

Within this paired run, positional compact syntax caused three net answer
losses without changing evidence. The failures included treating repeated
events as separate items, hedging a numeric answer, and emitting an internally
contradictory duration. This makes the positional array schema and short local
references a material suspect independent of extra memories.

The hosted reader was not stable enough for a stronger causal estimate. The
fresh auditable judgments agreed with the prior run on only 13 of 20 questions,
despite the same declared model alias, digest, prompt settings and temperature
zero. That variability is retained as evidence against using a one-generation
cloud trial to tune fine differences. The within-run comparison still rejects
the renderer on this observed cohort, but cannot allocate every original loss
between serialization, additional records and reader variance.

## Consequence

Do not proceed with the 381-question monotonic confirmation, expose the
experimental function, or add a public receipt version. Keep auditable packing
as the product behavior. The next retrieval experiment should reduce redundant
or distracting evidence while retaining named, self-describing fields. If a
shorter renderer is revisited, use named fields rather than positional arrays
and require repeated paired trials or a stable local reader before evaluating a
fresh task cohort.

The registration is bound to commit `7a2d192`, the failed answer result, its
complete execution, neutral prepared inputs, reference file, judge calibration
and exact model declarations. Raw contexts, answers, judgments and retry state
remain under `data/benchmarks/longmemeval-s-compact-localization-v1/`.

The subsequent [named chronological view](LONGMEMEVAL-S-TEMPORAL-VIEW-DEV-V1.md)
also failed its paired development gate, scoring 14/29 against the auditable
control's 18/29 while retaining exactly the same records. The next temporal
candidate therefore needs query-aware relation computation rather than another
whole-context serialization change.
