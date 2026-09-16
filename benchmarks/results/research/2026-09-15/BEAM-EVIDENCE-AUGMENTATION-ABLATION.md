# BEAM dual-representation evidence ablation

Adding one direct source beside each of the top ten exact evidence groups scored
**14/20 (70.0%)** with a mean rubric score of **0.59667**. The accepted extracted
baseline scored 13/20 with a 0.56750 mean. The paired outcome was one pass-level
win, zero losses, nineteen ties, and a **+0.02916** mean delta. This meets the
registered tuned-development rule and advances the policy to an untouched
confirmation. It does not authorize a default change.

The run copied the accepted extracted-memory pack and changed retrieval only.
It kept every derived candidate, added at most one active source for each of ten
groups, and reused the same questions, answerer, judge, prompts, top-50 cutoff,
reference clock, and source-time policy. Hash checks confirmed that the original
DuckDB, vector, and lexical artifacts were unchanged. The
[registration](beam-100k-evidence-augmentation-ablation-v3-registration.json)
and [machine-readable result](beam-100k-evidence-augmentation-ablation-v3-results.json)
bind the protocol and aggregate outcome.

## Results

| Ability | Baseline pass | Augmented pass | Baseline score | Augmented score |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 0/2 | 0/2 | 0.00000 | 0.00000 |
| Contradiction resolution | 1/2 | 1/2 | 0.37500 | 0.37500 |
| Event ordering | 0/2 | 1/2 | 0.31665 | 0.46665 |
| Information extraction | 1/2 | 1/2 | 0.45835 | 0.50000 |
| Instruction following | 2/2 | 2/2 | 0.75000 | 0.75000 |
| Knowledge update | 2/2 | 2/2 | 1.00000 | 1.00000 |
| Multi-session reasoning | 2/2 | 2/2 | 0.87500 | 0.87500 |
| Preference following | 2/2 | 2/2 | 1.00000 | 1.00000 |
| Summarization | 2/2 | 2/2 | 0.65000 | 0.75000 |
| Temporal reasoning | 1/2 | 1/2 | 0.25000 | 0.25000 |
| **Overall** | **13/20** | **14/20** | **0.56750** | **0.59667** |

The recovered ordering question rose from 0.30 to 0.60. Detailed sprint
extraction rose from 0.9167 to 1.0, and both summarization questions gained 0.1.
The complete dependency list, the newest dashboard response time, contradiction
handling, preferences, and both multi-session answers retained their baseline
scores. This is the behavior that hard evidence caps and wholesale source
replacement failed to preserve.

## Remaining gaps

Both abstention questions still fail because related context encourages the
reader to invent an unsupported relation. One broad chronological question still
selects the wrong five milestones. The initial-schedule temporal question still
follows later sprint dates rather than the older January 15-to-March 15 plan.
Those failures need answerability and explicit timeline-state work rather than a
larger source quota.

## Scope

This is a tuned one-conversation development ablation over an already examined
pack and hosted model aliases. A positive result requires confirmation on a
separately registered, untouched conversation. It is not an official-scale BEAM
result or a market comparison.
