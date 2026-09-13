# Completed native raw-memory source comparison

Both fresh `2f3ac2e` workers completed all 119 development questions and verified
all 59,021 admitted source turns, with zero errors and observed native exit zero.
The analyzer reproduced all 714 contexts across both products and three budgets,
verified saved capture identities and token ceilings, and exited zero. Failed
earlier captures were not reused. The [complete results](hindsight-prme-native-dev-source-results.json)
retain every category, query and cluster bootstrap summaries, runtime counts,
artifact hashes and native completion records.

This follows the [registered native-provenance protocol](HINDSIGHT-PRME-NATIVE-DEV-PROTOCOL.md):
installed PRME `b7521bc` and Hindsight `bde55237`, matched BGE assets and numerical
dependencies, raw ingestion without LLM extraction or consolidation. PRME uses
default density packing. Hindsight contexts use the disclosed whole-record JSONL
adapter with exact native text and provenance fields. Six returned units across
two cases lacked optional custom metadata; they passed the registered native
ownership/text checks, and their missing fields were not reconstructed.

## Registered source-ranking outcome

The primary descriptive comparison uses the first 100 unique ranked source IDs
with the same evaluator whole-turn packer. This reconstructs original turns and
is separate from the actual contexts sent to readers. Scores below are mean
labelled-source recall over 114 questions; five unlabelled questions have null
source scores. They are not counted as correct abstentions.

| Token budget | PRME | Hindsight | PRME minus Hindsight, points | 95% cluster interval, points |
|---|---:|---:|---:|---:|
| 2,048 | 92.25% | 41.59% | +50.66 | +42.52 to +58.62 |
| 4,096 | 96.35% | 52.27% | +44.08 | +35.67 to +51.96 |
| 8,192 | 97.95% | 82.82% | +15.13 | +9.49 to +21.88 |

## Actual rendered context coverage

A document hit means a rendered record contains nonblank text from a labelled
source. A partial chunk can qualify even if it omits the answer-bearing sentence.
Whole-turn record recall additionally requires the entire original turn in one
record. Neither metric proves an answer is supported or correctly generated.

| Token budget | PRME document hits | Hindsight document hits | PRME whole-turn records | Hindsight whole-turn records |
|---|---:|---:|---:|---:|
| 2,048 | 66.08% | 23.03% | 66.08% | 22.15% |
| 4,096 | 74.85% | 45.91% | 74.85% | 45.03% |
| 8,192 | 82.02% | 75.66% | 82.02% | 74.78% |

At 4K the document-hit difference is +28.95 points, with a cluster interval of
+18.90 to +38.39. At 8K the interval includes zero (-2.58 to +14.94 points).
The actual 4K category results expose a substantial PRME packing failure:

| Category | Labelled questions | PRME document hits | Hindsight document hits |
|---|---:|---:|---:|
| Knowledge update | 20 | 85.00% | 55.00% |
| Multi-session | 32 | 66.67% | 29.69% |
| Assistant evidence | 9 | 0.00% | 77.78% |
| Preference | 7 | 85.71% | 7.14% |
| User evidence | 17 | 94.12% | 76.47% |
| Temporal reasoning | 29 | 86.21% | 39.08% |

PRME's shared whole-turn packer retained every assistant evidence source at 4K,
while its actual default bundle retained none. This locates a loss between the
available ranking and product context. It does not establish that any particular
replacement policy solves it without other regressions.

## Next evaluation and limits

The [registered common readers](HINDSIGHT-PRME-READER-PROTOCOL.md) consume only
the exact verified actual 4K contexts, with a fresh empty-memory control. Both
plans and the prepared input hash were committed at `f1a0b64` before generation.
Both readers completed all 357 logical predictions each with native exit zero;
every saved response and prompt reproduced offline. Separate calibrated judging
and scoring also completed with native exit zero. The [answer report](HINDSIGHT-PRME-READER-RESULTS.md)
retains every outcome and category loss; the source metrics here remain distinct.

These are development questions that already informed PRME changes, not an
independent holdout. The 111 base-question/history groups do not capture every
partially overlapping history. The source profiles omit both products' full
extraction/graph features. Common input normalization, native metadata omissions
and the Hindsight rendering adapter remain part of the comparison. Individual
timings and embedding request counts are retained, but concurrent host load and
different storage backends do not support a fair speed or dollar-cost ratio.
The result does not establish overall system leadership or authorize a default
packing change.
