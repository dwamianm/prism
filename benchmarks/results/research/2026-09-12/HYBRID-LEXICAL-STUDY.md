# Full-hybrid lexical development study

All 119 registered questions completed at frozen commit
`a744fe0b7715f749614bb141282fc8783c6b8f04`, with zero errors and native exit 0.
The separate verifier reproduced all 1,428 saved contexts and their measurements,
checked source content, speaker provenance, scope, dates, candidate invariance,
snapshot hashes and registered runtime/model identities, and recomputed the
summary. See the [registration](hybrid-lexical-dev-plan.json) and
[completion record](hybrid-lexical-dev-completion-a744fe0.json).

Each question used one raw-source memory pack. Every arm called the real hybrid
retrieval pipeline; no extraction model was called. The two lexical policies
were the native parser and an experimental literal query with English stopwords
removed. Both used both product packing orders and all three budgets. All other
configuration and source data were held fixed. A repeated control per question
matched. Quality results were examined only after the study's native completion.

Source-evidence recall on the 114 labelled development questions:

| Packing | Tokens | Native parser | Stopwords removed | Change, percentage points (95% interval) |
|---|---:|---:|---:|---:|
| Density | 2,048 | 65.13% | 82.16% | +17.03 [11.62, 23.03] |
| Density | 4,096 | 74.85% | 85.96% | +11.11 [6.58, 16.01] |
| Density | 8,192 | 82.02% | 91.81% | +9.80 [5.48, 14.62] |
| Score | 2,048 | 88.16% | 86.84% | −1.32 [−4.97, 2.34] |
| Score | 4,096 | 93.49% | 90.94% | −2.56 [−6.07, 0.22] |
| Score | 8,192 | 96.35% | 96.42% | +0.07 [−1.68, 1.90] |

The effect depends on packing. At 4K, density improved in five categories but
still retained no complete labelled source for any of the nine assistant
questions. Under score packing, removing stopwords reduced multi-session recall
from 84.64% to 78.65% (32 questions; delta interval −11.46 to −1.56 points),
assistant recall from 100% to 88.89%, and user recall from 100% to 94.12%.
Preference recall increased from 92.86% to 100%, on only seven questions.
All category results, including ties and losses, are retained in the record.

Production defaults remain unchanged. Stopword removal is not uniformly
beneficial, and the completed 381-question packing confirmation's failed
preference gate still stands. A moderate length penalty is a new exploratory
hypothesis; it must not inherit either study's evidence as confirmation.

These are previously examined development questions and a source-retention
proxy, not independent confirmation or answer accuracy. Question bootstrap
intervals do not account for shared histories. This English-only local study
does not evaluate PostgreSQL query semantics, semantic extraction, or competitive
leadership. Runtime timing is operational accounting, not a speed comparison.

Raw output: `data/benchmarks/hybrid-lexical-dev-a744fe0.json`, SHA-256
`046a800f5eb5f36be06b160d65e48f17a32ca43ba82ceb8615b410e39348e75b`.
Snapshots remain under `data/benchmarks/hybrid-lexical-dev-a744fe0-snapshots/`.
