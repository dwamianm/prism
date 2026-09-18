# BEAM direct-source projection ablation

Projecting all top-50 exact evidence groups to their direct source passages
scored **11/20 (55.0%)** with a mean rubric score of **0.46250**. The registered
cap-only baseline scored 13/20 with a 0.54833 mean. The paired result was one
pass-level win, three losses, sixteen ties, and a **-0.08583** mean delta, so the
registered failure rule rejects this policy for confirmation or default use.

The run copied the accepted extracted-memory pack and changed retrieval only.
It reused the same 20 questions, answerer, judge, prompts, top-50 cutoff,
reference clock, and source-time policy. Hash checks confirmed that the original
DuckDB, vector, and lexical artifacts were unchanged. The
[registration](beam-100k-source-projection-ablation-v2-registration.json) and
[machine-readable result](beam-100k-source-projection-ablation-v2-results.json)
bind the protocol and aggregate outcome.

## Ability tradeoff

| Ability | Cap-only pass | Projection pass | Cap-only score | Projection score |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 0/2 | 0/2 | 0.00000 | 0.00000 |
| Contradiction resolution | 1/2 | 0/2 | 0.25000 | 0.25000 |
| Event ordering | 1/2 | 2/2 | 0.41665 | 0.65000 |
| Information extraction | 1/2 | 1/2 | 0.29165 | 0.50000 |
| Instruction following | 1/2 | 1/2 | 0.50000 | 0.50000 |
| Knowledge update | 2/2 | 1/2 | 1.00000 | 0.50000 |
| Multi-session reasoning | 2/2 | 2/2 | 0.87500 | 0.87500 |
| Preference following | 2/2 | 2/2 | 1.00000 | 0.62500 |
| Summarization | 2/2 | 2/2 | 0.65000 | 0.60000 |
| Temporal reasoning | 1/2 | 0/2 | 0.50000 | 0.12500 |
| **Overall** | **13/20** | **11/20** | **0.54833** | **0.46250** |

Direct sources fixed the remaining event-ordering question and raised the other
ordering score from 0.50 to 0.80. The detailed sprint extraction rose from
0.5833 to 1.0. These gains show that full source wording solves a real failure
mode rather than merely changing the answer model at random.

Wholesale replacement also discarded useful abstraction. One contradiction
question, one current-state update, and one temporal calculation crossed from
pass to fail. Preference and summarization questions retained pass status but
lost partial credit. Compared with the accepted uncapped dev5 result, projection
gained both ordering questions but lost contradiction, instruction, current
state, and temporal questions.

## Diagnosis

The source projection did place the original January 15-to-March 15 schedule and
the complete versioned dependency response in the top 50. That alone was not
sufficient. Fifty full conversation turns greatly increased prompt volume, and
the chronological adapter ordering diluted the compact evidence. For current
state, raw messages also exposed obsolete values beside their replacements: the
answer chose 300ms instead of the newer 250ms. For temporal arithmetic, it chose
an extended March 31 sprint date instead of the requested initial March 29 date.

The evidence supports keeping direct-source projection as a bounded opt-in
primitive. A stronger retrieval policy must preserve both layers: concise claims
for current state and contradiction handling, plus selected sources for
chronology and missing detail. It also needs an explicit timeline/initial-state
signal instead of treating every temporal question as a recency query.

## Scope

This is a tuned one-conversation development ablation over an already examined
pack. It is useful causal evidence about representation choice, but it is not an
untouched confirmation, official-scale BEAM result, or market comparison.
